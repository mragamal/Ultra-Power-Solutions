from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from auth import current_user
from layout import render_page
from db import get_conn

router = APIRouter()


def safe_float(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def scalar(conn, query: str, params=(), default=0):
    try:
        row = conn.execute(query, params).fetchone()
        if not row:
            return default
        return default if row[0] is None else row[0]
    except Exception:
        return default


def next_invoice_no(conn) -> str:
    last_id = scalar(conn, "SELECT COALESCE(MAX(id), 0) FROM invoices", default=0)
    return f"INV-{int(last_id) + 1:05d}"


def next_entry_no(conn) -> str:
    last_id = scalar(conn, "SELECT COALESCE(MAX(id), 0) FROM journal_entries", default=0)
    return f"JE-{int(last_id) + 1:05d}"


def ensure_tables():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            notes TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            invoice_no TEXT,
            invoice_date TEXT,
            subtotal REAL DEFAULT 0,
            tax_percent REAL DEFAULT 0,
            tax_value REAL DEFAULT 0,
            withholding_percent REAL DEFAULT 0,
            withholding_value REAL DEFAULT 0,
            total REAL DEFAULT 0,
            paid_amount REAL DEFAULT 0,
            status TEXT DEFAULT 'draft',
            notes TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER,
            amount REAL,
            payment_method TEXT DEFAULT 'cash',
            payment_date TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            type TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_no TEXT,
            entry_date TEXT,
            description TEXT,
            reference_type TEXT,
            reference_id INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS journal_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER,
            account_id INTEGER,
            debit REAL DEFAULT 0,
            credit REAL DEFAULT 0,
            line_description TEXT
        )
    """)

    defaults = [
        ("1001", "Cash", "asset"),
        ("1002", "Bank", "asset"),
        ("1100", "Accounts Receivable", "asset"),
        ("1110", "Withholding Tax Receivable", "asset"),
        ("2100", "Output VAT", "liability"),
        ("4000", "Sales Revenue", "income"),
    ]
    for code, name, acc_type in defaults:
        exists = conn.execute("SELECT id FROM accounts WHERE code = ?", (code,)).fetchone()
        if not exists:
            cur.execute(
                "INSERT INTO accounts (code, name, type) VALUES (?, ?, ?)",
                (code, name, acc_type),
            )

    conn.commit()
    conn.close()


def get_account_id_by_code(conn, code: str):
    row = conn.execute("SELECT id FROM accounts WHERE code = ?", (code,)).fetchone()
    return row["id"] if row else None


def post_journal_entry(conn, entry_date: str, description: str, reference_type: str, reference_id: int, lines: list[dict]):
    existing = conn.execute("""
        SELECT id FROM journal_entries
        WHERE reference_type = ? AND reference_id = ?
        LIMIT 1
    """, (reference_type, reference_id)).fetchone()
    if existing:
        return existing["id"]

    total_debit = round(sum(safe_float(x.get("debit")) for x in lines), 2)
    total_credit = round(sum(safe_float(x.get("credit")) for x in lines), 2)
    if total_debit <= 0 or total_credit <= 0 or total_debit != total_credit:
        return None

    entry_no = next_entry_no(conn)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO journal_entries (entry_no, entry_date, description, reference_type, reference_id)
        VALUES (?, ?, ?, ?, ?)
    """, (entry_no, entry_date, description, reference_type, reference_id))
    entry_id = cur.lastrowid

    for line in lines:
        cur.execute("""
            INSERT INTO journal_lines (entry_id, account_id, debit, credit, line_description)
            VALUES (?, ?, ?, ?, ?)
        """, (
            entry_id,
            line["account_id"],
            safe_float(line.get("debit")),
            safe_float(line.get("credit")),
            line.get("line_description", ""),
        ))

    conn.commit()
    return entry_id


ensure_tables()


@router.get("/ui/clients")
def old_clients_redirect():
    return RedirectResponse("/ui/customers", status_code=302)


@router.get("/ui/customers", response_class=HTMLResponse)
def customers_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    conn = get_conn()
    customers = conn.execute("SELECT * FROM clients ORDER BY id DESC").fetchall()
    invoices = conn.execute("""
        SELECT invoices.*, clients.name AS customer_name
        FROM invoices
        LEFT JOIN clients ON clients.id = invoices.client_id
        ORDER BY invoices.id DESC
    """).fetchall()

    customer_rows = ""
    for c in customers:
        customer_rows += f"""
        <tr>
            <td style="padding:12px;">{c["id"]}</td>
            <td style="padding:12px;">{c["name"] or ""}</td>
            <td style="padding:12px;">{c["phone"] or ""}</td>
            <td style="padding:12px;">
                <a class="lang-btn" href="/ui/customers/invoices/new?customer_id={c["id"]}">New Invoice</a>
            </td>
        </tr>
        """

    invoice_rows = ""
    for i in invoices:
        due = safe_float(i["total"]) - safe_float(i["paid_amount"])
        invoice_rows += f"""
        <tr>
            <td style="padding:12px;"><a href="/ui/customers/invoices/{i["id"]}">{i["invoice_no"] or "-"}</a></td>
            <td style="padding:12px;">{i["customer_name"] or "-"}</td>
            <td style="padding:12px;">{safe_float(i["total"]):.2f}</td>
            <td style="padding:12px;">{safe_float(i["paid_amount"]):.2f}</td>
            <td style="padding:12px;">{due:.2f}</td>
            <td style="padding:12px;">{i["status"] or "draft"}</td>
        </tr>
        """

    conn.close()

    content = f"""
    <h1 class="page-title">Customers</h1>
    <div class="page-subtitle">Add customer + create invoice + confirm + payment</div>

    <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px;">
        <a class="btn-primary" href="/ui/customers/invoices/new">New Invoice</a>
    </div>

    <div class="card" style="margin-bottom:18px;">
        <h2 style="margin-bottom:14px;">Add Customer</h2>
        <form method="post" action="/ui/customers/add">
            <input name="name" placeholder="Customer Name" required>
            <input name="phone" placeholder="Phone">
            <input name="email" placeholder="Email">
            <input name="address" placeholder="Address">
            <input name="notes" placeholder="Notes">
            <button class="btn-primary" type="submit">Save Customer</button>
        </form>
    </div>

    <div class="card" style="margin-bottom:18px;">
        <h2 style="margin-bottom:14px;">Customers List</h2>
        <table style="width:100%; border-collapse:collapse;">
            <thead>
                <tr>
                    <th style="text-align:left; padding:12px;">ID</th>
                    <th style="text-align:left; padding:12px;">Name</th>
                    <th style="text-align:left; padding:12px;">Phone</th>
                    <th style="text-align:left; padding:12px;">Action</th>
                </tr>
            </thead>
            <tbody>
                {customer_rows if customer_rows else '<tr><td colspan="4" style="padding:12px;">No customers</td></tr>'}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h2 style="margin-bottom:14px;">Invoices</h2>
        <table style="width:100%; border-collapse:collapse;">
            <thead>
                <tr>
                    <th style="text-align:left; padding:12px;">Invoice No</th>
                    <th style="text-align:left; padding:12px;">Customer</th>
                    <th style="text-align:left; padding:12px;">Total</th>
                    <th style="text-align:left; padding:12px;">Paid</th>
                    <th style="text-align:left; padding:12px;">Due</th>
                    <th style="text-align:left; padding:12px;">Status</th>
                </tr>
            </thead>
            <tbody>
                {invoice_rows if invoice_rows else '<tr><td colspan="6" style="padding:12px;">No invoices</td></tr>'}
            </tbody>
        </table>
    </div>
    """

    return HTMLResponse(render_page("Customers", "dashboard", content, user["username"], "en"))


@router.post("/ui/customers/add")
def add_customer(
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
):
    conn = get_conn()
    conn.execute("""
        INSERT INTO clients (name, phone, email, address, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (name, phone, email, address, notes))
    conn.commit()
    conn.close()
    return RedirectResponse("/ui/customers", status_code=303)


@router.get("/ui/customers/invoices/new", response_class=HTMLResponse)
def new_invoice_page(request: Request, customer_id: int | None = None):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    conn = get_conn()
    customers = conn.execute("SELECT id, name FROM clients ORDER BY name").fetchall()
    invoice_no = next_invoice_no(conn)
    conn.close()

    options = ""
    for c in customers:
        selected = "selected" if customer_id and c["id"] == customer_id else ""
        options += f'<option value="{c["id"]}" {selected}>{c["name"]}</option>'

    content = f"""
    <h1 class="page-title">New Invoice</h1>

    <div class="card">
        <form method="post" action="/ui/customers/invoices/create">
            <select name="customer_id" required>
                <option value="">Select Customer</option>
                {options}
            </select>
            <input name="invoice_no" value="{invoice_no}">
            <input type="date" name="invoice_date">
            <input type="number" step="0.01" name="amount" placeholder="Amount" required>
            <input type="number" step="0.01" name="tax_percent" value="14" placeholder="Tax %">
            <input type="number" step="0.01" name="withholding_percent" value="0" placeholder="Withholding %">
            <input name="notes" placeholder="Notes">
            <button class="btn-primary" type="submit">Create Invoice</button>
        </form>
    </div>
    """

    return HTMLResponse(render_page("New Invoice", "dashboard", content, user["username"], "en"))


@router.post("/ui/customers/invoices/create")
def create_invoice(
    customer_id: int = Form(...),
    invoice_no: str = Form(""),
    invoice_date: str = Form(""),
    amount: float = Form(...),
    tax_percent: float = Form(14),
    withholding_percent: float = Form(0),
    notes: str = Form(""),
):
    subtotal = safe_float(amount)
    tax_value = subtotal * (safe_float(tax_percent) / 100.0)
    withholding_value = subtotal * (safe_float(withholding_percent) / 100.0)
    total = subtotal + tax_value - withholding_value

    conn = get_conn()
    final_invoice_no = invoice_no.strip() if invoice_no.strip() else next_invoice_no(conn)

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO invoices (
            client_id, invoice_no, invoice_date, subtotal, tax_percent, tax_value,
            withholding_percent, withholding_value, total, paid_amount, status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        customer_id,
        final_invoice_no,
        invoice_date or None,
        subtotal,
        tax_percent,
        tax_value,
        withholding_percent,
        withholding_value,
        total,
        0,
        "draft",
        notes,
    ))
    invoice_id = cur.lastrowid

    conn.commit()
    conn.close()

    return RedirectResponse(f"/ui/customers/invoices/{invoice_id}", status_code=303)


@router.get("/ui/customers/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_view(invoice_id: int, request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    conn = get_conn()

    invoice = conn.execute("""
        SELECT invoices.*, clients.name AS customer_name
        FROM invoices
        LEFT JOIN clients ON clients.id = invoices.client_id
        WHERE invoices.id = ?
    """, (invoice_id,)).fetchone()

    if not invoice:
        conn.close()
        return RedirectResponse("/ui/customers", status_code=303)

    payments = conn.execute("""
        SELECT * FROM payments
        WHERE invoice_id = ?
        ORDER BY id DESC
    """, (invoice_id,)).fetchall()

    entry = conn.execute("""
        SELECT * FROM journal_entries
        WHERE reference_type = 'customer_invoice' AND reference_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (invoice_id,)).fetchone()

    due = safe_float(invoice["total"]) - safe_float(invoice["paid_amount"])

    payment_rows = ""
    for p in payments:
        payment_rows += f"""
        <tr>
            <td style="padding:12px;">{p["payment_date"] or "-"}</td>
            <td style="padding:12px;">{p["payment_method"] or "cash"}</td>
            <td style="padding:12px;">{safe_float(p["amount"]):.2f}</td>
        </tr>
        """

    entry_html = f"<div><b>Entry No:</b> {entry['entry_no']}</div>" if entry else "<div><b>Entry No:</b> -</div>"

    conn.close()

    content = f"""
    <h1 class="page-title">Invoice</h1>
    <div class="page-subtitle">{invoice["invoice_no"] or "-"}</div>

    <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px;">
        <form method="post" action="/ui/customers/invoices/{invoice_id}/confirm">
            <button class="btn-primary" type="submit">Confirm</button>
        </form>

        <form method="post" action="/ui/customers/pay" style="display:flex; gap:10px; flex-wrap:wrap;">
            <input type="hidden" name="invoice_id" value="{invoice_id}">
            <input type="number" step="0.01" name="amount" placeholder="Amount" required>
            <select name="payment_method">
                <option value="cash">Cash</option>
                <option value="bank">Bank</option>
            </select>
            <button class="btn-primary" type="submit">Register Payment</button>
        </form>
    </div>

    <div class="dashboard-grid" style="margin-bottom:18px;">
        <div class="card"><div class="card-title">Customer</div><div class="card-value" style="font-size:20px;">{invoice["customer_name"] or "-"}</div></div>
        <div class="card"><div class="card-title">Total</div><div class="card-value">{safe_float(invoice["total"]):.2f}</div></div>
        <div class="card"><div class="card-title">Paid</div><div class="card-value">{safe_float(invoice["paid_amount"]):.2f}</div></div>
        <div class="card"><div class="card-title">Due</div><div class="card-value">{due:.2f}</div></div>
    </div>

    <div class="card" style="margin-bottom:18px;">
        <h2 style="margin-bottom:14px;">Journal Entry</h2>
        {entry_html}
    </div>

    <div class="card">
        <h2 style="margin-bottom:14px;">Payments</h2>
        <table style="width:100%; border-collapse:collapse;">
            <thead>
                <tr>
                    <th style="text-align:left; padding:12px;">Date</th>
                    <th style="text-align:left; padding:12px;">Method</th>
                    <th style="text-align:left; padding:12px;">Amount</th>
                </tr>
            </thead>
            <tbody>
                {payment_rows if payment_rows else '<tr><td colspan="3" style="padding:12px;">No payments</td></tr>'}
            </tbody>
        </table>
    </div>
    """

    return HTMLResponse(render_page("Invoice", "dashboard", content, user["username"], "en"))


@router.post("/ui/customers/invoices/{invoice_id}/confirm")
def confirm_invoice(invoice_id: int):
    conn = get_conn()
    ensure_accounting_tables(conn)

    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not invoice:
        conn.close()
        return RedirectResponse("/ui/customers", status_code=303)

    ar_acc = get_account_id_by_code(conn, "1100")
    wht_acc = get_account_id_by_code(conn, "1110")
    vat_acc = get_account_id_by_code(conn, "2100")
    sales_acc = get_account_id_by_code(conn, "4000")

    subtotal = safe_float(invoice["subtotal"])
    tax_value = safe_float(invoice["tax_value"])
    withholding_value = safe_float(invoice["withholding_value"])
    total = safe_float(invoice["total"])

    lines = []
    if ar_acc and total > 0:
        lines.append({"account_id": ar_acc, "debit": total, "credit": 0, "line_description": "Accounts Receivable"})
    if wht_acc and withholding_value > 0:
        lines.append({"account_id": wht_acc, "debit": withholding_value, "credit": 0, "line_description": "Withholding Tax Receivable"})
    if sales_acc and subtotal > 0:
        lines.append({"account_id": sales_acc, "debit": 0, "credit": subtotal, "line_description": "Sales Revenue"})
    if vat_acc and tax_value > 0:
        lines.append({"account_id": vat_acc, "debit": 0, "credit": tax_value, "line_description": "Output VAT"})

    post_journal_entry(
        conn,
        invoice["invoice_date"] or "",
        f"Customer Invoice {invoice['invoice_no'] or invoice_id}",
        "customer_invoice",
        invoice_id,
        lines,
    )

    conn.execute("UPDATE invoices SET status = 'confirmed' WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()

    return RedirectResponse(f"/ui/customers/invoices/{invoice_id}", status_code=303)


@router.post("/ui/customers/pay")
def register_payment(
    invoice_id: int = Form(...),
    amount: float = Form(...),
    payment_method: str = Form("cash"),
):
    conn = get_conn()
    ensure_accounting_tables(conn)

    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not invoice:
        conn.close()
        return RedirectResponse("/ui/customers", status_code=303)

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO payments (invoice_id, amount, payment_method)
        VALUES (?, ?, ?)
    """, (invoice_id, amount, payment_method))
    payment_id = cur.lastrowid

    new_paid = safe_float(invoice["paid_amount"]) + safe_float(amount)
    new_status = calc_status(safe_float(invoice["total"]), new_paid)

    conn.execute("""
        UPDATE invoices
        SET paid_amount = ?, status = ?
        WHERE id = ?
    """, (new_paid, new_status, invoice_id))

    cash_acc = get_account_id_by_code(conn, "1001" if payment_method == "cash" else "1002")
    ar_acc = get_account_id_by_code(conn, "1100")

    if cash_acc and ar_acc:
        post_journal_entry(
            conn,
            invoice["invoice_date"] or "",
            f"Customer Payment {invoice['invoice_no'] or invoice_id}",
            "customer_payment",
            payment_id,
            [
                {"account_id": cash_acc, "debit": safe_float(amount), "credit": 0, "line_description": "Cash/Bank"},
                {"account_id": ar_acc, "debit": 0, "credit": safe_float(amount), "line_description": "Accounts Receivable"},
            ],
        )

    conn.commit()
    conn.close()

    return RedirectResponse(f"/ui/customers/invoices/{invoice_id}", status_code=303)