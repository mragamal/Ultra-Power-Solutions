from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db import get_conn
from layout import render_page
from i18n import get_lang

try:
    from modules.accounting.config import get_setting_value
except Exception:
    def get_setting_value(key, default=None):
        defaults = {
            "employee_custody_account": "1020504",
        }
        return defaults.get(key, default)

router = APIRouter()


# =========================================================
# HELPERS
# =========================================================
def safe(x):
    return "" if x is None else str(x).strip()


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


def employee_name_expr(conn):
    cols = get_table_columns(conn, "employees")

    direct = [c for c in ["name", "employee_name", "full_name"] if c in cols]
    if direct:
        return "COALESCE(" + ", ".join(direct) + ")"

    if "first_name" in cols and "last_name" in cols:
        return "TRIM(COALESCE(first_name,'') || ' ' || COALESCE(last_name,''))"

    for c in cols:
        if "name" in c.lower():
            return c

    return None


def employee_code_expr(conn):
    cols = get_table_columns(conn, "employees")
    for c in ["code", "employee_code", "emp_code"]:
        if c in cols:
            return c
    return "''"


def get_employee_row(conn, employee_id):
    name_expr = employee_name_expr(conn)
    code_expr = employee_code_expr(conn)

    if not name_expr:
        return None

    return conn.execute(f"""
        SELECT id, {code_expr} AS code, {name_expr} AS name
        FROM employees
        WHERE id = ?
        LIMIT 1
    """, (employee_id,)).fetchone()


def get_employee_options(conn, selected_id=""):
    name_expr = employee_name_expr(conn)
    code_expr = employee_code_expr(conn)

    if not name_expr:
        return "<option value=''>-- No Employees Found --</option>"

    rows = conn.execute(f"""
        SELECT id, {code_expr} AS code, {name_expr} AS name
        FROM employees
        WHERE COALESCE(is_active,1) = 1
        ORDER BY name
    """).fetchall()

    html = "<option value=''>-- Select Employee --</option>"
    for r in rows:
        label = safe(r["name"])
        if safe(r["code"]):
            label = f"{safe(r['code'])} - {label}"
        sel = "selected" if str(selected_id or "") == str(r["id"]) else ""
        html += f"<option value='{r['id']}' {sel}>{label}</option>"
    return html


def get_custody_account():
    return (
        get_setting_value("employee_custody_account", "")
        or get_setting_value("default_employee_account", "")
        or get_setting_value("employee_advance_account", "")
        or "1020504"
    )


def get_custody_accounts(conn):
    accounts = set()

    current_account = safe(get_custody_account())
    if current_account:
        accounts.add(current_account)

    rows = conn.execute("""
        SELECT DISTINCT COALESCE(jl.account_code,'') AS account_code
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        LEFT JOIN accounts a ON a.code = jl.account_code
        WHERE LOWER(COALESCE(je.status,'')) = 'posted'
          AND COALESCE(jl.partner_type,'') = 'employee'
          AND COALESCE(jl.account_code,'') <> ''
          AND (
                COALESCE(jl.account_code,'') = ?
                OR COALESCE(a.name,'') LIKE '%عهد%'
                OR LOWER(COALESCE(a.name,'')) LIKE '%custody%'
          )
    """, (current_account,)).fetchall()

    for row in rows:
        code = safe(row["account_code"])
        if code:
            accounts.add(code)

    return sorted(accounts)


# =========================================================
# DATA
# =========================================================
def get_opening_balance(conn, employee_id, account_codes, date_from):
    if not date_from:
        return 0.0

    account_codes = [safe(x) for x in (account_codes or []) if safe(x)]
    if not account_codes:
        return 0.0

    placeholders = ",".join("?" for _ in account_codes)

    row = conn.execute("""
        SELECT
            COALESCE(SUM(jl.debit), 0) AS total_debit,
            COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE LOWER(COALESCE(je.status,'')) = 'posted'
          AND COALESCE(jl.partner_type,'') = 'employee'
          AND jl.partner_id = ?
          AND COALESCE(jl.account_code,'') IN (""" + placeholders + """)
          AND COALESCE(je.entry_date,'') < ?
    """, [employee_id, *account_codes, date_from]).fetchone()

    return float(row["total_debit"] or 0) - float(row["total_credit"] or 0)


def get_statement_rows(conn, employee_id, date_from="", date_to=""):
    custody_accounts = get_custody_accounts(conn)
    if not custody_accounts:
        return [], []

    placeholders = ",".join("?" for _ in custody_accounts)

    sql = """
        SELECT
            je.id AS journal_id,
            COALESCE(je.entry_date,'') AS entry_date,
            COALESCE(je.entry_no,'') AS entry_no,
            COALESCE(je.reference,'') AS reference,
            COALESCE(je.description,'') AS journal_description,
            COALESCE(jl.line_description,'') AS line_description,
            COALESCE(jl.debit,0) AS debit,
            COALESCE(jl.credit,0) AS credit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE LOWER(COALESCE(je.status,'')) = 'posted'
          AND COALESCE(jl.partner_type,'') = 'employee'
          AND jl.partner_id = ?
          AND COALESCE(jl.account_code,'') IN (""" + placeholders + """)
    """
    params = [employee_id, *custody_accounts]

    if date_from:
        sql += " AND COALESCE(je.entry_date,'') >= ?"
        params.append(date_from)

    if date_to:
        sql += " AND COALESCE(je.entry_date,'') <= ?"
        params.append(date_to)

    sql += """
        ORDER BY COALESCE(je.entry_date,''), je.id, COALESCE(jl.line_no,0), jl.id
    """

    return conn.execute(sql, params).fetchall(), custody_accounts


# =========================================================
# ROUTE
# =========================================================
@router.get("/ui/accounting/petty-cash/statement", response_class=HTMLResponse)
def petty_cash_statement(
    request: Request,
    employee_id: str = "",
    date_from: str = "",
    date_to: str = "",
    embed: int = 0,
):
    lang = get_lang(request)
    conn = get_conn()

    employee_options_html = get_employee_options(conn, employee_id)

    filter_html = f"""
    <div class="card">
        <h2>Employee Custody Statement</h2>

        <form method="get">
            <div class="row">
                <div class="col">
                    <label>Employee</label>
                    <select name="employee_id" required>
                        {employee_options_html}
                    </select>
                </div>

                <div class="col">
                    <label>From Date</label>
                    <input type="date" name="date_from" value="{safe(date_from)}">
                </div>

                <div class="col">
                    <label>To Date</label>
                    <input type="date" name="date_to" value="{safe(date_to)}">
                </div>
            </div>

            <div style="margin-top:14px;">
                <button class="btn green" type="submit">Show Statement</button>
                <a class="btn gray" href="/ui/accounting/petty-cash/statement">Clear</a>
                <a class="btn gray" href="/ui/accounting/export-center">Export</a>
            </div>
        </form>
    </div>
    """

    if not employee_id:
        conn.close()
        if int(embed or 0) == 1:
            return HTMLResponse(filter_html)
        return HTMLResponse(render_page("Employee Custody Statement", filter_html, lang, current_path=str(request.url.path)))

    try:
        employee_id_int = int(employee_id)
    except Exception:
        conn.close()
        return HTMLResponse("Invalid employee.", status_code=400)

    employee = get_employee_row(conn, employee_id_int)
    if not employee:
        conn.close()
        return HTMLResponse("Employee not found in HR module.", status_code=404)

    rows, custody_accounts = get_statement_rows(conn, employee_id_int, date_from, date_to)
    opening_balance = get_opening_balance(conn, employee_id_int, custody_accounts, date_from)

    body = ""
    running = float(opening_balance)
    total_debit = 0.0
    total_credit = 0.0

    if date_from:
        body += f"""
        <tr style="background:#f3f4f6;font-weight:700;">
            <td></td>
            <td>B/F</td>
            <td></td>
            <td>Opening Balance</td>
            <td>{money(opening_balance) if opening_balance > 0 else '0.00'}</td>
            <td>{money(abs(opening_balance)) if opening_balance < 0 else '0.00'}</td>
            <td>{money(opening_balance)}</td>
            <td></td>
        </tr>
        """

    for r in rows:
        debit = float(r["debit"] or 0)
        credit = float(r["credit"] or 0)
        total_debit += debit
        total_credit += credit
        running += debit - credit

        description = safe(r["line_description"]) or safe(r["journal_description"])

        body += f"""
        <tr>
            <td>{safe(r['entry_date'])}</td>
            <td>{safe(r['entry_no'])}</td>
            <td>{safe(r['reference'])}</td>
            <td>{description}</td>
            <td>{money(debit)}</td>
            <td>{money(credit)}</td>
            <td>{money(running)}</td>
            <td><a class="btn gray" href="/ui/accounting/journal/{r['journal_id']}">Open</a></td>
        </tr>
        """

    if not body:
        body = """
        <tr>
            <td colspan="8" style="text-align:center;">No custody movements found.</td>
        </tr>
        """

    employee_label = safe(employee["name"])
    if safe(employee["code"]):
        employee_label = f"{safe(employee['code'])} - {employee_label}"

    closing_balance = opening_balance + total_debit - total_credit

    summary_html = f"""
    <div class="card">
        <p><b>Employee:</b> {employee_label}</p>
        <p><b>Custody Accounts:</b> {", ".join(custody_accounts) if custody_accounts else "-"}</p>
        <p><b>Opening Balance:</b> {money(opening_balance)}</p>
        <p><b>Total Debit:</b> {money(total_debit)}</p>
        <p><b>Total Credit:</b> {money(total_credit)}</p>
        <p><b>Closing Balance:</b> {money(closing_balance)}</p>
    </div>
    """

    table_html = f"""
    <div class="card">
        <table>
            <tr>
                <th>Date</th>
                <th>Journal</th>
                <th>Reference</th>
                <th>Description</th>
                <th>Debit</th>
                <th>Credit</th>
                <th>Balance</th>
                <th>Open</th>
            </tr>
            {body}
        </table>
    </div>
    """

    conn.close()
    content = filter_html + summary_html + table_html
    if int(embed or 0) == 1:
        return HTMLResponse(content)

    return HTMLResponse(
        render_page(
            "Employee Custody Statement",
            content,
            lang,
            current_path=str(request.url.path)
        )
    )
