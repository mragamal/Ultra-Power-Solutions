from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

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
            "employee_custody_account": "1020504",
            "employee_custody_prefix": "EC",
            "employee_return_prefix": "ER",
            "journal_prefix": "JV",
            "default_cash_account": "111100",
            "default_bank_account": "112000",
        }
        return defaults.get(key, default)

router = APIRouter()


def L(lang, en_text, ar_text):
    return ar_text if str(lang or "en").lower() == "ar" else en_text


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
# EMPLOYEE LOOKUP FROM HR
# =========================================================
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


def get_employee_name(conn, employee_id):
    name_expr = employee_name_expr(conn)
    code_expr = employee_code_expr(conn)

    if not name_expr:
        return None, None

    row = conn.execute(f"""
        SELECT id, {code_expr} AS code, {name_expr} AS name
        FROM employees
        WHERE id = ?
        LIMIT 1
    """, (employee_id,)).fetchone()

    if not row:
        return None, None

    name = safe(row["name"])
    code = safe(row["code"])

    if not name:
        return None, None

    return code, name


def employee_display(conn, employee_id):
    code, name = get_employee_name(conn, employee_id)
    if not name:
        return ""
    return f"{code} - {name}" if code else name


# =========================================================
# DB INIT
# =========================================================
def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            employee_name TEXT,
            full_name TEXT,
            first_name TEXT,
            last_name TEXT,
            department TEXT,
            job_title TEXT,
            phone TEXT,
            email TEXT,
            hire_date TEXT,
            status TEXT DEFAULT 'Active',
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
            is_group INTEGER DEFAULT 0,
            allow_posting INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS petty_cash_custody (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            custody_no TEXT,
            custody_date TEXT,
            employee_id INTEGER,
            source_account_code TEXT,
            amount REAL DEFAULT 0,
            note TEXT,
            status TEXT DEFAULT 'draft',
            journal_id INTEGER,
            reversed_journal_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS petty_cash_return (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            return_no TEXT,
            return_date TEXT,
            employee_id INTEGER,
            source_account_code TEXT,
            amount REAL DEFAULT 0,
            note TEXT,
            status TEXT DEFAULT 'draft',
            journal_id INTEGER,
            reversed_journal_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS petty_cash_transfer (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transfer_no TEXT,
            transfer_date TEXT,
            source_employee_id INTEGER,
            target_employee_id INTEGER,
            amount REAL DEFAULT 0,
            note TEXT,
            status TEXT DEFAULT 'draft',
            journal_id INTEGER,
            reversed_journal_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS employee_custody_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_no TEXT,
            request_date TEXT,
            employee_id INTEGER,
            amount REAL DEFAULT 0,
            notes TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS employee_custody_return_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_no TEXT,
            request_date TEXT,
            employee_id INTEGER,
            amount REAL DEFAULT 0,
            notes TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    ensure_column(conn, "employees", "code", "ALTER TABLE employees ADD COLUMN code TEXT")
    ensure_column(conn, "employees", "name", "ALTER TABLE employees ADD COLUMN name TEXT")
    ensure_column(conn, "employees", "employee_name", "ALTER TABLE employees ADD COLUMN employee_name TEXT")
    ensure_column(conn, "employees", "full_name", "ALTER TABLE employees ADD COLUMN full_name TEXT")
    ensure_column(conn, "employees", "first_name", "ALTER TABLE employees ADD COLUMN first_name TEXT")
    ensure_column(conn, "employees", "last_name", "ALTER TABLE employees ADD COLUMN last_name TEXT")
    ensure_column(conn, "employees", "department", "ALTER TABLE employees ADD COLUMN department TEXT")
    ensure_column(conn, "employees", "job_title", "ALTER TABLE employees ADD COLUMN job_title TEXT")
    ensure_column(conn, "employees", "phone", "ALTER TABLE employees ADD COLUMN phone TEXT")
    ensure_column(conn, "employees", "email", "ALTER TABLE employees ADD COLUMN email TEXT")
    ensure_column(conn, "employees", "hire_date", "ALTER TABLE employees ADD COLUMN hire_date TEXT")
    ensure_column(conn, "employees", "status", "ALTER TABLE employees ADD COLUMN status TEXT DEFAULT 'Active'")
    ensure_column(conn, "employees", "is_active", "ALTER TABLE employees ADD COLUMN is_active INTEGER DEFAULT 1")

    conn.execute("""
        UPDATE employees
        SET is_active = CASE
            WHEN LOWER(COALESCE(status, '')) = 'active' THEN 1
            WHEN LOWER(COALESCE(status, '')) = 'inactive' THEN 0
            ELSE COALESCE(is_active, 1)
        END
        WHERE COALESCE(status, '') <> ''
    """)

    ensure_column(conn, "accounts", "allow_posting", "ALTER TABLE accounts ADD COLUMN allow_posting INTEGER DEFAULT 1")

    ensure_column(conn, "petty_cash_custody", "custody_no", "ALTER TABLE petty_cash_custody ADD COLUMN custody_no TEXT")
    ensure_column(conn, "petty_cash_custody", "custody_date", "ALTER TABLE petty_cash_custody ADD COLUMN custody_date TEXT")
    ensure_column(conn, "petty_cash_custody", "employee_id", "ALTER TABLE petty_cash_custody ADD COLUMN employee_id INTEGER")
    ensure_column(conn, "petty_cash_custody", "source_account_code", "ALTER TABLE petty_cash_custody ADD COLUMN source_account_code TEXT")
    ensure_column(conn, "petty_cash_custody", "amount", "ALTER TABLE petty_cash_custody ADD COLUMN amount REAL DEFAULT 0")
    ensure_column(conn, "petty_cash_custody", "note", "ALTER TABLE petty_cash_custody ADD COLUMN note TEXT")
    ensure_column(conn, "petty_cash_custody", "status", "ALTER TABLE petty_cash_custody ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "petty_cash_custody", "journal_id", "ALTER TABLE petty_cash_custody ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "petty_cash_custody", "reversed_journal_id", "ALTER TABLE petty_cash_custody ADD COLUMN reversed_journal_id INTEGER")
    ensure_column(conn, "petty_cash_custody", "created_at", "ALTER TABLE petty_cash_custody ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "petty_cash_return", "return_no", "ALTER TABLE petty_cash_return ADD COLUMN return_no TEXT")
    ensure_column(conn, "petty_cash_return", "return_date", "ALTER TABLE petty_cash_return ADD COLUMN return_date TEXT")
    ensure_column(conn, "petty_cash_return", "employee_id", "ALTER TABLE petty_cash_return ADD COLUMN employee_id INTEGER")
    ensure_column(conn, "petty_cash_return", "source_account_code", "ALTER TABLE petty_cash_return ADD COLUMN source_account_code TEXT")
    ensure_column(conn, "petty_cash_return", "amount", "ALTER TABLE petty_cash_return ADD COLUMN amount REAL DEFAULT 0")
    ensure_column(conn, "petty_cash_return", "note", "ALTER TABLE petty_cash_return ADD COLUMN note TEXT")
    ensure_column(conn, "petty_cash_return", "status", "ALTER TABLE petty_cash_return ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "petty_cash_return", "journal_id", "ALTER TABLE petty_cash_return ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "petty_cash_return", "reversed_journal_id", "ALTER TABLE petty_cash_return ADD COLUMN reversed_journal_id INTEGER")
    ensure_column(conn, "petty_cash_return", "created_at", "ALTER TABLE petty_cash_return ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "petty_cash_transfer", "transfer_no", "ALTER TABLE petty_cash_transfer ADD COLUMN transfer_no TEXT")
    ensure_column(conn, "petty_cash_transfer", "transfer_date", "ALTER TABLE petty_cash_transfer ADD COLUMN transfer_date TEXT")
    ensure_column(conn, "petty_cash_transfer", "source_employee_id", "ALTER TABLE petty_cash_transfer ADD COLUMN source_employee_id INTEGER")
    ensure_column(conn, "petty_cash_transfer", "target_employee_id", "ALTER TABLE petty_cash_transfer ADD COLUMN target_employee_id INTEGER")
    ensure_column(conn, "petty_cash_transfer", "amount", "ALTER TABLE petty_cash_transfer ADD COLUMN amount REAL DEFAULT 0")
    ensure_column(conn, "petty_cash_transfer", "note", "ALTER TABLE petty_cash_transfer ADD COLUMN note TEXT")
    ensure_column(conn, "petty_cash_transfer", "status", "ALTER TABLE petty_cash_transfer ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "petty_cash_transfer", "journal_id", "ALTER TABLE petty_cash_transfer ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "petty_cash_transfer", "reversed_journal_id", "ALTER TABLE petty_cash_transfer ADD COLUMN reversed_journal_id INTEGER")
    ensure_column(conn, "petty_cash_transfer", "created_at", "ALTER TABLE petty_cash_transfer ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "employee_custody_requests", "request_no", "ALTER TABLE employee_custody_requests ADD COLUMN request_no TEXT")
    ensure_column(conn, "employee_custody_requests", "request_date", "ALTER TABLE employee_custody_requests ADD COLUMN request_date TEXT")
    ensure_column(conn, "employee_custody_requests", "employee_id", "ALTER TABLE employee_custody_requests ADD COLUMN employee_id INTEGER")
    ensure_column(conn, "employee_custody_requests", "amount", "ALTER TABLE employee_custody_requests ADD COLUMN amount REAL DEFAULT 0")
    ensure_column(conn, "employee_custody_requests", "notes", "ALTER TABLE employee_custody_requests ADD COLUMN notes TEXT")
    ensure_column(conn, "employee_custody_requests", "status", "ALTER TABLE employee_custody_requests ADD COLUMN status TEXT DEFAULT 'active'")
    ensure_column(conn, "employee_custody_requests", "created_at", "ALTER TABLE employee_custody_requests ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "employee_custody_return_requests", "request_no", "ALTER TABLE employee_custody_return_requests ADD COLUMN request_no TEXT")
    ensure_column(conn, "employee_custody_return_requests", "request_date", "ALTER TABLE employee_custody_return_requests ADD COLUMN request_date TEXT")
    ensure_column(conn, "employee_custody_return_requests", "employee_id", "ALTER TABLE employee_custody_return_requests ADD COLUMN employee_id INTEGER")
    ensure_column(conn, "employee_custody_return_requests", "amount", "ALTER TABLE employee_custody_return_requests ADD COLUMN amount REAL DEFAULT 0")
    ensure_column(conn, "employee_custody_return_requests", "notes", "ALTER TABLE employee_custody_return_requests ADD COLUMN notes TEXT")
    ensure_column(conn, "employee_custody_return_requests", "status", "ALTER TABLE employee_custody_return_requests ADD COLUMN status TEXT DEFAULT 'active'")
    ensure_column(conn, "employee_custody_return_requests", "created_at", "ALTER TABLE employee_custody_return_requests ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    conn.commit()
    conn.close()


ensure_tables()


# =========================================================
# NUMBERING
# =========================================================
def next_custody_no():
    prefix = get_setting_value("employee_custody_prefix", "EC")
    conn = get_conn()
    row = conn.execute("""
        SELECT custody_no
        FROM petty_cash_custody
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["custody_no"]:
        return f"{prefix}-0001"

    try:
        num = int(str(row["custody_no"]).split("-")[-1])
    except Exception:
        num = 0

    return f"{prefix}-{num + 1:04d}"


def next_return_no():
    prefix = get_setting_value("employee_return_prefix", "ER")
    conn = get_conn()
    row = conn.execute("""
        SELECT return_no
        FROM petty_cash_return
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["return_no"]:
        return f"{prefix}-0001"

    try:
        num = int(str(row["return_no"]).split("-")[-1])
    except Exception:
        num = 0

    return f"{prefix}-{num + 1:04d}"


def next_transfer_no():
    prefix = get_setting_value("employee_transfer_prefix", "ET")
    conn = get_conn()
    row = conn.execute("""
        SELECT transfer_no
        FROM petty_cash_transfer
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["transfer_no"]:
        return f"{prefix}-0001"

    try:
        num = int(str(row["transfer_no"]).split("-")[-1])
    except Exception:
        num = 0

    return f"{prefix}-{num + 1:04d}"


def next_custody_request_no():
    prefix = get_setting_value("employee_custody_prefix", "EC")
    conn = get_conn()
    row = conn.execute("""
        SELECT request_no
        FROM employee_custody_requests
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["request_no"]:
        return f"{prefix}-REQ-0001"

    try:
        num = int(str(row["request_no"]).split("-")[-1])
    except Exception:
        num = 0

    return f"{prefix}-REQ-{num + 1:04d}"


def next_return_request_no():
    prefix = get_setting_value("employee_return_prefix", "ER")
    conn = get_conn()
    row = conn.execute("""
        SELECT request_no
        FROM employee_custody_return_requests
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["request_no"]:
        return f"{prefix}-REQ-0001"

    try:
        num = int(str(row["request_no"]).split("-")[-1])
    except Exception:
        num = 0

    return f"{prefix}-REQ-{num + 1:04d}"


# =========================================================
# LOOKUPS
# =========================================================
def employee_records():
    conn = get_conn()
    name_expr = employee_name_expr(conn)
    code_expr = employee_code_expr(conn)

    if not name_expr:
        conn.close()
        return []

    rows = conn.execute(f"""
        SELECT id, {code_expr} AS code, {name_expr} AS name
        FROM employees
        WHERE CASE
            WHEN LOWER(COALESCE(status, '')) = 'active' THEN 1
            WHEN LOWER(COALESCE(status, '')) = 'inactive' THEN 0
            ELSE COALESCE(is_active, 1)
        END = 1
        ORDER BY name
    """).fetchall()
    conn.close()

    result = []
    for r in rows:
        name = safe(r["name"])
        code = safe(r["code"])
        if not name:
            continue
        label = f"{code} - {name}" if code else name
        result.append({
            "id": str(r["id"]),
            "label": label
        })
    return result


def cash_bank_account_records():
    conn = get_conn()

    default_cash = str(get_setting_value("default_cash_account", "") or "")
    default_bank = str(get_setting_value("default_bank_account", "") or "")
    default_petty = str(get_setting_value("default_petty_cash_account", "") or "")

    rows = conn.execute("""
        SELECT code, name, type
        FROM accounts
        WHERE COALESCE(is_active, 1) = 1
          AND COALESCE(is_group, 0) = 0
          AND COALESCE(allow_posting, 1) = 1
        ORDER BY code, name
    """).fetchall()
    conn.close()

    result = []
    for r in rows:
        code = safe(r["code"])
        name = safe(r["name"])
        type_ = safe(r["type"]).lower()

        is_cash_bank = (
            code in [default_cash, default_bank, default_petty]
            or "cash" in name.lower()
            or "bank" in name.lower()
            or "petty cash" in name.lower()
            or (type_ in ["asset", "current asset"] and (code.startswith("111") or code.startswith("112")))
        )

        if not is_cash_bank:
            continue

        result.append({
            "code": code,
            "label": f"{code} - {name}"
        })
    return result


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


def is_valid_cash_bank_account(source_account_code):
    rows = cash_bank_account_records()
    return any(r["code"] == str(source_account_code or "") for r in rows)


def get_employee_custody_account():
    return (
        get_setting_value("employee_custody_account", "")
        or get_setting_value("default_employee_account", "")
        or get_setting_value("employee_advance_account", "")
        or "1020504"
    )


def get_employee_custody_accounts(conn):
    accounts = set()

    current_account = safe(get_employee_custody_account())
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


def get_employee_custody_balance(conn, employee_id):
    custody_accounts = get_employee_custody_accounts(conn)
    if not custody_accounts:
        custody_accounts = [get_employee_custody_account() or "1020504"]

    placeholders = ",".join("?" for _ in custody_accounts)
    row = conn.execute("""
        SELECT COALESCE(SUM(jl.debit - jl.credit), 0) AS balance
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE LOWER(COALESCE(je.status,'')) = 'posted'
          AND COALESCE(jl.partner_type,'') = 'employee'
          AND jl.partner_id = ?
          AND COALESCE(jl.account_code,'') IN (""" + placeholders + """)
    """, [employee_id, *custody_accounts]).fetchone()
    return q2(row["balance"] if row else 0)


# =========================================================
# DATA ACCESS
# =========================================================
def get_custody(conn, custody_id: int):
    return conn.execute("""
        SELECT *
        FROM petty_cash_custody
        WHERE id = ?
        LIMIT 1
    """, (custody_id,)).fetchone()


def get_return(conn, return_id: int):
    return conn.execute("""
        SELECT *
        FROM petty_cash_return
        WHERE id = ?
        LIMIT 1
    """, (return_id,)).fetchone()


def get_transfer(conn, transfer_id: int):
    return conn.execute("""
        SELECT *
        FROM petty_cash_transfer
        WHERE id = ?
        LIMIT 1
    """, (transfer_id,)).fetchone()


def get_linked_journal_status(conn, journal_id) -> str:
    if not journal_id:
        return ""
    row = conn.execute(
        "SELECT COALESCE(status,'') AS status FROM journal_entries WHERE id = ? LIMIT 1",
        (journal_id,),
    ).fetchone()
    return safe(row["status"]).lower() if row else ""


def movement_can_modify(conn, row) -> bool:
    if not row:
        return False
    status = safe(row["status"]).lower()
    if status in ("reversed", "cancelled"):
        return False
    journal_status = get_linked_journal_status(conn, row["journal_id"])
    return journal_status in ("", "draft", "pending_final_post")


def sync_petty_cash_movement_status(conn, table_name: str, row):
    if not row:
        return
    status = safe(row["status"]).lower()
    if status in ("reversed", "cancelled"):
        return
    journal_status = get_linked_journal_status(conn, row["journal_id"])
    new_status = "posted" if journal_status == "posted" else "draft"
    if status != new_status:
        conn.execute(f"UPDATE {table_name} SET status = ? WHERE id = ?", (new_status, row["id"]))


def movement_display_status(conn, row) -> str:
    status = safe(row["status"]).lower()
    if status in ("reversed", "cancelled"):
        return status
    return "posted" if get_linked_journal_status(conn, row["journal_id"]) == "posted" else "draft"


def petty_cash_row_actions(lang, path: str, row) -> str:
    row_id = int(row["id"] or 0)
    return f"""
        <a class="btn blue" href="/ui/accounting/petty-cash/{path}/{row_id}">{L(lang, "Open", "فتح")}</a>
        <a class="btn orange" href="/ui/accounting/petty-cash/{path}/{row_id}/edit">{L(lang, "Edit", "تعديل")}</a>
        <form method="post" action="/ui/accounting/petty-cash/{path}/{row_id}/delete" style="display:inline;" onsubmit="return confirm('{L(lang, "Delete this movement?", "هل تريد حذف هذه الحركة؟")}');">
            <button class="btn red" type="submit">{L(lang, "Delete", "حذف")}</button>
        </form>
    """


def custody_request_has_disbursement(conn, request_id: int) -> bool:
    row = conn.execute(
        """
        SELECT v.id
        FROM cash_vouchers v
        JOIN journal_entries j ON j.id = v.journal_id
        WHERE COALESCE(v.custody_request_id, 0) = ?
          AND LOWER(COALESCE(v.voucher_type,'')) = 'payment'
          AND LOWER(COALESCE(v.employee_trans_type,'')) = 'custody'
          AND LOWER(COALESCE(v.status,'')) <> 'reversed'
          AND LOWER(COALESCE(j.status,'')) = 'posted'
        LIMIT 1
        """,
        (request_id,),
    ).fetchone()
    return bool(row)


def custody_request_can_modify(conn, row) -> bool:
    if not row:
        return False
    status = safe(row["status"]).lower()
    if status in ("open", "cancelled"):
        return False
    return not custody_request_has_disbursement(conn, int(row["id"] or 0))


def custody_request_effective_status(conn, row) -> str:
    if not row:
        return ""
    stored_status = safe(row["status"]).lower()
    if stored_status == "cancelled":
        return "cancelled"
    if custody_request_has_disbursement(conn, int(row["id"] or 0)):
        return "open"
    return "active"


def sync_custody_request_status_if_needed(conn, row):
    if not row:
        return
    current_status = safe(row["status"]).lower()
    effective_status = custody_request_effective_status(conn, row)
    if effective_status and current_status != effective_status:
        conn.execute(
            "UPDATE employee_custody_requests SET status = ? WHERE id = ?",
            (effective_status, int(row["id"] or 0)),
        )


def return_request_has_receipt(conn, request_id: int) -> bool:
    row = conn.execute(
        """
        SELECT v.id
        FROM cash_vouchers v
        JOIN journal_entries j ON j.id = v.journal_id
        WHERE LOWER(COALESCE(v.source_type,'')) = 'custody_return_request'
          AND COALESCE(v.source_id, 0) = ?
          AND LOWER(COALESCE(v.voucher_type,'')) = 'receipt'
          AND LOWER(COALESCE(v.status,'')) <> 'reversed'
          AND LOWER(COALESCE(j.status,'')) = 'posted'
        LIMIT 1
        """,
        (request_id,),
    ).fetchone()
    return bool(row)


def return_request_can_modify(conn, row) -> bool:
    if not row:
        return False
    status = safe(row["status"]).lower()
    if status in ("received", "cancelled"):
        return False
    return not return_request_has_receipt(conn, int(row["id"] or 0))


def return_request_effective_status(conn, row) -> str:
    if not row:
        return ""
    stored_status = safe(row["status"]).lower()
    if stored_status == "cancelled":
        return "cancelled"
    if return_request_has_receipt(conn, int(row["id"] or 0)):
        return "received"
    return "active"


def sync_return_request_status_if_needed(conn, row):
    if not row:
        return
    current_status = safe(row["status"]).lower()
    effective_status = return_request_effective_status(conn, row)
    if effective_status and current_status != effective_status:
        conn.execute(
            "UPDATE employee_custody_return_requests SET status = ? WHERE id = ?",
            (effective_status, int(row["id"] or 0)),
        )


# =========================================================
# JOURNAL BUILDERS
# =========================================================
def create_draft_journal_for_custody(conn, custody_id: int):
    custody = get_custody(conn, custody_id)
    if not custody:
        raise Exception("Custody not found.")

    source_account = safe(custody["source_account_code"])
    custody_account = get_employee_custody_account()

    if not source_account:
        raise Exception("Please select cash/bank account.")

    if not is_valid_cash_bank_account(source_account):
        raise Exception("Selected account must be Cash / Bank only.")

    if not custody_account:
        raise Exception("Please set employee_custody_account in Configuration.")

    emp_code, emp_name = get_employee_name(conn, custody["employee_id"])
    if not emp_name:
        raise Exception("Employee not found in HR module.")

    amount = q2(custody["amount"])
    if amount <= Decimal("0.00"):
        raise Exception("Amount must be greater than zero.")

    journal_desc = f"Employee Custody {custody['custody_no']}"
    if custody["note"]:
        journal_desc += f" - {custody['note']}"

    lines = [
        {
            "description": safe(custody["note"]) or f"Employee Custody - {emp_name}",
            "account_code": custody_account,
            "debit": amount,
            "credit": Decimal("0.00"),
            "partner_type": "employee",
            "partner_id": custody["employee_id"],
        },
        {
            "description": f"Cash/Bank Issue to {emp_name}",
            "account_code": source_account,
            "debit": Decimal("0.00"),
            "credit": amount,
            "partner_type": None,
            "partner_id": None,
        }
    ]

    journal_id = create_journal_entry(
        conn=conn,
        entry_date=custody["custody_date"],
        description=journal_desc,
        reference=custody["custody_no"],
        source_type="petty_cash_custody",
        source_id=custody_id,
        lines=lines,
    )

    conn.execute("""
        UPDATE petty_cash_custody
        SET journal_id = ?, status = 'draft'
        WHERE id = ?
    """, (journal_id, custody_id))

    return journal_id


def rebuild_draft_journal_for_custody(conn, custody_id: int):
    custody = get_custody(conn, custody_id)
    if not custody:
        raise Exception("Custody not found.")

    if custody["journal_id"]:
        delete_draft_journal_entry(conn, custody["journal_id"])
        conn.execute("""
            UPDATE petty_cash_custody
            SET journal_id = NULL
            WHERE id = ?
        """, (custody_id,))

    return create_draft_journal_for_custody(conn, custody_id)


def create_draft_journal_for_return(conn, return_id: int):
    ret = get_return(conn, return_id)
    if not ret:
        raise Exception("Return not found.")

    source_account = safe(ret["source_account_code"])
    custody_account = get_employee_custody_account()

    if not source_account:
        raise Exception("Please select cash/bank account.")

    if not is_valid_cash_bank_account(source_account):
        raise Exception("Selected account must be Cash / Bank only.")

    if not custody_account:
        raise Exception("Please set employee_custody_account in Configuration.")

    emp_code, emp_name = get_employee_name(conn, ret["employee_id"])
    if not emp_name:
        raise Exception("Employee not found in HR module.")

    amount = q2(ret["amount"])
    if amount <= Decimal("0.00"):
        raise Exception("Amount must be greater than zero.")

    journal_desc = f"Return Custody {ret['return_no']}"
    if ret["note"]:
        journal_desc += f" - {ret['note']}"

    lines = [
        {
            "description": f"Cash/Bank Receipt from {emp_name}",
            "account_code": source_account,
            "debit": amount,
            "credit": Decimal("0.00"),
            "partner_type": None,
            "partner_id": None,
        },
        {
            "description": safe(ret["note"]) or f"Return Custody - {emp_name}",
            "account_code": custody_account,
            "debit": Decimal("0.00"),
            "credit": amount,
            "partner_type": "employee",
            "partner_id": ret["employee_id"],
        }
    ]

    journal_id = create_journal_entry(
        conn=conn,
        entry_date=ret["return_date"],
        description=journal_desc,
        reference=ret["return_no"],
        source_type="petty_cash_return",
        source_id=return_id,
        lines=lines,
    )

    conn.execute("""
        UPDATE petty_cash_return
        SET journal_id = ?, status = 'draft'
        WHERE id = ?
    """, (journal_id, return_id))

    return journal_id


def rebuild_draft_journal_for_return(conn, return_id: int):
    ret = get_return(conn, return_id)
    if not ret:
        raise Exception("Return not found.")

    if ret["journal_id"]:
        delete_draft_journal_entry(conn, ret["journal_id"])
        conn.execute("""
            UPDATE petty_cash_return
            SET journal_id = NULL
            WHERE id = ?
        """, (return_id,))

    return create_draft_journal_for_return(conn, return_id)


def create_draft_journal_for_transfer(conn, transfer_id: int):
    transfer = get_transfer(conn, transfer_id)
    if not transfer:
        raise Exception("Custody transfer not found.")

    custody_account = get_employee_custody_account()
    if not custody_account:
        raise Exception("Please set employee_custody_account in Configuration.")

    source_employee_id = int(transfer["source_employee_id"] or 0)
    target_employee_id = int(transfer["target_employee_id"] or 0)
    if source_employee_id <= 0 or target_employee_id <= 0:
        raise Exception("Please select source and target employees.")
    if source_employee_id == target_employee_id:
        raise Exception("Source employee and target employee must be different.")

    _, source_name = get_employee_name(conn, source_employee_id)
    _, target_name = get_employee_name(conn, target_employee_id)
    if not source_name or not target_name:
        raise Exception("Employee not found in HR module.")

    amount = q2(transfer["amount"])
    if amount <= Decimal("0.00"):
        raise Exception("Amount must be greater than zero.")

    available_balance = get_employee_custody_balance(conn, source_employee_id)
    if available_balance < amount:
        raise Exception(f"Source employee custody balance is not enough. Available: {money(available_balance)}")

    journal_desc = f"Custody Transfer {transfer['transfer_no']}"
    if transfer["note"]:
        journal_desc += f" - {transfer['note']}"

    lines = [
        {
            "description": safe(transfer["note"]) or f"Custody Transfer To - {target_name}",
            "account_code": custody_account,
            "debit": amount,
            "credit": Decimal("0.00"),
            "partner_type": "employee",
            "partner_id": target_employee_id,
        },
        {
            "description": safe(transfer["note"]) or f"Custody Transfer From - {source_name}",
            "account_code": custody_account,
            "debit": Decimal("0.00"),
            "credit": amount,
            "partner_type": "employee",
            "partner_id": source_employee_id,
        },
    ]

    journal_id = create_journal_entry(
        conn=conn,
        entry_date=transfer["transfer_date"],
        description=journal_desc,
        reference=transfer["transfer_no"],
        source_type="petty_cash_transfer",
        source_id=transfer_id,
        lines=lines,
    )

    conn.execute("""
        UPDATE petty_cash_transfer
        SET journal_id = ?, status = 'draft'
        WHERE id = ?
    """, (journal_id, transfer_id))

    return journal_id


def rebuild_draft_journal_for_transfer(conn, transfer_id: int):
    transfer = get_transfer(conn, transfer_id)
    if not transfer:
        raise Exception("Custody transfer not found.")

    if transfer["journal_id"]:
        delete_draft_journal_entry(conn, transfer["journal_id"])
        conn.execute("""
            UPDATE petty_cash_transfer
            SET journal_id = NULL
            WHERE id = ?
        """, (transfer_id,))

    return create_draft_journal_for_transfer(conn, transfer_id)


# =========================================================
# UI
# =========================================================
def datalist_script():
    return """
    <script>
    function bindDatalistInput(inputId, hiddenId, listId, dataAttr) {
        const input = document.getElementById(inputId);
        const hidden = document.getElementById(hiddenId);
        const list = document.getElementById(listId);

        if (!input || !hidden || !list) return;

        function syncHidden() {
            const val = input.value.trim();
            hidden.value = "";

            const options = list.querySelectorAll("option");
            for (const opt of options) {
                if ((opt.value || "").trim() === val) {
                    hidden.value = opt.getAttribute(dataAttr) || "";
                    break;
                }
            }
        }

        input.addEventListener("input", syncHidden);
        input.addEventListener("change", syncHidden);
        input.addEventListener("blur", syncHidden);
    }

    window.addEventListener("DOMContentLoaded", function() {
        bindDatalistInput("employee_label", "employee_id", "employee_list", "data-id");
        bindDatalistInput("source_account_label", "source_account_code", "cash_bank_list", "data-code");
        bindDatalistInput("return_employee_label", "return_employee_id", "return_employee_list", "data-id");
        bindDatalistInput("return_source_account_label", "return_source_account_code", "return_cash_bank_list", "data-code");
        bindDatalistInput("transfer_source_employee_label", "transfer_source_employee_id", "transfer_source_employee_list", "data-id");
        bindDatalistInput("transfer_target_employee_label", "transfer_target_employee_id", "transfer_target_employee_list", "data-id");
    });
    </script>
    """


def employee_datalist_html():
    items = employee_records()
    return "".join([
        f"<option value=\"{r['label']}\" data-id=\"{r['id']}\"></option>"
        for r in items
    ])


def cash_bank_datalist_html():
    items = cash_bank_account_records()
    return "".join([
        f"<option value=\"{r['label']}\" data-code=\"{r['code']}\"></option>"
        for r in items
    ])


# =========================================================
# ROUTES
# =========================================================
@router.get("/ui/accounting/petty-cash", response_class=HTMLResponse)
def petty_cash_home(request: Request):
    lang = get_lang(request)
    conn = get_conn()
    custody_request_rows = conn.execute("""
        SELECT *
        FROM employee_custody_requests
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    return_request_rows = conn.execute("""
        SELECT *
        FROM employee_custody_return_requests
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    custody_rows = conn.execute("""
        SELECT *
        FROM petty_cash_custody
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    return_rows = conn.execute("""
        SELECT *
        FROM petty_cash_return
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    transfer_rows = conn.execute("""
        SELECT *
        FROM petty_cash_transfer
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    body_custody = ""
    for r in custody_rows:
        sync_petty_cash_movement_status(conn, "petty_cash_custody", r)
        actions = petty_cash_row_actions(lang, "custody", r) if movement_can_modify(conn, r) else f'<a class="btn blue" href="/ui/accounting/petty-cash/custody/{r["id"]}">{L(lang, "Open", "فتح")}</a>'
        body_custody += f"""
        <tr>
            <td>{r['custody_no'] or ''}</td>
            <td>{r['custody_date'] or ''}</td>
            <td>{employee_display(conn, r['employee_id'])}</td>
            <td>{account_display(r['source_account_code'])}</td>
            <td>{money(r['amount'])}</td>
            <td>{movement_display_status(conn, r)}</td>
            <td>{r['journal_id'] or ''}</td>
            <td style="white-space:nowrap;">{actions}</td>
        </tr>
        """

    if not body_custody:
        body_custody = f"<tr><td colspan='8' style='text-align:center;'>{L(lang, 'No custody records found.', 'لا توجد حركات عهدة.')}</td></tr>"

    body_custody_requests = ""
    for r in custody_request_rows:
        sync_custody_request_status_if_needed(conn, r)
        status = custody_request_effective_status(conn, r)
        can_modify_request = status == "active"
        status_label = {
            "active": L(lang, "Pending", "معلق"),
            "open": L(lang, "Disbursed", "تم الصرف"),
            "cancelled": L(lang, "Cancelled", "ملغي"),
        }.get(status, safe(r["status"]))
        status_cls = "blue" if status == "active" else ("green" if status == "open" else "red")
        disburse_action = ""
        edit_action = ""
        delete_action = ""
        if can_modify_request:
            disburse_action = f'<a class="btn green" href="/ui/accounting/cash-payments/new?party_type=employee&employee_id={r["employee_id"]}&employee_trans_type=custody&custody_request_id={r["id"]}&amount={r["amount"]}">{L(lang, "Disburse", "صرف")}</a>'
            edit_action = f'<a class="btn orange" href="/ui/accounting/petty-cash/custody-request/{r["id"]}/edit">{L(lang, "Edit", "تعديل")}</a>'
            delete_action = f'<form method="post" action="/ui/accounting/petty-cash/custody-request/{r["id"]}/delete" style="display:inline;" onsubmit="return confirm(\'{L(lang, "Delete this request?", "هل تريد حذف هذا الطلب؟")}\');"><button class="btn red" type="submit">{L(lang, "Delete", "حذف")}</button></form>'
        body_custody_requests += f"""
        <tr>
            <td>{safe(r['request_no'])}</td>
            <td>{safe(r['request_date'])}</td>
            <td>{employee_display(conn, r['employee_id'])}</td>
            <td>{money(r['amount'])}</td>
            <td><span class="status-chip {status_cls}">{status_label}</span></td>
            <td style="white-space:nowrap;">
                {disburse_action}
                {edit_action}
                {delete_action}
                <a class="btn blue" href="/ui/accounting/petty-cash/custody-request/{r['id']}">{L(lang, "Open", "فتح")}</a>
            </td>
        </tr>
        """

    if not body_custody_requests:
        body_custody_requests = f"<tr><td colspan='6' style='text-align:center;'>{L(lang, 'No custody requests found.', 'لا توجد طلبات عهدة.')}</td></tr>"

    body_return_requests = ""
    for r in return_request_rows:
        sync_return_request_status_if_needed(conn, r)
        status = return_request_effective_status(conn, r)
        can_modify_request = status == "active"
        status_label = {
            "active": L(lang, "Pending", "معلق"),
            "received": L(lang, "Received", "تم الاستلام"),
            "cancelled": L(lang, "Cancelled", "ملغي"),
        }.get(status, safe(r["status"]))
        status_cls = "blue" if status == "active" else ("green" if status == "received" else "red")
        receive_action = ""
        edit_action = ""
        delete_action = ""
        if can_modify_request:
            receive_action = f'<a class="btn green" href="/ui/accounting/cash-receipts/new?party_type=employee&employee_id={r["employee_id"]}&employee_trans_type=custody_return&source_type=custody_return_request&source_id={r["id"]}&amount={r["amount"]}">{L(lang, "Receive", "استلام")}</a>'
            edit_action = f'<a class="btn orange" href="/ui/accounting/petty-cash/return-request/{r["id"]}/edit">{L(lang, "Edit", "تعديل")}</a>'
            delete_action = f'<form method="post" action="/ui/accounting/petty-cash/return-request/{r["id"]}/delete" style="display:inline;" onsubmit="return confirm(\'{L(lang, "Delete this request?", "هل تريد حذف هذا الطلب؟")}\');"><button class="btn red" type="submit">{L(lang, "Delete", "حذف")}</button></form>'
        body_return_requests += f"""
        <tr>
            <td>{safe(r['request_no'])}</td>
            <td>{safe(r['request_date'])}</td>
            <td>{employee_display(conn, r['employee_id'])}</td>
            <td>{money(r['amount'])}</td>
            <td><span class="status-chip {status_cls}">{status_label}</span></td>
            <td style="white-space:nowrap;">
                {receive_action}
                {edit_action}
                {delete_action}
                <a class="btn blue" href="/ui/accounting/petty-cash/return-request/{r['id']}">{L(lang, "Open", "فتح")}</a>
            </td>
        </tr>
        """

    if not body_return_requests:
        body_return_requests = f"<tr><td colspan='6' style='text-align:center;'>{L(lang, 'No custody return requests found.', 'لا توجد طلبات رد عهدة.')}</td></tr>"

    conn.commit()
    body_return = ""
    for r in return_rows:
        sync_petty_cash_movement_status(conn, "petty_cash_return", r)
        actions = petty_cash_row_actions(lang, "return", r) if movement_can_modify(conn, r) else f'<a class="btn blue" href="/ui/accounting/petty-cash/return/{r["id"]}">{L(lang, "Open", "فتح")}</a>'
        body_return += f"""
        <tr>
            <td>{r['return_no'] or ''}</td>
            <td>{r['return_date'] or ''}</td>
            <td>{employee_display(conn, r['employee_id'])}</td>
            <td>{account_display(r['source_account_code'])}</td>
            <td>{money(r['amount'])}</td>
            <td>{movement_display_status(conn, r)}</td>
            <td>{r['journal_id'] or ''}</td>
            <td style="white-space:nowrap;">{actions}</td>
        </tr>
        """

    if not body_return:
        body_return = f"<tr><td colspan='8' style='text-align:center;'>{L(lang, 'No return records found.', 'لا توجد حركات رد عهدة.')}</td></tr>"

    body_transfer = ""
    for r in transfer_rows:
        sync_petty_cash_movement_status(conn, "petty_cash_transfer", r)
        actions = petty_cash_row_actions(lang, "transfer", r) if movement_can_modify(conn, r) else f'<a class="btn blue" href="/ui/accounting/petty-cash/transfer/{r["id"]}">{L(lang, "Open", "فتح")}</a>'
        body_transfer += f"""
        <tr>
            <td>{r['transfer_no'] or ''}</td>
            <td>{r['transfer_date'] or ''}</td>
            <td>{employee_display(conn, r['source_employee_id'])}</td>
            <td>{employee_display(conn, r['target_employee_id'])}</td>
            <td>{money(r['amount'])}</td>
            <td>{movement_display_status(conn, r)}</td>
            <td>{r['journal_id'] or ''}</td>
            <td style="white-space:nowrap;">{actions}</td>
        </tr>
        """

    if not body_transfer:
        body_transfer = f"<tr><td colspan='8' style='text-align:center;'>{L(lang, 'No transfer records found.', 'لا توجد حركات تحويل عهدة.')}</td></tr>"

    conn.close()

    html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>{L(lang, "Petty Cash", "العهدة النقدية")}</h2>
            <div>
                <a class="btn orange" href="/ui/accounting/petty-cash/custody-request/new">{L(lang, "Custody Request", "طلب صرف عهدة")}</a>
                <a class="btn green" href="/ui/accounting/petty-cash/return-request/new">{L(lang, "Return Request", "طلب رد عهدة")}</a>
                <a class="btn purple" href="/ui/accounting/petty-cash/transfer">{L(lang, "Custody Transfer", "تحويل عهدة بين الموظفين")}</a>
                <a class="btn gray" href="/ui/accounting/petty-cash/list">{L(lang, "Employee Balances", "أرصدة الموظفين")}</a>
                <a class="btn gray" href="/ui/accounting/petty-cash/statement">{L(lang, "Custody Statement", "كشف حساب العهدة")}</a>
            </div>
        </div>
    </div>

    <div class="card">
        <h3>{L(lang, "Custody Requests", "طلبات صرف العهدة")}</h3>
        <div style="overflow-x:auto; width:100%; padding-bottom:6px;">
            <table style="min-width:980px;">
                <tr>
                    <th>{L(lang, "Request No", "رقم الطلب")}</th>
                    <th>{L(lang, "Date", "التاريخ")}</th>
                    <th>{L(lang, "Employee", "الموظف")}</th>
                    <th>{L(lang, "Amount", "المبلغ")}</th>
                    <th>{L(lang, "Status", "الحالة")}</th>
                    <th>{L(lang, "Action", "الإجراء")}</th>
                </tr>
                {body_custody_requests}
            </table>
        </div>
    </div>

    <div class="card">
        <h3>{L(lang, "Custody Return Requests", "طلبات رد العهدة")}</h3>
        <div style="overflow-x:auto; width:100%; padding-bottom:6px;">
            <table style="min-width:980px;">
                <tr>
                    <th>{L(lang, "Request No", "رقم الطلب")}</th>
                    <th>{L(lang, "Date", "التاريخ")}</th>
                    <th>{L(lang, "Employee", "الموظف")}</th>
                    <th>{L(lang, "Amount", "المبلغ")}</th>
                    <th>{L(lang, "Status", "الحالة")}</th>
                    <th>{L(lang, "Action", "الإجراء")}</th>
                </tr>
                {body_return_requests}
            </table>
        </div>
    </div>

    <div class="card">
        <h3>{L(lang, "Latest Employee Custodies", "أحدث صرف العهد")}</h3>
        <div style="overflow-x:auto; width:100%; padding-bottom:6px;">
            <table style="min-width:980px;">
                <tr>
                    <th>{L(lang, "No", "الرقم")}</th>
                    <th>{L(lang, "Date", "التاريخ")}</th>
                    <th>{L(lang, "Employee", "الموظف")}</th>
                    <th>{L(lang, "Cash/Bank", "الخزنة/البنك")}</th>
                    <th>{L(lang, "Amount", "المبلغ")}</th>
                    <th>{L(lang, "Status", "الحالة")}</th>
                    <th>{L(lang, "Journal", "القيد")}</th>
                    <th>{L(lang, "Action", "الإجراء")}</th>
                </tr>
                {body_custody}
            </table>
        </div>
    </div>

    <div class="card">
        <h3>{L(lang, "Latest Employee Transfers", "أحدث تحويلات العهد")}</h3>
        <div style="overflow-x:auto; width:100%; padding-bottom:6px;">
            <table style="min-width:980px;">
                <tr>
                    <th>{L(lang, "No", "الرقم")}</th>
                    <th>{L(lang, "Date", "التاريخ")}</th>
                    <th>{L(lang, "Source Employee", "من الموظف")}</th>
                    <th>{L(lang, "Target Employee", "إلى الموظف")}</th>
                    <th>{L(lang, "Amount", "المبلغ")}</th>
                    <th>{L(lang, "Status", "الحالة")}</th>
                    <th>{L(lang, "Journal", "القيد")}</th>
                    <th>{L(lang, "Action", "الإجراء")}</th>
                </tr>
                {body_transfer}
            </table>
        </div>
    </div>

    <div class="card">
        <h3>{L(lang, "Latest Employee Returns", "أحدث ردود العهد")}</h3>
        <div style="overflow-x:auto; width:100%; padding-bottom:6px;">
            <table style="min-width:980px;">
                <tr>
                    <th>{L(lang, "No", "الرقم")}</th>
                    <th>{L(lang, "Date", "التاريخ")}</th>
                    <th>{L(lang, "Employee", "الموظف")}</th>
                    <th>{L(lang, "Cash/Bank", "الخزنة/البنك")}</th>
                    <th>{L(lang, "Amount", "المبلغ")}</th>
                    <th>{L(lang, "Status", "الحالة")}</th>
                    <th>{L(lang, "Journal", "القيد")}</th>
                    <th>{L(lang, "Action", "الإجراء")}</th>
                </tr>
                {body_return}
            </table>
        </div>
    </div>
    """

    return HTMLResponse(render_page(L(lang, "Petty Cash", "العهدة النقدية"), html, lang, current_path=request.url.path))


@router.get("/ui/accounting/petty-cash/custody", response_class=HTMLResponse)
def custody_form(request: Request):
    lang = get_lang(request)
    html = f"""
    <div class="card">
        <h2>{L(lang, "Employee Custody", "صرف عهدة موظف")}</h2>

        <form method="post" action="/ui/accounting/petty-cash/custody">
            <div class="row">
                <div class="col">
                    <label>{L(lang, "Custody No", "رقم العهدة")}</label>
                    <input name="custody_no" value="{next_custody_no()}" readonly>
                </div>
                <div class="col">
                    <label>{L(lang, "Date", "التاريخ")}</label>
                    <input type="date" name="custody_date" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Employee", "الموظف")}</label>
                    <input id="employee_label" list="employee_list" autocomplete="off" placeholder="{L(lang, 'Search employee...', 'ابحث عن الموظف...')}">
                    <input type="hidden" id="employee_id" name="employee_id">
                    <datalist id="employee_list">
                        {employee_datalist_html()}
                    </datalist>
                </div>
                <div class="col">
                    <label>{L(lang, "Cash / Bank Account", "حساب الخزنة / البنك")}</label>
                    <input id="source_account_label" list="cash_bank_list" autocomplete="off" placeholder="{L(lang, 'Search cash / bank account...', 'ابحث عن حساب خزنة / بنك...')}">
                    <input type="hidden" id="source_account_code" name="source_account_code">
                    <datalist id="cash_bank_list">
                        {cash_bank_datalist_html()}
                    </datalist>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Amount", "المبلغ")}</label>
                    <input type="number" step="0.01" name="amount" required>
                </div>
                <div class="col">
                    <label>{L(lang, "Note", "البيان")}</label>
                    <input name="note">
                </div>
            </div>

            <div style="margin-top:20px;">
                <button class="btn green" type="submit">{L(lang, "Save Draft", "حفظ كمسودة")}</button>
                <a class="btn gray" href="/ui/accounting/petty-cash">{L(lang, "Back", "رجوع")}</a>
            </div>
        </form>
    </div>

    {datalist_script()}
    """
    return HTMLResponse(render_page(L(lang, "Custody", "صرف عهدة"), html, lang, current_path=request.url.path))


@router.get("/ui/accounting/petty-cash/custody-request/new", response_class=HTMLResponse)
def custody_request_form(request: Request):
    lang = get_lang(request)
    request_no = next_custody_request_no()
    request_date = ""
    employee_id = ""
    employee_label = ""
    amount = ""
    notes = ""
    form_action = "/ui/accounting/petty-cash/custody-request/new"
    page_title = L(lang, "Custody Request", "طلب صرف عهدة")
    submit_label = L(lang, "Save Request", "حفظ الطلب")
    error_html = ""
    html = f"""
    <div class="card">
        <h2>{L(lang, "Employee Custody Request", "طلب صرف عهدة موظف")}</h2>
        {error_html}
        <p style="color:#6b7280;">{L(lang, "Create request first, then disburse it from Cash Payment Voucher.", "أنشئ طلب العهدة أولًا، ثم صرفه من شاشة سند الصرف.")}</p>
        <form method="post" action="{form_action}">
            <div class="row">
                <div class="col">
                    <label>{L(lang, "Request No", "رقم الطلب")}</label>
                    <input name="request_no" value="{safe(request_no)}" readonly>
                </div>
                <div class="col">
                    <label>{L(lang, "Date", "التاريخ")}</label>
                    <input type="date" name="request_date" value="{safe(request_date)}" required>
                </div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Employee", "الموظف")}</label>
                    <input id="employee_label" list="employee_list" autocomplete="off" value="{safe(employee_label)}" placeholder="{L(lang, 'Search employee...', 'ابحث عن الموظف...')}">
                    <input type="hidden" id="employee_id" name="employee_id" value="{safe(employee_id)}">
                    <datalist id="employee_list">
                        {employee_datalist_html()}
                    </datalist>
                </div>
                <div class="col">
                    <label>{L(lang, "Amount", "المبلغ")}</label>
                    <input type="number" step="0.01" min="0" name="amount" value="{safe(amount)}" required>
                </div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Notes", "ملاحظات")}</label>
                    <input name="notes" value="{safe(notes)}">
                </div>
            </div>
            <div style="margin-top:20px;">
                <button class="btn green" type="submit">{submit_label}</button>
                <a class="btn gray" href="/ui/accounting/petty-cash">{L(lang, "Back", "رجوع")}</a>
            </div>
        </form>
    </div>
    {datalist_script()}
    """
    return HTMLResponse(render_page(page_title, html, lang, current_path=request.url.path))


def render_custody_request_edit_form(
    request: Request,
    request_id: int,
    request_no: str,
    request_date: str,
    employee_id: int,
    amount,
    notes: str,
    error_text: str = "",
):
    lang = get_lang(request)
    employee_label = ""
    conn = get_conn()
    try:
        employee_label = employee_display(conn, employee_id)
    finally:
        conn.close()
    error_html = f'<div class="msg error" style="margin-bottom:12px;">{safe(error_text)}</div>' if safe(error_text) else ""
    html = f"""
    <div class="card">
        <h2>{L(lang, "Edit Custody Request", "تعديل طلب العهدة")} {safe(request_no)}</h2>
        {error_html}
        <p style="color:#6b7280;">{L(lang, "You can edit this request only before disbursement.", "يمكن تعديل هذا الطلب فقط قبل الصرف.")}</p>
        <form method="post" action="/ui/accounting/petty-cash/custody-request/{request_id}/edit">
            <div class="row">
                <div class="col">
                    <label>{L(lang, "Request No", "رقم الطلب")}</label>
                    <input name="request_no" value="{safe(request_no)}" readonly>
                </div>
                <div class="col">
                    <label>{L(lang, "Date", "التاريخ")}</label>
                    <input type="date" name="request_date" value="{safe(request_date)}" required>
                </div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Employee", "الموظف")}</label>
                    <input id="employee_label" list="employee_list" autocomplete="off" value="{safe(employee_label)}" placeholder="{L(lang, 'Search employee...', 'ابحث عن الموظف...')}">
                    <input type="hidden" id="employee_id" name="employee_id" value="{safe(employee_id)}">
                    <datalist id="employee_list">
                        {employee_datalist_html()}
                    </datalist>
                </div>
                <div class="col">
                    <label>{L(lang, "Amount", "المبلغ")}</label>
                    <input type="number" step="0.01" min="0" name="amount" value="{money(amount)}" required>
                </div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Notes", "ملاحظات")}</label>
                    <input name="notes" value="{safe(notes)}">
                </div>
            </div>
            <div style="margin-top:20px;">
                <button class="btn green" type="submit">{L(lang, "Update Request", "تحديث الطلب")}</button>
                <a class="btn gray" href="/ui/accounting/petty-cash">{L(lang, "Back", "رجوع")}</a>
            </div>
        </form>
    </div>
    {datalist_script()}
    """
    return HTMLResponse(render_page(L(lang, "Edit Custody Request", "تعديل طلب عهدة"), html, lang, current_path=request.url.path))


@router.post("/ui/accounting/petty-cash/custody-request/new")
def custody_request_create(
    request_no: str = Form(""),
    request_date: str = Form(""),
    employee_id: str = Form(""),
    amount: float = Form(0),
    notes: str = Form(""),
):
    if amount <= 0:
        return HTMLResponse("Amount must be greater than zero.", status_code=400)
    if not employee_id:
        return HTMLResponse("Please select employee from HR list.", status_code=400)

    conn = get_conn()
    try:
        emp_id = int(employee_id)
        _, emp_name = get_employee_name(conn, emp_id)
        if not emp_name:
            raise Exception("Employee not found in HR module.")

        cur = conn.execute(
            """
            INSERT INTO employee_custody_requests (
                request_no, request_date, employee_id, amount, notes, status
            ) VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (safe(request_no) or next_custody_request_no(), safe(request_date), emp_id, float(amount or 0), safe(notes)),
        )
        request_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/petty-cash/custody-request/{request_id}", status_code=302)


@router.get("/ui/accounting/petty-cash/custody-request/{request_id}/edit", response_class=HTMLResponse)
def custody_request_edit_form(request: Request, request_id: int):
    lang = get_lang(request)
    conn = get_conn()
    row = conn.execute("SELECT * FROM employee_custody_requests WHERE id = ? LIMIT 1", (request_id,)).fetchone()
    if not row:
        conn.close()
        return HTMLResponse(L(lang, "Request not found", "الطلب غير موجود"), status_code=404)
    if not custody_request_can_modify(conn, row):
        conn.close()
        return HTMLResponse(L(lang, "Only not-disbursed requests can be edited.", "يمكن تعديل الطلبات غير المصروفة فقط."), status_code=400)
    if safe(row["status"]).lower() not in ("", "active", "pending"):
        conn.close()
        return HTMLResponse(L(lang, "Only pending requests can be edited.", "يمكن تعديل الطلبات المعلقة فقط."), status_code=400)
    conn.close()
    return render_custody_request_edit_form(
        request=request,
        request_id=request_id,
        request_no=safe(row["request_no"]),
        request_date=safe(row["request_date"]),
        employee_id=int(row["employee_id"] or 0),
        amount=row["amount"],
        notes=safe(row["notes"]),
    )


@router.post("/ui/accounting/petty-cash/custody-request/{request_id}/edit")
def custody_request_update(
    request: Request,
    request_id: int,
    request_no: str = Form(""),
    request_date: str = Form(""),
    employee_id: str = Form(""),
    amount: float = Form(0),
    notes: str = Form(""),
):
    lang = get_lang(request)
    if amount <= 0:
        return HTMLResponse(L(lang, "Amount must be greater than zero.", "المبلغ يجب أن يكون أكبر من صفر."), status_code=400)
    if not employee_id:
        return HTMLResponse(L(lang, "Please select employee from HR list.", "من فضلك اختر موظفًا من قائمة الموارد البشرية."), status_code=400)

    conn = get_conn()
    try:
        current_row = conn.execute("SELECT * FROM employee_custody_requests WHERE id = ? LIMIT 1", (request_id,)).fetchone()
        if not current_row:
            raise Exception(L(lang, "Request not found", "الطلب غير موجود"))
        if not custody_request_can_modify(conn, current_row):
            raise Exception(L(lang, "Only not-disbursed requests can be edited.", "يمكن تعديل الطلبات غير المصروفة فقط."))

        emp_id = int(employee_id)
        _, emp_name = get_employee_name(conn, emp_id)
        if not emp_name:
            raise Exception(L(lang, "Employee not found in HR module.", "الموظف غير موجود في وحدة الموارد البشرية."))

        conn.execute(
            """
            UPDATE employee_custody_requests
            SET request_date = ?, employee_id = ?, amount = ?, notes = ?
            WHERE id = ?
            """,
            (safe(request_date), emp_id, float(amount or 0), safe(notes), request_id),
        )
        conn.commit()
        conn.close()
        return RedirectResponse(f"/ui/accounting/petty-cash/custody-request/{request_id}", status_code=302)
    except Exception as e:
        conn.rollback()
        conn.close()
        return render_custody_request_edit_form(
            request=request,
            request_id=request_id,
            request_no=safe(request_no),
            request_date=safe(request_date),
            employee_id=int(employee_id or 0),
            amount=amount,
            notes=safe(notes),
            error_text=str(e),
        )


@router.post("/ui/accounting/petty-cash/custody-request/{request_id}/delete")
def custody_request_delete(request: Request, request_id: int):
    lang = get_lang(request)
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM employee_custody_requests WHERE id = ? LIMIT 1", (request_id,)).fetchone()
        if not row:
            raise Exception(L(lang, "Request not found", "الطلب غير موجود"))
        if not custody_request_can_modify(conn, row):
            raise Exception(L(lang, "Only not-disbursed requests can be deleted.", "يمكن حذف الطلبات غير المصروفة فقط."))
        conn.execute("DELETE FROM employee_custody_requests WHERE id = ?", (request_id,))
        conn.commit()
        conn.close()
        return RedirectResponse("/ui/accounting/petty-cash", status_code=302)
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)


@router.get("/ui/accounting/petty-cash/custody-request/{request_id}", response_class=HTMLResponse)
def custody_request_open(request: Request, request_id: int):
    lang = get_lang(request)
    conn = get_conn()
    row = conn.execute("SELECT * FROM employee_custody_requests WHERE id = ? LIMIT 1", (request_id,)).fetchone()
    if not row:
        conn.close()
        return HTMLResponse(L(lang, "Request not found", "الطلب غير موجود"), status_code=404)

    employee_label = employee_display(conn, row["employee_id"])
    sync_custody_request_status_if_needed(conn, row)
    conn.commit()
    status = custody_request_effective_status(conn, row)
    can_modify_request = status == "active"
    status_label = {"active": L(lang, "Pending", "معلق"), "open": L(lang, "Disbursed", "تم الصرف"), "cancelled": L(lang, "Cancelled", "ملغي")}.get(status, safe(row["status"]))
    status_cls = "blue" if status == "active" else ("green" if status == "open" else "red")
    edit_actions = ""
    if can_modify_request:
        edit_actions = f"""
        <a class="btn orange" href="/ui/accounting/petty-cash/custody-request/{row['id']}/edit">{L(lang, "Edit", "تعديل")}</a>
        <form method="post" action="/ui/accounting/petty-cash/custody-request/{row['id']}/delete" style="display:inline;" onsubmit="return confirm('{L(lang, 'Delete this request?', 'هل تريد حذف هذا الطلب؟')}');">
            <button class="btn red" type="submit">{L(lang, "Delete", "حذف")}</button>
        </form>
        """
    conn.close()

    html = f"""
    <div class="card">
        <h2>{L(lang, "Custody Request", "طلب صرف عهدة")} {safe(row['request_no'])}</h2>
        <p><b>{L(lang, "Date", "التاريخ")}:</b> {safe(row['request_date'])}</p>
        <p><b>{L(lang, "Employee", "الموظف")}:</b> {employee_label}</p>
        <p><b>{L(lang, "Amount", "المبلغ")}:</b> {money(row['amount'])}</p>
        <p><b>{L(lang, "Notes", "ملاحظات")}:</b> {safe(row['notes'])}</p>
        <p><b>{L(lang, "Status", "الحالة")}:</b> <span class="status-chip {status_cls}">{status_label}</span></p>
        <div style="margin-top:16px; display:flex; gap:8px; flex-wrap:wrap;">
            {f'<a class="btn green" href="/ui/accounting/cash-payments/new?party_type=employee&employee_id={row["employee_id"]}&employee_trans_type=custody&custody_request_id={row["id"]}&amount={row["amount"]}">{L(lang, "Disburse From Cash Payment", "صرف من سند الصرف")}</a>' if can_modify_request else ""}
            {edit_actions}
            <a class="btn gray" href="/ui/accounting/petty-cash">{L(lang, "Back", "رجوع")}</a>
        </div>
    </div>
    """
    return HTMLResponse(render_page(L(lang, "Custody Request", "طلب صرف عهدة"), html, lang, current_path=request.url.path))


def render_return_request_form(request: Request, row=None, error_text: str = ""):
    lang = get_lang(request)
    is_edit = bool(row) and safe_int(row["id"]) > 0
    request_id = int(row["id"] or 0) if row else 0
    request_no = safe(row["request_no"]) if row else next_return_request_no()
    request_date = safe(row["request_date"]) if row else ""
    employee_id = int(row["employee_id"] or 0) if row else 0
    amount = row["amount"] if row else ""
    notes = safe(row["notes"]) if row else ""
    conn = get_conn()
    try:
        employee_label = employee_display(conn, employee_id) if employee_id else ""
    finally:
        conn.close()
    action = f"/ui/accounting/petty-cash/return-request/{request_id}/edit" if is_edit else "/ui/accounting/petty-cash/return-request/new"
    title = L(lang, "Edit Custody Return Request", "تعديل طلب رد عهدة") if is_edit else L(lang, "Custody Return Request", "طلب رد عهدة")
    error_html = f'<div class="msg error" style="margin-bottom:12px;">{safe(error_text)}</div>' if safe(error_text) else ""
    html = f"""
    <div class="card">
        <h2>{title}</h2>
        {error_html}
        <form method="post" action="{action}">
            <div class="row">
                <div class="col">
                    <label>{L(lang, "Request No", "رقم الطلب")}</label>
                    <input name="request_no" value="{safe(request_no)}" readonly>
                </div>
                <div class="col">
                    <label>{L(lang, "Date", "التاريخ")}</label>
                    <input type="date" name="request_date" value="{safe(request_date)}" required>
                </div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Employee", "الموظف")}</label>
                    <input id="employee_label" list="employee_list" autocomplete="off" value="{safe(employee_label)}" placeholder="{L(lang, 'Search employee...', 'ابحث عن الموظف...')}">
                    <input type="hidden" id="employee_id" name="employee_id" value="{safe(employee_id)}">
                    <datalist id="employee_list">{employee_datalist_html()}</datalist>
                </div>
                <div class="col">
                    <label>{L(lang, "Amount", "المبلغ")}</label>
                    <input type="number" step="0.01" min="0" name="amount" value="{safe(amount)}" required>
                </div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Notes", "ملاحظات")}</label>
                    <input name="notes" value="{safe(notes)}">
                </div>
            </div>
            <div style="margin-top:20px;">
                <button class="btn green" type="submit">{L(lang, "Save", "حفظ")}</button>
                <a class="btn gray" href="/ui/accounting/petty-cash">{L(lang, "Back", "رجوع")}</a>
            </div>
        </form>
    </div>
    {datalist_script()}
    """
    return HTMLResponse(render_page(title, html, lang, current_path=request.url.path))


@router.get("/ui/accounting/petty-cash/return-request/new", response_class=HTMLResponse)
def return_request_form(request: Request):
    return render_return_request_form(request)


@router.post("/ui/accounting/petty-cash/return-request/new")
def return_request_create(
    request: Request,
    request_no: str = Form(""),
    request_date: str = Form(""),
    employee_id: str = Form(""),
    amount: float = Form(0),
    notes: str = Form(""),
):
    lang = get_lang(request)
    if amount <= 0:
        return HTMLResponse(L(lang, "Amount must be greater than zero.", "المبلغ يجب أن يكون أكبر من صفر."), status_code=400)
    if not employee_id:
        return HTMLResponse(L(lang, "Please select employee from HR list.", "من فضلك اختر الموظف من القائمة."), status_code=400)

    conn = get_conn()
    try:
        emp_id = int(employee_id)
        _, emp_name = get_employee_name(conn, emp_id)
        if not emp_name:
            raise Exception(L(lang, "Employee not found in HR module.", "الموظف غير موجود في الموارد البشرية."))
        available = get_employee_custody_balance(conn, emp_id)
        if available < q2(amount):
            raise Exception(L(lang, "Employee custody balance is not enough.", "رصيد عهدة الموظف غير كاف.") + f" {money(available)}")
        cur = conn.execute(
            """
            INSERT INTO employee_custody_return_requests (
                request_no, request_date, employee_id, amount, notes, status
            ) VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (safe(request_no) or next_return_request_no(), safe(request_date), emp_id, float(amount or 0), safe(notes)),
        )
        request_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return render_return_request_form(request, {
            "id": 0,
            "request_no": safe(request_no) or next_return_request_no(),
            "request_date": safe(request_date),
            "employee_id": safe_int(employee_id),
            "amount": amount,
            "notes": safe(notes),
        }, str(e))
    conn.close()
    return RedirectResponse(f"/ui/accounting/petty-cash/return-request/{request_id}", status_code=302)


@router.get("/ui/accounting/petty-cash/return-request/{request_id}", response_class=HTMLResponse)
def return_request_open(request: Request, request_id: int):
    lang = get_lang(request)
    conn = get_conn()
    row = conn.execute("SELECT * FROM employee_custody_return_requests WHERE id = ? LIMIT 1", (request_id,)).fetchone()
    if not row:
        conn.close()
        return HTMLResponse(L(lang, "Request not found", "الطلب غير موجود"), status_code=404)
    sync_return_request_status_if_needed(conn, row)
    conn.commit()
    status = return_request_effective_status(conn, row)
    can_modify_request = status == "active"
    employee_label = employee_display(conn, row["employee_id"])
    status_label = {"active": L(lang, "Pending", "معلق"), "received": L(lang, "Received", "تم الاستلام"), "cancelled": L(lang, "Cancelled", "ملغي")}.get(status, safe(row["status"]))
    status_cls = "blue" if status == "active" else ("green" if status == "received" else "red")
    receive_url = f"/ui/accounting/cash-receipts/new?party_type=employee&employee_id={row['employee_id']}&employee_trans_type=custody_return&source_type=custody_return_request&source_id={row['id']}&amount={row['amount']}"
    edit_actions = ""
    if can_modify_request:
        edit_actions = f"""
        <a class="btn green" href="{receive_url}">{L(lang, "Receive From Cash Receipts", "استلام من سند القبض")}</a>
        <a class="btn orange" href="/ui/accounting/petty-cash/return-request/{row['id']}/edit">{L(lang, "Edit", "تعديل")}</a>
        <form method="post" action="/ui/accounting/petty-cash/return-request/{row['id']}/delete" style="display:inline;" onsubmit="return confirm('{L(lang, 'Delete this request?', 'هل تريد حذف هذا الطلب؟')}');">
            <button class="btn red" type="submit">{L(lang, "Delete", "حذف")}</button>
        </form>
        """
    conn.close()
    html = f"""
    <div class="card">
        <h2>{L(lang, "Custody Return Request", "طلب رد عهدة")} {safe(row['request_no'])}</h2>
        <p><b>{L(lang, "Date", "التاريخ")}:</b> {safe(row['request_date'])}</p>
        <p><b>{L(lang, "Employee", "الموظف")}:</b> {employee_label}</p>
        <p><b>{L(lang, "Amount", "المبلغ")}:</b> {money(row['amount'])}</p>
        <p><b>{L(lang, "Notes", "ملاحظات")}:</b> {safe(row['notes'])}</p>
        <p><b>{L(lang, "Status", "الحالة")}:</b> <span class="status-chip {status_cls}">{status_label}</span></p>
        <div style="margin-top:16px; display:flex; gap:8px; flex-wrap:wrap;">
            {edit_actions}
            <a class="btn gray" href="/ui/accounting/petty-cash">{L(lang, "Back", "رجوع")}</a>
        </div>
    </div>
    """
    return HTMLResponse(render_page(L(lang, "Custody Return Request", "طلب رد عهدة"), html, lang, current_path=request.url.path))


@router.get("/ui/accounting/petty-cash/return-request/{request_id}/edit", response_class=HTMLResponse)
def return_request_edit_form(request: Request, request_id: int):
    lang = get_lang(request)
    conn = get_conn()
    row = conn.execute("SELECT * FROM employee_custody_return_requests WHERE id = ? LIMIT 1", (request_id,)).fetchone()
    if not row:
        conn.close()
        return HTMLResponse(L(lang, "Request not found", "الطلب غير موجود"), status_code=404)
    if not return_request_can_modify(conn, row):
        conn.close()
        return HTMLResponse(L(lang, "Only not-received requests can be edited.", "يمكن تعديل الطلبات غير المستلمة فقط."), status_code=400)
    conn.close()
    return render_return_request_form(request, row)


@router.post("/ui/accounting/petty-cash/return-request/{request_id}/edit")
def return_request_update(
    request: Request,
    request_id: int,
    request_date: str = Form(""),
    employee_id: str = Form(""),
    amount: float = Form(0),
    notes: str = Form(""),
    request_no: str = Form(""),
):
    lang = get_lang(request)
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM employee_custody_return_requests WHERE id = ? LIMIT 1", (request_id,)).fetchone()
        if not row:
            raise Exception(L(lang, "Request not found", "الطلب غير موجود"))
        if not return_request_can_modify(conn, row):
            raise Exception(L(lang, "Only not-received requests can be edited.", "يمكن تعديل الطلبات غير المستلمة فقط."))
        emp_id = int(employee_id)
        if amount <= 0:
            raise Exception(L(lang, "Amount must be greater than zero.", "المبلغ يجب أن يكون أكبر من صفر."))
        available = get_employee_custody_balance(conn, emp_id)
        if available < q2(amount):
            raise Exception(L(lang, "Employee custody balance is not enough.", "رصيد عهدة الموظف غير كاف.") + f" {money(available)}")
        conn.execute(
            """
            UPDATE employee_custody_return_requests
            SET request_date = ?, employee_id = ?, amount = ?, notes = ?, status = 'active'
            WHERE id = ?
            """,
            (safe(request_date), emp_id, float(amount or 0), safe(notes), request_id),
        )
        conn.commit()
        conn.close()
        return RedirectResponse(f"/ui/accounting/petty-cash/return-request/{request_id}", status_code=302)
    except Exception as e:
        conn.rollback()
        conn.close()
        return render_return_request_form(request, {
            "id": request_id,
            "request_no": safe(request_no),
            "request_date": safe(request_date),
            "employee_id": safe_int(employee_id),
            "amount": amount,
            "notes": safe(notes),
        }, str(e))


@router.post("/ui/accounting/petty-cash/return-request/{request_id}/delete")
def return_request_delete(request: Request, request_id: int):
    lang = get_lang(request)
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM employee_custody_return_requests WHERE id = ? LIMIT 1", (request_id,)).fetchone()
        if not row:
            raise Exception(L(lang, "Request not found", "الطلب غير موجود"))
        if not return_request_can_modify(conn, row):
            raise Exception(L(lang, "Only not-received requests can be deleted.", "يمكن حذف الطلبات غير المستلمة فقط."))
        conn.execute("DELETE FROM employee_custody_return_requests WHERE id = ?", (request_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)
    conn.close()
    return RedirectResponse("/ui/accounting/petty-cash", status_code=302)


@router.post("/ui/accounting/petty-cash/custody")
def create_custody(
    custody_no: str = Form(...),
    custody_date: str = Form(...),
    employee_id: str = Form(...),
    source_account_code: str = Form(...),
    amount: float = Form(...),
    note: str = Form("")
):
    if amount <= 0:
        return HTMLResponse("Amount must be greater than zero.", status_code=400)

    if not employee_id:
        return HTMLResponse("Please select employee from HR list.", status_code=400)

    if not source_account_code:
        return HTMLResponse("Please select cash/bank account from the list.", status_code=400)

    if not is_valid_cash_bank_account(source_account_code):
        return HTMLResponse("Selected account must be Cash / Bank only.", status_code=400)

    conn = get_conn()

    try:
        employee_id_int = int(employee_id)
        emp_code, emp_name = get_employee_name(conn, employee_id_int)
        if not emp_name:
            raise Exception("Employee not found in HR module.")

        cur = conn.execute("""
            INSERT INTO petty_cash_custody (
                custody_no, custody_date, employee_id, source_account_code, amount, note, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'draft')
        """, (
            safe(custody_no),
            safe(custody_date),
            employee_id_int,
            safe(source_account_code),
            float(amount or 0),
            safe(note),
        ))
        custody_id = cur.lastrowid

        journal_id = create_draft_journal_for_custody(conn, custody_id)
        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/petty-cash/custody/{custody_id}", status_code=302)


@router.get("/ui/accounting/petty-cash/custody/{custody_id}", response_class=HTMLResponse)
def open_custody(request: Request, custody_id: int):
    lang = get_lang(request)
    conn = get_conn()
    row = get_custody(conn, custody_id)
    if not row:
        conn.close()
        return HTMLResponse(L(lang, "Custody not found", "العهدة غير موجودة"), status_code=404)

    html = f"""
    <div class="card">
        <h2>{L(lang, "Custody", "العهدة")} {row['custody_no']}</h2>
        <p><b>{L(lang, "Date", "التاريخ")}:</b> {row['custody_date'] or ''}</p>
        <p><b>{L(lang, "Employee", "الموظف")}:</b> {employee_display(conn, row['employee_id'])}</p>
        <p><b>{L(lang, "Cash / Bank", "الخزنة / البنك")}:</b> {account_display(row['source_account_code'])}</p>
        <p><b>{L(lang, "Amount", "المبلغ")}:</b> {money(row['amount'])}</p>
        <p><b>{L(lang, "Note", "البيان")}:</b> {row['note'] or ''}</p>
        <p><b>{L(lang, "Status", "الحالة")}:</b> {row['status'] or ''}</p>
        <p><b>{L(lang, "Journal ID", "رقم القيد")}:</b> {row['journal_id'] or ''}</p>
        <p><b>{L(lang, "Reverse Journal ID", "رقم قيد العكس")}:</b> {row['reversed_journal_id'] or ''}</p>

        <div style="margin-top:20px;">
            {petty_cash_row_actions(lang, "custody", row) if movement_can_modify(conn, row) else ""}
            <a class="btn gray" href="/ui/accounting/petty-cash">{L(lang, "Back", "رجوع")}</a>
        </div>
    </div>
    """
    conn.close()
    return HTMLResponse(render_page(L(lang, "Custody", "العهدة"), html, lang, current_path=request.url.path))


@router.get("/ui/accounting/petty-cash/custody/{custody_id}/edit", response_class=HTMLResponse)
def edit_custody_form(request: Request, custody_id: int):
    lang = get_lang(request)
    conn = get_conn()
    row = get_custody(conn, custody_id)
    if not row:
        conn.close()
        return HTMLResponse(L(lang, "Custody not found", "العهدة غير موجودة"), status_code=404)
    if not movement_can_modify(conn, row):
        conn.close()
        return HTMLResponse(L(lang, "Final posted movement cannot be edited.", "لا يمكن تعديل حركة تم ترحيل قيدها نهائيًا."), status_code=400)
    html = f"""
    <div class="card">
        <h2>{L(lang, "Edit Custody", "تعديل عهدة")} {safe(row['custody_no'])}</h2>
        <form method="post" action="/ui/accounting/petty-cash/custody/{custody_id}/edit">
            <div class="row">
                <div class="col"><label>{L(lang, "Date", "التاريخ")}</label><input type="date" name="custody_date" value="{safe(row['custody_date'])}" required></div>
                <div class="col"><label>{L(lang, "Employee", "الموظف")}</label><input id="employee_label" list="employee_list" autocomplete="off" value="{employee_display(conn, row['employee_id'])}" required><input type="hidden" id="employee_id" name="employee_id" value="{safe(row['employee_id'])}"><datalist id="employee_list">{employee_datalist_html()}</datalist></div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col"><label>{L(lang, "Cash / Bank Account", "حساب الخزنة / البنك")}</label><input id="source_account_label" list="cash_bank_list" autocomplete="off" value="{account_display(row['source_account_code'])}" required><input type="hidden" id="source_account_code" name="source_account_code" value="{safe(row['source_account_code'])}"><datalist id="cash_bank_list">{cash_bank_datalist_html()}</datalist></div>
                <div class="col"><label>{L(lang, "Amount", "المبلغ")}</label><input type="number" step="0.01" name="amount" value="{safe(row['amount'])}" required></div>
            </div>
            <div class="row" style="margin-top:14px;"><div class="col"><label>{L(lang, "Note", "البيان")}</label><input name="note" value="{safe(row['note'])}"></div></div>
            <div style="margin-top:20px;"><button class="btn green" type="submit">{L(lang, "Save", "حفظ")}</button><a class="btn gray" href="/ui/accounting/petty-cash/custody/{custody_id}">{L(lang, "Back", "رجوع")}</a></div>
        </form>
    </div>
    {datalist_script()}
    """
    conn.close()
    return HTMLResponse(render_page(L(lang, "Edit Custody", "تعديل عهدة"), html, lang, current_path=request.url.path))


@router.post("/ui/accounting/petty-cash/custody/{custody_id}/edit")
def edit_custody(custody_id: int, custody_date: str = Form(...), employee_id: str = Form(...), source_account_code: str = Form(...), amount: float = Form(...), note: str = Form("")):
    conn = get_conn()
    try:
        row = get_custody(conn, custody_id)
        if not row:
            raise Exception("Custody not found.")
        if not movement_can_modify(conn, row):
            raise Exception("Final posted movement cannot be edited.")
        emp_id = int(employee_id)
        if amount <= 0:
            raise Exception("Amount must be greater than zero.")
        if not is_valid_cash_bank_account(source_account_code):
            raise Exception("Selected account must be Cash / Bank only.")
        conn.execute("""
            UPDATE petty_cash_custody
            SET custody_date = ?, employee_id = ?, source_account_code = ?, amount = ?, note = ?, status = 'draft'
            WHERE id = ?
        """, (safe(custody_date), emp_id, safe(source_account_code), float(amount or 0), safe(note), custody_id))
        rebuild_draft_journal_for_custody(conn, custody_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)
    conn.close()
    return RedirectResponse(f"/ui/accounting/petty-cash/custody/{custody_id}", status_code=302)


@router.post("/ui/accounting/petty-cash/custody/{custody_id}/delete")
def delete_custody(custody_id: int):
    conn = get_conn()
    try:
        row = get_custody(conn, custody_id)
        if not row:
            raise Exception("Custody not found.")
        if not movement_can_modify(conn, row):
            raise Exception("Final posted movement cannot be deleted.")
        if row["journal_id"]:
            delete_draft_journal_entry(conn, row["journal_id"])
        conn.execute("DELETE FROM petty_cash_custody WHERE id = ?", (custody_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)
    conn.close()
    return RedirectResponse("/ui/accounting/petty-cash", status_code=302)


@router.post("/ui/accounting/petty-cash/custody/{custody_id}/post")
def post_custody(custody_id: int):
    return HTMLResponse("Post is allowed from Journal screen only.", status_code=400)


@router.post("/ui/accounting/petty-cash/custody/{custody_id}/reverse")
def reverse_custody(custody_id: int):
    conn = get_conn()
    try:
        row = get_custody(conn, custody_id)
        if not row:
            raise Exception("Custody not found.")
        if safe(row["status"]).lower() != "posted":
            raise Exception("Only posted custody can be reversed.")
        if row["reversed_journal_id"]:
            raise Exception("Custody already reversed.")

        reverse_id = reverse_journal_entry(conn, row["journal_id"])
        conn.execute("""
            UPDATE petty_cash_custody
            SET status = 'reversed',
                reversed_journal_id = ?
            WHERE id = ?
        """, (reverse_id, custody_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reverse failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/petty-cash/custody/{custody_id}", status_code=302)


@router.get("/ui/accounting/petty-cash/return", response_class=HTMLResponse)
def return_form(request: Request):
    lang = get_lang(request)
    html = f"""
    <div class="card">
        <h2>{L(lang, "Return Custody", "رد عهدة")}</h2>

        <form method="post" action="/ui/accounting/petty-cash/return">
            <div class="row">
                <div class="col">
                    <label>{L(lang, "Return No", "رقم الرد")}</label>
                    <input name="return_no" value="{next_return_no()}" readonly>
                </div>
                <div class="col">
                    <label>{L(lang, "Date", "التاريخ")}</label>
                    <input type="date" name="return_date" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Employee", "الموظف")}</label>
                    <input id="return_employee_label" list="return_employee_list" autocomplete="off" placeholder="{L(lang, 'Search employee...', 'ابحث عن الموظف...')}">
                    <input type="hidden" id="return_employee_id" name="employee_id">
                    <datalist id="return_employee_list">
                        {employee_datalist_html()}
                    </datalist>
                </div>
                <div class="col">
                    <label>{L(lang, "Cash / Bank Account", "حساب الخزنة / البنك")}</label>
                    <input id="return_source_account_label" list="return_cash_bank_list" autocomplete="off" placeholder="{L(lang, 'Search cash / bank account...', 'ابحث عن حساب خزنة / بنك...')}">
                    <input type="hidden" id="return_source_account_code" name="source_account_code">
                    <datalist id="return_cash_bank_list">
                        {cash_bank_datalist_html()}
                    </datalist>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Amount", "المبلغ")}</label>
                    <input type="number" step="0.01" name="amount" required>
                </div>
                <div class="col">
                    <label>{L(lang, "Note", "البيان")}</label>
                    <input name="note">
                </div>
            </div>

            <div style="margin-top:20px;">
                <button class="btn green" type="submit">{L(lang, "Save Draft", "حفظ كمسودة")}</button>
                <a class="btn gray" href="/ui/accounting/petty-cash">{L(lang, "Back", "رجوع")}</a>
            </div>
        </form>
    </div>

    {datalist_script()}
    """
    return HTMLResponse(render_page(L(lang, "Return Custody", "رد عهدة"), html, lang, current_path=request.url.path))


@router.post("/ui/accounting/petty-cash/return")
def create_return(
    return_no: str = Form(...),
    return_date: str = Form(...),
    employee_id: str = Form(...),
    source_account_code: str = Form(...),
    amount: float = Form(...),
    note: str = Form("")
):
    if amount <= 0:
        return HTMLResponse("Amount must be greater than zero.", status_code=400)

    if not employee_id:
        return HTMLResponse("Please select employee from HR list.", status_code=400)

    if not source_account_code:
        return HTMLResponse("Please select cash/bank account from the list.", status_code=400)

    if not is_valid_cash_bank_account(source_account_code):
        return HTMLResponse("Selected account must be Cash / Bank only.", status_code=400)

    conn = get_conn()

    try:
        employee_id_int = int(employee_id)
        emp_code, emp_name = get_employee_name(conn, employee_id_int)
        if not emp_name:
            raise Exception("Employee not found in HR module.")

        cur = conn.execute("""
            INSERT INTO petty_cash_return (
                return_no, return_date, employee_id, source_account_code, amount, note, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'draft')
        """, (
            safe(return_no),
            safe(return_date),
            employee_id_int,
            safe(source_account_code),
            float(amount or 0),
            safe(note),
        ))
        return_id = cur.lastrowid

        journal_id = create_draft_journal_for_return(conn, return_id)
        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/petty-cash/return/{return_id}", status_code=302)


@router.get("/ui/accounting/petty-cash/return/{return_id}", response_class=HTMLResponse)
def open_return(request: Request, return_id: int):
    lang = get_lang(request)
    conn = get_conn()
    row = get_return(conn, return_id)
    if not row:
        conn.close()
        return HTMLResponse(L(lang, "Return not found", "رد العهدة غير موجود"), status_code=404)

    html = f"""
    <div class="card">
        <h2>{L(lang, "Return", "رد")} {row['return_no']}</h2>
        <p><b>{L(lang, "Date", "التاريخ")}:</b> {row['return_date'] or ''}</p>
        <p><b>{L(lang, "Employee", "الموظف")}:</b> {employee_display(conn, row['employee_id'])}</p>
        <p><b>{L(lang, "Cash / Bank", "الخزنة / البنك")}:</b> {account_display(row['source_account_code'])}</p>
        <p><b>{L(lang, "Amount", "المبلغ")}:</b> {money(row['amount'])}</p>
        <p><b>{L(lang, "Note", "البيان")}:</b> {row['note'] or ''}</p>
        <p><b>{L(lang, "Status", "الحالة")}:</b> {row['status'] or ''}</p>
        <p><b>{L(lang, "Journal ID", "رقم القيد")}:</b> {row['journal_id'] or ''}</p>
        <p><b>{L(lang, "Reverse Journal ID", "رقم قيد العكس")}:</b> {row['reversed_journal_id'] or ''}</p>

        <div style="margin-top:20px;">
            {petty_cash_row_actions(lang, "return", row) if movement_can_modify(conn, row) else ""}
            <a class="btn gray" href="/ui/accounting/petty-cash">{L(lang, "Back", "رجوع")}</a>
        </div>
    </div>
    """
    conn.close()
    return HTMLResponse(render_page(L(lang, "Return Custody", "رد عهدة"), html, lang, current_path=request.url.path))


@router.get("/ui/accounting/petty-cash/return/{return_id}/edit", response_class=HTMLResponse)
def edit_return_form(request: Request, return_id: int):
    lang = get_lang(request)
    conn = get_conn()
    row = get_return(conn, return_id)
    if not row:
        conn.close()
        return HTMLResponse(L(lang, "Return not found", "رد العهدة غير موجود"), status_code=404)
    if not movement_can_modify(conn, row):
        conn.close()
        return HTMLResponse(L(lang, "Final posted movement cannot be edited.", "لا يمكن تعديل حركة تم ترحيل قيدها نهائيًا."), status_code=400)
    html = f"""
    <div class="card">
        <h2>{L(lang, "Edit Return", "تعديل رد عهدة")} {safe(row['return_no'])}</h2>
        <form method="post" action="/ui/accounting/petty-cash/return/{return_id}/edit">
            <div class="row">
                <div class="col"><label>{L(lang, "Date", "التاريخ")}</label><input type="date" name="return_date" value="{safe(row['return_date'])}" required></div>
                <div class="col"><label>{L(lang, "Employee", "الموظف")}</label><input id="return_employee_label" list="return_employee_list" autocomplete="off" value="{employee_display(conn, row['employee_id'])}" required><input type="hidden" id="return_employee_id" name="employee_id" value="{safe(row['employee_id'])}"><datalist id="return_employee_list">{employee_datalist_html()}</datalist></div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col"><label>{L(lang, "Cash / Bank Account", "حساب الخزنة / البنك")}</label><input id="return_source_account_label" list="return_cash_bank_list" autocomplete="off" value="{account_display(row['source_account_code'])}" required><input type="hidden" id="return_source_account_code" name="source_account_code" value="{safe(row['source_account_code'])}"><datalist id="return_cash_bank_list">{cash_bank_datalist_html()}</datalist></div>
                <div class="col"><label>{L(lang, "Amount", "المبلغ")}</label><input type="number" step="0.01" name="amount" value="{safe(row['amount'])}" required></div>
            </div>
            <div class="row" style="margin-top:14px;"><div class="col"><label>{L(lang, "Note", "البيان")}</label><input name="note" value="{safe(row['note'])}"></div></div>
            <div style="margin-top:20px;"><button class="btn green" type="submit">{L(lang, "Save", "حفظ")}</button><a class="btn gray" href="/ui/accounting/petty-cash/return/{return_id}">{L(lang, "Back", "رجوع")}</a></div>
        </form>
    </div>
    {datalist_script()}
    """
    conn.close()
    return HTMLResponse(render_page(L(lang, "Edit Return", "تعديل رد عهدة"), html, lang, current_path=request.url.path))


@router.post("/ui/accounting/petty-cash/return/{return_id}/edit")
def edit_return(return_id: int, return_date: str = Form(...), employee_id: str = Form(...), source_account_code: str = Form(...), amount: float = Form(...), note: str = Form("")):
    conn = get_conn()
    try:
        row = get_return(conn, return_id)
        if not row:
            raise Exception("Return not found.")
        if not movement_can_modify(conn, row):
            raise Exception("Final posted movement cannot be edited.")
        emp_id = int(employee_id)
        if amount <= 0:
            raise Exception("Amount must be greater than zero.")
        if not is_valid_cash_bank_account(source_account_code):
            raise Exception("Selected account must be Cash / Bank only.")
        conn.execute("""
            UPDATE petty_cash_return
            SET return_date = ?, employee_id = ?, source_account_code = ?, amount = ?, note = ?, status = 'draft'
            WHERE id = ?
        """, (safe(return_date), emp_id, safe(source_account_code), float(amount or 0), safe(note), return_id))
        rebuild_draft_journal_for_return(conn, return_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)
    conn.close()
    return RedirectResponse(f"/ui/accounting/petty-cash/return/{return_id}", status_code=302)


@router.post("/ui/accounting/petty-cash/return/{return_id}/delete")
def delete_return(return_id: int):
    conn = get_conn()
    try:
        row = get_return(conn, return_id)
        if not row:
            raise Exception("Return not found.")
        if not movement_can_modify(conn, row):
            raise Exception("Final posted movement cannot be deleted.")
        if row["journal_id"]:
            delete_draft_journal_entry(conn, row["journal_id"])
        conn.execute("DELETE FROM petty_cash_return WHERE id = ?", (return_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)
    conn.close()
    return RedirectResponse("/ui/accounting/petty-cash", status_code=302)


@router.get("/ui/accounting/petty-cash/transfer", response_class=HTMLResponse)
def transfer_form(request: Request):
    lang = get_lang(request)
    html = f"""
    <div class="card">
        <h2>{L(lang, "Custody Transfer Between Employees", "تحويل عهدة بين الموظفين")}</h2>

        <form method="post" action="/ui/accounting/petty-cash/transfer">
            <div class="row">
                <div class="col">
                    <label>{L(lang, "Transfer No", "رقم التحويل")}</label>
                    <input name="transfer_no" value="{next_transfer_no()}" readonly>
                </div>
                <div class="col">
                    <label>{L(lang, "Date", "التاريخ")}</label>
                    <input type="date" name="transfer_date" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Source Employee", "من الموظف")}</label>
                    <input id="transfer_source_employee_label" list="transfer_source_employee_list" autocomplete="off" placeholder="{L(lang, 'Search source employee...', 'ابحث عن الموظف المصدر...')}">
                    <input type="hidden" id="transfer_source_employee_id" name="source_employee_id">
                    <datalist id="transfer_source_employee_list">
                        {employee_datalist_html()}
                    </datalist>
                </div>
                <div class="col">
                    <label>{L(lang, "Target Employee", "إلى الموظف")}</label>
                    <input id="transfer_target_employee_label" list="transfer_target_employee_list" autocomplete="off" placeholder="{L(lang, 'Search target employee...', 'ابحث عن الموظف المستلم...')}">
                    <input type="hidden" id="transfer_target_employee_id" name="target_employee_id">
                    <datalist id="transfer_target_employee_list">
                        {employee_datalist_html()}
                    </datalist>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{L(lang, "Amount", "المبلغ")}</label>
                    <input type="number" step="0.01" name="amount" required>
                </div>
                <div class="col">
                    <label>{L(lang, "Note", "البيان")}</label>
                    <input name="note">
                </div>
            </div>

            <div style="margin-top:20px;">
                <button class="btn green" type="submit">{L(lang, "Save Draft", "حفظ كمسودة")}</button>
                <a class="btn gray" href="/ui/accounting/petty-cash">{L(lang, "Back", "رجوع")}</a>
            </div>
        </form>
    </div>

    {datalist_script()}
    """
    return HTMLResponse(render_page(L(lang, "Custody Transfer", "تحويل عهدة"), html, lang, current_path=request.url.path))


@router.post("/ui/accounting/petty-cash/transfer")
def create_transfer(
    transfer_no: str = Form(...),
    transfer_date: str = Form(...),
    source_employee_id: str = Form(...),
    target_employee_id: str = Form(...),
    amount: float = Form(...),
    note: str = Form(""),
):
    if amount <= 0:
        return HTMLResponse("Amount must be greater than zero.", status_code=400)
    if not source_employee_id or not target_employee_id:
        return HTMLResponse("Please select source and target employees from HR list.", status_code=400)

    conn = get_conn()
    try:
        source_employee_id_int = int(source_employee_id)
        target_employee_id_int = int(target_employee_id)
        if source_employee_id_int == target_employee_id_int:
            raise Exception("Source employee and target employee must be different.")

        _, source_name = get_employee_name(conn, source_employee_id_int)
        _, target_name = get_employee_name(conn, target_employee_id_int)
        if not source_name or not target_name:
            raise Exception("Employee not found in HR module.")

        source_balance = get_employee_custody_balance(conn, source_employee_id_int)
        if source_balance < q2(amount):
            raise Exception(f"Source employee custody balance is not enough. Available: {money(source_balance)}")

        cur = conn.execute("""
            INSERT INTO petty_cash_transfer (
                transfer_no, transfer_date, source_employee_id, target_employee_id, amount, note, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'draft')
        """, (
            safe(transfer_no),
            safe(transfer_date),
            source_employee_id_int,
            target_employee_id_int,
            float(amount or 0),
            safe(note),
        ))
        transfer_id = cur.lastrowid
        create_draft_journal_for_transfer(conn, transfer_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/petty-cash/transfer/{transfer_id}", status_code=302)


@router.get("/ui/accounting/petty-cash/transfer/{transfer_id}", response_class=HTMLResponse)
def open_transfer(request: Request, transfer_id: int):
    lang = get_lang(request)
    conn = get_conn()
    row = get_transfer(conn, transfer_id)
    if not row:
        conn.close()
        return HTMLResponse(L(lang, "Custody transfer not found", "تحويل العهدة غير موجود"), status_code=404)

    html = f"""
    <div class="card">
        <h2>{L(lang, "Custody Transfer", "تحويل عهدة")} {row['transfer_no']}</h2>
        <p><b>{L(lang, "Date", "التاريخ")}:</b> {row['transfer_date'] or ''}</p>
        <p><b>{L(lang, "Source Employee", "من الموظف")}:</b> {employee_display(conn, row['source_employee_id'])}</p>
        <p><b>{L(lang, "Target Employee", "إلى الموظف")}:</b> {employee_display(conn, row['target_employee_id'])}</p>
        <p><b>{L(lang, "Amount", "المبلغ")}:</b> {money(row['amount'])}</p>
        <p><b>{L(lang, "Note", "البيان")}:</b> {row['note'] or ''}</p>
        <p><b>{L(lang, "Status", "الحالة")}:</b> {row['status'] or ''}</p>
        <p><b>{L(lang, "Journal ID", "رقم القيد")}:</b> {row['journal_id'] or ''}</p>
        <p><b>{L(lang, "Reverse Journal ID", "رقم قيد العكس")}:</b> {row['reversed_journal_id'] or ''}</p>

        <div style="margin-top:20px;">
            {petty_cash_row_actions(lang, "transfer", row) if movement_can_modify(conn, row) else ""}
            <a class="btn gray" href="/ui/accounting/petty-cash">{L(lang, "Back", "رجوع")}</a>
        </div>
    </div>
    """
    conn.close()
    return HTMLResponse(render_page(L(lang, "Custody Transfer", "تحويل عهدة"), html, lang, current_path=request.url.path))


@router.get("/ui/accounting/petty-cash/transfer/{transfer_id}/edit", response_class=HTMLResponse)
def edit_transfer_form(request: Request, transfer_id: int):
    lang = get_lang(request)
    conn = get_conn()
    row = get_transfer(conn, transfer_id)
    if not row:
        conn.close()
        return HTMLResponse(L(lang, "Custody transfer not found", "تحويل العهدة غير موجود"), status_code=404)
    if not movement_can_modify(conn, row):
        conn.close()
        return HTMLResponse(L(lang, "Final posted movement cannot be edited.", "لا يمكن تعديل حركة تم ترحيل قيدها نهائيًا."), status_code=400)
    html = f"""
    <div class="card">
        <h2>{L(lang, "Edit Custody Transfer", "تعديل تحويل عهدة")} {safe(row['transfer_no'])}</h2>
        <form method="post" action="/ui/accounting/petty-cash/transfer/{transfer_id}/edit">
            <div class="row">
                <div class="col"><label>{L(lang, "Date", "التاريخ")}</label><input type="date" name="transfer_date" value="{safe(row['transfer_date'])}" required></div>
                <div class="col"><label>{L(lang, "Amount", "المبلغ")}</label><input type="number" step="0.01" name="amount" value="{safe(row['amount'])}" required></div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col"><label>{L(lang, "Source Employee", "من الموظف")}</label><input id="transfer_source_employee_label" list="transfer_source_employee_list" autocomplete="off" value="{employee_display(conn, row['source_employee_id'])}" required><input type="hidden" id="transfer_source_employee_id" name="source_employee_id" value="{safe(row['source_employee_id'])}"><datalist id="transfer_source_employee_list">{employee_datalist_html()}</datalist></div>
                <div class="col"><label>{L(lang, "Target Employee", "إلى الموظف")}</label><input id="transfer_target_employee_label" list="transfer_target_employee_list" autocomplete="off" value="{employee_display(conn, row['target_employee_id'])}" required><input type="hidden" id="transfer_target_employee_id" name="target_employee_id" value="{safe(row['target_employee_id'])}"><datalist id="transfer_target_employee_list">{employee_datalist_html()}</datalist></div>
            </div>
            <div class="row" style="margin-top:14px;"><div class="col"><label>{L(lang, "Note", "البيان")}</label><input name="note" value="{safe(row['note'])}"></div></div>
            <div style="margin-top:20px;"><button class="btn green" type="submit">{L(lang, "Save", "حفظ")}</button><a class="btn gray" href="/ui/accounting/petty-cash/transfer/{transfer_id}">{L(lang, "Back", "رجوع")}</a></div>
        </form>
    </div>
    {datalist_script()}
    """
    conn.close()
    return HTMLResponse(render_page(L(lang, "Edit Custody Transfer", "تعديل تحويل عهدة"), html, lang, current_path=request.url.path))


@router.post("/ui/accounting/petty-cash/transfer/{transfer_id}/edit")
def edit_transfer(transfer_id: int, transfer_date: str = Form(...), source_employee_id: str = Form(...), target_employee_id: str = Form(...), amount: float = Form(...), note: str = Form("")):
    conn = get_conn()
    try:
        row = get_transfer(conn, transfer_id)
        if not row:
            raise Exception("Custody transfer not found.")
        if not movement_can_modify(conn, row):
            raise Exception("Final posted movement cannot be edited.")
        source_id = int(source_employee_id)
        target_id = int(target_employee_id)
        if source_id == target_id:
            raise Exception("Source employee and target employee must be different.")
        if amount <= 0:
            raise Exception("Amount must be greater than zero.")
        source_balance = get_employee_custody_balance(conn, source_id)
        if source_balance < q2(amount):
            raise Exception(f"Source employee custody balance is not enough. Available: {money(source_balance)}")
        conn.execute("""
            UPDATE petty_cash_transfer
            SET transfer_date = ?, source_employee_id = ?, target_employee_id = ?, amount = ?, note = ?, status = 'draft'
            WHERE id = ?
        """, (safe(transfer_date), source_id, target_id, float(amount or 0), safe(note), transfer_id))
        rebuild_draft_journal_for_transfer(conn, transfer_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)
    conn.close()
    return RedirectResponse(f"/ui/accounting/petty-cash/transfer/{transfer_id}", status_code=302)


@router.post("/ui/accounting/petty-cash/transfer/{transfer_id}/delete")
def delete_transfer(transfer_id: int):
    conn = get_conn()
    try:
        row = get_transfer(conn, transfer_id)
        if not row:
            raise Exception("Custody transfer not found.")
        if not movement_can_modify(conn, row):
            raise Exception("Final posted movement cannot be deleted.")
        if row["journal_id"]:
            delete_draft_journal_entry(conn, row["journal_id"])
        conn.execute("DELETE FROM petty_cash_transfer WHERE id = ?", (transfer_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)
    conn.close()
    return RedirectResponse("/ui/accounting/petty-cash", status_code=302)


@router.post("/ui/accounting/petty-cash/transfer/{transfer_id}/post")
def post_transfer(transfer_id: int):
    return HTMLResponse("Post is allowed from Journal screen only.", status_code=400)


@router.post("/ui/accounting/petty-cash/transfer/{transfer_id}/reverse")
def reverse_transfer(transfer_id: int):
    conn = get_conn()
    try:
        row = get_transfer(conn, transfer_id)
        if not row:
            raise Exception("Custody transfer not found.")
        if safe(row["status"]).lower() != "posted":
            raise Exception("Only posted transfer can be reversed.")
        if row["reversed_journal_id"]:
            raise Exception("Transfer already reversed.")

        reverse_id = reverse_journal_entry(conn, row["journal_id"])
        conn.execute("""
            UPDATE petty_cash_transfer
            SET status = 'reversed',
                reversed_journal_id = ?
            WHERE id = ?
        """, (reverse_id, transfer_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reverse failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/petty-cash/transfer/{transfer_id}", status_code=302)


@router.post("/ui/accounting/petty-cash/return/{return_id}/post")
def post_return(return_id: int):
    return HTMLResponse("Post is allowed from Journal screen only.", status_code=400)


@router.post("/ui/accounting/petty-cash/return/{return_id}/reverse")
def reverse_return(return_id: int):
    conn = get_conn()
    try:
        row = get_return(conn, return_id)
        if not row:
            raise Exception("Return not found.")
        if safe(row["status"]).lower() != "posted":
            raise Exception("Only posted return can be reversed.")
        if row["reversed_journal_id"]:
            raise Exception("Return already reversed.")

        reverse_id = reverse_journal_entry(conn, row["journal_id"])
        conn.execute("""
            UPDATE petty_cash_return
            SET status = 'reversed',
                reversed_journal_id = ?
            WHERE id = ?
        """, (reverse_id, return_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reverse failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/petty-cash/return/{return_id}", status_code=302)
