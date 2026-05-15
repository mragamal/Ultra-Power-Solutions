from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db import get_conn
from layout import render_page
from modules.accounting.config import get_setting_value

router = APIRouter()


# =========================================================
# HELPERS
# =========================================================
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


def vendor_options(selected_id=""):
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, code, name
        FROM partners
        WHERE partner_type = 'vendor'
          AND COALESCE(is_active, 1) = 1
        ORDER BY name
    """).fetchall()
    conn.close()

    html = "<option value=''>-- All Vendors --</option>"
    for r in rows:
        selected = "selected" if str(selected_id or "") == str(r["id"]) else ""
        label = f"{safe(r['code'])} - {safe(r['name'])}" if safe(r["code"]) else safe(r["name"])
        html += f"<option value='{r['id']}' {selected}>{label}</option>"
    return html


def get_vendor_row(conn, vendor_id: int):
    return conn.execute("""
        SELECT *
        FROM partners
        WHERE id = ?
          AND partner_type = 'vendor'
        LIMIT 1
    """, (vendor_id,)).fetchone()


def get_vendor_account_code(conn, vendor_id: int):
    vendor = get_vendor_row(conn, vendor_id)
    if vendor and "account_code" in vendor.keys() and safe(vendor["account_code"]):
        return safe(vendor["account_code"])
    return safe(get_setting_value("vendor_control_account", get_setting_value("default_vendor_account", "211100")))


# =========================================================
# DATA BUILDERS
# =========================================================
def get_vendor_opening_journal_balance(conn, vendor_id: int, date_from: str = ""):
    if not date_from:
        return Decimal("0.00")
    vendor_account_code = get_vendor_account_code(conn, vendor_id)
    row = conn.execute("""
        SELECT
            COALESCE(SUM(l.debit), 0) AS total_debit,
            COALESCE(SUM(l.credit), 0) AS total_credit
        FROM journal_lines l
        JOIN journal_entries j ON j.id = l.journal_id
        WHERE LOWER(COALESCE(j.status,'')) = 'posted'
          AND LOWER(COALESCE(l.partner_type,'')) = 'vendor'
          AND COALESCE(l.partner_id, 0) = ?
          AND COALESCE(l.account_code, '') = ?
          AND COALESCE(j.entry_date,'') < ?
    """, (vendor_id, vendor_account_code, date_from)).fetchone()
    total_credit = Decimal(str(row["total_credit"] if row else 0))
    total_debit = Decimal(str(row["total_debit"] if row else 0))
    return (total_credit - total_debit).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)


def get_vendor_statement_rows(conn, vendor_id: int, date_from: str = "", date_to: str = ""):
    vendor = get_vendor_row(conn, vendor_id)
    if not vendor:
        return None, [], Decimal("0.00"), Decimal("0.00"), Decimal("0.00")
    vendor_account_code = get_vendor_account_code(conn, vendor_id)

    rows = []

    sql = """
        SELECT
            j.id AS journal_id,
            j.entry_date AS trx_date,
            j.entry_no AS doc_no,
            j.reference,
            COALESCE(NULLIF(l.line_description, ''), NULLIF(j.description, ''), '') AS description,
            COALESCE(l.debit, 0) AS debit,
            COALESCE(l.credit, 0) AS credit
        FROM journal_lines l
        JOIN journal_entries j ON j.id = l.journal_id
        WHERE LOWER(COALESCE(j.status,'')) = 'posted'
          AND LOWER(COALESCE(l.partner_type,'')) = 'vendor'
          AND COALESCE(l.partner_id, 0) = ?
          AND COALESCE(l.account_code, '') = ?
    """
    params = [vendor_id, vendor_account_code]
    if date_from:
        sql += " AND COALESCE(j.entry_date, '') >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND COALESCE(j.entry_date, '') <= ?"
        params.append(date_to)
    sql += " ORDER BY j.entry_date, j.id, COALESCE(l.line_no,0), l.id"
    journal_rows = conn.execute(sql, params).fetchall()

    for r in journal_rows:
        rows.append({
            "trx_date": safe(r["trx_date"]),
            "doc_type": "Journal",
            "doc_no": safe(r["doc_no"]),
            "due_date": "",
            "description": safe(r["description"]) or f"Journal {safe(r['reference']) or safe(r['doc_no'])}",
            "debit": Decimal(str(r["debit"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP),
            "credit": Decimal(str(r["credit"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP),
            "allocated": Decimal("0.00"),
            "open_amount": Decimal("0.00"),
            "unapplied": Decimal("0.00"),
            "journal_id": safe_int(r["journal_id"]),
            "reference": safe(r["reference"]),
            "sort_key": (safe(r["trx_date"]), 1, safe(r["doc_no"]), str(safe_int(r["journal_id"]))),
        })

    rows.sort(key=lambda x: x["sort_key"])

    opening_balance = get_vendor_opening_journal_balance(conn, vendor_id, date_from).quantize(
        Decimal("1.00"),
        rounding=ROUND_HALF_UP,
    )
    running_balance = opening_balance
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")

    for row in rows:
        total_debit += row["debit"]
        total_credit += row["credit"]
        running_balance += row["credit"] - row["debit"]
        row["running_balance"] = running_balance.quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)

    closing_balance = running_balance.quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    return vendor, rows, total_debit, total_credit, closing_balance


def get_vendor_summary_rows(conn, date_from: str = "", date_to: str = ""):
    vendors = conn.execute("""
        SELECT id, code, name
        FROM partners
        WHERE partner_type = 'vendor'
          AND COALESCE(is_active, 1) = 1
        ORDER BY name
    """).fetchall()

    result = []

    for v in vendors:
        _, rows, total_debit, total_credit, closing_balance = get_vendor_statement_rows(
            conn,
            v["id"],
            date_from=date_from,
            date_to=date_to,
        )

        result.append({
            "vendor_id": v["id"],
            "code": safe(v["code"]),
            "name": safe(v["name"]),
            "payments": total_debit,
            "bills": total_credit,
            "closing_balance": closing_balance,
            "count_rows": len(rows),
        })

    return result


# =========================================================
# ROUTES
# =========================================================
@router.get("/ui/accounting/vendor-statement", response_class=HTMLResponse)
def vendor_statement(request: Request, vendor_id: str = "", date_from: str = "", date_to: str = ""):
    conn = get_conn()

    vendor_id = safe(vendor_id)
    date_from = safe(date_from)
    date_to = safe(date_to)

    filter_html = f"""
    <div class="card">
        <h2>Vendor Statement</h2>

        <form method="get">
            <div class="form-grid">
                <div class="form-group">
                    <label>Vendor</label>
                    <select name="vendor_id">
                        {vendor_options(vendor_id)}
                    </select>
                </div>

                <div class="form-group">
                    <label>Date From</label>
                    <input type="date" name="date_from" value="{date_from}">
                </div>

                <div class="form-group">
                    <label>Date To</label>
                    <input type="date" name="date_to" value="{date_to}">
                </div>
            </div>

            <div class="form-actions">
                <button class="btn green" type="submit">Show</button>
                <a class="btn gray" href="/ui/accounting/vendor-statement">Clear</a>
                <a class="btn gray" href="/ui/accounting/export-center">Export</a>
            </div>
        </form>
    </div>
    """

    if vendor_id:
        try:
            vid = int(vendor_id)
        except Exception:
            conn.close()
            return HTMLResponse("Invalid vendor", status_code=400)

        data = get_vendor_statement_rows(conn, vid, date_from=date_from, date_to=date_to)
        conn.close()

        if not data or data[0] is None:
            return HTMLResponse("Vendor not found", status_code=404)

        vendor, rows, total_debit, total_credit, closing_balance = data

        body = ""
        for r in rows:
            alloc_info = ""
            if r["doc_type"] == "Bill":
                alloc_info = f"Paid: {money(r['allocated'])} | Open: {money(r['open_amount'])}"
            else:
                alloc_info = f"Allocated: {money(r['allocated'])} | Unapplied: {money(r['unapplied'])}"

            full_desc = safe(r["description"])
            if safe(r.get("reference")):
                full_desc = f"{full_desc} | Ref: {safe(r['reference'])}" if full_desc else f"Ref: {safe(r['reference'])}"

            body += f"""
            <tr>
                <td>{safe(r['trx_date'])}</td>
                <td>{safe(r['doc_type'])}</td>
                <td>{safe(r['doc_no'])}</td>
                <td>{safe(r['due_date'])}</td>
                <td>{full_desc}</td>
                <td>{money(r['debit'])}</td>
                <td>{money(r['credit'])}</td>
                <td>{money(r['running_balance'])}</td>
            </tr>
            """

        if not body:
            body = "<tr><td colspan='8'>No transactions found for this period.</td></tr>"

        vendor_label = f"{safe(vendor['code'])} - {safe(vendor['name'])}" if safe(vendor["code"]) else safe(vendor["name"])

        content = filter_html + f"""
        <div class="card">
            <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center;">
                <div>
                    <h3>Vendor Details</h3>
                    <p><b>Vendor:</b> {vendor_label}</p>
                    <p><b>Closing Balance:</b> {money(closing_balance)}</p>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    <a class="btn green" href="/ui/accounting/vendor-bills/new?vendor_id={vid}">+ New Bill</a>
                    <a class="btn green" href="/ui/accounting/cash-payments/new?party_type=vendor&vendor_id={vid}">+ Cash Payment</a>
                    <a class="btn gray" href="/ui/accounting/vendors/{vid}/view">Back to Vendor Hub</a>
                </div>
            </div>
        </div>

        <div class="card">
            <table>
                <tr>
                    <th>Date</th>
                    <th>Type</th>
                    <th>Document No</th>
                    <th>Due Date</th>
                    <th>Description</th>
                    <th>Debit</th>
                    <th>Credit</th>
                    <th>Running Balance</th>
                </tr>
                {body}
                <tr>
                    <th colspan="5">Totals</th>
                    <th>{money(total_debit)}</th>
                    <th>{money(total_credit)}</th>
                    <th>{money(closing_balance)}</th>
                </tr>
            </table>
        </div>
        """

        return HTMLResponse(render_page("Vendor Statement", content, current_path=str(request.url.path)))

    summary_rows = get_vendor_summary_rows(conn, date_from=date_from, date_to=date_to)
    conn.close()

    summary_body = ""
    total_bills = Decimal("0.00")
    total_payments = Decimal("0.00")
    total_closing = Decimal("0.00")

    for r in summary_rows:
        total_bills += r["bills"]
        total_payments += r["payments"]
        total_closing += r["closing_balance"]

        label = f"{r['code']} - {r['name']}" if r["code"] else r["name"]

        summary_body += f"""
        <tr>
            <td>
                <a href="/ui/accounting/vendor-statement?vendor_id={r['vendor_id']}&date_from={date_from}&date_to={date_to}">
                    {label}
                </a>
            </td>
            <td>{r['count_rows']}</td>
            <td>{money(r['bills'])}</td>
            <td>{money(r['payments'])}</td>
            <td>{money(r['closing_balance'])}</td>
        </tr>
        """

    if not summary_body:
        summary_body = "<tr><td colspan='5'>No vendors found.</td></tr>"

    content = filter_html + f"""
    <div class="card">
        <h3>Vendors Summary</h3>
        <table>
            <tr>
                <th>Vendor</th>
                <th>Movements</th>
                <th>Bills</th>
                <th>Payments</th>
                <th>Closing Balance</th>
            </tr>
            {summary_body}
            <tr>
                <th>Total</th>
                <th></th>
                <th>{money(total_bills)}</th>
                <th>{money(total_payments)}</th>
                <th>{money(total_closing)}</th>
            </tr>
        </table>
    </div>
    """

    return HTMLResponse(render_page("Vendor Statement", content, current_path=str(request.url.path)))
