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
            "employee_custody_account": "112200",
        }
        return defaults.get(key, default)

router = APIRouter()


def L(lang, en_text, ar_text):
    return ar_text if str(lang or "en").lower() == "ar" else en_text


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


def get_custody_accounts(conn):
    accounts = set()

    current_account = (
        get_setting_value("employee_custody_account", "")
        or get_setting_value("default_employee_account", "")
        or get_setting_value("employee_advance_account", "")
        or "112200"
    )
    if safe(current_account):
        accounts.add(safe(current_account))

    rows = conn.execute("""
        SELECT DISTINCT COALESCE(jl.account_code,'') AS account_code
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE LOWER(COALESCE(je.status,'')) = 'posted'
          AND COALESCE(jl.partner_type,'') = 'employee'
          AND LOWER(COALESCE(je.source_type,'')) IN ('petty_cash_custody','petty_cash_return','petty_cash_transfer')
          AND COALESCE(jl.account_code,'') <> ''
    """).fetchall()

    for row in rows:
        code = safe(row["account_code"])
        if code:
            accounts.add(code)

    return sorted(accounts)


def get_balance(conn, employee_id):
    custody_accounts = get_custody_accounts(conn)
    if not custody_accounts:
        custody_accounts = ["112200"]

    placeholders = ",".join("?" for _ in custody_accounts)

    row = conn.execute("""
        SELECT 
            COALESCE(SUM(jl.debit - jl.credit), 0) as balance
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE LOWER(COALESCE(je.status,'')) = 'posted'
          AND jl.partner_type = 'employee'
          AND jl.partner_id = ?
          AND COALESCE(jl.account_code,'') IN (""" + placeholders + """)
    """, [employee_id, *custody_accounts]).fetchone()

    return float(row["balance"] or 0) if row else 0.0


@router.get("/ui/accounting/petty-cash/list", response_class=HTMLResponse)
def petty_cash_list(request: Request):
    lang = get_lang(request)
    conn = get_conn()

    name_expr = employee_name_expr(conn)
    code_expr = employee_code_expr(conn)

    if not name_expr:
        conn.close()
        return HTMLResponse("Employees table not available from HR module.", status_code=400)

    employees = conn.execute(f"""
        SELECT id, {code_expr} AS code, {name_expr} AS name
        FROM employees
        WHERE COALESCE(is_active, 1) = 1
        ORDER BY name
    """).fetchall()

    rows_html = ""

    for emp in employees:
        balance = get_balance(conn, emp["id"])
        label = safe(emp["name"])
        if safe(emp["code"]):
            label = f"{safe(emp['code'])} - {label}"

        rows_html += f"""
        <tr>
            <td>{label}</td>
            <td>{money(balance)}</td>
            <td>
                <a class="btn purple" href="/ui/accounting/petty-cash/transfer">{L(lang, "Transfer", "تحويل")}</a>
                <a class="btn blue" href="/ui/accounting/petty-cash/statement?employee_id={emp['id']}">{L(lang, "Statement", "كشف حساب")}</a>
            </td>
        </tr>
        """

    if not rows_html:
        rows_html = f"""
        <tr>
            <td colspan="3" style="text-align:center;">{L(lang, "No active employees found.", "لا يوجد موظفون نشطون.")}</td>
        </tr>
        """

    conn.close()

    content = f"""
    <div class="card">
        <h2>{L(lang, "Petty Cash Employee Balances", "أرصدة عهد الموظفين")}</h2>

        <table>
            <thead>
                <tr>
                    <th>{L(lang, "Employee", "الموظف")}</th>
                    <th>{L(lang, "Balance", "الرصيد")}</th>
                    <th>{L(lang, "Actions", "الإجراءات")}</th>
                </tr>
            </thead>

            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
    """

    return HTMLResponse(render_page(L(lang, "Petty Cash", "العهدة النقدية"), content, lang, current_path=str(request.url.path)))
