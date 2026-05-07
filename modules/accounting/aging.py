from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from db import get_conn
from layout import render_page
from i18n import get_lang
from modules.accounting.allocation_engine import get_document_open_amount

router = APIRouter()


def money(x):
    try:
        return f"{float(x or 0):,.2f}"
    except Exception:
        return "0.00"


# =========================
# PARTNERS API
# =========================
@router.get("/ui/accounting/aging/partners")
def partners_api(partner_type: str = ""):
    conn = get_conn()

    rows = conn.execute("""
        SELECT id, code, name
        FROM partners
        WHERE LOWER(COALESCE(partner_type,'')) = LOWER(?)
          AND COALESCE(is_active,1) = 1
        ORDER BY name
    """, (partner_type,)).fetchall()

    items = []
    for r in rows:
        label = f"{r['code']} - {r['name']}" if r["code"] else (r["name"] or "")
        items.append({
            "id": r["id"],
            "text": label
        })

    conn.close()
    return JSONResponse({"items": items})


def partner_options(conn, partner_type: str, selected_id: str = ""):
    partner_type = (partner_type or "").strip().lower()
    selected_id = str(selected_id or "").strip()

    if partner_type not in ("customer", "vendor"):
        return "<option value=''>Select Partner</option>"

    rows = conn.execute("""
        SELECT id, code, name
        FROM partners
        WHERE LOWER(COALESCE(partner_type,'')) = ?
          AND COALESCE(is_active,1) = 1
        ORDER BY name
    """, (partner_type,)).fetchall()

    html = "<option value=''>All Partners</option>"
    for r in rows:
        label = f"{r['code']} - {r['name']}" if r["code"] else (r["name"] or "")
        selected = "selected" if selected_id == str(r["id"]) else ""
        html += f"<option value='{r['id']}' {selected}>{label}</option>"
    return html


# =========================
# HELPERS
# =========================
def parse_as_of(as_of: str):
    try:
        if as_of:
            return datetime.fromisoformat(as_of)
    except Exception:
        pass
    return datetime.today()


def days_past_due(due_date: str, as_of: str):
    if not due_date:
        return 0
    try:
        due = datetime.fromisoformat(due_date)
        today = parse_as_of(as_of)
        return (today - due).days
    except Exception:
        return 0


def get_bucket_name(days: int):
    if days <= 0:
        return "Not Due", "not_due"
    if days <= 30:
        return "1-30", "0_30"
    if days <= 60:
        return "31-60", "31_60"
    if days <= 90:
        return "61-90", "61_90"
    if days <= 120:
        return "91-120", "91_120"
    return "120+", "120_plus"


def empty_buckets():
    return {
        "not_due": 0.0,
        "0_30": 0.0,
        "31_60": 0.0,
        "61_90": 0.0,
        "91_120": 0.0,
        "120_plus": 0.0,
    }


# =========================
# DATA LOADERS
# =========================
def load_customer_aging(conn, partner_id: str, as_of: str):
    buckets = empty_buckets()
    rows_html = ""
    summary_map = {}
    total_outstanding = 0.0

    sql = """
        SELECT
            i.id,
            i.invoice_no AS doc_no,
            i.invoice_date AS doc_date,
            i.due_date,
            i.customer_id AS pid,
            p.code,
            p.name,
            COALESCE(i.net_amount, i.total_amount, 0) AS doc_total
        FROM customer_invoices i
        LEFT JOIN partners p ON p.id = i.customer_id
        WHERE LOWER(COALESCE(i.status,'')) = 'posted'
          AND COALESCE(i.reversed_journal_id, 0) = 0
    """
    params = []

    if partner_id:
        sql += " AND i.customer_id = ?"
        params.append(partner_id)

    sql += " ORDER BY p.name, i.due_date, i.id"

    docs = conn.execute(sql, params).fetchall()

    for inv in docs:
        open_amount = float(get_document_open_amount(conn, "customer_invoice", inv["id"]) or 0)

        if open_amount <= 0:
            continue

        days = days_past_due(inv["due_date"] or "", as_of)
        bucket_label, bucket_key = get_bucket_name(days)
        buckets[bucket_key] += open_amount
        total_outstanding += open_amount

        code = inv["code"] or ""
        name = inv["name"] or ""
        pid = inv["pid"]
        label = f"{code} - {name}" if code else name

        if pid not in summary_map:
            summary_map[pid] = {
                "label": label,
                "total": 0.0,
                "not_due": 0.0,
                "0_30": 0.0,
                "31_60": 0.0,
                "61_90": 0.0,
                "91_120": 0.0,
                "120_plus": 0.0,
            }

        summary_map[pid]["total"] += open_amount
        summary_map[pid][bucket_key] += open_amount

        allocated = float((float(inv["doc_total"] or 0) - open_amount))

        rows_html += f"""
        <tr>
            <td>{label}</td>
            <td>{inv['doc_no'] or ''}</td>
            <td>{inv['doc_date'] or ''}</td>
            <td>{inv['due_date'] or ''}</td>
            <td>{days}</td>
            <td>{bucket_label}</td>
            <td>{money(inv['doc_total'])}</td>
            <td>{money(allocated)}</td>
            <td>{money(open_amount)}</td>
        </tr>
        """

    return buckets, rows_html, summary_map, total_outstanding


def load_vendor_aging(conn, partner_id: str, as_of: str):
    buckets = empty_buckets()
    rows_html = ""
    summary_map = {}
    total_outstanding = 0.0

    sql = """
        SELECT
            b.id,
            b.bill_no AS doc_no,
            b.bill_date AS doc_date,
            b.due_date,
            b.vendor_id AS pid,
            p.code,
            p.name,
            COALESCE(b.net_amount, b.total_amount, 0) AS doc_total
        FROM vendor_bills b
        LEFT JOIN partners p ON p.id = b.vendor_id
        WHERE LOWER(COALESCE(b.status,'')) = 'posted'
          AND COALESCE(b.reversed_journal_id, 0) = 0
    """
    params = []

    if partner_id:
        sql += " AND b.vendor_id = ?"
        params.append(partner_id)

    sql += " ORDER BY p.name, b.due_date, b.id"

    docs = conn.execute(sql, params).fetchall()

    for bill in docs:
        open_amount = float(get_document_open_amount(conn, "vendor_bill", bill["id"]) or 0)

        if open_amount <= 0:
            continue

        days = days_past_due(bill["due_date"] or "", as_of)
        bucket_label, bucket_key = get_bucket_name(days)
        buckets[bucket_key] += open_amount
        total_outstanding += open_amount

        code = bill["code"] or ""
        name = bill["name"] or ""
        pid = bill["pid"]
        label = f"{code} - {name}" if code else name

        if pid not in summary_map:
            summary_map[pid] = {
                "label": label,
                "total": 0.0,
                "not_due": 0.0,
                "0_30": 0.0,
                "31_60": 0.0,
                "61_90": 0.0,
                "91_120": 0.0,
                "120_plus": 0.0,
            }

        summary_map[pid]["total"] += open_amount
        summary_map[pid][bucket_key] += open_amount

        allocated = float((float(bill["doc_total"] or 0) - open_amount))

        rows_html += f"""
        <tr>
            <td>{label}</td>
            <td>{bill['doc_no'] or ''}</td>
            <td>{bill['doc_date'] or ''}</td>
            <td>{bill['due_date'] or ''}</td>
            <td>{days}</td>
            <td>{bucket_label}</td>
            <td>{money(bill['doc_total'])}</td>
            <td>{money(allocated)}</td>
            <td>{money(open_amount)}</td>
        </tr>
        """

    return buckets, rows_html, summary_map, total_outstanding


def build_summary_rows(summary_map, partner_type: str, as_of: str):
    body = ""

    for pid, row in summary_map.items():
        link = f"/ui/accounting/aging?partner_type={partner_type}&partner_id={pid}&as_of={as_of}"
        body += f"""
        <tr>
            <td><a href="{link}">{row['label']}</a></td>
            <td>{money(row['not_due'])}</td>
            <td>{money(row['0_30'])}</td>
            <td>{money(row['31_60'])}</td>
            <td>{money(row['61_90'])}</td>
            <td>{money(row['91_120'])}</td>
            <td>{money(row['120_plus'])}</td>
            <td>{money(row['total'])}</td>
        </tr>
        """

    if not body:
        body = """
        <tr>
            <td colspan="8" style="text-align:center;">No open balances found.</td>
        </tr>
        """
    return body


# =========================
# MAIN PAGE
# =========================
@router.get("/ui/accounting/aging", response_class=HTMLResponse)
def aging_page(
    request: Request,
    partner_type: str = "",
    partner_id: str = "",
    as_of: str = "",
    embed: int = 0,
):
    lang = get_lang(request)
    conn = get_conn()

    partner_type = (partner_type or "").strip().lower()
    partner_id = (partner_id or "").strip()
    as_of = (as_of or "").strip()

    buckets = empty_buckets()
    rows_html = ""
    summary_map = {}
    total_outstanding = 0.0

    if partner_type == "customer":
        buckets, rows_html, summary_map, total_outstanding = load_customer_aging(conn, partner_id, as_of)
    elif partner_type == "vendor":
        buckets, rows_html, summary_map, total_outstanding = load_vendor_aging(conn, partner_id, as_of)

    summary_rows_html = build_summary_rows(summary_map, partner_type, as_of)

    customer_selected = "selected" if partner_type == "customer" else ""
    vendor_selected = "selected" if partner_type == "vendor" else ""
    partner_options_html = partner_options(conn, partner_type, partner_id)

    details_title = "Open Documents"
    doc_label = "Document No"

    html = f"""
    <div class="card">
        <h2>Aging Report</h2>

        <form method="get">
            <div class="row">

                <div class="col">
                    <label>Type</label>
                    <select name="partner_type" id="ptype">
                        <option value="">Select</option>
                        <option value="customer" {customer_selected}>Customer</option>
                        <option value="vendor" {vendor_selected}>Vendor</option>
                    </select>
                </div>

                <div class="col">
                    <label>Partner</label>
                    <select name="partner_id" id="partner">{partner_options_html}</select>
                </div>

                <div class="col">
                    <label>As Of</label>
                    <input type="date" name="as_of" value="{as_of}">
                </div>

            </div>

            <button class="btn green" type="submit" style="margin-top:10px;">Show</button>
            <a class="btn gray" style="margin-top:10px;" href="/ui/accounting/aging">Clear</a>
            <a class="btn gray" style="margin-top:10px;" href="/ui/accounting/export-center">Export</a>
        </form>
    </div>

    <div class="card">
        <h3>Summary</h3>

        <p><b>Not Due:</b> {money(buckets['not_due'])}</p>
        <p><b>1-30:</b> {money(buckets['0_30'])}</p>
        <p><b>31-60:</b> {money(buckets['31_60'])}</p>
        <p><b>61-90:</b> {money(buckets['61_90'])}</p>
        <p><b>91-120:</b> {money(buckets['91_120'])}</p>
        <p><b>120+:</b> {money(buckets['120_plus'])}</p>
        <p><b>Total Open:</b> {money(total_outstanding)}</p>

        <table style="margin-top:20px;">
            <tr>
                <th>Partner</th>
                <th>Not Due</th>
                <th>1-30</th>
                <th>31-60</th>
                <th>61-90</th>
                <th>91-120</th>
                <th>120+</th>
                <th>Total</th>
            </tr>
            {summary_rows_html}
        </table>
    </div>

    <div class="card">
        <h3>{details_title}</h3>

        <table style="margin-top:20px;">
            <tr>
                <th>Partner</th>
                <th>{doc_label}</th>
                <th>Document Date</th>
                <th>Due Date</th>
                <th>Days</th>
                <th>Bucket</th>
                <th>Total</th>
                <th>Allocated</th>
                <th>Open</th>
            </tr>
            {rows_html if rows_html else "<tr><td colspan='9' style='text-align:center;'>No open documents found.</td></tr>"}
        </table>
    </div>

    <script>
    async function loadPartners(selectedId=null) {{
        const type = document.getElementById("ptype").value;
        const sel = document.getElementById("partner");

        if (!type) {{
            sel.innerHTML = "<option value=''>Select Partner</option>";
            return;
        }}

        const res = await fetch(`/ui/accounting/aging/partners?partner_type=${{type}}`);
        const data = await res.json();

        sel.innerHTML = "<option value=''>All Partners</option>";

        data.items.forEach(i => {{
            const opt = document.createElement("option");
            opt.value = i.id;
            opt.text = i.text;

            if (selectedId && String(selectedId) === String(i.id)) {{
                opt.selected = true;
            }}

            sel.appendChild(opt);
        }});
    }}

    document.addEventListener("DOMContentLoaded", function() {{
        const ptype = document.getElementById("ptype");
        ptype.addEventListener("change", function() {{
            loadPartners();
        }});

        if (!document.getElementById("partner").options.length || document.getElementById("partner").value !== "{partner_id}") {{
            loadPartners("{partner_id}");
        }}
    }});
    </script>
    """

    conn.close()
    if int(embed or 0) == 1:
        return HTMLResponse(html)

    return HTMLResponse(render_page("Aging Report", html, lang, current_path=str(request.url.path)))
