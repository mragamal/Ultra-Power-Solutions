from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db import get_conn
from layout import render_page
from i18n import get_lang
from modules.accounting.partner_ledger import get_partner_account_code

router = APIRouter()


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
# CUSTOMER LOOKUP
# =========================================================
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


def customer_options(conn, selected_id=""):
    table_name, name_col = customer_name_expr(conn)
    if not table_name or not name_col:
        return "<option value=''>-- No Customers Found --</option>"

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

    html = "<option value=''>-- Select Customer --</option>"
    for r in rows:
        label = safe(r["name"])
        if safe(r["code"]):
            label = f"{safe(r['code'])} - {label}"
        sel = "selected" if str(selected_id or "") == str(r["id"]) else ""
        html += f"<option value='{r['id']}' {sel}>{label}</option>"
    return html


def get_customer_name(conn, customer_id):
    table_name, name_col = customer_name_expr(conn)
    if not table_name or not name_col:
        return ""

    code_col = customer_code_expr(conn, table_name)

    row = conn.execute(f"""
        SELECT id, {code_col} AS code, {name_col} AS name
        FROM {table_name}
        WHERE id = ?
        LIMIT 1
    """, (customer_id,)).fetchone()

    if not row:
        return ""

    label = safe(row["name"])
    if safe(row["code"]):
        label = f"{safe(row['code'])} - {label}"
    return label


# =========================================================
# OPENING / MOVEMENTS FROM POSTED JOURNAL ONLY
# =========================================================
def get_customer_partner_account_code(conn, customer_id):
    return safe(get_partner_account_code(conn, "customer", str(customer_id)))


def customer_journal_match_sql():
    # Older invoice journals may have been posted before partner_id was stored
    # on the receivable line. The statement still reads posted journal lines
    # only, but can infer the customer from the invoice linked to that journal.
    return """
      AND COALESCE(l.account_code,'') = ?
      AND (
            (
                COALESCE(l.partner_id, 0) = ?
                AND LOWER(COALESCE(l.partner_type,'')) = 'customer'
            )
            OR EXISTS (
                SELECT 1
                FROM customer_invoices ci
                WHERE ci.journal_id = j.id
                  AND COALESCE(ci.customer_id, 0) = ?
            )
      )
      AND LOWER(COALESCE(j.status,'')) = 'posted'
    """


def get_opening_journal_balance(conn, customer_id, account_code, date_from):
    if not date_from:
        return Decimal("0.00")
    row = conn.execute(f"""
        SELECT
            COALESCE(SUM(l.debit), 0) AS total_debit,
            COALESCE(SUM(l.credit), 0) AS total_credit
        FROM journal_lines l
        JOIN journal_entries j ON j.id = l.journal_id
        WHERE 1 = 1
        {customer_journal_match_sql()}
          AND COALESCE(j.entry_date,'') < ?
    """, (account_code, customer_id, customer_id, date_from)).fetchone()
    return q2(D(row["total_debit"] if row else 0) - D(row["total_credit"] if row else 0))


def get_journal_statement_rows(conn, customer_id, account_code, date_from="", date_to=""):
    sql = f"""
        SELECT
            j.id AS journal_id,
            j.source_type,
            j.source_id,
            j.entry_date AS trx_date,
            j.entry_no AS ref_no,
            j.reference,
            j.status,
            j.source_type,
            COALESCE(NULLIF(l.line_description, ''), NULLIF(j.description, ''), '') AS description,
            COALESCE(l.debit, 0) AS debit,
            COALESCE(l.credit, 0) AS credit
        FROM journal_lines l
        JOIN journal_entries j ON j.id = l.journal_id
        WHERE 1 = 1
        {customer_journal_match_sql()}
    """
    params = [account_code, customer_id, customer_id]

    if safe(date_from):
        sql += " AND COALESCE(j.entry_date,'') >= ?"
        params.append(date_from)

    if safe(date_to):
        sql += " AND COALESCE(j.entry_date,'') <= ?"
        params.append(date_to)

    sql += " ORDER BY j.entry_date, j.id, COALESCE(l.line_no,0), l.id"
    return conn.execute(sql, params).fetchall()


def build_statement_rows(conn, customer_id, account_code, date_from="", date_to=""):
    rows = []

    for r in get_journal_statement_rows(conn, customer_id, account_code, date_from, date_to):
        ref_text = safe(r["reference"])
        status_text = safe(r["status"]) or "draft"
        source_type = safe(r["source_type"]).lower()
        trx_type = "Journal"
        if source_type in ("customer_invoice", "customer_bill"):
            trx_type = "Invoice Journal"
        elif source_type in ("cash_receipt", "customer_payment"):
            trx_type = "Receipt Journal"
        rows.append({
            "trx_date": safe(r["trx_date"]),
            "trx_type": trx_type if status_text == "posted" else f"{trx_type} ({status_text})",
            "ref_no": safe(r["ref_no"]),
            "description": safe(r["description"]) or f"Journal {ref_text or safe(r['ref_no'])}",
            "debit": q2(r["debit"]),
            "credit": q2(r["credit"]),
            "extra": f"Status: {status_text}" + (f" | Reference: {ref_text}" if ref_text else ""),
            "open_link": f"/ui/accounting/journal/{r['journal_id']}",
        })

    rows.sort(key=lambda x: (x["trx_date"], x["trx_type"], x["ref_no"]))
    return rows


# =========================================================
# AGING SNAPSHOT
# =========================================================
def get_posted_allocated_amount_for_invoice(conn, invoice_id):
    row = conn.execute("""
        SELECT COALESCE(SUM(a.allocated_amount), 0) AS total_allocated
        FROM customer_payment_allocations a
        JOIN customer_payments p ON p.id = a.payment_id
        WHERE a.invoice_id = ?
          AND LOWER(COALESCE(p.status,'')) = 'posted'
    """, (invoice_id,)).fetchone()

    return q2(row["total_allocated"] if row else 0)


def get_open_invoices_snapshot(conn, customer_id):
    rows = conn.execute("""
        SELECT *
        FROM customer_invoices
        WHERE customer_id = ?
          AND LOWER(COALESCE(status,'')) = 'posted'
        ORDER BY invoice_date, id
    """, (customer_id,)).fetchall()

    snapshot_html = ""
    total_open = Decimal("0.00")

    for r in rows:
        allocated = get_posted_allocated_amount_for_invoice(conn, r["id"])
        open_amt = q2(q2(r["net_amount"]) - allocated)
        if open_amt <= Decimal("0.00"):
            continue

        total_open += open_amt

        snapshot_html += f"""
        <tr>
            <td>{safe(r['invoice_no'])}</td>
            <td>{safe(r['invoice_date'])}</td>
            <td>{safe(r['due_date'])}</td>
            <td>{money(r['net_amount'])}</td>
            <td>{money(allocated)}</td>
            <td>{money(open_amt)}</td>
            <td>{safe(r['payment_status'])}</td>
        </tr>
        """

    if not snapshot_html:
        snapshot_html = "<tr><td colspan='7' style='text-align:center;'>No open invoices.</td></tr>"

    return snapshot_html, total_open


# =========================================================
# ROUTE
# =========================================================
@router.get("/ui/accounting/customer-statement", response_class=HTMLResponse)
def customer_statement(
    request: Request,
    customer_id: str = "",
    date_from: str = "",
    date_to: str = ""
):
    lang = get_lang(request)
    conn = get_conn()

    customer_options_html = customer_options(conn, customer_id)

    filter_html = f"""
    <div class="card">
        <h3 class="sub-title" style="margin-top:0;">Customer Statement</h3>

        <form method="get">
            <div class="form-grid">
                <div class="form-group">
                    <label>Customer</label>
                    <select name="customer_id" required>
                        {customer_options_html}
                    </select>
                </div>

                <div class="form-group">
                    <label>From Date</label>
                    <input type="date" name="date_from" value="{safe(date_from)}">
                </div>

                <div class="form-group">
                    <label>To Date</label>
                    <input type="date" name="date_to" value="{safe(date_to)}">
                </div>
            </div>

            <div class="form-actions">
                <button class="btn green" type="submit">Show Statement</button>
                <a class="btn gray" href="/ui/accounting/customer-statement">Clear</a>
                <a class="btn gray" href="/ui/accounting/export-center">Export</a>
            </div>
        </form>
    </div>
    """

    if not customer_id:
        conn.close()
        return HTMLResponse(render_page("Customer Statement", filter_html, lang, current_path=request.url.path))

    try:
        customer_id_int = int(customer_id)
    except Exception:
        conn.close()
        return HTMLResponse("Invalid customer.", status_code=400)

    customer_label = get_customer_name(conn, customer_id_int)
    if not customer_label:
        conn.close()
        return HTMLResponse("Customer not found.", status_code=404)

    customer_account_code = get_customer_partner_account_code(conn, customer_id_int)
    if not customer_account_code:
        conn.close()
        return HTMLResponse("Customer account is missing.", status_code=400)

    opening_balance = q2(get_opening_journal_balance(conn, customer_id_int, customer_account_code, date_from))

    rows = build_statement_rows(conn, customer_id_int, customer_account_code, date_from, date_to)

    body = ""
    running = opening_balance
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")

    if safe(date_from):
        body += f"""
        <tr style="background:#f8fafc;font-weight:700;">
            <td></td>
            <td>Opening</td>
            <td></td>
            <td>Balance B/F</td>
            <td>{money(opening_balance) if opening_balance > 0 else '0.00'}</td>
            <td>{money(abs(opening_balance)) if opening_balance < 0 else '0.00'}</td>
            <td>{money(opening_balance)}</td>
            <td></td>
            <td></td>
        </tr>
        """

    for r in rows:
        debit = q2(r["debit"])
        credit = q2(r["credit"])

        total_debit += debit
        total_credit += credit
        running = q2(running + debit - credit)

        open_link = f'<a class="btn gray" href="{r["open_link"]}">Open</a>' if safe(r["open_link"]) else ""

        body += f"""
        <tr>
            <td>{safe(r['trx_date'])}</td>
            <td>{safe(r['trx_type'])}</td>
            <td>{safe(r['ref_no'])}</td>
            <td>{safe(r['description'])}</td>
            <td>{money(debit)}</td>
            <td>{money(credit)}</td>
            <td>{money(running)}</td>
            <td>{safe(r['extra'])}</td>
            <td>{open_link}</td>
        </tr>
        """

    if not body:
        body = "<tr><td colspan='9' style='text-align:center;'>No movements found.</td></tr>"

    closing_balance = q2(opening_balance + total_debit - total_credit)

    summary_html = f"""
    <div class="card">
        <div class="form-grid">
            <div><label>Customer</label><input value="{customer_label}" readonly></div>
            <div><label>Opening Balance</label><input value="{money(opening_balance)}" readonly></div>
            <div><label>Total Debit</label><input value="{money(total_debit)}" readonly></div>
            <div><label>Total Credit</label><input value="{money(total_credit)}" readonly></div>
            <div><label>Closing Balance</label><input value="{money(closing_balance)}" readonly></div>
        </div>
    </div>
    """

    statement_html = f"""
    <div class="card">
        <h3 class="sub-title">Statement</h3>
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Type</th>
                    <th>Reference</th>
                    <th>Description</th>
                    <th>Debit</th>
                    <th>Credit</th>
                    <th>Balance</th>
                    <th>Info</th>
                    <th>Open</th>
                </tr>
            </thead>
            <tbody>
                {body}
                <tr style="font-weight:800;background:#f9fafb;">
                    <td colspan="4">TOTAL</td>
                    <td>{money(total_debit)}</td>
                    <td>{money(total_credit)}</td>
                    <td>{money(closing_balance)}</td>
                    <td colspan="2"></td>
                </tr>
            </tbody>
        </table>
    </div>
    """

    open_snapshot_html, total_open = get_open_invoices_snapshot(conn, customer_id_int)

    open_invoices_html = f"""
    <div class="card">
        <div class="toolbar">
            <h3 class="sub-title" style="margin:0;">Open Invoices Snapshot</h3>
            <div><b>Total Open:</b> {money(total_open)}</div>
        </div>

        <table style="margin-top:12px;">
            <thead>
                <tr>
                    <th>Invoice #</th>
                    <th>Invoice Date</th>
                    <th>Due Date</th>
                    <th>Net Amount</th>
                    <th>Allocated</th>
                    <th>Open Amount</th>
                    <th>Payment Status</th>
                </tr>
            </thead>
            <tbody>
                {open_snapshot_html}
            </tbody>
        </table>
    </div>
    """

    conn.close()

    return HTMLResponse(
        render_page(
            "Customer Statement",
            filter_html + summary_html + statement_html + open_invoices_html,
            lang,
            current_path=request.url.path
        )
    )
