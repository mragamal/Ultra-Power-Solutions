import json
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from audit import actor_name_from_request, render_audit_log_card, safe_log_action
from auth import can
from db import get_conn
from layout import render_page
from modules.accounting.accounting_engine import (
    create_journal_entry,
    post_journal_entry,
    submit_journal_for_final_post,
    reverse_journal_entry,
    delete_draft_journal_entry,
)
from modules.accounting.allocation_engine import (
    auto_allocate_vendor_payment,
    delete_payment_allocations,
    get_allocated_total_for_document,
    refresh_vendor_bill_payment_status,
)

router = APIRouter()


def accounting_allowed(request: Request, action: str) -> bool:
    return can(request, "accounting", action)


def permission_denied(en: str, ar: str):
    return HTMLResponse(en, status_code=403)


def safe(x):
    return "" if x is None else str(x).strip()


def to_decimal(value, default="0"):
    try:
        text = safe(value).replace(",", "")
        if text in ["", ".", "-", "-."]:
            text = default
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def money(value, places=2):
    try:
        d = Decimal(str(value or 0))
    except Exception:
        d = Decimal("0")
    q = Decimal("1." + ("0" * places))
    d = d.quantize(q, rounding=ROUND_HALF_UP)
    return f"{d:,.{places}f}"


def safe_int(x, default=0):
    try:
        if x is None or str(x).strip() == "":
            return default
        return int(float(x))
    except Exception:
        return default


def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


def get_setting_value(key: str, default=None):
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT value
            FROM accounting_settings
            WHERE key = ?
            LIMIT 1
        """, (key,)).fetchone()
        if row and row["value"] not in [None, ""]:
            return row["value"]
    except Exception:
        pass
    finally:
        conn.close()

    fallback = {
        "vendor_payment_prefix": "VP",
        "vendor_control_account": "211100",
        "default_cash_account": "111100",
        "default_bank_account": "111300",
    }
    return fallback.get(key, default)


def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendor_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_no TEXT,
            payment_date TEXT,
            vendor_id INTEGER,
            bill_id INTEGER,
            apply_mode TEXT DEFAULT 'against_bill',
            payment_method TEXT DEFAULT 'cash',
            payment_account_code TEXT,
            reference TEXT,
            description TEXT,
            amount REAL DEFAULT 0,
            status TEXT DEFAULT 'draft',
            journal_id INTEGER,
            reversed_journal_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendor_bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_no TEXT,
            bill_date TEXT,
            due_date TEXT,
            vendor_id INTEGER,
            vendor_name TEXT,
            description TEXT,
            payment_term_days INTEGER DEFAULT 0,
            subtotal REAL DEFAULT 0,
            vat_rate REAL DEFAULT 14,
            vat_amount REAL DEFAULT 0,
            wht_rate REAL DEFAULT 0,
            wht_amount REAL DEFAULT 0,
            total_amount REAL DEFAULT 0,
            net_amount REAL DEFAULT 0,
            payment_status TEXT DEFAULT 'unpaid',
            status TEXT DEFAULT 'draft',
            journal_id INTEGER,
            reversed_journal_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS partners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            partner_type TEXT DEFAULT 'vendor',
            phone TEXT,
            email TEXT,
            address TEXT,
            payment_term_days INTEGER DEFAULT 0,
            opening_balance REAL DEFAULT 0,
            account_code TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            name TEXT NOT NULL,
            type TEXT,
            parent_id INTEGER,
            level1 TEXT,
            level2 TEXT,
            statement_type TEXT,
            is_group INTEGER DEFAULT 0,
            allow_posting INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1
        )
    """)

    ensure_column(conn, "vendor_payments", "payment_no", "ALTER TABLE vendor_payments ADD COLUMN payment_no TEXT")
    ensure_column(conn, "vendor_payments", "payment_date", "ALTER TABLE vendor_payments ADD COLUMN payment_date TEXT")
    ensure_column(conn, "vendor_payments", "vendor_id", "ALTER TABLE vendor_payments ADD COLUMN vendor_id INTEGER")
    ensure_column(conn, "vendor_payments", "bill_id", "ALTER TABLE vendor_payments ADD COLUMN bill_id INTEGER")
    ensure_column(conn, "vendor_payments", "apply_mode", "ALTER TABLE vendor_payments ADD COLUMN apply_mode TEXT DEFAULT 'against_bill'")
    ensure_column(conn, "vendor_payments", "payment_method", "ALTER TABLE vendor_payments ADD COLUMN payment_method TEXT DEFAULT 'cash'")
    ensure_column(conn, "vendor_payments", "payment_account_code", "ALTER TABLE vendor_payments ADD COLUMN payment_account_code TEXT")
    ensure_column(conn, "vendor_payments", "reference", "ALTER TABLE vendor_payments ADD COLUMN reference TEXT")
    ensure_column(conn, "vendor_payments", "description", "ALTER TABLE vendor_payments ADD COLUMN description TEXT")
    ensure_column(conn, "vendor_payments", "amount", "ALTER TABLE vendor_payments ADD COLUMN amount REAL DEFAULT 0")
    ensure_column(conn, "vendor_payments", "status", "ALTER TABLE vendor_payments ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "vendor_payments", "journal_id", "ALTER TABLE vendor_payments ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "vendor_payments", "reversed_journal_id", "ALTER TABLE vendor_payments ADD COLUMN reversed_journal_id INTEGER")
    ensure_column(conn, "vendor_payments", "created_at", "ALTER TABLE vendor_payments ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")
    ensure_column(conn, "vendor_bills", "payment_status", "ALTER TABLE vendor_bills ADD COLUMN payment_status TEXT DEFAULT 'unpaid'")
    ensure_column(conn, "vendor_bills", "reversed_journal_id", "ALTER TABLE vendor_bills ADD COLUMN reversed_journal_id INTEGER")

    conn.commit()
    conn.close()


def next_payment_no():
    prefix = get_setting_value("vendor_payment_prefix", "VP")
    conn = get_conn()
    row = conn.execute("""
        SELECT payment_no
        FROM vendor_payments
        WHERE COALESCE(payment_no, '') <> ''
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["payment_no"]:
        return f"{prefix}-0000001"

    last = str(row["payment_no"])
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0

    return f"{prefix}-{num + 1:07d}"


def vendor_rows():
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, code, name, account_code
        FROM partners
        WHERE partner_type = 'vendor'
          AND COALESCE(is_active, 1) = 1
        ORDER BY name
    """).fetchall()
    conn.close()
    return rows


def vendor_options(selected_id=None):
    html = "<option value=''>-- Select Vendor --</option>"
    for r in vendor_rows():
        selected = "selected" if str(selected_id or "") == str(r["id"]) else ""
        label = f"{safe(r['code'])} - {safe(r['name'])}" if safe(r["code"]) else safe(r["name"])
        html += f"<option value='{r['id']}' {selected}>{label}</option>"
    return html


def payment_account_options(selected_code=""):
    conn = get_conn()
    rows = conn.execute("""
        SELECT code, name
        FROM accounts
        WHERE COALESCE(is_active, 1) = 1
          AND COALESCE(is_group, 0) = 0
          AND COALESCE(allow_posting, 1) = 1
        ORDER BY code, name
    """).fetchall()
    conn.close()
    html = "<option value=''>-- Select Liquidity Account --</option>"
    for row in rows:
        sel = "selected" if safe(selected_code) == safe(row["code"]) else ""
        html += f"<option value='{safe(row['code'])}' {sel}>{safe(row['code'])} - {safe(row['name'])}</option>"
    return html


def get_vendor(conn, vendor_id: int):
    return conn.execute("""
        SELECT *
        FROM partners
        WHERE id = ?
          AND partner_type = 'vendor'
        LIMIT 1
    """, (vendor_id,)).fetchone()


def bill_select_options(selected_bill_id=None):
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, bill_no, bill_date, due_date, vendor_id, vendor_name, net_amount, payment_status, status, COALESCE(reversed_journal_id, 0) AS reversed_journal_id
        FROM vendor_bills
        ORDER BY id DESC
    """).fetchall()
    html = "<option value=''>-- Select Bill --</option>"
    for r in rows:
        sel = "selected" if str(selected_bill_id or "") == str(r["id"]) else ""
        net_amount = Decimal(str(r["net_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
        paid_amount = get_bill_paid_amount(conn, r["id"])
        open_amount = (net_amount - paid_amount).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
        if open_amount < Decimal("0.00"):
            open_amount = Decimal("0.00")
        html += (
            f"<option value='{r['id']}' "
            f"data-vendor-id='{safe(r['vendor_id'])}' "
            f"data-vendor-name='{safe(r['vendor_name'])}' "
            f"data-bill-no='{safe(r['bill_no'])}' "
            f"data-bill-date='{safe(r['bill_date'])}' "
            f"data-due-date='{safe(r['due_date'])}' "
            f"data-net-amount='{safe(r['net_amount'])}' "
            f"data-paid-amount='{paid_amount}' "
            f"data-open-amount='{open_amount}' "
            f"data-status='{safe(r['status'])}' "
            f"data-payment-status='{safe(r['payment_status'])}' "
            f"data-reversed='{safe(r['reversed_journal_id'])}' "
            f"{sel}>{safe(r['bill_no'])}</option>"
        )
    conn.close()
    return html


def get_bill(conn, bill_id: int):
    return conn.execute("""
        SELECT *
        FROM vendor_bills
        WHERE id = ?
        LIMIT 1
    """, (bill_id,)).fetchone()


def get_bill_paid_amount(conn, bill_id: int):
    return get_allocated_total_for_document(conn, "vendor_bill", bill_id)


def refresh_bill_payment_status(conn, bill_id: int):
    refresh_vendor_bill_payment_status(conn, bill_id)


def get_bill_snapshot(conn, bill_id: int):
    bill = get_bill(conn, bill_id)
    if not bill:
        return None

    net_amount = Decimal(str(bill["net_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    paid_amount = get_bill_paid_amount(conn, bill_id)
    open_amount = (net_amount - paid_amount).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    if open_amount < Decimal("0.00"):
        open_amount = Decimal("0.00")

    return {
        "id": bill["id"],
        "bill_no": safe(bill["bill_no"]),
        "bill_date": safe(bill["bill_date"]),
        "due_date": safe(bill["due_date"]),
        "vendor_name": safe(bill["vendor_name"]),
        "net_amount": str(net_amount),
        "paid_amount": str(paid_amount),
        "open_amount": str(open_amount),
        "status": safe(bill["status"]),
        "payment_status": safe(bill["payment_status"]),
        "reversed_journal_id": safe(bill["reversed_journal_id"]),
    }


def validate_vendor_payment(conn, vendor_id: int, bill_id: int, apply_mode: str, amount: Decimal, payment_account_code: str):
    vendor = get_vendor(conn, vendor_id)
    if not vendor:
        raise Exception("Vendor not found")

    if amount <= Decimal("0.00"):
        raise Exception("Amount must be greater than zero")

    if not safe(payment_account_code):
        raise Exception("Liquidity account is required")

    bill = None
    if safe(apply_mode).lower() == "against_bill":
        if bill_id <= 0:
            raise Exception("Bill is required in against bill mode")
        bill = get_bill(conn, bill_id)
        if not bill:
            raise Exception("Bill not found")
        if bill["vendor_id"] != vendor_id:
            raise Exception("Bill does not belong to selected vendor")
        if safe(bill["status"]).lower() != "posted":
            raise Exception("Only posted bills can be paid")
        if bill["reversed_journal_id"]:
            raise Exception("Reversed bill cannot receive payment")

        open_amount = get_bill_snapshot(conn, bill_id)
        bill_open = Decimal(str(open_amount["open_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
        if bill_open <= Decimal("0.00"):
            raise Exception("Selected bill is already fully paid")
        if amount > bill_open:
            raise Exception(f"Payment amount exceeds bill open amount ({bill_open})")

    return vendor, bill


def build_vendor_payment_journal_lines(payment, vendor):
    amount = Decimal(str(payment["amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    if amount <= Decimal("0"):
        raise Exception("Payment amount must be greater than zero")
    payment_account = safe(payment["payment_account_code"])
    if not payment_account:
        raise Exception("Liquidity account is required")
    vendor_account = safe(vendor["account_code"]) or safe(get_setting_value("vendor_control_account", "112100"))
    if not vendor_account:
        raise Exception("Vendor control account is missing")
    return [
        {
            "description": f"Liquidity for {payment['payment_no']}",
            "account_code": payment_account,
            "debit": amount,
            "credit": Decimal("0"),
            "partner_type": "vendor",
            "partner_id": payment["vendor_id"],
        },
        {
            "description": f"Vendor settlement for {payment['payment_no']}",
            "account_code": vendor_account,
            "debit": Decimal("0"),
            "credit": amount,
            "partner_type": "vendor",
            "partner_id": payment["vendor_id"],
        },
    ]


def create_vendor_payment_draft_journal(conn, payment_id: int):
    payment = conn.execute("""
        SELECT *
        FROM vendor_payments
        WHERE id = ?
        LIMIT 1
    """, (payment_id,)).fetchone()
    if not payment:
        raise Exception("Payment not found")
    vendor = get_vendor(conn, payment["vendor_id"])
    if not vendor:
        raise Exception("Vendor not found")
    journal_lines = build_vendor_payment_journal_lines(payment, vendor)
    journal_id = create_journal_entry(
        conn=conn,
        entry_date=payment["payment_date"],
        description=f"Vendor Payment {payment['payment_no']}",
        reference=payment["payment_no"],
        source_type="vendor_payment",
        source_id=payment["id"],
        lines=journal_lines,
    )
    conn.execute("UPDATE vendor_payments SET journal_id = ? WHERE id = ?", (journal_id, payment_id))
    return journal_id


def payment_form(values=None, row_id=None, readonly=False, error_message=""):
    values = values or {}
    action = f"/ui/accounting/vendor-payments/{row_id}/edit" if row_id else "/ui/accounting/vendor-payments/new"
    form_title = "View Vendor Payment" if readonly else ("Edit Vendor Payment" if row_id else "New Vendor Payment")
    text_readonly = "readonly" if readonly else ""
    select_disabled = "disabled" if readonly else ""
    save_button = "" if readonly else '<button class="btn green" type="submit">Save Draft</button>'
    selected_bill_id = safe_int(values.get("bill_id"), 0)
    selected_bill_json = "{}"
    if selected_bill_id > 0:
        conn = get_conn()
        selected_bill = get_bill_snapshot(conn, selected_bill_id)
        conn.close()
        if selected_bill:
            selected_bill_json = json.dumps(selected_bill)
    error_html = f'<div class="msg error">{safe(error_message)}</div>' if error_message else ""
    return f"""
    {error_html}
    <div class="card">
        <h2>{form_title}</h2>
        <form method="post" action="{action}">
            <div class="form-grid">
                <div class="form-group">
                    <label>Payment No</label>
                    <input type="text" name="payment_no" value="{safe(values.get('payment_no', next_payment_no()))}" required {text_readonly}>
                </div>
                <div class="form-group">
                    <label>Payment Date</label>
                    <input type="date" name="payment_date" value="{safe(values.get('payment_date', ''))}" {"readonly" if readonly else "required"}>
                </div>
                <div class="form-group">
                    <label>Vendor</label>
                    <select id="vendor_id" name="vendor_id" {select_disabled} {"required" if not readonly else ""}>
                        {vendor_options(values.get('vendor_id', ''))}
                    </select>
                    {"<input type='hidden' name='vendor_id' value='%s'>" % safe(values.get('vendor_id', '')) if readonly else ""}
                </div>
                <div class="form-group">
                    <label>Apply Mode</label>
                    <select id="apply_mode" name="apply_mode" {select_disabled}>
                        <option value="against_bill" {"selected" if safe(values.get('apply_mode', 'against_bill')) == "against_bill" else ""}>Against Bill</option>
                        <option value="on_account" {"selected" if safe(values.get('apply_mode', 'against_bill')) == "on_account" else ""}>On Account</option>
                    </select>
                    {"<input type='hidden' name='apply_mode' value='%s'>" % safe(values.get('apply_mode', 'against_bill')) if readonly else ""}
                </div>
                <div class="form-group">
                    <label>Bill</label>
                    <select id="bill_id" name="bill_id" {select_disabled}>
                        {bill_select_options(values.get('bill_id', ''))}
                    </select>
                    {"<input type='hidden' name='bill_id' value='%s'>" % safe(values.get('bill_id', '')) if readonly else ""}
                </div>
                <div class="form-group">
                    <label>Bill No</label>
                    <input type="text" id="bill_preview_no" readonly>
                </div>
                <div class="form-group">
                    <label>Bill Date</label>
                    <input type="text" id="bill_preview_date" readonly>
                </div>
                <div class="form-group">
                    <label>Due Date</label>
                    <input type="text" id="bill_preview_due" readonly>
                </div>
                <div class="form-group">
                    <label>Bill Status</label>
                    <input type="text" id="bill_preview_status" readonly>
                </div>
                <div class="form-group">
                    <label>Paid Amount</label>
                    <input type="text" id="bill_preview_paid" readonly>
                </div>
                <div class="form-group">
                    <label>Open Amount</label>
                    <input type="text" id="bill_preview_open" readonly>
                </div>
                <div class="form-group">
                    <label>Payment Method</label>
                    <select name="payment_method" {select_disabled}>
                        <option value="cash" {"selected" if safe(values.get('payment_method', 'cash')) == "cash" else ""}>Cash</option>
                        <option value="bank" {"selected" if safe(values.get('payment_method', 'cash')) == "bank" else ""}>Bank</option>
                    </select>
                    {"<input type='hidden' name='payment_method' value='%s'>" % safe(values.get('payment_method', 'cash')) if readonly else ""}
                </div>
                <div class="form-group">
                    <label>Liquidity Account</label>
                    <select name="payment_account_code" {select_disabled}>
                        {payment_account_options(values.get('payment_account_code', ''))}
                    </select>
                    {"<input type='hidden' name='payment_account_code' value='%s'>" % safe(values.get('payment_account_code', '')) if readonly else ""}
                </div>
                <div class="form-group">
                    <label>Amount</label>
                    <input type="text" inputmode="decimal" name="amount" value="{safe(values.get('amount', '0.00'))}" {text_readonly}>
                </div>
                <div class="form-group">
                    <label>Reference</label>
                    <input type="text" name="reference" value="{safe(values.get('reference', ''))}" {text_readonly}>
                </div>
                <div class="form-group">
                    <label>Description</label>
                    <input type="text" name="description" value="{safe(values.get('description', ''))}" {text_readonly}>
                </div>
                <div class="form-group">
                    <label>Status</label>
                    <input type="text" value="{safe(values.get('status', 'draft'))}" readonly>
                </div>
            </div>
            <div class="form-actions">
                {save_button}
                <a class="btn gray" href="/ui/accounting/vendor-payments">Back</a>
            </div>
        </form>
    </div>
    <script>
    (function() {{
        const isReadonly = {"true" if readonly else "false"};
        const initialBill = {selected_bill_json};
        function setBillPreview(data) {{
            const fields = {{
                bill_preview_no: data?.bill_no || "",
                bill_preview_date: data?.bill_date || "",
                bill_preview_due: data?.due_date || "",
                bill_preview_status: data ? [data.status, data.payment_status].filter(Boolean).join(" / ") : "",
                bill_preview_paid: data?.paid_amount || "",
                bill_preview_open: data?.open_amount || "",
            }};
            Object.keys(fields).forEach((id) => {{
                const el = document.getElementById(id);
                if (el) el.value = fields[id];
            }});
        }}

        function currentBillData() {{
            const billSelect = document.getElementById("bill_id");
            if (!billSelect || !billSelect.value) return null;
            const selected = billSelect.options[billSelect.selectedIndex];
            if (!selected || !selected.value || selected.hidden) return null;
            return {{
                bill_no: selected.getAttribute("data-bill-no") || "",
                bill_date: selected.getAttribute("data-bill-date") || "",
                due_date: selected.getAttribute("data-due-date") || "",
                status: selected.getAttribute("data-status") || "",
                payment_status: selected.getAttribute("data-payment-status") || "",
                paid_amount: selected.getAttribute("data-paid-amount") || "",
                open_amount: selected.getAttribute("data-open-amount") || "",
            }};
        }}

        function maybeSuggestAmount() {{
            if (isReadonly) return;
            const amountInput = document.querySelector('input[name="amount"]');
            const applyMode = document.getElementById("apply_mode")?.value || "against_bill";
            const bill = currentBillData();
            if (!amountInput || applyMode !== "against_bill" || !bill) return;
            const current = parseFloat(amountInput.value || "0");
            if (!amountInput.dataset.userEdited || !isFinite(current) || current <= 0) {{
                amountInput.value = bill.open_amount || amountInput.value;
            }}
        }}

        function filterBills() {{
            const vendorId = document.getElementById("vendor_id")?.value || "";
            const applyMode = document.getElementById("apply_mode")?.value || "against_bill";
            const billSelect = document.getElementById("bill_id");
            if (!billSelect) return;
            Array.from(billSelect.options).forEach((opt, idx) => {{
                if (idx === 0) {{
                    opt.hidden = false;
                    return;
                }}
                const optVendor = opt.getAttribute("data-vendor-id") || "";
                const optStatus = opt.getAttribute("data-status") || "";
                const optPaymentStatus = opt.getAttribute("data-payment-status") || "";
                const optReversed = opt.getAttribute("data-reversed") || "0";
                let visible = true;
                if (applyMode === "on_account") {{
                    visible = false;
                }} else {{
                    visible = optVendor === vendorId && optStatus === "posted" && optPaymentStatus !== "paid" && (optReversed === "" || optReversed === "0");
                }}
                opt.hidden = !visible;
            }});
            if (applyMode === "on_account") {{
                billSelect.value = "";
                billSelect.disabled = true;
            }} else {{
                billSelect.disabled = isReadonly ? true : false;
                const selected = billSelect.options[billSelect.selectedIndex];
                if (selected && selected.hidden) {{
                    billSelect.value = "";
                }}
            }}
            setBillPreview(applyMode === "against_bill" ? currentBillData() : null);
            maybeSuggestAmount();
        }}
        document.addEventListener("DOMContentLoaded", function() {{
            const vendorSelect = document.getElementById("vendor_id");
            const applyMode = document.getElementById("apply_mode");
            const billSelect = document.getElementById("bill_id");
            const amountInput = document.querySelector('input[name="amount"]');
            if (vendorSelect && !isReadonly) vendorSelect.addEventListener("change", filterBills);
            if (applyMode && !isReadonly) applyMode.addEventListener("change", filterBills);
            if (billSelect && !isReadonly) billSelect.addEventListener("change", filterBills);
            if (amountInput && !isReadonly) amountInput.addEventListener("input", function() {{
                amountInput.dataset.userEdited = "1";
            }});
            setBillPreview(initialBill && initialBill.bill_no ? initialBill : null);
            filterBills();
        }});
    }})();
    </script>
    """


def render_payment_page(request: Request, title: str, values=None, row_id=None, readonly=False, status_code=200, error_message=""):
    content = payment_form(values=values, row_id=row_id, readonly=readonly, error_message=error_message)
    return HTMLResponse(render_page(title, content, current_path=str(request.url.path)), status_code=status_code)


ensure_tables()

@router.get("/ui/accounting/vendor-payments", response_class=HTMLResponse)
def list_payments(request: Request):
    can_create_perm = accounting_allowed(request, "create")
    can_edit_perm = accounting_allowed(request, "edit")
    can_post_perm = accounting_allowed(request, "post")
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.*, c.name AS vendor_name, i.bill_no
        FROM vendor_payments p
        LEFT JOIN partners c ON c.id = p.vendor_id
        LEFT JOIN vendor_bills i ON i.id = p.bill_id
        ORDER BY p.id DESC
    """).fetchall()
    conn.close()
    rows_html = ""
    for r in rows:
        edit_btn = ""
        post_btn = ""
        reverse_btn = ""
        if safe(r["status"]).lower() == "draft" and can_edit_perm:
            edit_btn = f"<a class='btn green' href='/ui/accounting/vendor-payments/{r['id']}/edit'>Edit</a>"
        if safe(r["status"]).lower() == "draft" and can_post_perm:
            post_btn = f"<form method='post' action='/ui/accounting/vendor-payments/{r['id']}/post' style='display:inline;'><button class='btn green' type='submit'>Post</button></form>"
        rows_html += f"""
        <tr>
            <td>{safe(r['payment_no'])}</td>
            <td>{safe(r['payment_date'])}</td>
            <td>{safe(r['vendor_name'])}</td>
            <td>{safe(r['apply_mode'])}</td>
            <td>{safe(r['bill_no'])}</td>
            <td>{safe(r['payment_method'])}</td>
            <td>{safe(r['payment_account_code'])}</td>
            <td>{money(r['amount'])}</td>
            <td>{safe(r['status'])}</td>
            <td>{safe(r['journal_id'])}</td>
            <td>{safe(r['reversed_journal_id'])}</td>
            <td><a class="btn blue" href="/ui/accounting/vendor-payments/{r['id']}/view">View</a>{edit_btn}{post_btn}</td>
        </tr>
        """
    if not rows_html:
        rows_html = "<tr><td colspan='12'>No vendor payments found.</td></tr>"
    content = f"""
    <div class="table-header">
        <h3>Vendor Payments</h3>
        {"<a class='btn green' href='/ui/accounting/vendor-payments/new'>+ New Payment</a>" if can_create_perm else ""}
    </div>
    <table>
        <tr><th>No</th><th>Date</th><th>Vendor</th><th>Apply Mode</th><th>Bill</th><th>Method</th><th>Liquidity Account</th><th>Amount</th><th>Status</th><th>Journal</th><th>Reverse Journal</th><th>Action</th></tr>
        {rows_html}
    </table>
    """
    return HTMLResponse(render_page("Vendor Payments", content, current_path=str(request.url.path)))

@router.get("/ui/accounting/vendor-payments/new", response_class=HTMLResponse)
def new_payment(request: Request, vendor_id: str = "", bill_id: str = ""):
    if not accounting_allowed(request, "create"):
        return permission_denied("You do not have permission to create vendor payments.", "ليس لديك صلاحية إنشاء مدفوعات الموردين.")
    today = datetime.today().date().isoformat()
    selected_vendor_id = safe_int(vendor_id, 0)
    selected_bill_id = safe_int(bill_id, 0)
    values = {
        "payment_no": next_payment_no(),
        "payment_date": today,
        "apply_mode": "against_bill",
        "payment_method": "cash",
        "payment_account_code": get_setting_value("default_cash_account", "111100"),
        "amount": "0.00",
        "status": "draft",
        "vendor_id": selected_vendor_id if selected_vendor_id > 0 else "",
        "bill_id": "",
    }
    if selected_bill_id > 0:
        conn = get_conn()
        bill = get_bill(conn, selected_bill_id)
        if bill and safe(bill["status"]).lower() == "posted" and not bill["reversed_journal_id"]:
            values["bill_id"] = selected_bill_id
            values["vendor_id"] = bill["vendor_id"]
            open_amount = Decimal(str(bill["net_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
            paid = get_bill_paid_amount(conn, selected_bill_id)
            remaining = (open_amount - paid).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
            if remaining > Decimal("0"):
                values["amount"] = str(remaining)
        conn.close()
    return render_payment_page(request, "New Vendor Payment", values=values)

@router.post("/ui/accounting/vendor-payments/new")
async def create_payment(request: Request):
    if not accounting_allowed(request, "create"):
        return permission_denied("You do not have permission to create vendor payments.", "ليس لديك صلاحية إنشاء مدفوعات الموردين.")
    form = await request.form()
    payment_no = safe(form.get("payment_no"))
    payment_date = safe(form.get("payment_date"))
    vendor_id = safe_int(form.get("vendor_id"))
    bill_id = safe_int(form.get("bill_id"), 0)
    apply_mode = safe(form.get("apply_mode")) or "against_bill"
    payment_method = safe(form.get("payment_method")) or "cash"
    payment_account_code = safe(form.get("payment_account_code"))
    reference = safe(form.get("reference"))
    description = safe(form.get("description"))
    amount = to_decimal(form.get("amount"), "0").quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    values = {
        "payment_no": payment_no,
        "payment_date": payment_date,
        "vendor_id": safe(form.get("vendor_id")),
        "bill_id": safe(form.get("bill_id")),
        "apply_mode": apply_mode,
        "payment_method": payment_method,
        "payment_account_code": payment_account_code,
        "reference": reference,
        "description": description,
        "amount": str(amount),
        "status": "draft",
    }
    conn = get_conn()
    try:
        vendor, bill = validate_vendor_payment(conn, vendor_id, bill_id, apply_mode, amount, payment_account_code)
        if safe(apply_mode).lower() != "against_bill":
            bill_id = None
        cur = conn.execute("""
            INSERT INTO vendor_payments (
                payment_no, payment_date, vendor_id, bill_id,
                apply_mode, payment_method, payment_account_code,
                reference, description, amount, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
        """, (payment_no, payment_date, vendor_id, bill_id, apply_mode, payment_method, payment_account_code, reference, description, float(amount)))
        payment_id = cur.lastrowid
        create_vendor_payment_draft_journal(conn, payment_id)
        safe_log_action(
            "vendor_payment",
            payment_id,
            "Created",
            done_by=actor_name_from_request(request),
            notes=f"Draft vendor payment created for {safe(vendor['name'])} | Amount: {amount}",
            conn=conn,
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return render_payment_page(request, "New Vendor Payment", values=values, status_code=400, error_message=safe(e))
    conn.close()
    return RedirectResponse(f"/ui/accounting/vendor-payments/{payment_id}/view", status_code=303)

@router.get("/ui/accounting/vendor-payments/{row_id}/edit", response_class=HTMLResponse)
def edit_payment(request: Request, row_id: int):
    if not accounting_allowed(request, "edit"):
        return permission_denied("You do not have permission to edit vendor payments.", "ليس لديك صلاحية تعديل مدفوعات الموردين.")
    conn = get_conn()
    row = conn.execute("SELECT * FROM vendor_payments WHERE id = ? LIMIT 1", (row_id,)).fetchone()
    conn.close()
    if not row:
        return HTMLResponse("Payment not found", status_code=404)
    if safe(row["status"]).lower() != "draft":
        return HTMLResponse("Only draft payments can be edited", status_code=400)
    return render_payment_page(request, "Edit Vendor Payment", values=dict(row), row_id=row_id)

@router.post("/ui/accounting/vendor-payments/{row_id}/edit")
async def update_payment(request: Request, row_id: int):
    if not accounting_allowed(request, "edit"):
        return permission_denied("You do not have permission to edit vendor payments.", "ليس لديك صلاحية تعديل مدفوعات الموردين.")
    form = await request.form()
    payment_no = safe(form.get("payment_no"))
    payment_date = safe(form.get("payment_date"))
    vendor_id = safe_int(form.get("vendor_id"))
    bill_id = safe_int(form.get("bill_id"), 0)
    apply_mode = safe(form.get("apply_mode")) or "against_bill"
    payment_method = safe(form.get("payment_method")) or "cash"
    payment_account_code = safe(form.get("payment_account_code"))
    reference = safe(form.get("reference"))
    description = safe(form.get("description"))
    amount = to_decimal(form.get("amount"), "0").quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    values = {
        "payment_no": payment_no,
        "payment_date": payment_date,
        "vendor_id": safe(form.get("vendor_id")),
        "bill_id": safe(form.get("bill_id")),
        "apply_mode": apply_mode,
        "payment_method": payment_method,
        "payment_account_code": payment_account_code,
        "reference": reference,
        "description": description,
        "amount": str(amount),
        "status": "draft",
    }
    conn = get_conn()
    existing = conn.execute("SELECT * FROM vendor_payments WHERE id = ? LIMIT 1", (row_id,)).fetchone()
    if not existing:
        conn.close()
        return HTMLResponse("Payment not found", status_code=404)
    if safe(existing["status"]).lower() != "draft":
        conn.close()
        return HTMLResponse("Only draft payments can be edited", status_code=400)
    try:
        vendor, bill = validate_vendor_payment(conn, vendor_id, bill_id, apply_mode, amount, payment_account_code)
        if safe(apply_mode).lower() != "against_bill":
            bill_id = None
        conn.execute("""
            UPDATE vendor_payments
            SET payment_no = ?, payment_date = ?, vendor_id = ?, bill_id = ?,
                apply_mode = ?, payment_method = ?, payment_account_code = ?,
                reference = ?, description = ?, amount = ?
            WHERE id = ?
        """, (payment_no, payment_date, vendor_id, bill_id, apply_mode, payment_method, payment_account_code, reference, description, float(amount), row_id))
        old_journal_id = existing["journal_id"]
        if old_journal_id:
            delete_draft_journal_entry(conn, old_journal_id)
        create_vendor_payment_draft_journal(conn, row_id)
        safe_log_action(
            "vendor_payment",
            row_id,
            "Updated",
            done_by=actor_name_from_request(request),
            notes=f"Draft vendor payment updated for {safe(vendor['name'])} | Amount: {amount}",
            conn=conn,
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return render_payment_page(request, "Edit Vendor Payment", values=values, row_id=row_id, status_code=400, error_message=safe(e))
    conn.close()
    return RedirectResponse(f"/ui/accounting/vendor-payments/{row_id}/view", status_code=303)

@router.post("/ui/accounting/vendor-payments/{row_id}/post")
def post_payment(request: Request, row_id: int):
    if not accounting_allowed(request, "post"):
        return permission_denied("You do not have permission to post vendor payments.", "ليس لديك صلاحية ترحيل مدفوعات الموردين.")
    conn = get_conn()
    try:
        payment = conn.execute("SELECT * FROM vendor_payments WHERE id = ? LIMIT 1", (row_id,)).fetchone()
        if not payment:
            raise Exception("Payment not found")
        if safe(payment["status"]).lower() != "draft":
            raise Exception("Only draft payments can be posted")
        if safe(payment["apply_mode"]).lower() == "against_bill":
            if not payment["bill_id"]:
                raise Exception("Bill is required for against bill mode")
            amount = Decimal(str(payment["amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
            validate_vendor_payment(
                conn,
                safe_int(payment["vendor_id"]),
                safe_int(payment["bill_id"]),
                safe(payment["apply_mode"]),
                amount,
                safe(payment["payment_account_code"]),
            )
        if not payment["journal_id"]:
            create_vendor_payment_draft_journal(conn, row_id)
            payment = conn.execute("SELECT * FROM vendor_payments WHERE id = ?", (row_id,)).fetchone()
        bill_before = get_bill(conn, payment["bill_id"]) if payment["bill_id"] else None
        old_payment_status = safe(bill_before["payment_status"]).lower() if bill_before else ""
        submit_journal_for_final_post(conn, payment["journal_id"])
        conn.execute("UPDATE vendor_payments SET status = 'posted' WHERE id = ?", (row_id,))
        safe_log_action(
            "vendor_payment",
            row_id,
            "Posted",
            done_by=actor_name_from_request(request),
            notes=f"Payment {safe(payment['payment_no'])} moved to posted and journal is waiting final post.",
            conn=conn,
        )
        auto_allocate_vendor_payment(conn, row_id)
        if payment["bill_id"]:
            refresh_bill_payment_status(conn, payment["bill_id"])
            bill_after = get_bill(conn, payment["bill_id"])
            new_payment_status = safe(bill_after["payment_status"]).lower() if bill_after else ""
            if bill_after and old_payment_status != new_payment_status:
                safe_log_action(
                    "vendor_bill",
                    payment["bill_id"],
                    "Payment Status Changed",
                    done_by=actor_name_from_request(request),
                    notes=f"Bill {safe(bill_after['bill_no'])} payment status changed from {old_payment_status or '-'} to {new_payment_status or '-'}.",
                    conn=conn,
                )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Post error: {safe(e)}", status_code=400)
    conn.close()
    return RedirectResponse(f"/ui/accounting/vendor-payments/{row_id}/view", status_code=303)

@router.post("/ui/accounting/vendor-payments/{row_id}/reverse")
def reverse_payment(request: Request, row_id: int):
    if not accounting_allowed(request, "post"):
        return permission_denied("You do not have permission to reverse vendor payments.", "ليس لديك صلاحية عكس مدفوعات الموردين.")
    conn = get_conn()
    try:
        payment = conn.execute("SELECT * FROM vendor_payments WHERE id = ? LIMIT 1", (row_id,)).fetchone()
        if not payment:
            raise Exception("Payment not found")
        if safe(payment["status"]).lower() != "posted":
            raise Exception("Only posted payments can be reversed")
        if payment["reversed_journal_id"]:
            raise Exception("Payment already reversed")
        if not payment["journal_id"]:
            raise Exception("Posted payment has no journal")
        bill_id = payment["bill_id"]
        bill_before = get_bill(conn, bill_id) if bill_id else None
        old_payment_status = safe(bill_before["payment_status"]).lower() if bill_before else ""
        reverse_id = reverse_journal_entry(conn, payment["journal_id"])
        delete_payment_allocations(conn, "vendor_payment", row_id)
        conn.execute("UPDATE vendor_payments SET status = 'reversed', reversed_journal_id = ? WHERE id = ?", (reverse_id, row_id))
        safe_log_action(
            "vendor_payment",
            row_id,
            "Reversed",
            done_by=actor_name_from_request(request),
            notes=f"Payment {safe(payment['payment_no'])} reversed.",
            conn=conn,
        )
        if bill_id:
            refresh_bill_payment_status(conn, bill_id)
            bill_after = get_bill(conn, bill_id)
            new_payment_status = safe(bill_after["payment_status"]).lower() if bill_after else ""
            if bill_after and old_payment_status != new_payment_status:
                safe_log_action(
                    "vendor_bill",
                    bill_id,
                    "Payment Status Changed",
                    done_by=actor_name_from_request(request),
                    notes=f"Bill {safe(bill_after['bill_no'])} payment status changed from {old_payment_status or '-'} to {new_payment_status or '-'}.",
                    conn=conn,
                )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reverse error: {safe(e)}", status_code=400)
    conn.close()
    return RedirectResponse(f"/ui/accounting/vendor-payments/{row_id}/view", status_code=303)

@router.get("/ui/accounting/vendor-payments/{row_id}/view", response_class=HTMLResponse)
def view_payment(request: Request, row_id: int):
    can_edit_perm = accounting_allowed(request, "edit")
    can_post_perm = accounting_allowed(request, "post")
    conn = get_conn()
    row = conn.execute("""
        SELECT p.*, c.name AS vendor_name, i.bill_no
        FROM vendor_payments p
        LEFT JOIN partners c ON c.id = p.vendor_id
        LEFT JOIN vendor_bills i ON i.id = p.bill_id
        WHERE p.id = ?
        LIMIT 1
    """, (row_id,)).fetchone()
    conn.close()
    if not row:
        return HTMLResponse("Payment not found", status_code=404)
    content = payment_form(dict(row), row_id=row_id, readonly=True)
    extra_buttons = ""
    if safe(row["status"]).lower() == "draft" and can_edit_perm:
        extra_buttons += f"<a class='btn green' href='/ui/accounting/vendor-payments/{row_id}/edit'>Edit Draft</a>"
    if safe(row["status"]).lower() == "draft" and can_post_perm:
        extra_buttons += f"<form method='post' action='/ui/accounting/vendor-payments/{row_id}/post' style='display:inline;'><button class='btn green' type='submit'>Post</button></form>"
    extra = f"""
    <div class="card" style="margin-top:20px;">
        <h3>Payment Summary</h3>
        <p><b>Vendor:</b> {safe(row['vendor_name'])}</p>
        <p><b>Bill:</b> {safe(row['bill_no'])}</p>
        <p><b>Journal ID:</b> {safe(row['journal_id'])}</p>
        <p><b>Reverse Journal ID:</b> {safe(row['reversed_journal_id'])}</p>
    </div>
    <div class="form-actions" style="margin-top:16px;">
        {extra_buttons}
        <a class="btn gray" href="/ui/accounting/vendor-payments">Back to Payments</a>
    </div>
    """
    return HTMLResponse(render_page("View Vendor Payment", content + extra + render_audit_log_card("vendor_payment", row_id), current_path=str(request.url.path)))
