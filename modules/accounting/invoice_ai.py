import base64
import json
import mimetypes
import os
import re
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request as UrlRequest, urlopen


def safe_text(value):
    return "" if value is None else str(value).strip()


def to_decimal(value, default="0"):
    try:
        text = safe_text(value).replace(",", "")
        if text in ["", ".", "-", "-."]:
            text = default
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def decimal_float(value):
    return float(to_decimal(value).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP))


def extract_response_text(data):
    raw_text = data.get("output_text") or ""
    if raw_text:
        return raw_text
    for item in data.get("output", []):
        for content in item.get("content", []):
            raw_text += content.get("text", "")
    return raw_text


def extract_json(text):
    text = safe_text(text)
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text or "{}")


def normalize_ai_invoice(data, target_type):
    today_fallback = ""
    lines = data.get("lines") or []
    clean_lines = []
    for idx, line in enumerate(lines, start=1):
        desc = safe_text(line.get("description") or line.get("item_description") or f"Line {idx}")
        qty = to_decimal(line.get("qty") or line.get("quantity") or "1", "1")
        amount = to_decimal(line.get("amount") or line.get("line_amount") or "0")
        unit_price = to_decimal(line.get("unit_price") or line.get("price") or "0")
        if unit_price == 0 and qty != 0 and amount != 0:
            unit_price = amount / qty
        if amount == 0:
            amount = qty * unit_price
        if qty == 0 and amount != 0:
            qty = Decimal("1")
            unit_price = amount
        if amount == 0 and unit_price == 0 and not desc:
            continue
        clean_lines.append({
            "line_no": idx,
            "item_description": desc,
            "qty": qty,
            "unit_price": unit_price,
            "line_amount": amount,
        })

    if not clean_lines:
        total = to_decimal(data.get("subtotal") or data.get("total_amount") or data.get("net_amount") or "0")
        clean_lines = [{
            "line_no": 1,
            "item_description": safe_text(data.get("description")) or "AI extracted invoice",
            "qty": Decimal("1"),
            "unit_price": total,
            "line_amount": total,
        }]

    return {
        "target_type": target_type,
        "invoice_no": safe_text(data.get("invoice_no") or data.get("bill_no") or data.get("document_no")),
        "invoice_date": safe_text(data.get("invoice_date") or data.get("bill_date") or data.get("date")) or today_fallback,
        "due_date": safe_text(data.get("due_date")),
        "party_name": safe_text(data.get("party_name") or data.get("customer_name") or data.get("vendor_name")),
        "description": safe_text(data.get("description")) or "AI extracted invoice",
        "vat_rate": decimal_float(data.get("vat_rate") or data.get("tax_rate") or 14),
        "wht_rate": decimal_float(data.get("wht_rate") or 0),
        "lines": clean_lines,
    }


def parse_invoice_upload(file_name, content_type, file_bytes, target_type):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OpenAI API key is not configured.")
    if not file_bytes:
        raise RuntimeError("Uploaded file is empty.")

    guessed_type = mimetypes.guess_type(file_name or "")[0]
    if not content_type or content_type == "application/octet-stream":
        mime_type = guessed_type or "application/octet-stream"
    else:
        mime_type = content_type
    b64_data = base64.b64encode(file_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64_data}"
    file_item = {
        "type": "input_file" if mime_type == "application/pdf" else "input_image",
    }
    if mime_type == "application/pdf":
        file_item["filename"] = file_name or "invoice.pdf"
        file_item["file_data"] = data_url
    else:
        file_item["image_url"] = data_url

    prompt = {
        "task": "Extract invoice data from the uploaded document for an ERP draft.",
        "target_invoice_type": target_type,
        "rules": [
            "Return JSON only.",
            "Use YYYY-MM-DD for dates when possible.",
            "Do not invent missing values. Use empty strings or zero.",
            "For lines, return description, qty, unit_price, and amount.",
            "If this is a customer invoice, party_name is the customer.",
            "If this is a vendor bill, party_name is the vendor.",
        ],
        "schema": {
            "invoice_no": "string",
            "invoice_date": "YYYY-MM-DD",
            "due_date": "YYYY-MM-DD",
            "party_name": "string",
            "description": "string",
            "vat_rate": 0,
            "wht_rate": 0,
            "lines": [{"description": "string", "qty": 1, "unit_price": 0, "amount": 0}],
        },
    }
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": json.dumps(prompt, ensure_ascii=False)},
                file_item,
            ],
        }],
    }

    req = UrlRequest(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=45) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(body).get("error", {}).get("message") or body
        except Exception:
            message = body
        raise RuntimeError(f"OpenAI error: {message}") from e
    return normalize_ai_invoice(extract_json(extract_response_text(response_data)), target_type)


def find_partner_id(conn, partner_type, party_name):
    name = safe_text(party_name)
    if not name:
        return 0
    row = conn.execute(
        """
        SELECT id
        FROM partners
        WHERE partner_type = ?
          AND LOWER(TRIM(name)) = LOWER(TRIM(?))
        LIMIT 1
        """,
        (partner_type, name),
    ).fetchone()
    if row:
        return int(row["id"])
    row = conn.execute(
        """
        SELECT id
        FROM partners
        WHERE partner_type = ?
          AND (
              LOWER(TRIM(name)) LIKE LOWER(TRIM(?))
              OR LOWER(TRIM(?)) LIKE '%' || LOWER(TRIM(name)) || '%'
          )
        ORDER BY LENGTH(name) DESC
        LIMIT 1
        """,
        (partner_type, f"%{name}%", name),
    ).fetchone()
    return int(row["id"]) if row else 0


def missing_partner_card(partner_type, party_name, upload_action_url):
    label = "Customer" if partner_type == "customer" else "Vendor"
    add_href = "/ui/accounting/customers/new" if partner_type == "customer" else "/ui/accounting/vendors/new"
    name = safe_text(party_name)
    if name:
        add_href += f"?name={quote(name)}"
    return f"""
    {ai_upload_card(upload_action_url)}
    <div class="card">
        <div class="msg error" style="margin-bottom:14px;">{label} not found: {name or "Unknown"}</div>
        <div class="form-actions">
            <a class="btn green" href="{add_href}">+ Add {label}</a>
            <a class="btn gray" href="javascript:history.back()">Back</a>
        </div>
    </div>
    """


def save_uploaded_invoice(file_name, file_bytes):
    uploads_dir = Path("uploads") / "invoice_ai"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    original = safe_text(file_name) or "invoice"
    suffix = Path(original).suffix.lower()
    if suffix not in [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"]:
        suffix = mimetypes.guess_extension(mimetypes.guess_type(original)[0] or "") or ".bin"
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(original).stem).strip("-") or "invoice"
    stored_name = f"{stem[:40]}-{uuid.uuid4().hex[:10]}{suffix}"
    target = uploads_dir / stored_name
    target.write_bytes(file_bytes or b"")
    return f"/uploads/invoice_ai/{stored_name}", original


async def attachment_from_form(form):
    existing_url = safe_text(form.get("attachment_url"))
    existing_name = safe_text(form.get("attachment_name"))
    upload = form.get("invoice_attachment")
    if upload is not None and safe_text(getattr(upload, "filename", "")):
        file_bytes = await upload.read()
        if file_bytes:
            return save_uploaded_invoice(upload.filename, file_bytes)
    return existing_url, existing_name


async def attachments_from_form(form):
    uploads = []
    try:
        uploads.extend(form.getlist("invoice_attachments"))
    except Exception:
        pass
    single = form.get("invoice_attachment")
    if single is not None:
        uploads.append(single)

    saved = []
    seen = set()
    for upload in uploads:
        filename = safe_text(getattr(upload, "filename", ""))
        if not filename:
            continue
        key = id(upload)
        if key in seen:
            continue
        seen.add(key)
        file_bytes = await upload.read()
        if file_bytes:
            file_url, file_name = save_uploaded_invoice(filename, file_bytes)
            saved.append({"file_url": file_url, "file_name": file_name})
    return saved


def invoice_preview_card(file_url, file_name=""):
    if not file_url:
        return ""
    name = safe_text(file_name) or "Uploaded invoice"
    lower = file_url.lower()
    if lower.endswith(".pdf"):
        preview = f'<iframe src="{file_url}" style="width:100%;height:520px;border:1px solid #dce5f1;border-radius:8px;background:#fff;"></iframe>'
    elif lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")):
        preview = f'<img src="{file_url}" alt="{name}" style="max-width:100%;max-height:620px;border:1px solid #dce5f1;border-radius:8px;background:#fff;object-fit:contain;">'
    else:
        preview = f'<a class="btn blue" href="{file_url}" target="_blank">Open Uploaded File</a>'
    return f"""
    <div class="card" style="margin-bottom:16px;">
        <div class="toolbar" style="margin-bottom:12px;">
            <h3 style="margin:0;">Invoice Review</h3>
            <a class="btn gray" href="{file_url}" target="_blank">Open File</a>
        </div>
        <div style="color:#6f819d;margin-bottom:10px;">{name}</div>
        {preview}
    </div>
    """


def attachment_gallery(attachments):
    attachments = attachments or []
    if not attachments:
        return ""

    cards = ""
    for idx, item in enumerate(attachments, start=1):
        file_url = safe_text(item.get("file_url") or item.get("attachment_url"))
        file_name = safe_text(item.get("file_name") or item.get("attachment_name") or f"Attachment {idx}")
        lower = file_url.lower()
        kind = "PDF" if lower.endswith(".pdf") else "Image" if lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")) else "File"
        cards += f"""
        <button type="button" class="attachment-chip" data-url="{file_url}" data-name="{file_name}">
            <span class="attachment-kind">{kind}</span>
            <span class="attachment-name">{file_name}</span>
        </button>
        """

    return f"""
    <div class="card" style="margin-top:16px;">
        <div class="toolbar" style="margin-bottom:12px;">
            <h3 style="margin:0;">Attachments</h3>
        </div>
        <div class="attachment-list">{cards}</div>
    </div>
    <div id="attachmentViewer" class="attachment-viewer" style="display:none;">
        <div class="attachment-viewer-panel">
            <div class="attachment-viewer-head">
                <strong id="attachmentViewerTitle">Attachment</strong>
                <button type="button" class="btn gray" id="attachmentViewerClose">Close</button>
            </div>
            <div id="attachmentViewerBody" class="attachment-viewer-body"></div>
        </div>
    </div>
    <style>
        .attachment-list {{ display:flex; flex-wrap:wrap; gap:10px; }}
        .attachment-chip {{
            display:flex; align-items:center; gap:10px; max-width:320px;
            border:1px solid #dce5f1; background:#fff; color:#0b2d5c;
            border-radius:8px; padding:10px 12px; cursor:pointer; font-weight:700;
        }}
        .attachment-chip:hover {{ border-color:#2f6df6; box-shadow:0 6px 18px rgba(47,109,246,.12); }}
        .attachment-kind {{ background:#eef4ff; color:#2f6df6; border-radius:6px; padding:4px 8px; font-size:12px; }}
        .attachment-name {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
        .attachment-viewer {{
            position:fixed; inset:0; z-index:9999; background:rgba(5,20,45,.58);
            align-items:center; justify-content:center; padding:24px;
        }}
        .attachment-viewer-panel {{
            width:min(1100px,96vw); height:min(780px,92vh); background:#fff;
            border-radius:10px; overflow:hidden; display:flex; flex-direction:column;
        }}
        .attachment-viewer-head {{
            display:flex; align-items:center; justify-content:space-between;
            padding:12px 14px; border-bottom:1px solid #dce5f1;
        }}
        .attachment-viewer-body {{ flex:1; background:#f7f9fc; overflow:auto; }}
        .attachment-viewer-body iframe {{ width:100%; height:100%; border:0; background:#fff; }}
        .attachment-viewer-body img {{ display:block; max-width:100%; max-height:100%; margin:auto; object-fit:contain; }}
    </style>
    <script>
    (function() {{
        const modal = document.getElementById("attachmentViewer");
        const body = document.getElementById("attachmentViewerBody");
        const title = document.getElementById("attachmentViewerTitle");
        const close = document.getElementById("attachmentViewerClose");
        if (!modal || !body || !title || !close) return;
        function openAttachment(url, name) {{
            title.textContent = name || "Attachment";
            const lower = (url || "").toLowerCase();
            if (lower.endsWith(".pdf")) {{
                body.innerHTML = `<iframe src="${{url}}"></iframe>`;
            }} else if (lower.match(/\\.(png|jpg|jpeg|webp|bmp|gif)$/)) {{
                body.innerHTML = `<img src="${{url}}" alt="">`;
            }} else {{
                body.innerHTML = `<div style="padding:20px;"><a class="btn blue" href="${{url}}" target="_blank">Open File</a></div>`;
            }}
            modal.style.display = "flex";
        }}
        document.querySelectorAll(".attachment-chip").forEach(btn => {{
            btn.addEventListener("click", () => openAttachment(btn.dataset.url, btn.dataset.name));
        }});
        close.addEventListener("click", () => modal.style.display = "none");
        modal.addEventListener("click", (ev) => {{ if (ev.target === modal) modal.style.display = "none"; }});
    }})();
    </script>
    """


def ai_upload_card(action_url):
    return f"""
    <div class="card" style="margin-bottom:16px;">
        <h3>AI Invoice Upload</h3>
        <form method="post" action="{action_url}" enctype="multipart/form-data">
            <div class="form-grid">
                <div class="form-group">
                    <label>Invoice File</label>
                    <input type="file" name="file" accept=".pdf,image/*" required>
                </div>
            </div>
            <div class="form-actions">
                <button class="btn blue" type="submit">Read Invoice</button>
            </div>
        </form>
    </div>
    """
