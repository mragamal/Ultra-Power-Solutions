from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page
from i18n import get_lang
from modules.accounting.config import get_setting_value
from modules.accounting.invoice_ai import attachment_gallery, attachments_from_form
from modules.accounting.accounting_engine import (
    create_journal_entry,
    delete_draft_journal_entry,
    post_journal_entry,
    submit_journal_for_final_post,
    reverse_journal_entry,
)

try:
    from audit import log_action, get_audit_logs
except Exception:
    log_action = None
    get_audit_logs = None


router = APIRouter()


def tr(lang: str, en: str, ar: str) -> str:
    return ar if lang == "ar" else en


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


def to_int_flag(v):
    try:
        return 1 if int(v or 0) == 1 else 0
    except Exception:
        return 0


CASH_PAYMENT_SOURCE = "cash_payment"
DIRECT_EXPENSE_SOURCES = {"petty_cash", "employee_custody"}


def normalize_payment_source(value):
    source = safe(value).lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "cash_payment": CASH_PAYMENT_SOURCE,
        "cash": "cash",
        "bank": "bank",
        "petty": "petty_cash",
        "petty_cash": "petty_cash",
        "custody": "employee_custody",
        "employee_custody": "employee_custody",
    }
    return aliases.get(source, source or CASH_PAYMENT_SOURCE)


def is_direct_expense_source(source):
    return normalize_payment_source(source) in DIRECT_EXPENSE_SOURCES


def looks_like_custody_settlement(description):
    text = safe(description).lower()
    return (
        "تسوية عهدة" in text
        or "تسويه عهدة" in text
        or "custody settlement" in text
        or "custody adjustment" in text
    )


def resolve_expense_payment_source(form_source, description, employee_id):
    payment_source = normalize_payment_source(form_source)
    if looks_like_custody_settlement(description) and employee_id:
        return "employee_custody"
    return payment_source


def expense_status_for_source(source):
    return "draft" if is_direct_expense_source(source) else "pending_payment"


def safe_log(entity_type, entity_id, action, notes="", done_by="admin"):
    if not log_action:
        return
    try:
        log_action(entity_type, entity_id, action, notes, done_by)
    except Exception:
        try:
            log_action(
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                notes=notes,
                done_by=done_by,
            )
        except Exception:
            pass


def safe_get_logs(entity_type, entity_id):
    if not get_audit_logs:
        return []
    try:
        return get_audit_logs(entity_type, entity_id)
    except Exception:
        return []


def expense_attachments(conn, expense_id):
    rows = conn.execute(
        """
        SELECT file_url, file_name
        FROM expense_attachments
        WHERE expense_id = ?
        ORDER BY id
        """,
        (expense_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def insert_expense_attachments(conn, expense_id, attachments):
    for item in attachments or []:
        conn.execute(
            """
            INSERT INTO expense_attachments (expense_id, file_url, file_name)
            VALUES (?, ?, ?)
            """,
            (expense_id, safe(item.get("file_url")), safe(item.get("file_name"))),
        )


def expense_form_line_indexes(form):
    indexes = set()
    for key in form.keys():
        match = re.match(r"^(?:acc|cc|amt|desc)_(\d+)$", safe(key))
        if match:
            indexes.add(int(match.group(1)))
    return sorted(indexes)


def expense_lines_from_form(form):
    lines = []
    for i in expense_form_line_indexes(form):
        lines.append({
            "idx": i,
            "acc": form.get(f"acc_{i}") or "",
            "cc": form.get(f"cc_{i}") or "",
            "amt": form.get(f"amt_{i}") or "",
            "desc": form.get(f"desc_{i}") or "",
        })
    return lines


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
# EMPLOYEE / SUPPORT LOOKUPS
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

    name = (row["name"] or "").strip()
    code = (row["code"] or "").strip()

    if not name:
        return None, None

    return code, name


def employee_display(employee_id):
    if not employee_id:
        return ""
    conn = get_conn()
    code, name = get_employee_name(conn, employee_id)
    conn.close()
    if not name:
        return ""
    return f"{code} - {name}" if code else name


def account_display(code):
    if not code:
        return ""
    conn = get_conn()
    row = conn.execute("""
        SELECT code, name
        FROM accounts
        WHERE code = ?
        LIMIT 1
    """, (code,)).fetchone()
    conn.close()
    if row:
        return f"{row['code'] or ''} - {row['name']}"
    return ""


def cost_center_display(cost_center_id):
    if not cost_center_id:
        return ""
    conn = get_conn()
    if not table_exists(conn, "cost_centers"):
        conn.close()
        return ""
    row = conn.execute("""
        SELECT code, name
        FROM cost_centers
        WHERE id = ?
        LIMIT 1
    """, (cost_center_id,)).fetchone()
    conn.close()
    if row:
        return f"{row['code']} - {row['name']}"
    return ""


# =========================================================
# DB INIT
# =========================================================
def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_no TEXT,
            expense_date TEXT,
            description TEXT,
            payment_source TEXT,
            payment_account_code TEXT,
            employee_id INTEGER,
            total_amount REAL DEFAULT 0,
            status TEXT DEFAULT 'draft',
            journal_id INTEGER,
            reversed_journal_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS expense_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_id INTEGER NOT NULL,
            line_no INTEGER DEFAULT 1,
            account_code TEXT,
            cost_center_id INTEGER,
            line_description TEXT,
            amount REAL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS expense_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_id INTEGER NOT NULL,
            file_url TEXT NOT NULL,
            file_name TEXT,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    ensure_column(conn, "expenses", "expense_no", "ALTER TABLE expenses ADD COLUMN expense_no TEXT")
    ensure_column(conn, "expenses", "expense_date", "ALTER TABLE expenses ADD COLUMN expense_date TEXT")
    ensure_column(conn, "expenses", "description", "ALTER TABLE expenses ADD COLUMN description TEXT")
    ensure_column(conn, "expenses", "payment_source", "ALTER TABLE expenses ADD COLUMN payment_source TEXT")
    ensure_column(conn, "expenses", "payment_account_code", "ALTER TABLE expenses ADD COLUMN payment_account_code TEXT")
    ensure_column(conn, "expenses", "employee_id", "ALTER TABLE expenses ADD COLUMN employee_id INTEGER")
    ensure_column(conn, "expenses", "total_amount", "ALTER TABLE expenses ADD COLUMN total_amount REAL DEFAULT 0")
    ensure_column(conn, "expenses", "status", "ALTER TABLE expenses ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "expenses", "journal_id", "ALTER TABLE expenses ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "expenses", "reversed_journal_id", "ALTER TABLE expenses ADD COLUMN reversed_journal_id INTEGER")
    ensure_column(conn, "expenses", "created_at", "ALTER TABLE expenses ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "expense_lines", "line_no", "ALTER TABLE expense_lines ADD COLUMN line_no INTEGER DEFAULT 1")
    ensure_column(conn, "expense_lines", "account_code", "ALTER TABLE expense_lines ADD COLUMN account_code TEXT")
    ensure_column(conn, "expense_lines", "cost_center_id", "ALTER TABLE expense_lines ADD COLUMN cost_center_id INTEGER")
    ensure_column(conn, "expense_lines", "line_description", "ALTER TABLE expense_lines ADD COLUMN line_description TEXT")
    ensure_column(conn, "expense_lines", "amount", "ALTER TABLE expense_lines ADD COLUMN amount REAL DEFAULT 0")

    conn.commit()
    conn.close()


ensure_tables()


# =========================================================
# NUMBERING / LOOKUPS
# =========================================================
def next_expense_no():
    prefix = get_setting_value("expense_prefix", "EXP")
    conn = get_conn()
    row = conn.execute("""
        SELECT expense_no
        FROM expenses
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["expense_no"]:
        return f"{prefix}-0001"

    last = str(row["expense_no"])
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"{prefix}-{num + 1:04d}"


def expense_account_options(lang="en"):
    conn = get_conn()
    rows = conn.execute("""
        SELECT code, name
        FROM accounts
        WHERE COALESCE(is_group, 0) = 0
          AND COALESCE(is_active, 1) = 1
          AND COALESCE(allow_posting, 1) = 1
        ORDER BY code, name
    """).fetchall()
    conn.close()

    out = f'<option value="">{tr(lang, "-- Select Account --", "-- اختر الحساب --")}</option>'
    for r in rows:
        out += f'<option value="{r["code"]}">{r["code"]} - {r["name"]}</option>'
    return out


def employee_options(lang="en"):
    conn = get_conn()
    name_expr = employee_name_expr(conn)
    code_expr = employee_code_expr(conn)

    if not name_expr:
        conn.close()
        return f'<option value="">{tr(lang, "-- No Employees Found --", "-- لا يوجد موظفون --")}</option>'

    try:
        rows = conn.execute(f"""
            SELECT id, {code_expr} AS code, {name_expr} AS name
            FROM employees
            ORDER BY name
        """).fetchall()
    except Exception:
        rows = []
    conn.close()

    out = f'<option value="">{tr(lang, "-- Select Employee --", "-- اختر الموظف --")}</option>'
    for r in rows:
        label = (r["name"] or "").strip()
        code = (r["code"] or "").strip()
        if code:
            label = f"{code} - {label}"
        out += f'<option value="{r["id"]}">{label}</option>'
    return out


def cost_center_options(lang="en"):
    conn = get_conn()
    if not table_exists(conn, "cost_centers"):
        conn.close()
        return f'<option value="">{tr(lang, "-- No Cost Centers --", "-- لا توجد مراكز تكلفة --")}</option>'

    cols = get_table_columns(conn, "cost_centers")

    where_conditions = []
    if "is_group" in cols:
        where_conditions.append("COALESCE(is_group, 0) = 0")
    if "is_active" in cols:
        where_conditions.append("COALESCE(is_active, 1) = 1")

    where_sql = ""
    if where_conditions:
        where_sql = "WHERE " + " AND ".join(where_conditions)

    rows = conn.execute(f"""
        SELECT id, code, name
        FROM cost_centers
        {where_sql}
        ORDER BY code, name
    """).fetchall()

    conn.close()

    out = f'<option value="">{tr(lang, "-- Select Cost Center --", "-- اختر مركز التكلفة --")}</option>'
    for r in rows:
        out += f'<option value="{r["id"]}">{r["code"]} - {r["name"]}</option>'
    return out


def get_credit_account_by_source(source):
    source = normalize_payment_source(source)
    if source == "cash":
        return get_setting_value("default_cash_account", "")
    if source == "bank":
        return get_setting_value("default_bank_account", "")
    if source == "petty_cash":
        return (
            get_setting_value("default_petty_cash_account", "")
            or get_setting_value("petty_cash_account", "")
        )
    if source == "employee_custody":
        return get_setting_value("employee_custody_account", "")
    return ""


# =========================================================
# DATA ACCESS
# =========================================================
def get_expense(conn, expense_id: int):
    return conn.execute("""
        SELECT *
        FROM expenses
        WHERE id = ?
        LIMIT 1
    """, (expense_id,)).fetchone()


def get_expense_lines(conn, expense_id: int):
    return conn.execute("""
        SELECT *
        FROM expense_lines
        WHERE expense_id = ?
        ORDER BY line_no, id
    """, (expense_id,)).fetchall()


def get_expense_payment(conn, expense_id: int):
    return conn.execute(
        """
        SELECT *
        FROM cash_vouchers
        WHERE LOWER(COALESCE(source_type, '')) = 'expense'
          AND COALESCE(source_id, 0) = ?
          AND LOWER(COALESCE(status, '')) <> 'reversed'
        ORDER BY id DESC
        LIMIT 1
        """,
        (expense_id,),
    ).fetchone()


def row_journal_status(conn, journal_id):
    if not journal_id:
        return "draft"
    row = conn.execute(
        "SELECT status FROM journal_entries WHERE id = ? LIMIT 1",
        (journal_id,),
    ).fetchone()
    return safe(row["status"]).lower() if row else "draft"


def get_expense_journal_status(expense_row):
    if not expense_row or not expense_row["journal_id"]:
        return "draft"

    conn = get_conn()
    row = conn.execute("""
        SELECT status
        FROM journal_entries
        WHERE id = ?
        LIMIT 1
    """, (expense_row["journal_id"],)).fetchone()
    conn.close()

    if not row:
        return "draft"
    return (row["status"] or "draft").lower()


def expense_can_edit_before_final(expense_row):
    return get_expense_journal_status(expense_row) in ("draft", "pending_final_post")


def expense_payment_can_modify(conn, payment_row):
    if not payment_row:
        return True
    if safe(payment_row["status"]).lower() == "reversed":
        return True
    return row_journal_status(conn, payment_row["journal_id"]) in ("", "draft", "pending_final_post")


def expense_can_manage(conn, expense_row):
    if not expense_row:
        return False
    if not expense_can_edit_before_final(expense_row):
        return False
    return expense_payment_can_modify(conn, get_expense_payment(conn, expense_row["id"]))


def delete_linked_draft_payment(conn, payment_row):
    if not payment_row:
        return
    if not expense_payment_can_modify(conn, payment_row):
        raise Exception("Linked cash payment is final posted and cannot be deleted.")
    if payment_row["journal_id"]:
        delete_draft_journal_entry(conn, payment_row["journal_id"])
    conn.execute("DELETE FROM cash_voucher_attachments WHERE voucher_id = ?", (payment_row["id"],))
    conn.execute("DELETE FROM cash_vouchers WHERE id = ?", (payment_row["id"],))


# =========================================================
# JOURNAL LOGIC
# =========================================================
def build_expense_journal_lines(conn, expense_row, expense_lines):
    payment_source = normalize_payment_source(expense_row["payment_source"])
    credit_account = get_credit_account_by_source(payment_source)
    if not credit_account:
        raise Exception("Please configure the default account for the selected payment source first.")

    journal_lines = []
    total = Decimal("0.00")

    for l in expense_lines:
        amt = q2(l["amount"])
        if amt <= Decimal("0.00"):
            continue

        line_desc = safe(l["line_description"]) or safe(expense_row["description"])
        cc_name = cost_center_display(l["cost_center_id"])
        if cc_name:
            line_desc = f"{line_desc} | CC: {cc_name}" if line_desc else f"CC: {cc_name}"

        journal_lines.append({
            "description": line_desc,
            "account_code": safe(l["account_code"]),
            "debit": amt,
            "credit": Decimal("0.00"),
            "partner_type": None,
            "partner_id": None,
        })
        total += amt

    if total <= Decimal("0.00"):
        raise Exception("Expense total must be greater than zero.")

    credit_partner_type = None
    credit_partner_id = None
    if payment_source in ("petty_cash", "employee_custody") and expense_row["employee_id"]:
        credit_partner_type = "employee"
        credit_partner_id = expense_row["employee_id"]

    journal_lines.append({
        "description": safe(expense_row["description"]) or "Expense Payment",
        "account_code": credit_account,
        "debit": Decimal("0.00"),
        "credit": total,
        "partner_type": credit_partner_type,
        "partner_id": credit_partner_id,
    })

    return journal_lines, credit_account, total


def create_draft_journal_for_expense(conn, expense_id: int):
    expense_row = get_expense(conn, expense_id)
    if not expense_row:
        raise Exception("Expense not found")

    expense_lines = get_expense_lines(conn, expense_id)
    journal_lines, credit_account, total = build_expense_journal_lines(conn, expense_row, expense_lines)

    journal_desc = f"Expense {expense_row['expense_no']}"
    if expense_row["description"]:
        journal_desc += f" - {expense_row['description']}"

    journal_id = create_journal_entry(
        conn=conn,
        entry_date=expense_row["expense_date"],
        description=journal_desc,
        reference=expense_row["expense_no"],
        source_type="expense",
        source_id=expense_id,
        lines=journal_lines,
    )

    conn.execute("""
        UPDATE expenses
        SET journal_id = ?, payment_account_code = ?, total_amount = ?, status = 'draft'
        WHERE id = ?
    """, (journal_id, credit_account, float(total), expense_id))

    return journal_id


def rebuild_draft_journal_for_expense(conn, expense_id: int):
    expense_row = get_expense(conn, expense_id)
    if not expense_row:
        raise Exception("Expense not found")

    if expense_row["journal_id"]:
        delete_draft_journal_entry(conn, expense_row["journal_id"])
        conn.execute("""
            UPDATE expenses
            SET journal_id = NULL, payment_account_code = NULL
            WHERE id = ?
        """, (expense_id,))

    return create_draft_journal_for_expense(conn, expense_id)


def normalize_existing_custody_settlement_expenses():
    conn = get_conn()
    try:
        voucher_filter = ""
        if table_exists(conn, "cash_vouchers"):
            voucher_filter = """
              AND NOT EXISTS (
                  SELECT 1
                  FROM cash_vouchers cv
                  WHERE LOWER(COALESCE(cv.source_type, '')) = 'expense'
                    AND COALESCE(cv.source_id, 0) = e.id
                    AND LOWER(COALESCE(cv.status, '')) <> 'reversed'
              )
            """

        rows = conn.execute(f"""
            SELECT e.id
            FROM expenses e
            WHERE LOWER(REPLACE(REPLACE(COALESCE(e.payment_source, ''), ' ', '_'), '-', '_')) = ?
              AND LOWER(COALESCE(e.status, '')) = 'pending_payment'
              AND e.journal_id IS NULL
              AND e.employee_id IS NOT NULL
              AND (
                  COALESCE(e.description, '') LIKE '%تسوية عهدة%'
                  OR COALESCE(e.description, '') LIKE '%تسويه عهدة%'
                  OR LOWER(COALESCE(e.description, '')) LIKE '%custody settlement%'
                  OR LOWER(COALESCE(e.description, '')) LIKE '%custody adjustment%'
              )
              {voucher_filter}
        """, (CASH_PAYMENT_SOURCE,)).fetchall()

        for row in rows:
            conn.execute(
                "UPDATE expenses SET payment_source = ? WHERE id = ?",
                ("employee_custody", row["id"]),
            )
            create_draft_journal_for_expense(conn, row["id"])
            safe_log(
                "expense",
                row["id"],
                "repair",
                "Custody settlement converted to direct expense journal.",
                "System",
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


# =========================================================
# UI HELPERS
# =========================================================
def searchable_script(lang="en"):
    return ""


def build_expense_form_html(lang, action_url, error_message="", form_data=None, initial_lines=None):
    form_data = form_data or {}
    initial_lines = initial_lines or []

    acc_options = expense_account_options(lang)
    emp_options = employee_options(lang)
    cc_options = cost_center_options(lang)

    expense_no = form_data.get("expense_no") or next_expense_no()
    expense_date = form_data.get("expense_date") or ""
    description = form_data.get("description") or ""
    employee_id = form_data.get("employee_id") or ""
    payment_source = normalize_payment_source(form_data.get("payment_source") or CASH_PAYMENT_SOURCE)
    attachments = form_data.get("attachments") or []
    existing_attachment_html = attachment_gallery(attachments) if attachments else ""
    attachment_required = "required" if not attachments else ""

    error_html = f'<div class="msg error">{error_message}</div>' if error_message else ""

    import json
    initial_lines_json = json.dumps(initial_lines)

    html = f"""
    <div class="card">
        <h2>{tr(lang, 'Edit Expense', 'تعديل المصروف') if '/edit' in action_url else tr(lang, 'New Expense', 'مصروف جديد')}</h2>
        {error_html}

        <form method="post" id="expenseForm" action="{action_url}" enctype="multipart/form-data">
            <div class="row">
                <div class="col">
                    <label>{tr(lang, 'No', 'الرقم')}</label>
                    <input name="expense_no" value="{expense_no}" readonly>
                </div>
                <div class="col">
                    <label>{tr(lang, 'Date', 'التاريخ')}</label>
                    <input type="date" name="expense_date" value="{expense_date}" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{tr(lang, 'Description', 'البيان')}</label>
                    <input name="description" value="{description}" required>
                </div>
                <input type="hidden" name="payment_source" value="{payment_source}">
            </div>

            <div class="row" id="employeeRow" style="margin-top:14px; display:none;">
                <div class="col">
                    <label>{tr(lang, 'Employee', 'الموظف')}</label>
                    <select name="employee_id" id="employee_id" data-selected="{employee_id}">
                        {emp_options}
                    </select>
                </div>
                <div class="col"></div>
            </div>

            <div class="card" style="margin-top:20px;">
                <h3>{tr(lang, 'Lines', 'السطور')}</h3>

                <table id="linesTable">
                    <thead>
                        <tr>
                            <th>{tr(lang, 'Account', 'الحساب')}</th>
                            <th>{tr(lang, 'Cost Center', 'مركز التكلفة')}</th>
                            <th>{tr(lang, 'Amount', 'المبلغ')}</th>
                            <th>{tr(lang, 'Description', 'البيان')}</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody id="linesBody"></tbody>
                </table>

                <div style="margin-top:12px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
                    <button type="button" class="btn blue" onclick="addLine()">{tr(lang, '+ Add Line', '+ إضافة سطر')}</button>
                    <div><b>{tr(lang, 'Total:', 'الإجمالي:')}</b> <span id="expenseTotal">0.00</span></div>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{tr(lang, 'Expense Attachments', 'مرفقات المصروف')}</label>
                    <input type="file" name="invoice_attachments" accept=".pdf,image/*" multiple {attachment_required}>
                </div>
            </div>
            {existing_attachment_html}

            <div style="margin-top:20px;">
                <button class="btn green" type="submit">{tr(lang, 'Save', 'حفظ')}</button>
                <a class="btn gray" href="/ui/accounting/expenses?lang={lang}">{tr(lang, 'Back', 'رجوع')}</a>
            </div>
        </form>
    </div>

    <script>
    let lineIndex = 0;

    function recalcTotal() {{
        let total = 0;
        document.querySelectorAll("input[name^='amt_']").forEach(inp => {{
            total += parseFloat(inp.value || 0);
        }});
        document.getElementById("expenseTotal").innerText = total.toFixed(2);
    }}

    function addLine(accVal="", ccVal="", amtVal="", descVal="") {{
        const tbody = document.getElementById("linesBody");
        const row = document.createElement("tr");
        const currentIndex = lineIndex;

        row.innerHTML = `
            <td>
                <select name="acc_${{currentIndex}}" id="acc_${{currentIndex}}">
                    {acc_options}
                </select>
            </td>
            <td>
                <select name="cc_${{currentIndex}}" id="cc_${{currentIndex}}">
                    {cc_options}
                </select>
            </td>
            <td>
                <input name="amt_${{currentIndex}}" type="number" step="0.01" min="0" value="${{amtVal}}" oninput="recalcTotal()">
            </td>
            <td>
                <input name="desc_${{currentIndex}}" type="text" value="${{descVal}}">
            </td>
            <td>
                <button type="button" class="btn red" onclick="this.closest('tr').remove(); recalcTotal();">X</button>
            </td>
        `;

        tbody.appendChild(row);

        const acc = document.getElementById(`acc_${{currentIndex}}`);
        const cc = document.getElementById(`cc_${{currentIndex}}`);
        if (accVal) acc.value = accVal;
        if (ccVal) cc.value = ccVal;

        setupSearchableSelect(`acc_${{currentIndex}}`);
        setupSearchableSelect(`cc_${{currentIndex}}`);

        lineIndex++;
        recalcTotal();
    }}

    window.addEventListener("DOMContentLoaded", function() {{
        const employeeId = document.getElementById("employee_id");
        const selectedEmployee = employeeId.dataset.selected || "";
        if (selectedEmployee) employeeId.value = selectedEmployee;

        setupSearchableSelect("employee_id");

        const initialLines = {initial_lines_json};
        if (initialLines.length) {{
            initialLines.forEach(l => addLine(l.acc || "", l.cc || "", l.amt || "", l.desc || ""));
        }} else {{
            addLine();
        }}
    }});
    </script>

    {searchable_script(lang)}
    """
    return html


# =========================================================
# ROUTES
# =========================================================
@router.get("/ui/accounting/expenses", response_class=HTMLResponse)
def expenses_list(request: Request):
    lang = get_lang(request)
    normalize_existing_custody_settlement_expenses()
    conn = get_conn()

    rows = conn.execute("""
        SELECT *
        FROM expenses
        ORDER BY id DESC
    """).fetchall()

    body = ""
    for r in rows:
        body += f"""
        <tr>
            <td>{r['expense_no'] or ''}</td>
            <td>{r['expense_date'] or ''}</td>
            <td>{r['description'] or ''}</td>
            <td>{r['payment_source'] or ''}</td>
            <td>{money(r['total_amount'])}</td>
            <td>{r['status'] or ''}</td>
            <td><a class="btn gray" href="/ui/accounting/expenses/{r['id']}?lang={lang}">{tr(lang, "Open", "فتح")}</a></td>
        </tr>
        """

    if not body:
        body = f"<tr><td colspan='7' style='text-align:center;'>{tr(lang, 'No expenses found.', 'لا توجد مصروفات.')}</td></tr>"

    conn.close()

    html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>{tr(lang, "Expenses", "المصروفات")}</h2>
            <a class="btn green" href="/ui/accounting/expenses/new?lang={lang}">{tr(lang, "+ New Expense", "+ مصروف جديد")}</a>
        </div>

        <table>
            <tr>
                <th>{tr(lang, "No", "الرقم")}</th>
                <th>{tr(lang, "Date", "التاريخ")}</th>
                <th>{tr(lang, "Description", "البيان")}</th>
                <th>{tr(lang, "Source", "المصدر")}</th>
                <th>{tr(lang, "Total", "الإجمالي")}</th>
                <th>{tr(lang, "Status", "الحالة")}</th>
                <th>{tr(lang, "Actions", "الإجراءات")}</th>
            </tr>
            {body}
        </table>
    </div>
    """

    return HTMLResponse(render_page(tr(lang, "Expenses", "المصروفات"), html, lang, current_path=str(request.url.path)))


@router.get("/ui/accounting/expenses/new", response_class=HTMLResponse)
def new_expense(request: Request):
    lang = get_lang(request)
    html = build_expense_form_html(lang, "/ui/accounting/expenses/new")
    return HTMLResponse(render_page(tr(lang, "New Expense", "مصروف جديد"), html, lang, current_path=str(request.url.path)))


@router.post("/ui/accounting/expenses/new")
async def save_expense(request: Request):
    lang = get_lang(request)
    form = await request.form()

    expense_no = safe(form.get("expense_no")) or next_expense_no()
    expense_date = safe(form.get("expense_date"))
    description = safe(form.get("description"))
    employee_id_raw = safe(form.get("employee_id"))
    employee_id = int(employee_id_raw) if employee_id_raw.isdigit() else None
    payment_source = resolve_expense_payment_source(form.get("payment_source"), description, employee_id)
    initial_status = expense_status_for_source(payment_source)

    form_data = {
        "expense_no": expense_no,
        "expense_date": expense_date,
        "description": description,
        "payment_source": payment_source,
        "employee_id": employee_id_raw,
    }

    initial_lines = expense_lines_from_form(form)

    new_attachments = await attachments_from_form(form)
    if not new_attachments:
        html = build_expense_form_html(
            lang,
            "/ui/accounting/expenses/new",
            tr(lang, "Attachment is required.", "لا يمكن حفظ المصروف بدون مرفق."),
            form_data,
            initial_lines
        )
        return HTMLResponse(render_page("New Expense", html, lang, current_path="/ui/accounting/expenses/new"), status_code=400)

    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO expenses (
                expense_no, expense_date, description,
                payment_source, employee_id, total_amount, status
            )
            VALUES (?, ?, ?, ?, ?, 0, ?)
        """, (
            expense_no,
            expense_date,
            description,
            payment_source,
            employee_id,
            initial_status,
        ))
        expense_id = cur.lastrowid
        insert_expense_attachments(conn, expense_id, new_attachments)

        total = Decimal("0.00")
        line_no = 1

        for line in expense_lines_from_form(form):
            acc = safe(line["acc"])
            cc_raw = safe(line["cc"])
            desc = safe(line["desc"])

            try:
                cc_id = int(cc_raw) if cc_raw else None
            except Exception:
                cc_id = None

            amt = q2(line["amt"] or "0")

            if acc and amt > Decimal("0.00"):
                if not cc_id:
                    raise Exception("Please select cost center in all lines.")

                conn.execute("""
                    INSERT INTO expense_lines (
                        expense_id, line_no, account_code, cost_center_id, line_description, amount
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    expense_id,
                    line_no,
                    acc,
                    cc_id,
                    desc,
                    float(amt),
                ))
                total += amt
                line_no += 1

        if total <= Decimal("0.00"):
            raise Exception("Please enter at least one valid expense line.")

        conn.execute("""
            UPDATE expenses
            SET total_amount = ?
            WHERE id = ?
        """, (float(total), expense_id))

        if is_direct_expense_source(payment_source):
            create_draft_journal_for_expense(conn, expense_id)

        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        html = build_expense_form_html(
            lang,
            "/ui/accounting/expenses/new",
            str(e),
            form_data,
            initial_lines
        )
        return HTMLResponse(render_page("New Expense", html, lang, current_path="/ui/accounting/expenses/new"), status_code=400)

    conn.close()

    safe_log(
        "expense",
        expense_id,
        "create",
        "Custody settlement expense created with a direct draft journal."
        if is_direct_expense_source(payment_source)
        else "Expense created and waiting for cash payment.",
        "admin"
    )

    return RedirectResponse(f"/ui/accounting/expenses/{expense_id}", status_code=302)


@router.get("/ui/accounting/expenses/{expense_id}", response_class=HTMLResponse)
def open_expense(request: Request, expense_id: int):
    lang = get_lang(request)
    normalize_existing_custody_settlement_expenses()
    conn = get_conn()

    expense = get_expense(conn, expense_id)
    if not expense:
        conn.close()
        return HTMLResponse("Expense not found", status_code=404)

    lines = get_expense_lines(conn, expense_id)
    payment = get_expense_payment(conn, expense_id)
    attachments_html = attachment_gallery(expense_attachments(conn, expense_id))
    journal_status = get_expense_journal_status(expense)
    is_manageable = expense_can_manage(conn, expense)
    conn.close()

    logs = safe_get_logs("expense", expense_id)

    lines_html = ""
    for l in lines:
        lines_html += f"""
        <tr>
            <td>{l['line_no']}</td>
            <td>{account_display(l['account_code'])}</td>
            <td>{cost_center_display(l['cost_center_id'])}</td>
            <td>{l['line_description'] or ''}</td>
            <td>{money(l['amount'])}</td>
        </tr>
        """

    if not lines_html:
        lines_html = "<tr><td colspan='5' style='text-align:center;'>No lines found.</td></tr>"

    logs_html = ""
    for log in logs:
        action = log.get("action", "") if isinstance(log, dict) else log["action"]
        notes = log.get("notes", "") if isinstance(log, dict) else (log["notes"] or "")
        done_by = log.get("done_by", "") if isinstance(log, dict) else (log["done_by"] or "")
        done_at = log.get("done_at", "") if isinstance(log, dict) else (log["done_at"] or "")

        logs_html += f"""
        <div style="padding:10px 0;border-bottom:1px solid #e5e7eb;">
            <div><b>{action}</b></div>
            <div>{notes}</div>
            <div style="font-size:12px;color:#6b7280;">{done_by} - {done_at}</div>
        </div>
        """

    if not logs_html:
        logs_html = "<div>No activity log found.</div>"

    has_payment = bool(payment)
    payment_source = normalize_payment_source(expense["payment_source"])
    edit_btn = f'<a class="btn blue" href="/ui/accounting/expenses/{expense_id}/edit">Edit</a>' if is_manageable else ""
    delete_btn = ""
    if is_manageable:
        delete_btn = f"""
        <form method="post" action="/ui/accounting/expenses/{expense_id}/delete" style="display:inline;" onsubmit="return confirm('Delete this draft expense?');">
            <button class="btn red" type="submit">Delete</button>
        </form>
        """

    post_btn = ""

    if payment_source == CASH_PAYMENT_SOURCE and not has_payment and safe(expense["status"]).lower() in ("draft", "pending_payment"):
        post_btn = f"""
        <a class="btn green" href="/ui/accounting/cash-payments/new?source_type=expense&expense_id={expense_id}&amount={expense['total_amount']}">Pay</a>
        """
    elif has_payment:
        post_btn = f"""
        <a class="btn blue" href="/ui/accounting/cash-payments/{payment['id']}">Open Cash Payment</a>
        """

    html = f"""
    <div class="card">
        <h2>Expense {expense['expense_no']}</h2>

        <p><b>Date:</b> {expense['expense_date'] or ''}</p>
        <p><b>Description:</b> {expense['description'] or ''}</p>
        <p><b>Payment Source:</b> {expense['payment_source'] or 'Cash Payment'}</p>
        <p><b>Payment Account:</b> {account_display(expense['payment_account_code'])}</p>
        <p><b>Employee:</b> {employee_display(expense['employee_id'])}</p>
        <p><b>Total:</b> {money(expense['total_amount'])}</p>
        <p><b>Status:</b> {expense['status'] or ''}</p>
        <p><b>Cash Payment:</b> {payment['voucher_no'] if payment else ''}</p>
        <p><b>Journal ID:</b> {expense['journal_id'] or ''}</p>
        <p><b>Journal Status:</b> {journal_status}</p>
        <p><b>Reverse Journal ID:</b> {expense['reversed_journal_id'] or ''}</p>

        <div style="margin-top:20px;">
            {edit_btn}
            {post_btn}
            {delete_btn}
            <a class="btn gray" href="/ui/accounting/expenses">Back</a>
        </div>
    </div>

    {attachments_html}

    <div class="card">
        <h3>Lines</h3>
        <table>
            <tr>
                <th>#</th>
                <th>Account</th>
                <th>Cost Center</th>
                <th>Description</th>
                <th>Amount</th>
            </tr>
            {lines_html}
        </table>
    </div>

    <div class="card">
        <h3>Activity Log</h3>
        {logs_html}
    </div>
    """

    return HTMLResponse(render_page("Expense", html, lang, current_path=str(request.url.path)))


@router.get("/ui/accounting/expenses/{expense_id}/edit", response_class=HTMLResponse)
def edit_expense(request: Request, expense_id: int):
    lang = get_lang(request)
    conn = get_conn()

    expense = get_expense(conn, expense_id)
    if not expense:
        conn.close()
        return HTMLResponse("Expense not found", status_code=404)

    if not expense_can_manage(conn, expense):
        conn.close()
        return RedirectResponse(f"/ui/accounting/expenses/{expense_id}", status_code=302)

    lines = get_expense_lines(conn, expense_id)
    attachments = expense_attachments(conn, expense_id)
    conn.close()

    initial_lines = []
    for l in lines:
        initial_lines.append({
            "acc": l["account_code"] or "",
            "cc": str(l["cost_center_id"] or ""),
            "amt": str(float(l["amount"] or 0)),
            "desc": l["line_description"] or "",
        })

    html = build_expense_form_html(
        lang,
        f"/ui/accounting/expenses/{expense_id}/edit",
        form_data={
            "expense_no": expense["expense_no"] or "",
            "expense_date": expense["expense_date"] or "",
            "description": expense["description"] or "",
            "payment_source": expense["payment_source"] or "",
            "employee_id": str(expense["employee_id"] or ""),
            "attachments": attachments,
        },
        initial_lines=initial_lines
    )

    return HTMLResponse(render_page(tr(lang, "Edit Expense", "تعديل المصروف"), html, lang, current_path=str(request.url.path)))


@router.post("/ui/accounting/expenses/{expense_id}/edit")
async def update_expense(request: Request, expense_id: int):
    lang = get_lang(request)
    form = await request.form()

    conn = get_conn()
    expense = get_expense(conn, expense_id)

    if not expense:
        conn.close()
        return HTMLResponse("Expense not found", status_code=404)

    existing_attachments = expense_attachments(conn, expense_id)
    if not expense_can_manage(conn, expense):
        conn.close()
        return RedirectResponse(f"/ui/accounting/expenses/{expense_id}", status_code=302)

    expense_date = safe(form.get("expense_date"))
    description = safe(form.get("description"))
    employee_id_raw = safe(form.get("employee_id"))
    employee_id = int(employee_id_raw) if employee_id_raw.isdigit() else None
    payment_source = resolve_expense_payment_source(
        form.get("payment_source") or expense["payment_source"],
        description,
        employee_id,
    )

    form_data = {
        "expense_no": expense["expense_no"] or "",
        "expense_date": expense_date,
        "description": description,
        "payment_source": payment_source,
        "employee_id": employee_id_raw or "",
        "attachments": existing_attachments,
    }

    initial_lines = expense_lines_from_form(form)

    new_attachments = await attachments_from_form(form)
    if not existing_attachments and not new_attachments:
        conn.close()
        html = build_expense_form_html(
            lang,
            f"/ui/accounting/expenses/{expense_id}/edit",
            tr(lang, "Attachment is required.", "لا يمكن حفظ المصروف بدون مرفق."),
            form_data,
            initial_lines
        )
        return HTMLResponse(render_page(tr(lang, "Edit Expense", "تعديل المصروف"), html, lang, current_path=f"/ui/accounting/expenses/{expense_id}/edit"), status_code=400)

    try:
        conn.execute("""
            UPDATE expenses
            SET expense_date = ?, description = ?, payment_source = ?, employee_id = ?
            WHERE id = ?
        """, (
            expense_date,
            description,
            payment_source,
            employee_id,
            expense_id
        ))

        conn.execute("DELETE FROM expense_lines WHERE expense_id = ?", (expense_id,))

        total = Decimal("0.00")
        line_no = 1

        for line in expense_lines_from_form(form):
            acc = safe(line["acc"])
            cc_raw = safe(line["cc"])
            desc = safe(line["desc"])
            amt = q2(line["amt"] or "0")

            try:
                cc_id = int(cc_raw) if cc_raw else None
            except Exception:
                cc_id = None

            if acc and amt > Decimal("0.00"):
                if not cc_id:
                    raise Exception("Please select cost center in all lines.")

                conn.execute("""
                    INSERT INTO expense_lines (
                        expense_id, line_no, account_code, cost_center_id, line_description, amount
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    expense_id,
                    line_no,
                    acc,
                    cc_id,
                    desc,
                    float(amt),
                ))
                total += amt
                line_no += 1

        if total <= Decimal("0.00"):
            raise Exception("Please enter at least one valid expense line.")

        conn.execute("""
            UPDATE expenses
            SET total_amount = ?
            WHERE id = ?
        """, (float(total), expense_id))
        insert_expense_attachments(conn, expense_id, new_attachments)

        payment = get_expense_payment(conn, expense_id)
        if payment:
            delete_linked_draft_payment(conn, payment)

        if is_direct_expense_source(payment_source):
            if expense["journal_id"]:
                rebuild_draft_journal_for_expense(conn, expense_id)
            else:
                create_draft_journal_for_expense(conn, expense_id)
        else:
            if expense["journal_id"]:
                delete_draft_journal_entry(conn, expense["journal_id"])
            conn.execute(
                """
                UPDATE expenses
                SET status = 'pending_payment',
                    journal_id = NULL,
                    payment_account_code = NULL
                WHERE id = ?
                """,
                (expense_id,),
            )
        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        html = build_expense_form_html(
            lang,
            f"/ui/accounting/expenses/{expense_id}/edit",
            str(e),
            form_data,
            initial_lines
        )
        return HTMLResponse(render_page(tr(lang, "Edit Expense", "تعديل المصروف"), html, lang, current_path=f"/ui/accounting/expenses/{expense_id}/edit"), status_code=400)

    conn.close()

    safe_log(
        "expense",
        expense_id,
        "edit",
        "Expense updated while pending payment.",
        "admin"
    )

    return RedirectResponse(f"/ui/accounting/expenses/{expense_id}", status_code=302)


@router.post("/ui/accounting/expenses/{expense_id}/post")
def post_expense(expense_id: int):
    return RedirectResponse(f"/ui/accounting/expenses/{expense_id}", status_code=302)


@router.post("/ui/accounting/expenses/{expense_id}/delete")
def delete_expense(request: Request, expense_id: int):
    conn = get_conn()
    try:
        expense = get_expense(conn, expense_id)
        if not expense:
            raise Exception("Expense not found")
        if not expense_can_manage(conn, expense):
            raise Exception("Only draft or non-final expenses can be deleted.")
        payment = get_expense_payment(conn, expense_id)
        delete_linked_draft_payment(conn, payment)
        if expense["journal_id"]:
            delete_draft_journal_entry(conn, expense["journal_id"])
        conn.execute("DELETE FROM expense_attachments WHERE expense_id = ?", (expense_id,))
        conn.execute("DELETE FROM expense_lines WHERE expense_id = ?", (expense_id,))
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Delete failed: {str(e)}", status_code=400)
    conn.close()
    safe_log("expense", expense_id, "delete", f"Expense {expense_id} deleted.", "admin")
    return RedirectResponse("/ui/accounting/expenses", status_code=302)


@router.post("/ui/accounting/expenses/{expense_id}/reverse")
def reverse_expense(expense_id: int):
    conn = get_conn()
    try:
        expense = get_expense(conn, expense_id)
        if not expense:
            raise Exception("Expense not found")

        if safe(expense["status"]).lower() != "posted":
            raise Exception("Only posted expenses can be reversed")

        if expense["reversed_journal_id"]:
            raise Exception("Expense already reversed")

        if not expense["journal_id"]:
            raise Exception("Expense has no posted journal")

        reverse_id = reverse_journal_entry(conn, expense["journal_id"])

        conn.execute("""
            UPDATE expenses
            SET status = 'reversed',
                reversed_journal_id = ?
            WHERE id = ?
        """, (reverse_id, expense_id))

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reverse failed: {str(e)}", status_code=400)

    conn.close()

    safe_log(
        "expense",
        expense_id,
        "reverse",
        f"Expense reversed by journal {reverse_id}",
        "admin"
    )

    return RedirectResponse(f"/ui/accounting/expenses/{expense_id}", status_code=302)
