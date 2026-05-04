from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from audit import actor_name_from_request, render_audit_log_card, safe_log_action
from auth import can
from db import get_conn
from layout import render_page
from i18n import get_lang

from modules.accounting.accounting_engine import (
    create_journal_entry,
    delete_draft_journal_entry,
    post_journal_entry,
    submit_journal_for_final_post,
    reverse_journal_entry,
)

try:
    from modules.accounting.config import get_setting_value
except Exception:
    def get_setting_value(key, default=None):
        defaults = {
            "customer_payment_prefix": "CP",
            "customer_control_account": "112100",
            "default_cash_account": "111100",
            "default_bank_account": "111150",
        }
        return defaults.get(key, default)


router = APIRouter()


def accounting_allowed(request: Request, action: str) -> bool:
    return can(request, "accounting", action)


def permission_denied(en: str, ar: str):
    return HTMLResponse(en, status_code=403)


# =========================================================
# HELPERS
# =========================================================
def safe(x):
    return "" if x is None else str(x).strip()


def D(x):
    try:
        return Decimal(str(x if x is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def q2(x):
    return D(x).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)


def money(x):
    try:
        return f"{float(x or 0):,.2f}"
    except Exception:
        return "0.00"


def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


def table_exists(conn, table_name):
    row = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name=?
    """, (table_name,)).fetchone()
    return bool(row)


def get_table_columns(conn, table_name):
    if not table_exists(conn, table_name):
        return []
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [r["name"] for r in rows]


# =========================================================
# DB INIT
# =========================================================
def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_no TEXT,
            payment_date TEXT,
            customer_id INTEGER,
            customer_name TEXT,
            receipt_method TEXT,
            receipt_account_code TEXT,
            amount REAL DEFAULT 0,
            note TEXT,
            status TEXT DEFAULT 'draft',
            journal_id INTEGER,
            reversed_journal_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_payment_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER NOT NULL,
            invoice_id INTEGER NOT NULL,
            allocated_amount REAL DEFAULT 0
        )
    """)

    ensure_column(conn, "customer_payments", "payment_no", "ALTER TABLE customer_payments ADD COLUMN payment_no TEXT")
    ensure_column(conn, "customer_payments", "payment_date", "ALTER TABLE customer_payments ADD COLUMN payment_date TEXT")
    ensure_column(conn, "customer_payments", "customer_id", "ALTER TABLE customer_payments ADD COLUMN customer_id INTEGER")
    ensure_column(conn, "customer_payments", "customer_name", "ALTER TABLE customer_payments ADD COLUMN customer_name TEXT")
    ensure_column(conn, "customer_payments", "receipt_method", "ALTER TABLE customer_payments ADD COLUMN receipt_method TEXT")
    ensure_column(conn, "customer_payments", "receipt_account_code", "ALTER TABLE customer_payments ADD COLUMN receipt_account_code TEXT")
    ensure_column(conn, "customer_payments", "amount", "ALTER TABLE customer_payments ADD COLUMN amount REAL DEFAULT 0")
    ensure_column(conn, "customer_payments", "note", "ALTER TABLE customer_payments ADD COLUMN note TEXT")
    ensure_column(conn, "customer_payments", "status", "ALTER TABLE customer_payments ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "customer_payments", "journal_id", "ALTER TABLE customer_payments ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "customer_payments", "reversed_journal_id", "ALTER TABLE customer_payments ADD COLUMN reversed_journal_id INTEGER")
    ensure_column(conn, "customer_payments", "created_at", "ALTER TABLE customer_payments ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "customer_payment_allocations", "payment_id", "ALTER TABLE customer_payment_allocations ADD COLUMN payment_id INTEGER")
    ensure_column(conn, "customer_payment_allocations", "invoice_id", "ALTER TABLE customer_payment_allocations ADD COLUMN invoice_id INTEGER")
    ensure_column(conn, "customer_payment_allocations", "allocated_amount", "ALTER TABLE customer_payment_allocations ADD COLUMN allocated_amount REAL DEFAULT 0")

    conn.commit()
    conn.close()


ensure_tables()


# =========================================================
# LOOKUPS
# =========================================================
def next_payment_no():
    prefix = get_setting_value("customer_payment_prefix", "CP")
    conn = get_conn()
    row = conn.execute("""
        SELECT payment_no
        FROM customer_payments
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["payment_no"]:
        return f"{prefix}-0001"

    last = safe(row["payment_no"])
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"{prefix}-{num + 1:04d}"


def account_options(selected=""):
    conn = get_conn()
    rows = conn.execute("""
        SELECT code, name
        FROM accounts
        WHERE COALESCE(is_active,1) = 1
          AND COALESCE(is_group,0) = 0
          AND COALESCE(allow_posting,1) = 1
        ORDER BY code, name
    """).fetchall()
    conn.close()

    html = '<option value="">-- Select Account --</option>'
    for r in rows:
        code = safe(r["code"])
        name = safe(r["name"])
        sel = "selected" if code == safe(selected) else ""
        html += f'<option value="{code}" {sel}>{code} - {name}</option>'
    return html


def customer_name_expr(conn):
    if table_exists(conn, "customers"):
        cols = get_table_columns(conn, "customers")
        for c in ["name", "customer_name", "full_name"]:
            if c in cols:
                return ("customers", c)
    if table_exists(conn, "partners"):
        cols = get_table_columns(conn, "partners")
        for c in ["name", "partner_name", "full_name"]:
            if c in cols:
                return ("partners", c)
    return None, None


def customer_code_expr(conn, table_name):
    cols = get_table_columns(conn, table_name)
    for c in ["code", "customer_code"]:
        if c in cols:
            return c
    return "''"


def customer_datalist_html():
    conn = get_conn()
    table_name, name_col = customer_name_expr(conn)
    if not table_name or not name_col:
        conn.close()
        return ""

    code_col = customer_code_expr(conn, table_name)

    where_sql = ""
    if table_name == "partners":
        cols = get_table_columns(conn, "partners")
        if "partner_type" in cols:
            where_sql = "WHERE LOWER(COALESCE(partner_type,'')) = 'customer'"

    rows = conn.execute(f"""
        SELECT id, {code_col} AS code, {name_col} AS name
        FROM {table_name}
        {where_sql}
        ORDER BY name
    """).fetchall()
    conn.close()

    html = ""
    for r in rows:
        label = safe(r["name"])
        if safe(r["code"]):
            label = f"{safe(r['code'])} - {label}"
        html += f"""
        <option value="{label}"
                data-id="{r['id']}"
                data-name="{safe(r['name'])}">
        </option>
        """
    return html


def account_display(code):
    if not code:
        return ""
    conn = get_conn()
    row = conn.execute("""
        SELECT code, name
        FROM accounts
        WHERE code = ?
        LIMIT 1
    """, (safe(code),)).fetchone()
    conn.close()
    if row:
        return f"{safe(row['code'])} - {safe(row['name'])}"
    return safe(code)


# =========================================================
# DATA ACCESS
# =========================================================
def get_payment(conn, payment_id: int):
    return conn.execute("""
        SELECT *
        FROM customer_payments
        WHERE id = ?
        LIMIT 1
    """, (payment_id,)).fetchone()


def get_payment_allocations(conn, payment_id: int):
    return conn.execute("""
        SELECT a.*, i.invoice_no, i.invoice_date, i.net_amount
        FROM customer_payment_allocations a
        JOIN customer_invoices i ON i.id = a.invoice_id
        WHERE a.payment_id = ?
        ORDER BY i.invoice_date, i.id
    """, (payment_id,)).fetchall()


def get_invoice(conn, invoice_id: int):
    return conn.execute("""
        SELECT *
        FROM customer_invoices
        WHERE id = ?
        LIMIT 1
    """, (invoice_id,)).fetchone()


def get_posted_allocated_amount_for_invoice(conn, invoice_id: int, exclude_payment_id=None):
    sql = """
        SELECT COALESCE(SUM(a.allocated_amount), 0) AS total_allocated
        FROM customer_payment_allocations a
        JOIN customer_payments p ON p.id = a.payment_id
        WHERE a.invoice_id = ?
          AND LOWER(COALESCE(p.status,'')) = 'posted'
    """
    params = [invoice_id]

    if exclude_payment_id:
        sql += " AND p.id <> ?"
        params.append(exclude_payment_id)

    row = conn.execute(sql, params).fetchone()
    return q2(row["total_allocated"] if row else 0)


def invoice_open_amount(conn, invoice_id: int, exclude_payment_id=None):
    inv = get_invoice(conn, invoice_id)
    if not inv:
        return Decimal("0.00")

    net_amount = q2(inv["net_amount"])
    allocated = get_posted_allocated_amount_for_invoice(conn, invoice_id, exclude_payment_id=exclude_payment_id)
    open_amt = q2(net_amount - allocated)
    return open_amt if open_amt > Decimal("0.00") else Decimal("0.00")


def update_invoice_payment_status(conn, invoice_id: int):
    inv = get_invoice(conn, invoice_id)
    if not inv:
        return

    net_amount = q2(inv["net_amount"])
    allocated = get_posted_allocated_amount_for_invoice(conn, invoice_id)

    if allocated <= Decimal("0.00"):
        status = "unpaid"
    elif allocated < net_amount:
        status = "partial"
    else:
        status = "paid"

    conn.execute("""
        UPDATE customer_invoices
        SET payment_status = ?
        WHERE id = ?
    """, (status, invoice_id))


# =========================================================
# CUSTOMER INVOICES FOR ALLOCATION
# =========================================================
def customer_open_invoices(conn, customer_id):
    if not customer_id:
        return []

    rows = conn.execute("""
        SELECT *
        FROM customer_invoices
        WHERE customer_id = ?
          AND LOWER(COALESCE(status,'')) = 'posted'
        ORDER BY invoice_date, id
    """, (customer_id,)).fetchall()

    result = []
    for r in rows:
        open_amt = invoice_open_amount(conn, r["id"])
        if open_amt > Decimal("0.00"):
            result.append({
                "id": r["id"],
                "invoice_no": safe(r["invoice_no"]),
                "invoice_date": safe(r["invoice_date"]),
                "net_amount": q2(r["net_amount"]),
                "open_amount": open_amt,
            })
    return result


def open_invoice_picker_payload():
    conn = get_conn()
    rows = conn.execute("""
        SELECT *
        FROM customer_invoices
        WHERE LOWER(COALESCE(status,'')) = 'posted'
        ORDER BY invoice_date DESC, id DESC
    """).fetchall()

    result = []
    for r in rows:
        open_amt = invoice_open_amount(conn, r["id"])
        if open_amt <= Decimal("0.00"):
            continue
        net_amount = q2(r["net_amount"])
        paid_amount = q2(net_amount - open_amt)
        result.append({
            "id": str(r["id"]),
            "customer_id": str(r["customer_id"] or ""),
            "customer_name": safe(r["customer_name"]),
            "invoice_no": safe(r["invoice_no"]),
            "invoice_date": safe(r["invoice_date"]),
            "due_date": safe(r["due_date"]),
            "net_amount": str(net_amount),
            "paid_amount": str(paid_amount),
            "open_amount": str(open_amt),
            "payment_status": safe(r["payment_status"]),
        })
    conn.close()
    return result


# =========================================================
# JOURNAL LOGIC
# =========================================================
def build_payment_journal_lines(payment_row):
    customer_control = (
        get_setting_value("customer_control_account", "")
        or "112100"
    )

    receipt_account = safe(payment_row["receipt_account_code"])
    if not receipt_account:
        raise Exception("Receipt account is required.")

    amount = q2(payment_row["amount"])
    if amount <= Decimal("0.00"):
        raise Exception("Payment amount must be greater than zero.")

    if not customer_control:
        raise Exception("Customer control account is missing in Configuration.")

    lines = [
        {
            "description": f"Customer Receipt - {safe(payment_row['customer_name'])}",
            "account_code": receipt_account,
            "debit": amount,
            "credit": Decimal("0.00"),
            "partner_type": None,
            "partner_id": None,
        },
        {
            "description": f"Clear A/R - {safe(payment_row['customer_name'])}",
            "account_code": customer_control,
            "debit": Decimal("0.00"),
            "credit": amount,
            "partner_type": "customer",
            "partner_id": payment_row["customer_id"],
        },
    ]

    return lines


def create_draft_journal_for_payment(conn, payment_id: int):
    payment = get_payment(conn, payment_id)
    if not payment:
        raise Exception("Payment not found.")

    lines = build_payment_journal_lines(payment)

    journal_id = create_journal_entry(
        conn=conn,
        entry_date=safe(payment["payment_date"]),
        description=f"Customer Payment {safe(payment['payment_no'])} - {safe(payment['customer_name'])}",
        reference=safe(payment["payment_no"]),
        source_type="customer_payment",
        source_id=payment_id,
        lines=lines,
    )

    conn.execute("""
        UPDATE customer_payments
        SET journal_id = ?,
            status = 'draft'
        WHERE id = ?
    """, (journal_id, payment_id))

    return journal_id


def rebuild_draft_journal_for_payment(conn, payment_id: int):
    payment = get_payment(conn, payment_id)
    if not payment:
        raise Exception("Payment not found.")

    if payment["journal_id"]:
        delete_draft_journal_entry(conn, payment["journal_id"])
        conn.execute("""
            UPDATE customer_payments
            SET journal_id = NULL
            WHERE id = ?
        """, (payment_id,))

    return create_draft_journal_for_payment(conn, payment_id)


# =========================================================
# FORM HTML
# =========================================================
def searchable_script():
    return """
    <script>
    function setupSearchableSelect(selectId) {
        const select = document.getElementById(selectId);
        if (!select || select.dataset.searchReady === "1") return;

        select.dataset.searchReady = "1";
        select.style.display = "none";

        const wrapper = document.createElement("div");
        wrapper.style.position = "relative";
        wrapper.style.width = "100%";

        const input = document.createElement("input");
        input.type = "text";
        input.placeholder = "Type first letters...";
        input.style.width = "100%";
        input.style.padding = "11px 12px";
        input.style.border = "1px solid #cfd8e3";
        input.style.borderRadius = "12px";
        input.style.autocomplete = "off";
        input.style.boxSizing = "border-box";

        const dropdown = document.createElement("div");
        dropdown.style.position = "absolute";
        dropdown.style.top = "100%";
        dropdown.style.left = "0";
        dropdown.style.right = "0";
        dropdown.style.background = "#fff";
        dropdown.style.border = "1px solid #d1d5db";
        dropdown.style.borderRadius = "12px";
        dropdown.style.maxHeight = "220px";
        dropdown.style.overflowY = "auto";
        dropdown.style.zIndex = "9999";
        dropdown.style.display = "none";
        dropdown.style.marginTop = "4px";

        function currentText() {
            const opt = select.options[select.selectedIndex];
            return opt && opt.value ? opt.text : "";
        }

        input.value = currentText();

        function renderOptions(filterText) {
            const q = (filterText || "").toLowerCase().trim();
            dropdown.innerHTML = "";

            const opts = Array.from(select.options).filter(opt => {
                if (!opt.value) return false;
                return !q || opt.text.toLowerCase().includes(q);
            });

            if (!opts.length) {
                dropdown.style.display = "none";
                return;
            }

            opts.forEach(opt => {
                const item = document.createElement("div");
                item.textContent = opt.text;
                item.style.padding = "10px 12px";
                item.style.cursor = "pointer";
                item.style.borderBottom = "1px solid #eee";

                item.onmouseenter = function() {
                    item.style.background = "#f3f4f6";
                };
                item.onmouseleave = function() {
                    item.style.background = "#fff";
                };
                item.onclick = function() {
                    select.value = opt.value;
                    input.value = opt.text;
                    dropdown.style.display = "none";
                    select.dispatchEvent(new Event("change"));
                };

                dropdown.appendChild(item);
            });

            dropdown.style.display = "block";
        }

        input.addEventListener("focus", function() {
            renderOptions(input.value);
        });

        input.addEventListener("input", function() {
            renderOptions(input.value);
        });

        document.addEventListener("click", function(e) {
            if (!wrapper.contains(e.target)) {
                dropdown.style.display = "none";
            }
        });

        select.parentNode.insertBefore(wrapper, select);
        wrapper.appendChild(input);
        wrapper.appendChild(dropdown);
    }

    function bindCustomerDatalist() {
        const input = document.getElementById("customer_label");
        const hiddenId = document.getElementById("customer_id");
        const hiddenName = document.getElementById("customer_name");
        const list = document.getElementById("customer_list");

        if (!input || !hiddenId || !hiddenName || !list) return;

        function syncCustomer() {
            hiddenId.value = "";
            hiddenName.value = "";

            const val = input.value.trim();
            const opts = list.querySelectorAll("option");

            for (const opt of opts) {
                if ((opt.value || "").trim() === val) {
                    hiddenId.value = opt.getAttribute("data-id") || "";
                    hiddenName.value = opt.getAttribute("data-name") || "";
                    break;
                }
            }
        }

        input.addEventListener("input", syncCustomer);
        input.addEventListener("change", syncCustomer);
        input.addEventListener("blur", syncCustomer);
    }
    </script>
    """


def build_payment_form_html(action_url, form_data=None, initial_allocations=None, error_message=""):
    form_data = form_data or {}
    initial_allocations = initial_allocations or []

    payment_no = safe(form_data.get("payment_no")) or next_payment_no()
    payment_date = safe(form_data.get("payment_date")) or datetime.today().strftime("%Y-%m-%d")
    customer_name = safe(form_data.get("customer_name"))
    receipt_method = safe(form_data.get("receipt_method")) or "cash"
    receipt_account_code = safe(form_data.get("receipt_account_code"))
    amount = safe(form_data.get("amount")) or "0"
    note = safe(form_data.get("note"))

    if not receipt_account_code:
        if receipt_method == "bank":
            receipt_account_code = safe(get_setting_value("default_bank_account", "111150"))
        else:
            receipt_account_code = safe(get_setting_value("default_cash_account", "111100"))

    cash_sel = "selected" if receipt_method == "cash" else ""
    bank_sel = "selected" if receipt_method == "bank" else ""

    import json
    allocations_json = json.dumps(initial_allocations)
    invoice_picker_json = json.dumps(open_invoice_picker_payload())

    error_html = f'<div class="msg error">{error_message}</div>' if error_message else ""

    content = f"""
    {error_html}

    <div class="toolbar">
        <h2>{"Edit Customer Payment" if "/edit" in action_url else "New Customer Payment"}</h2>
        <a href="/ui/accounting/customer-payments" class="btn gray">Back</a>
    </div>

    <form method="post" action="{action_url}" id="paymentForm">
        <div class="card">
            <div class="form-grid">
                <div class="form-group">
                    <label>Payment No</label>
                    <input name="payment_no" value="{payment_no}" readonly>
                </div>

                <div class="form-group">
                    <label>Payment Date</label>
                    <input type="date" name="payment_date" value="{payment_date}" required>
                </div>

                <div class="form-group" style="grid-column: span 2;">
                    <label>Customer</label>
                    <input id="customer_label" list="customer_list" autocomplete="off" value="{customer_name}" placeholder="Search customer...">
                    <input type="hidden" id="customer_id" name="customer_id" value="{safe(form_data.get('customer_id'))}">
                    <input type="hidden" id="customer_name" name="customer_name" value="{customer_name}">
                    <datalist id="customer_list">
                        {customer_datalist_html()}
                    </datalist>
                </div>

                <div class="form-group">
                    <label>Receipt Method</label>
                    <select name="receipt_method" id="receipt_method">
                        <option value="cash" {cash_sel}>Cash</option>
                        <option value="bank" {bank_sel}>Bank</option>
                    </select>
                </div>

                <div class="form-group">
                    <label>Receipt Account</label>
                    <select name="receipt_account_code" id="receipt_account_code">
                        {account_options(receipt_account_code)}
                    </select>
                </div>

                <div class="form-group">
                    <label>Amount</label>
                    <input name="amount" id="payment_amount" value="{amount}" oninput="recalcAllocated()" required>
                </div>

                <div class="form-group" style="grid-column: span 2;">
                    <label>Note</label>
                    <input name="note" value="{note}">
                </div>
            </div>
        </div>

        <div class="card">
            <div class="toolbar">
                <h3>Allocations</h3>
                <button type="button" class="btn blue" onclick="addSelectedInvoice()">+ Add Invoice</button>
            </div>

            <div class="form-grid" style="margin-top:14px;">
                <div class="form-group" style="grid-column: span 2;">
                    <label>Against Invoice</label>
                    <select id="invoice_picker">
                        <option value="">-- Select Invoice --</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Invoice Open Amount</label>
                    <input id="invoice_preview_open" readonly>
                </div>
                <div class="form-group">
                    <label>Invoice Status</label>
                    <input id="invoice_preview_status" readonly>
                </div>
            </div>

            <div class="form-grid" style="margin-top:12px;">
                <div class="form-group">
                    <label>Invoice No</label>
                    <input id="invoice_preview_no" readonly>
                </div>
                <div class="form-group">
                    <label>Invoice Date</label>
                    <input id="invoice_preview_date" readonly>
                </div>
                <div class="form-group">
                    <label>Due Date</label>
                    <input id="invoice_preview_due" readonly>
                </div>
                <div class="form-group">
                    <label>Paid Amount</label>
                    <input id="invoice_preview_paid" readonly>
                </div>
                <div class="form-group">
                    <label>Total Invoice</label>
                    <input id="invoice_preview_total" readonly>
                </div>
            </div>

            <table>
                <thead>
                    <tr>
                        <th style="width:34px;">#</th>
                        <th>Invoice</th>
                        <th style="width:140px;">Open Amount</th>
                        <th style="width:160px;">Allocate</th>
                        <th style="width:90px;">Action</th>
                    </tr>
                </thead>
                <tbody id="allocBody"></tbody>
            </table>
        </div>

        <div class="card">
            <div class="form-grid">
                <div></div>
                <div></div>
                <div class="form-group">
                    <label>Total Allocated</label>
                    <input id="allocated_total" value="0.00" readonly>
                </div>
                <div class="form-group">
                    <label>Unallocated Balance</label>
                    <input id="unallocated_balance" value="0.00" readonly>
                </div>
            </div>
        </div>

        <div class="form-actions">
            <button class="btn green" type="submit">Save Draft</button>
            <a href="/ui/accounting/customer-payments" class="btn gray">Cancel</a>
        </div>
    </form>

    <script>
    let allocIndex = 0;
    const openInvoices = {invoice_picker_json};

    function safeNum(v) {{
        const n = parseFloat(v);
        return isNaN(n) ? 0 : n;
    }}

    function currentCustomerId() {{
        return (document.getElementById("customer_id")?.value || "").trim();
    }}

    function currentInvoiceData() {{
        const picker = document.getElementById("invoice_picker");
        if (!picker || !picker.value) return null;
        return openInvoices.find(inv => String(inv.id) === String(picker.value)) || null;
    }}

    function renderInvoicePicker() {{
        const picker = document.getElementById("invoice_picker");
        if (!picker) return;
        const customerId = currentCustomerId();
        const currentValue = picker.value || "";
        const items = openInvoices.filter(inv => !customerId || String(inv.customer_id) === String(customerId));
        let html = '<option value="">-- Select Invoice --</option>';
        items.forEach(inv => {{
            html += `<option value="${{inv.id}}">${{inv.invoice_no}} | ${{inv.invoice_date}} | Open ${{inv.open_amount}}</option>`;
        }});
        picker.innerHTML = html;
        if (items.some(inv => String(inv.id) === String(currentValue))) {{
            picker.value = currentValue;
        }}
        updateInvoicePreview();
    }}

    function updateInvoicePreview() {{
        const inv = currentInvoiceData();
        const fields = {{
            invoice_preview_no: inv?.invoice_no || "",
            invoice_preview_date: inv?.invoice_date || "",
            invoice_preview_due: inv?.due_date || "",
            invoice_preview_total: inv?.net_amount || "",
            invoice_preview_paid: inv?.paid_amount || "",
            invoice_preview_open: inv?.open_amount || "",
            invoice_preview_status: inv ? [inv.payment_status].filter(Boolean).join(" / ") : "",
        }};
        Object.keys(fields).forEach(id => {{
            const el = document.getElementById(id);
            if (el) el.value = fields[id];
        }});
    }}

    function allocationExists(invoiceId) {{
        return Array.from(document.querySelectorAll('input[type="hidden"][name^="invoice_id_"]'))
            .some(inp => String(inp.value) === String(invoiceId));
    }}

    function recalcAllocated() {{
        let total = 0;
        document.querySelectorAll(".alloc-amount").forEach(inp => {{
            total += safeNum(inp.value);
        }});

        const paymentAmount = safeNum(document.getElementById("payment_amount").value);
        document.getElementById("allocated_total").value = total.toFixed(2);
        document.getElementById("unallocated_balance").value = (paymentAmount - total).toFixed(2);
    }}

    function addAllocation(invoiceId="", invoiceLabel="", openAmount="0.00", allocAmount="0.00") {{
        const tbody = document.getElementById("allocBody");
        const idx = allocIndex;

        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${{idx + 1}}</td>
            <td>
                <input name="invoice_label_${{idx}}" value="${{invoiceLabel}}" readonly>
                <input type="hidden" name="invoice_id_${{idx}}" value="${{invoiceId}}">
            </td>
            <td><input name="open_amount_${{idx}}" value="${{openAmount}}" readonly></td>
            <td><input class="alloc-amount" name="alloc_amount_${{idx}}" value="${{allocAmount}}" oninput="recalcAllocated()"></td>
            <td><button type="button" class="btn red" onclick="this.closest('tr').remove(); recalcAllocated();">X</button></td>
        `;
        tbody.appendChild(tr);
        allocIndex++;
        recalcAllocated();
    }}

    function addSelectedInvoice() {{
        const inv = currentInvoiceData();
        if (!inv) return;
        if (allocationExists(inv.id)) return;

        const paymentAmount = safeNum(document.getElementById("payment_amount").value);
        const allocated = safeNum(document.getElementById("allocated_total").value);
        const remaining = Math.max(paymentAmount - allocated, 0);
        const openAmount = safeNum(inv.open_amount);
        const suggested = remaining > 0 ? Math.min(remaining, openAmount) : openAmount;
        addAllocation(
            inv.id,
            `${{inv.invoice_no}} | ${{inv.invoice_date}} | Due ${{inv.due_date}}`,
            inv.open_amount,
            suggested.toFixed(2)
        );
    }}

    window.addEventListener("DOMContentLoaded", function() {{
        bindCustomerDatalist();
        setupSearchableSelect("receipt_account_code");
        renderInvoicePicker();

        const initialAllocs = {allocations_json};
        if (initialAllocs.length) {{
            initialAllocs.forEach(a => addAllocation(a.invoice_id, a.invoice_label, a.open_amount, a.alloc_amount));
        }}

        const customerInput = document.getElementById("customer_label");
        const invoicePicker = document.getElementById("invoice_picker");
        if (customerInput) {{
            ["input", "change", "blur"].forEach(evt => {{
                customerInput.addEventListener(evt, function() {{
                    setTimeout(renderInvoicePicker, 0);
                }});
            }});
        }}
        if (invoicePicker) {{
            invoicePicker.addEventListener("change", updateInvoicePreview);
        }}

        recalcAllocated();
    }});
    </script>

    {searchable_script()}
    """
    return content


# =========================================================
# LIST PAGE
# =========================================================
@router.get("/ui/accounting/customer-payments", response_class=HTMLResponse)
def customer_payments_page(
    request: Request,
    q: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = ""
):
    lang = get_lang(request)
    can_create_perm = accounting_allowed(request, "create")
    can_edit_perm = accounting_allowed(request, "edit")
    can_post_perm = accounting_allowed(request, "post")
    conn = get_conn()

    sql = """
        SELECT *
        FROM customer_payments
        WHERE 1 = 1
    """
    params = []

    if safe(q):
        sql += " AND (LOWER(COALESCE(payment_no,'')) LIKE ? OR LOWER(COALESCE(customer_name,'')) LIKE ?)"
        like_q = f"%{safe(q).lower()}%"
        params.extend([like_q, like_q])

    if safe(status):
        sql += " AND LOWER(COALESCE(status,'')) = ?"
        params.append(safe(status).lower())

    if safe(date_from):
        sql += " AND COALESCE(payment_date,'') >= ?"
        params.append(safe(date_from))

    if safe(date_to):
        sql += " AND COALESCE(payment_date,'') <= ?"
        params.append(safe(date_to))

    sql += " ORDER BY id DESC"

    rows = conn.execute(sql, params).fetchall()

    body = ""
    for r in rows:
        actions = f'<a class="btn gray" href="/ui/accounting/customer-payments/{r["id"]}">Open</a>'
        if safe(r["status"]).lower() == "draft" and can_edit_perm:
            actions += f' <a class="btn blue" href="/ui/accounting/customer-payments/{r["id"]}/edit">Edit</a>'
        if safe(r["status"]).lower() == "draft" and can_post_perm:
            actions += f' <form method="post" action="/ui/accounting/customer-payments/{r["id"]}/post" style="display:inline;"><button class="btn green" type="submit">Post</button></form>'
        body += f"""
        <tr>
            <td>{safe(r['payment_no'])}</td>
            <td>{safe(r['payment_date'])}</td>
            <td>{safe(r['customer_name'])}</td>
            <td>{safe(r['receipt_method'])}</td>
            <td>{money(r['amount'])}</td>
            <td>{safe(r['status'])}</td>
            <td>{actions}</td>
        </tr>
        """

    if not body:
        body = "<tr><td colspan='7' style='text-align:center;'>No payments found.</td></tr>"

    content = f"""
    <div class="card">
        <div class="toolbar">
            <h3 class="sub-title" style="margin:0;">Customer Payments</h3>
            {"<a href='/ui/accounting/customer-payments/new' class='btn green'>+ New Payment</a>" if can_create_perm else ""}
        </div>

        <form method="get" style="margin-top:14px;">
            <div class="form-grid">
                <div class="form-group">
                    <label>Search</label>
                    <input name="q" value="{safe(q)}" placeholder="Payment no / customer">
                </div>

                <div class="form-group">
                    <label>From Date</label>
                    <input type="date" name="date_from" value="{safe(date_from)}">
                </div>

                <div class="form-group">
                    <label>To Date</label>
                    <input type="date" name="date_to" value="{safe(date_to)}">
                </div>

                <div class="form-group">
                    <label>Status</label>
                    <select name="status">
                        <option value="" {"selected" if status == "" else ""}>All</option>
                        <option value="draft" {"selected" if status == "draft" else ""}>Draft</option>
                        <option value="posted" {"selected" if status == "posted" else ""}>Posted</option>
                        <option value="reversed" {"selected" if status == "reversed" else ""}>Reversed</option>
                    </select>
                </div>
            </div>

            <div class="form-actions">
                <button class="btn blue" type="submit">Filter</button>
                <a href="/ui/accounting/customer-payments" class="btn gray">Clear</a>
            </div>
        </form>
    </div>

    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Payment #</th>
                    <th>Date</th>
                    <th>Customer</th>
                    <th>Method</th>
                    <th>Amount</th>
                    <th>Status</th>
                    <th style="width:320px;">Actions</th>
                </tr>
            </thead>
            <tbody>
                {body}
            </tbody>
        </table>
    </div>
    """

    conn.close()
    return HTMLResponse(render_page("Customer Payments", content, lang, current_path=request.url.path))


# =========================================================
# NEW
# =========================================================
@router.get("/ui/accounting/customer-payments/new", response_class=HTMLResponse)
def new_payment_page(
    request: Request,
    customer_id: str = "",
    customer_name: str = "",
    invoice_id: str = "",
    amount: str = "",
):
    lang = get_lang(request)
    if not accounting_allowed(request, "create"):
        return permission_denied("You do not have permission to create customer payments.", "ليس لديك صلاحية إنشاء تحصيلات العملاء.")
    form_data = {}
    initial_allocations = []
    conn = get_conn()
    try:
        customer_id_int = int(customer_id) if safe(customer_id) else None
    except Exception:
        customer_id_int = None

    invoice_row = None
    if safe(invoice_id):
        try:
            invoice_row = get_invoice(conn, int(invoice_id))
        except Exception:
            invoice_row = None

    if invoice_row and safe(invoice_row["status"]).lower() == "posted":
        open_amt = invoice_open_amount(conn, int(invoice_row["id"]))
        if open_amt > Decimal("0.00"):
            initial_allocations.append({
                "invoice_id": str(invoice_row["id"]),
                "invoice_label": f"{safe(invoice_row['invoice_no'])} | {safe(invoice_row['invoice_date'])} | Due {safe(invoice_row['due_date'])}",
                "open_amount": money(open_amt),
                "alloc_amount": money(open_amt),
            })
            customer_id_int = int(invoice_row["customer_id"] or 0) or customer_id_int
            customer_name = safe(invoice_row["customer_name"]) or safe(customer_name)
            if not safe(amount):
                amount = money(open_amt).replace(",", "")

    if customer_id_int:
        form_data["customer_id"] = str(customer_id_int)
    if safe(customer_name):
        form_data["customer_name"] = safe(customer_name)
    if safe(amount):
        form_data["amount"] = safe(amount)

    conn.close()
    content = build_payment_form_html("/ui/accounting/customer-payments/new", form_data, initial_allocations)
    return HTMLResponse(render_page("New Customer Payment", content, lang, current_path=request.url.path))


@router.post("/ui/accounting/customer-payments/new")
async def create_payment(request: Request):
    lang = get_lang(request)
    if not accounting_allowed(request, "create"):
        return permission_denied("You do not have permission to create customer payments.", "ليس لديك صلاحية إنشاء تحصيلات العملاء.")
    form = await request.form()

    payment_no = safe(form.get("payment_no")) or next_payment_no()
    payment_date = safe(form.get("payment_date"))
    customer_id_raw = safe(form.get("customer_id"))
    customer_name = safe(form.get("customer_name"))
    receipt_method = safe(form.get("receipt_method")) or "cash"
    receipt_account_code = safe(form.get("receipt_account_code"))
    amount = q2(form.get("amount") or "0")
    note = safe(form.get("note"))

    try:
        customer_id = int(customer_id_raw) if customer_id_raw else None
    except Exception:
        customer_id = None

    initial_allocations = []
    i = 0
    while True:
        invoice_id = form.get(f"invoice_id_{i}")
        if invoice_id is None:
            break
        initial_allocations.append({
            "invoice_id": safe(invoice_id),
            "invoice_label": safe(form.get(f"invoice_label_{i}")),
            "open_amount": safe(form.get(f"open_amount_{i}")),
            "alloc_amount": safe(form.get(f"alloc_amount_{i}")),
        })
        i += 1

    form_data = {
        "payment_no": payment_no,
        "payment_date": payment_date,
        "customer_id": customer_id_raw,
        "customer_name": customer_name,
        "receipt_method": receipt_method,
        "receipt_account_code": receipt_account_code,
        "amount": str(amount),
        "note": note,
    }

    if not customer_name:
        content = build_payment_form_html("/ui/accounting/customer-payments/new", form_data, initial_allocations, "Customer is required.")
        return HTMLResponse(render_page("New Customer Payment", content, lang, current_path="/ui/accounting/customer-payments/new"), status_code=400)

    if amount <= Decimal("0.00"):
        content = build_payment_form_html("/ui/accounting/customer-payments/new", form_data, initial_allocations, "Amount must be greater than zero.")
        return HTMLResponse(render_page("New Customer Payment", content, lang, current_path="/ui/accounting/customer-payments/new"), status_code=400)

    if not receipt_account_code:
        content = build_payment_form_html("/ui/accounting/customer-payments/new", form_data, initial_allocations, "Receipt account is required.")
        return HTMLResponse(render_page("New Customer Payment", content, lang, current_path="/ui/accounting/customer-payments/new"), status_code=400)

    conn = get_conn()

    try:
        cur = conn.execute("""
            INSERT INTO customer_payments (
                payment_no, payment_date, customer_id, customer_name,
                receipt_method, receipt_account_code, amount, note, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft')
        """, (
            payment_no,
            payment_date,
            customer_id,
            customer_name,
            receipt_method,
            receipt_account_code,
            float(amount),
            note,
        ))
        payment_id = cur.lastrowid

        total_allocated = Decimal("0.00")
        i = 0
        while True:
            invoice_id_raw = form.get(f"invoice_id_{i}")
            if invoice_id_raw is None:
                break

            alloc_amount = q2(form.get(f"alloc_amount_{i}") or "0")
            if invoice_id_raw and alloc_amount > Decimal("0.00"):
                invoice_id = int(invoice_id_raw)
                inv = get_invoice(conn, invoice_id)
                if not inv:
                    raise Exception("Allocated invoice not found.")
                if inv["customer_id"] != customer_id:
                    raise Exception("Allocated invoice belongs to another customer.")

                open_amt = invoice_open_amount(conn, invoice_id)
                if alloc_amount > open_amt:
                    raise Exception(f"Allocated amount exceeds invoice open amount for {safe(inv['invoice_no'])}.")

                conn.execute("""
                    INSERT INTO customer_payment_allocations (
                        payment_id, invoice_id, allocated_amount
                    )
                    VALUES (?, ?, ?)
                """, (
                    payment_id,
                    invoice_id,
                    float(alloc_amount),
                ))
                total_allocated += alloc_amount

            i += 1

        if total_allocated > amount:
            raise Exception("Total allocated amount cannot exceed payment amount.")

        create_draft_journal_for_payment(conn, payment_id)
        safe_log_action(
            "customer_payment",
            payment_id,
            "Created",
            done_by=actor_name_from_request(request),
            notes=f"Draft customer payment created for {customer_name or '-'} | Amount: {amount}",
            conn=conn,
        )
        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        content = build_payment_form_html("/ui/accounting/customer-payments/new", form_data, initial_allocations, str(e))
        return HTMLResponse(render_page("New Customer Payment", content, lang, current_path="/ui/accounting/customer-payments/new"), status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/customer-payments/{payment_id}", status_code=302)


# =========================================================
# OPEN
# =========================================================
@router.get("/ui/accounting/customer-payments/{payment_id}", response_class=HTMLResponse)
def open_payment(request: Request, payment_id: int):
    lang = get_lang(request)
    can_edit_perm = accounting_allowed(request, "edit")
    can_post_perm = accounting_allowed(request, "post")
    conn = get_conn()

    payment = get_payment(conn, payment_id)
    if not payment:
        conn.close()
        return HTMLResponse("Payment not found.", status_code=404)

    allocations = get_payment_allocations(conn, payment_id)

    alloc_html = ""
    total_alloc = Decimal("0.00")
    for a in allocations:
        alloc_amount = q2(a["allocated_amount"])
        total_alloc += alloc_amount
        alloc_html += f"""
        <tr>
            <td>{safe(a['invoice_no'])}</td>
            <td>{safe(a['invoice_date'])}</td>
            <td>{money(a['net_amount'])}</td>
            <td>{money(a['allocated_amount'])}</td>
        </tr>
        """

    if not alloc_html:
        alloc_html = "<tr><td colspan='4' style='text-align:center;'>No allocations found.</td></tr>"

    actions = f'<a href="/ui/accounting/customer-payments" class="btn gray">Back</a>'
    if safe(payment["status"]).lower() == "draft" and can_edit_perm:
        actions = f'<a href="/ui/accounting/customer-payments/{payment_id}/edit" class="btn blue">Edit</a> ' + actions
    if safe(payment["status"]).lower() == "draft" and can_post_perm:
        actions = f'<form method="post" action="/ui/accounting/customer-payments/{payment_id}/post" style="display:inline;"><button class="btn green" type="submit">Post</button></form> ' + actions
    content = f"""
    <div class="card">
        <div class="toolbar">
            <h3 class="sub-title" style="margin:0;">Payment {safe(payment['payment_no'])}</h3>
            <div>{actions}</div>
        </div>

        <div class="form-grid" style="margin-top:12px;">
            <div><label>Customer</label><input value="{safe(payment['customer_name'])}" readonly></div>
            <div><label>Payment Date</label><input value="{safe(payment['payment_date'])}" readonly></div>
            <div><label>Receipt Method</label><input value="{safe(payment['receipt_method'])}" readonly></div>
            <div><label>Receipt Account</label><input value="{account_display(payment['receipt_account_code'])}" readonly></div>
            <div><label>Amount</label><input value="{money(payment['amount'])}" readonly></div>
            <div><label>Status</label><input value="{safe(payment['status'])}" readonly></div>
            <div><label>Journal ID</label><input value="{safe(payment['journal_id'])}" readonly></div>
            <div style="grid-column: span 2;"><label>Note</label><input value="{safe(payment['note'])}" readonly></div>
        </div>
    </div>

    <div class="card">
        <h3 class="sub-title">Allocations</h3>
        <table>
            <thead>
                <tr>
                    <th>Invoice #</th>
                    <th>Invoice Date</th>
                    <th>Invoice Net</th>
                    <th>Allocated Amount</th>
                </tr>
            </thead>
            <tbody>
                {alloc_html}
            </tbody>
        </table>

        <div class="form-grid" style="margin-top:14px;">
            <div></div>
            <div></div>
            <div><label>Total Allocated</label><input value="{money(total_alloc)}" readonly></div>
            <div><label>Unallocated</label><input value="{money(q2(payment['amount']) - total_alloc)}" readonly></div>
        </div>
    </div>
    """

    conn.close()
    return HTMLResponse(render_page("Customer Payment", content + render_audit_log_card("customer_payment", payment_id), lang, current_path=request.url.path))


# =========================================================
# EDIT
# =========================================================
@router.get("/ui/accounting/customer-payments/{payment_id}/edit", response_class=HTMLResponse)
def edit_payment_page(request: Request, payment_id: int):
    lang = get_lang(request)
    if not accounting_allowed(request, "edit"):
        return permission_denied("You do not have permission to edit customer payments.", "ليس لديك صلاحية تعديل تحصيلات العملاء.")
    conn = get_conn()

    payment = get_payment(conn, payment_id)
    if not payment:
        conn.close()
        return HTMLResponse("Payment not found.", status_code=404)

    if safe(payment["status"]).lower() != "draft":
        conn.close()
        return RedirectResponse(f"/ui/accounting/customer-payments/{payment_id}", status_code=302)

    allocations = get_payment_allocations(conn, payment_id)
    initial_allocations = []
    for a in allocations:
        initial_allocations.append({
            "invoice_id": str(a["invoice_id"]),
            "invoice_label": f"{safe(a['invoice_no'])} | {safe(a['invoice_date'])}",
            "open_amount": money(invoice_open_amount(conn, a["invoice_id"], exclude_payment_id=payment_id)),
            "alloc_amount": safe(a["allocated_amount"]),
        })

    form_data = {
        "payment_no": safe(payment["payment_no"]),
        "payment_date": safe(payment["payment_date"]),
        "customer_id": safe(payment["customer_id"]),
        "customer_name": safe(payment["customer_name"]),
        "receipt_method": safe(payment["receipt_method"]),
        "receipt_account_code": safe(payment["receipt_account_code"]),
        "amount": safe(payment["amount"]),
        "note": safe(payment["note"]),
    }

    conn.close()
    content = build_payment_form_html(f"/ui/accounting/customer-payments/{payment_id}/edit", form_data, initial_allocations)
    return HTMLResponse(render_page("Edit Customer Payment", content, lang, current_path=request.url.path))


@router.post("/ui/accounting/customer-payments/{payment_id}/edit")
async def update_payment(request: Request, payment_id: int):
    lang = get_lang(request)
    if not accounting_allowed(request, "edit"):
        return permission_denied("You do not have permission to edit customer payments.", "ليس لديك صلاحية تعديل تحصيلات العملاء.")
    form = await request.form()
    conn = get_conn()

    payment = get_payment(conn, payment_id)
    if not payment:
        conn.close()
        return HTMLResponse("Payment not found.", status_code=404)

    if safe(payment["status"]).lower() != "draft":
        conn.close()
        return RedirectResponse(f"/ui/accounting/customer-payments/{payment_id}", status_code=302)

    payment_date = safe(form.get("payment_date"))
    customer_id_raw = safe(form.get("customer_id"))
    customer_name = safe(form.get("customer_name"))
    receipt_method = safe(form.get("receipt_method")) or "cash"
    receipt_account_code = safe(form.get("receipt_account_code"))
    amount = q2(form.get("amount") or "0")
    note = safe(form.get("note"))

    try:
        customer_id = int(customer_id_raw) if customer_id_raw else None
    except Exception:
        customer_id = None

    initial_allocations = []
    i = 0
    while True:
        invoice_id = form.get(f"invoice_id_{i}")
        if invoice_id is None:
            break
        initial_allocations.append({
            "invoice_id": safe(invoice_id),
            "invoice_label": safe(form.get(f"invoice_label_{i}")),
            "open_amount": safe(form.get(f"open_amount_{i}")),
            "alloc_amount": safe(form.get(f"alloc_amount_{i}")),
        })
        i += 1

    form_data = {
        "payment_no": safe(payment["payment_no"]),
        "payment_date": payment_date,
        "customer_id": customer_id_raw,
        "customer_name": customer_name,
        "receipt_method": receipt_method,
        "receipt_account_code": receipt_account_code,
        "amount": str(amount),
        "note": note,
    }

    if not customer_name:
        conn.close()
        content = build_payment_form_html(f"/ui/accounting/customer-payments/{payment_id}/edit", form_data, initial_allocations, "Customer is required.")
        return HTMLResponse(render_page("Edit Customer Payment", content, lang, current_path=request.url.path), status_code=400)

    if amount <= Decimal("0.00"):
        conn.close()
        content = build_payment_form_html(f"/ui/accounting/customer-payments/{payment_id}/edit", form_data, initial_allocations, "Amount must be greater than zero.")
        return HTMLResponse(render_page("Edit Customer Payment", content, lang, current_path=request.url.path), status_code=400)

    try:
        conn.execute("""
            UPDATE customer_payments
            SET payment_date = ?,
                customer_id = ?,
                customer_name = ?,
                receipt_method = ?,
                receipt_account_code = ?,
                amount = ?,
                note = ?
            WHERE id = ?
        """, (
            payment_date,
            customer_id,
            customer_name,
            receipt_method,
            receipt_account_code,
            float(amount),
            note,
            payment_id,
        ))

        conn.execute("DELETE FROM customer_payment_allocations WHERE payment_id = ?", (payment_id,))

        total_allocated = Decimal("0.00")
        i = 0
        while True:
            invoice_id_raw = form.get(f"invoice_id_{i}")
            if invoice_id_raw is None:
                break

            alloc_amount = q2(form.get(f"alloc_amount_{i}") or "0")
            if invoice_id_raw and alloc_amount > Decimal("0.00"):
                invoice_id = int(invoice_id_raw)
                inv = get_invoice(conn, invoice_id)
                if not inv:
                    raise Exception("Allocated invoice not found.")
                if inv["customer_id"] != customer_id:
                    raise Exception("Allocated invoice belongs to another customer.")

                open_amt = invoice_open_amount(conn, invoice_id, exclude_payment_id=payment_id)
                if alloc_amount > open_amt:
                    raise Exception(f"Allocated amount exceeds invoice open amount for {safe(inv['invoice_no'])}.")

                conn.execute("""
                    INSERT INTO customer_payment_allocations (
                        payment_id, invoice_id, allocated_amount
                    )
                    VALUES (?, ?, ?)
                """, (
                    payment_id,
                    invoice_id,
                    float(alloc_amount),
                ))
                total_allocated += alloc_amount

            i += 1

        if total_allocated > amount:
            raise Exception("Total allocated amount cannot exceed payment amount.")

        rebuild_draft_journal_for_payment(conn, payment_id)
        safe_log_action(
            "customer_payment",
            payment_id,
            "Updated",
            done_by=actor_name_from_request(request),
            notes=f"Draft customer payment updated for {customer_name or '-'} | Amount: {amount}",
            conn=conn,
        )
        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        content = build_payment_form_html(f"/ui/accounting/customer-payments/{payment_id}/edit", form_data, initial_allocations, str(e))
        return HTMLResponse(render_page("Edit Customer Payment", content, lang, current_path=request.url.path), status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/customer-payments/{payment_id}", status_code=302)


# =========================================================
# POST / REVERSE
# =========================================================
@router.post("/ui/accounting/customer-payments/{payment_id}/post")
def post_payment(request: Request, payment_id: int):
    if not accounting_allowed(request, "post"):
        return permission_denied("You do not have permission to post customer payments.", "ليس لديك صلاحية ترحيل تحصيلات العملاء.")
    conn = get_conn()
    try:
        payment = get_payment(conn, payment_id)
        if not payment:
            raise Exception("Payment not found.")

        if safe(payment["status"]).lower() != "draft":
            raise Exception("Only draft payments can be posted.")

        if not payment["journal_id"]:
            create_draft_journal_for_payment(conn, payment_id)
            payment = get_payment(conn, payment_id)

        allocations = get_payment_allocations(conn, payment_id)
        total_allocated = Decimal("0.00")
        for a in allocations:
            alloc_amount = q2(a["allocated_amount"])
            open_amt = invoice_open_amount(conn, a["invoice_id"], exclude_payment_id=payment_id)
            if alloc_amount > open_amt:
                raise Exception(f"Allocation exceeds open amount for {safe(a['invoice_no'])}.")
            total_allocated += alloc_amount

        if total_allocated > q2(payment["amount"]):
            raise Exception("Total allocated amount cannot exceed payment amount.")

        submit_journal_for_final_post(conn, payment["journal_id"])

        conn.execute("""
            UPDATE customer_payments
            SET status = 'posted'
            WHERE id = ?
        """, (payment_id,))
        safe_log_action(
            "customer_payment",
            payment_id,
            "Posted",
            done_by=actor_name_from_request(request),
            notes=f"Payment {safe(payment['payment_no'])} moved to posted and journal is waiting final post.",
            conn=conn,
        )

        for a in allocations:
            invoice_before = get_invoice(conn, a["invoice_id"])
            old_status = safe(invoice_before["payment_status"]).lower() if invoice_before else ""
            update_invoice_payment_status(conn, a["invoice_id"])
            invoice_after = get_invoice(conn, a["invoice_id"])
            new_status = safe(invoice_after["payment_status"]).lower() if invoice_after else ""
            if old_status != new_status and invoice_after:
                safe_log_action(
                    "customer_invoice",
                    a["invoice_id"],
                    "Payment Status Changed",
                    done_by=actor_name_from_request(request),
                    notes=f"Invoice {safe(invoice_after['invoice_no'])} payment status changed from {old_status or '-'} to {new_status or '-'}.",
                    conn=conn,
                )

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Post failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/customer-payments/{payment_id}", status_code=302)


@router.post("/ui/accounting/customer-payments/{payment_id}/reverse")
def reverse_payment(request: Request, payment_id: int):
    if not accounting_allowed(request, "post"):
        return permission_denied("You do not have permission to reverse customer payments.", "ليس لديك صلاحية عكس تحصيلات العملاء.")
    conn = get_conn()
    try:
        payment = get_payment(conn, payment_id)
        if not payment:
            raise Exception("Payment not found.")

        if safe(payment["status"]).lower() != "posted":
            raise Exception("Only posted payments can be reversed.")

        if payment["reversed_journal_id"]:
            raise Exception("Payment already reversed.")

        reverse_id = reverse_journal_entry(conn, payment["journal_id"])

        conn.execute("""
            UPDATE customer_payments
            SET status = 'reversed',
                reversed_journal_id = ?
            WHERE id = ?
        """, (reverse_id, payment_id))
        safe_log_action(
            "customer_payment",
            payment_id,
            "Reversed",
            done_by=actor_name_from_request(request),
            notes=f"Payment {safe(payment['payment_no'])} reversed.",
            conn=conn,
        )

        allocations = get_payment_allocations(conn, payment_id)
        for a in allocations:
            invoice_before = get_invoice(conn, a["invoice_id"])
            old_status = safe(invoice_before["payment_status"]).lower() if invoice_before else ""
            update_invoice_payment_status(conn, a["invoice_id"])
            invoice_after = get_invoice(conn, a["invoice_id"])
            new_status = safe(invoice_after["payment_status"]).lower() if invoice_after else ""
            if old_status != new_status and invoice_after:
                safe_log_action(
                    "customer_invoice",
                    a["invoice_id"],
                    "Payment Status Changed",
                    done_by=actor_name_from_request(request),
                    notes=f"Invoice {safe(invoice_after['invoice_no'])} payment status changed from {old_status or '-'} to {new_status or '-'}.",
                    conn=conn,
                )

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reverse failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/customer-payments/{payment_id}", status_code=302)
