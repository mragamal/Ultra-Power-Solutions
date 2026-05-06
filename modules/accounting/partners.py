
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from html import escape

from db import get_conn
from layout import render_page
from audit import render_audit_log_card, safe_log_request_action

router = APIRouter()


# =========================================================
# HELPERS
# =========================================================
def safe(x):
    return "" if x is None else str(x).strip()


def to_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def to_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS partners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT NOT NULL,
            partner_type TEXT NOT NULL DEFAULT 'customer',
            phone TEXT,
            email TEXT,
            address TEXT,
            tax_no TEXT,
            payment_term_days INTEGER DEFAULT 0,
            opening_balance REAL DEFAULT 0,
            account_code TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    ensure_column(conn, "partners", "partner_type", "ALTER TABLE partners ADD COLUMN partner_type TEXT DEFAULT 'customer'")
    ensure_column(conn, "partners", "phone", "ALTER TABLE partners ADD COLUMN phone TEXT")
    ensure_column(conn, "partners", "email", "ALTER TABLE partners ADD COLUMN email TEXT")
    ensure_column(conn, "partners", "address", "ALTER TABLE partners ADD COLUMN address TEXT")
    ensure_column(conn, "partners", "tax_no", "ALTER TABLE partners ADD COLUMN tax_no TEXT")
    ensure_column(conn, "partners", "payment_term_days", "ALTER TABLE partners ADD COLUMN payment_term_days INTEGER DEFAULT 0")
    ensure_column(conn, "partners", "opening_balance", "ALTER TABLE partners ADD COLUMN opening_balance REAL DEFAULT 0")
    ensure_column(conn, "partners", "account_code", "ALTER TABLE partners ADD COLUMN account_code TEXT")
    ensure_column(conn, "partners", "is_active", "ALTER TABLE partners ADD COLUMN is_active INTEGER DEFAULT 1")

    cols = [c["name"] for c in conn.execute("PRAGMA table_info(partners)").fetchall()]
    if "type" in cols:
        conn.execute("""
            UPDATE partners
            SET partner_type = COALESCE(NULLIF(TRIM(partner_type), ''), TRIM(type), 'customer')
            WHERE COALESCE(NULLIF(TRIM(partner_type), ''), '') = ''
        """)

    conn.commit()
    conn.close()


ensure_tables()


def next_partner_code(partner_type: str) -> str:
    prefix_map = {"customer": "CUST", "vendor": "VEND"}
    prefix = prefix_map.get(partner_type, "PRT")

    conn = get_conn()
    row = conn.execute("""
        SELECT code
        FROM partners
        WHERE LOWER(COALESCE(partner_type,'')) = ?
          AND COALESCE(code,'') <> ''
        ORDER BY id DESC
        LIMIT 1
    """, (partner_type.lower(),)).fetchone()
    conn.close()

    if not row or not safe(row["code"]):
        return f"{prefix}-0001"

    last = safe(row["code"])
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"{prefix}-{num + 1:04d}"


def account_options(selected_code=None):
    conn = get_conn()
    rows = conn.execute("""
        SELECT code, name
        FROM accounts
        WHERE COALESCE(is_active,1) = 1
          AND COALESCE(is_group,0) = 0
        ORDER BY code, name
    """).fetchall()
    conn.close()

    html = "<option value=''>-- Select Account --</option>"
    for r in rows:
        selected = "selected" if str(selected_code or "") == str(r["code"] or "") else ""
        html += f"<option value='{safe(r['code'])}' {selected}>{safe(r['code'])} - {safe(r['name'])}</option>"
    return html


def money(value):
    try:
        return f"{float(value or 0):,.2f}"
    except Exception:
        return "0.00"


def q_count(conn, sql, params=()):
    try:
        return int(conn.execute(sql, params).fetchone()[0] or 0)
    except Exception:
        return 0


def q_sum(conn, sql, params=()):
    try:
        return float(conn.execute(sql, params).fetchone()[0] or 0)
    except Exception:
        return 0.0


def partner_balance(conn, partner_id, partner_type, account_code):
    if not account_code:
        return 0.0
    return q_sum(conn, """
        SELECT COALESCE(SUM(l.debit - l.credit), 0)
        FROM journal_lines l
        JOIN journal_entries j ON j.id = l.journal_id
        WHERE LOWER(COALESCE(j.status,'')) = 'posted'
          AND LOWER(COALESCE(l.partner_type,'')) = ?
          AND COALESCE(l.partner_id,0) = ?
          AND COALESCE(l.account_code,'') = ?
    """, (partner_type, partner_id, account_code))


def stat_button(label, value, href, accent="#2563eb"):
    return f"""
    <a href="{href}" style="display:flex;align-items:center;gap:10px;min-width:150px;padding:12px 16px;border:1px solid #dbe4f0;border-radius:8px;background:#fff;text-decoration:none;color:#0b2d5b;">
        <span style="width:34px;height:34px;border-radius:8px;background:{accent};color:white;display:flex;align-items:center;justify-content:center;font-weight:900;">#</span>
        <span>
            <span style="display:block;font-size:12px;color:#5f718d;font-weight:700;">{label}</span>
            <span style="display:block;font-size:18px;font-weight:900;">{value}</span>
        </span>
    </a>
    """


def partner_financial_stats(conn, partner):
    partner_id = int(partner["id"])
    partner_type = safe(partner["partner_type"]).lower()
    account_code = safe(partner["account_code"])
    balance = partner_balance(conn, partner_id, partner_type, account_code)

    if partner_type == "customer":
        invoices_count = q_count(conn, "SELECT COUNT(*) FROM customer_invoices WHERE customer_id = ?", (partner_id,))
        invoices_total = q_sum(conn, "SELECT COALESCE(SUM(net_amount),0) FROM customer_invoices WHERE customer_id = ? AND LOWER(COALESCE(status,'')) = 'posted'", (partner_id,))
        due_total = q_sum(conn, "SELECT COALESCE(SUM(net_amount),0) FROM customer_invoices WHERE customer_id = ? AND LOWER(COALESCE(status,'')) = 'posted' AND LOWER(COALESCE(payment_status,'')) <> 'paid'", (partner_id,))
        receipts_count = q_count(conn, "SELECT COUNT(*) FROM cash_vouchers WHERE voucher_type = 'receipt' AND LOWER(COALESCE(party_type,'')) = 'customer' AND party_id = ?", (partner_id,))
        return {
            "balance": balance,
            "buttons": [
                ("Invoices", str(invoices_count), f"/ui/accounting/customer-invoices?customer_id={partner_id}", "#2563eb"),
                ("Invoiced", money(invoices_total), f"/ui/accounting/customer-invoices?customer_id={partner_id}", "#0f766e"),
                ("Due", money(due_total), f"/ui/accounting/customer-statement?customer_id={partner_id}", "#dc2626"),
                ("Receipts", str(receipts_count), f"/ui/accounting/cash-receipts?party_type=customer&customer_id={partner_id}", "#7c3aed"),
                ("Ledger", money(balance), f"/ui/accounting/partner-ledger?partner_type=customer&partner_id={partner_id}", "#f59e0b"),
            ],
        }

    bills_count = q_count(conn, "SELECT COUNT(*) FROM vendor_bills WHERE vendor_id = ?", (partner_id,))
    bills_total = q_sum(conn, "SELECT COALESCE(SUM(net_amount),0) FROM vendor_bills WHERE vendor_id = ? AND LOWER(COALESCE(status,'')) = 'posted'", (partner_id,))
    due_total = q_sum(conn, "SELECT COALESCE(SUM(net_amount),0) FROM vendor_bills WHERE vendor_id = ? AND LOWER(COALESCE(status,'')) = 'posted' AND LOWER(COALESCE(payment_status,'')) <> 'paid'", (partner_id,))
    payments_count = q_count(conn, "SELECT COUNT(*) FROM cash_vouchers WHERE voucher_type = 'payment' AND LOWER(COALESCE(party_type,'')) = 'vendor' AND party_id = ?", (partner_id,))
    return {
        "balance": balance,
        "buttons": [
            ("Bills", str(bills_count), f"/ui/accounting/vendor-bills?vendor_id={partner_id}", "#2563eb"),
            ("Billed", money(bills_total), f"/ui/accounting/vendor-bills?vendor_id={partner_id}", "#0f766e"),
            ("Due", money(due_total), f"/ui/accounting/vendor-statement?vendor_id={partner_id}", "#dc2626"),
            ("Payments", str(payments_count), f"/ui/accounting/cash-payments?party_type=vendor&vendor_id={partner_id}", "#7c3aed"),
            ("Ledger", money(balance), f"/ui/accounting/partner-ledger?partner_type=vendor&partner_id={partner_id}", "#f59e0b"),
        ],
    }


def partner_profile_html(conn, partner, request_path):
    partner_type = safe(partner["partner_type"]).lower()
    title = "Customer" if partner_type == "customer" else "Vendor"
    back_url = "/ui/accounting/customers" if partner_type == "customer" else "/ui/accounting/vendors"
    stats = partner_financial_stats(conn, partner)
    initials = escape((safe(partner["name"]) or "?")[:1].upper())
    active = int(partner["is_active"] or 0) == 1
    status = '<span class="status-chip green">Active</span>' if active else '<span class="status-chip gray">Inactive</span>'
    smart_buttons = "".join(stat_button(label, value, href, color) for label, value, href, color in stats["buttons"])

    details = f"""
    <div class="card">
        <div class="toolbar">
            <div>
                <h2 style="margin:0;">{title} {escape(safe(partner['code']))}</h2>
                <div style="color:#6f819d;margin-top:6px;">{escape(safe(partner['name']))}</div>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <a class="btn blue" href="/ui/accounting/partners/{partner['id']}/edit">Edit</a>
                <a class="btn gray" href="{back_url}">Back</a>
            </div>
        </div>
    </div>

    <div class="card" style="display:flex;gap:10px;flex-wrap:wrap;align-items:stretch;">
        {smart_buttons}
    </div>

    <div style="display:grid;grid-template-columns:minmax(0,2fr) minmax(300px,1fr);gap:18px;align-items:start;">
        <div class="card">
            <div style="display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap;">
                <div style="width:118px;height:118px;border-radius:8px;background:#2f5fb8;color:white;display:flex;align-items:center;justify-content:center;font-size:58px;font-weight:900;">{initials}</div>
                <div style="flex:1;min-width:260px;">
                    <div style="font-size:30px;font-weight:900;color:#0b2d5b;">{escape(safe(partner['name']))}</div>
                    <div style="margin-top:10px;color:#102a4c;line-height:1.8;">
                        <div><b>Code:</b> {escape(safe(partner['code']))}</div>
                        <div><b>Phone:</b> {escape(safe(partner['phone']))}</div>
                        <div><b>Email:</b> {escape(safe(partner['email']))}</div>
                        <div><b>Tax No:</b> {escape(safe(partner['tax_no']))}</div>
                        <div><b>Payment Term Days:</b> {escape(safe(partner['payment_term_days']))}</div>
                        <div><b>Account:</b> {escape(safe(partner['account_code']))}</div>
                        <div><b>Status:</b> {status}</div>
                    </div>
                </div>
            </div>

            <div style="margin-top:24px;border-top:1px solid #e5edf7;padding-top:18px;">
                <h3 style="margin-top:0;">Address</h3>
                <div style="color:#102a4c;white-space:pre-wrap;">{escape(safe(partner['address'])) or '-'}</div>
            </div>
        </div>

        <div>
            {render_audit_log_card('partner', int(partner['id']), 'Activity Log')}
        </div>
    </div>
    """
    return render_page(f"{title} {safe(partner['code'])}", details, current_path=request_path)


def page_header(title: str, subtitle: str, new_href: str = "", new_label: str = "", back_href: str = ""):
    actions = ""
    if back_href:
        actions += f'<a href="{back_href}" class="btn gray">Back</a> '
    if new_href and new_label:
        actions += f'<a href="{new_href}" class="btn green">{new_label}</a>'

    return f"""
    <div class="card">
        <div class="toolbar">
            <div>
                <h3 class="sub-title" style="margin-bottom:6px;">{title}</h3>
                <div style="color:#6f819d;font-size:14px;">{subtitle}</div>
            </div>
            <div>{actions}</div>
        </div>
    </div>
    """


def build_table_card(title: str, subtitle: str, partner_type: str, rows, request_path: str):
    add_href = "/ui/accounting/customers/new" if partner_type == "customer" else "/ui/accounting/vendors/new"
    add_label = "+ New Customer" if partner_type == "customer" else "+ New Vendor"
    back_href = "/ui/accounting/customers-hub" if partner_type == "customer" else "/ui/accounting/vendors-hub"

    body = ""
    for r in rows:
        active_badge = (
            '<span style="padding:6px 10px;border-radius:999px;background:#e8f7ec;color:#217a3c;font-size:12px;font-weight:700;">Active</span>'
            if int(r["is_active"] or 0) == 1
            else '<span style="padding:6px 10px;border-radius:999px;background:#fdecec;color:#b42318;font-size:12px;font-weight:700;">Inactive</span>'
        )

        body += f"""
        <tr>
            <td>{safe(r['code'])}</td>
            <td>{safe(r['name'])}</td>
            <td>{safe(r['phone'])}</td>
            <td>{safe(r['email'])}</td>
            <td>{safe(r['account_code'])}</td>
            <td>{safe(r['tax_no'])}</td>
            <td>{safe(r['payment_term_days'])}</td>
            <td>{active_badge}</td>
            <td style="white-space:nowrap;">
                <a class="btn gray" href="/ui/accounting/partners/{r['id']}">Open</a>
                <a class="btn blue" href="/ui/accounting/partners/{r['id']}/edit">Edit</a>
            </td>
        </tr>
        """

    if not body:
        body = "<tr><td colspan='9' style='text-align:center;color:#6f819d;padding:24px;'>No records found.</td></tr>"

    content = ""
    content += page_header(title, subtitle, add_href, add_label, back_href)
    content += f"""
    <div class="card">
        <table style="margin-top:6px;">
            <tr>
                <th>Code</th>
                <th>Name</th>
                <th>Phone</th>
                <th>Email</th>
                <th>Account</th>
                <th>Tax No</th>
                <th>Payment Term</th>
                <th>Status</th>
                <th>Actions</th>
            </tr>
            {body}
        </table>
    </div>
    """
    return HTMLResponse(render_page(title, content, current_path=request_path))


def build_form_card(title: str, subtitle: str, post_url: str, back_url: str, values=None, request_path: str = "", form_error: str = ""):
    values = values or {}

    error_html = ""
    if form_error:
        error_html = f'<div class="msg error">{form_error}</div>'

    content = ""
    content += page_header(title, subtitle, back_href=back_url)
    content += f"""
    {error_html}
    <div class="card">
        <form method="post" action="{post_url}">
            <div class="form-grid">
                <div class="form-group">
                    <label>Name</label>
                    <input name="name" value="{safe(values.get('name', ''))}" required>
                </div>

                <div class="form-group">
                    <label>Phone</label>
                    <input name="phone" value="{safe(values.get('phone', ''))}">
                </div>

                <div class="form-group">
                    <label>Email</label>
                    <input name="email" value="{safe(values.get('email', ''))}">
                </div>

                <div class="form-group">
                    <label>Account Code</label>
                    <select name="account_code">
                        {account_options(values.get("account_code", ""))}
                    </select>
                </div>

                <div class="form-group">
                    <label>Tax No</label>
                    <input name="tax_no" value="{safe(values.get('tax_no', ''))}">
                </div>

                <div class="form-group">
                    <label>Payment Term Days</label>
                    <input name="payment_term_days" value="{safe(values.get('payment_term_days', '0'))}">
                </div>

                <div class="form-group" style="grid-column: span 2;">
                    <label>Address</label>
                    <input name="address" value="{safe(values.get('address', ''))}">
                </div>

                <div class="form-group">
                    <label>Active</label>
                    <select name="is_active">
                        <option value="1" {'selected' if str(values.get('is_active', 1)) == '1' else ''}>Yes</option>
                        <option value="0" {'selected' if str(values.get('is_active', 1)) == '0' else ''}>No</option>
                    </select>
                </div>
            </div>

            <div class="form-actions">
                <button type="submit" class="btn green">Save</button>
                <a href="{back_url}" class="btn gray">Cancel</a>
            </div>
        </form>
    </div>
    """
    return HTMLResponse(render_page(title, content, current_path=request_path))


# =========================
# CUSTOMERS LIST
# =========================
@router.get("/ui/accounting/customers", response_class=HTMLResponse)
def customers(request: Request):
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, code, name, phone, email, account_code, tax_no, payment_term_days, is_active
        FROM partners
        WHERE LOWER(COALESCE(partner_type,'')) = 'customer'
        ORDER BY id DESC
    """).fetchall()
    conn.close()
    return build_table_card("Customers", "Manage customer master data.", "customer", rows, str(request.url.path))


# =========================
# NEW CUSTOMER
# =========================
@router.get("/ui/accounting/customers/new", response_class=HTMLResponse)
def new_customer(request: Request):
    return build_form_card(
        "New Customer",
        "Create a new customer master record.",
        "/ui/accounting/customers/save",
        "/ui/accounting/customers",
        values={"name": safe(request.query_params.get("name", ""))},
        request_path=str(request.url.path),
    )


# =========================
# SAVE CUSTOMER
# =========================
@router.post("/ui/accounting/customers/save")
async def save_customer(request: Request):
    form = await request.form()

    name = safe(form.get("name", ""))
    phone = safe(form.get("phone", ""))
    email = safe(form.get("email", ""))
    account_code = safe(form.get("account_code", ""))
    tax_no = safe(form.get("tax_no", ""))
    address = safe(form.get("address", ""))
    payment_term_days = to_int(form.get("payment_term_days", "0"), 0)
    opening_balance = 0.0
    is_active = to_int(form.get("is_active", "1"), 1)

    if not name:
        return build_form_card(
            "New Customer",
            "Create a new customer master record.",
            "/ui/accounting/customers/save",
            "/ui/accounting/customers",
            values=dict(form),
            request_path="/ui/accounting/customers/new",
            form_error="Customer name is required.",
        )

    code = next_partner_code("customer")

    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO partners (
            code, name, partner_type, phone, email, address,
            account_code, tax_no, payment_term_days, opening_balance, is_active
        )
        VALUES (?, ?, 'customer', ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        code, name, phone, email, address,
        account_code, tax_no, payment_term_days, opening_balance, is_active,
    ))
    safe_log_request_action(
        request,
        "partner",
        int(cur.lastrowid),
        "Created",
        f"Customer {code} created.",
        conn=conn,
        module="accounting",
    )
    conn.commit()
    conn.close()

    return RedirectResponse("/ui/accounting/customers", status_code=303)


# =========================
# VENDORS LIST
# =========================
@router.get("/ui/accounting/vendors", response_class=HTMLResponse)
def vendors(request: Request):
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, code, name, phone, email, account_code, tax_no, payment_term_days, is_active
        FROM partners
        WHERE LOWER(COALESCE(partner_type,'')) = 'vendor'
        ORDER BY id DESC
    """).fetchall()
    conn.close()
    return build_table_card("Vendors", "Manage vendor master data.", "vendor", rows, str(request.url.path))


# =========================
# NEW VENDOR
# =========================
@router.get("/ui/accounting/vendors/new", response_class=HTMLResponse)
def new_vendor(request: Request):
    return build_form_card(
        "New Vendor",
        "Create a new vendor master record.",
        "/ui/accounting/vendors/save",
        "/ui/accounting/vendors",
        values={"name": safe(request.query_params.get("name", ""))},
        request_path=str(request.url.path),
    )


# =========================
# SAVE VENDOR
# =========================
@router.post("/ui/accounting/vendors/save")
async def save_vendor(request: Request):
    form = await request.form()

    name = safe(form.get("name", ""))
    phone = safe(form.get("phone", ""))
    email = safe(form.get("email", ""))
    account_code = safe(form.get("account_code", ""))
    tax_no = safe(form.get("tax_no", ""))
    address = safe(form.get("address", ""))
    payment_term_days = to_int(form.get("payment_term_days", "0"), 0)
    opening_balance = 0.0
    is_active = to_int(form.get("is_active", "1"), 1)

    if not name:
        return build_form_card(
            "New Vendor",
            "Create a new vendor master record.",
            "/ui/accounting/vendors/save",
            "/ui/accounting/vendors",
            values=dict(form),
            request_path="/ui/accounting/vendors/new",
            form_error="Vendor name is required.",
        )

    code = next_partner_code("vendor")

    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO partners (
            code, name, partner_type, phone, email, address,
            account_code, tax_no, payment_term_days, opening_balance, is_active
        )
        VALUES (?, ?, 'vendor', ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        code, name, phone, email, address,
        account_code, tax_no, payment_term_days, opening_balance, is_active,
    ))
    safe_log_request_action(
        request,
        "partner",
        int(cur.lastrowid),
        "Created",
        f"Vendor {code} created.",
        conn=conn,
        module="accounting",
    )
    conn.commit()
    conn.close()

    return RedirectResponse("/ui/accounting/vendors", status_code=303)


# =========================
# VIEW PARTNER PROFILE
# =========================
@router.get("/ui/accounting/partners/{partner_id}", response_class=HTMLResponse)
def open_partner(request: Request, partner_id: int):
    conn = get_conn()
    row = conn.execute("""
        SELECT *
        FROM partners
        WHERE id = ?
        LIMIT 1
    """, (partner_id,)).fetchone()

    if not row:
        conn.close()
        return HTMLResponse("Partner not found.", status_code=404)

    html = partner_profile_html(conn, row, str(request.url.path))
    conn.close()
    return HTMLResponse(html)


# =========================
# EDIT PARTNER
# =========================
@router.get("/ui/accounting/partners/{partner_id}/edit", response_class=HTMLResponse)
def edit_partner(request: Request, partner_id: int):
    conn = get_conn()
    row = conn.execute("""
        SELECT *
        FROM partners
        WHERE id = ?
        LIMIT 1
    """, (partner_id,)).fetchone()
    conn.close()

    if not row:
        return HTMLResponse("Partner not found.", status_code=404)

    row = dict(row)
    partner_type = safe(row.get("partner_type", "customer")).lower()
    back_url = "/ui/accounting/customers" if partner_type == "customer" else "/ui/accounting/vendors"
    title = "Edit Customer" if partner_type == "customer" else "Edit Vendor"
    subtitle = "Update master data and control fields."

    return build_form_card(
        title,
        subtitle,
        f"/ui/accounting/partners/{partner_id}/update",
        back_url,
        values=row,
        request_path=str(request.url.path),
    )


# =========================
# UPDATE PARTNER
# =========================
@router.post("/ui/accounting/partners/{partner_id}/update")
async def update_partner(request: Request, partner_id: int):
    form = await request.form()

    name = safe(form.get("name", ""))
    phone = safe(form.get("phone", ""))
    email = safe(form.get("email", ""))
    account_code = safe(form.get("account_code", ""))
    tax_no = safe(form.get("tax_no", ""))
    address = safe(form.get("address", ""))
    payment_term_days = to_int(form.get("payment_term_days", "0"), 0)
    opening_balance = 0.0
    is_active = to_int(form.get("is_active", "1"), 1)

    conn = get_conn()
    row = conn.execute("""
        SELECT id, partner_type
        FROM partners
        WHERE id = ?
        LIMIT 1
    """, (partner_id,)).fetchone()

    if not row:
        conn.close()
        return HTMLResponse("Partner not found.", status_code=404)

    partner_type = safe(row["partner_type"]).lower() or "customer"
    back_url = "/ui/accounting/customers" if partner_type == "customer" else "/ui/accounting/vendors"
    title = "Edit Customer" if partner_type == "customer" else "Edit Vendor"

    if not name:
        conn.close()
        return build_form_card(
            title,
            "Update master data and control fields.",
            f"/ui/accounting/partners/{partner_id}/update",
            back_url,
            values=dict(form),
            request_path=f"/ui/accounting/partners/{partner_id}/edit",
            form_error="Name is required.",
        )

    conn.execute("""
        UPDATE partners
        SET name = ?, phone = ?, email = ?, address = ?,
            account_code = ?, tax_no = ?, payment_term_days = ?,
            opening_balance = ?, is_active = ?
        WHERE id = ?
    """, (
        name, phone, email, address,
        account_code, tax_no, payment_term_days,
        opening_balance, is_active, partner_id,
    ))
    safe_log_request_action(
        request,
        "partner",
        partner_id,
        "Updated",
        f"{partner_type.title()} {name} updated.",
        conn=conn,
        module="accounting",
    )
    conn.commit()
    conn.close()

    return RedirectResponse(f"/ui/accounting/partners/{partner_id}", status_code=303)
