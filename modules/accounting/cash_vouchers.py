from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from datetime import date
from pathlib import Path
from uuid import uuid4
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from auth import can
from db import get_conn
from i18n import get_lang
from layout import render_page
from modules.accounting.accounting_engine import create_journal_entry, post_journal_entry, submit_journal_for_final_post, reverse_journal_entry
from modules.accounting.invoice_ai import attachment_gallery
from modules.accounting.allocation_engine import (
    delete_payment_allocations,
    get_allocated_total_for_payment,
    get_payment_allocations,
    get_payment_unallocated_amount,
    refresh_customer_invoice_payment_status,
    refresh_vendor_bill_payment_status,
)

try:
    from modules.accounting.config import get_setting_value
except Exception:
    def get_setting_value(key, default=None):
        defaults = {
            "default_cash_account": "111100",
            "default_bank_account": "111150",
            "customer_control_account": "112100",
            "vendor_control_account": "211100",
        }
        return defaults.get(key, default)


router = APIRouter()


def accounting_allowed(request: Request, action: str) -> bool:
    return can(request, "accounting", action)


def permission_denied(en: str, ar: str):
    return HTMLResponse(ar if get_lang_fallback() == "ar" else en, status_code=403)


def get_lang_fallback():
    return "en"


AR_TRANSLATIONS = {
    "Cash Receipt Voucher": "سند قبض نقدي",
    "Cash Payment Voucher": "سند صرف نقدي",
    "Cash Receipts": "سندات القبض",
    "Cash Payments": "سندات الصرف",
    "Received From": "استلمنا من",
    "Paid To": "تم الصرف إلى",
    "Received By / Depositor": "اسم المسلم",
    "Received By": "اسم المستلم",
    "Select Employee": "اختر الموظف",
    "Customer": "عميل",
    "Vendor": "مورد",
    "Other": "أخرى",
    "Employee": "موظف",
    "Counter Account": "الحساب المقابل",
    "Voucher No": "رقم السند",
    "Date": "التاريخ",
    "Party Type": "نوع الطرف",
    "Linked Customer": "العميل المرتبط",
    "Linked Vendor": "المورد المرتبط",
    "Linked Employee": "الموظف المرتبط",
    "Statement": "كشف الحساب",
    "Transaction Type": "نوع العملية",
    "Select Transaction Type": "اختر نوع العملية",
    "Advance": "سلفة",
    "Custody": "عهدة",
    "Select Advance Request": "اختر طلب السلفة",
    "Select Advance": "اختر السلفة",
    "Select Custody Request": "اختر طلب العهدة",
    "Custody Return": "رد عهدة",
    "Custody Return Request": "طلب رد عهدة",
    "Receive custody return no.": "استلام رد عهدة رقم",
    "Payment From": "مصدر الصرف",
    "Liquidity": "سيولة",
    "Employee Custody": "عهدة موظف",
    "Custody Employee": "موظف العهدة",
    "Cash / Bank Account": "حساب الخزنة / البنك",
    "Amount": "المبلغ",
    "Description": "البيان",
    "Save Draft": "حفظ كمسودة",
    "Back": "رجوع",
    "Auto from selected request": "تلقائي من الطلب المختار",
    "Installment": "القسط",
    "Disburse advance no.": "صرف سلفة رقم",
    "Disburse custody no.": "صرف عهدة رقم",
    "to employee": "للموظف",
    "Draft": "مسودة",
    "Posted": "مرحل",
    "Reversed": "معكوس",
    "Status": "الحالة",
    "Action": "الإجراء",
    "No": "الرقم",
    "New Voucher": "سند جديد",
    "No vouchers found.": "لا توجد سندات مسجلة.",
    "Print": "طباعة",
    "Edit": "تعديل",
    "Delete": "حذف",
    "Post": "ترحيل",
    "Journal ID:": "رقم القيد:",
    "Reverse Journal ID:": "رقم قيد العكس:",
    "Status:": "الحالة:",
    "Description:": "البيان:",
    "Voucher Attachments": "مرفقات السند",
    "Attachment is required for cash payment vouchers.": "لا يمكن حفظ سند الصرف بدون مرفق.",
    "Only PDF or image attachments are allowed.": "المرفقات المسموحة PDF أو صور فقط.",
}


def looks_mojibake(text: str) -> bool:
    text = str(text or "")
    return any(marker in text for marker in ("ط", "ظ", "آ", "â", "€"))


def tr(lang: str, en: str, ar: str) -> str:
    if lang != "ar":
        return en
    return AR_TRANSLATIONS.get(en) or (ar if ar and not looks_mojibake(ar) else en)


def safe(x):
    return "" if x is None else str(x).strip()


def q2(x):
    try:
        return Decimal(str(x if x is not None else 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


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


def save_voucher_uploads(files):
    saved = []
    upload_dir = Path("uploads") / "cash_vouchers"
    allowed_suffixes = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
    for upload in files or []:
        filename = safe(getattr(upload, "filename", ""))
        if not filename:
            continue
        suffix = Path(filename).suffix.lower()
        if suffix not in allowed_suffixes:
            raise Exception("Only PDF or image attachments are allowed.")
        upload_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid4().hex}{suffix}"
        target = upload_dir / stored_name
        with target.open("wb") as handle:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        saved.append({"file_url": f"/uploads/cash_vouchers/{stored_name}", "file_name": filename})
    return saved


def load_voucher_attachments(conn, voucher_id):
    rows = conn.execute(
        """
        SELECT file_url, file_name
        FROM cash_voucher_attachments
        WHERE voucher_id = ?
        ORDER BY id
        """,
        (voucher_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def insert_voucher_attachments(conn, voucher_id, attachments):
    for item in attachments or []:
        conn.execute(
            """
            INSERT INTO cash_voucher_attachments (voucher_id, file_url, file_name)
            VALUES (?, ?, ?)
            """,
            (voucher_id, safe(item.get("file_url")), safe(item.get("file_name"))),
        )


def ensure_tables():
    conn = get_conn()
    conn.execute(
        """
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
        """
    )
    conn.execute(
        """
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
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cash_vouchers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_type TEXT NOT NULL,
            voucher_no TEXT,
            voucher_date TEXT,
            party_name TEXT,
            party_type TEXT,
            party_id INTEGER,
            liquidity_account_code TEXT,
            counter_account_code TEXT,
            amount REAL DEFAULT 0,
            description TEXT,
            signature_name TEXT,
            status TEXT DEFAULT 'draft',
            journal_id INTEGER,
            reversed_journal_id INTEGER,
            employee_trans_type TEXT,
            advance_id INTEGER,
            custody_request_id INTEGER,
            source_type TEXT,
            source_id INTEGER,
            expense_payment_source TEXT,
            expense_employee_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cash_voucher_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_id INTEGER NOT NULL,
            file_url TEXT NOT NULL,
            file_name TEXT,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    ensure_column(conn, "cash_vouchers", "voucher_type", "ALTER TABLE cash_vouchers ADD COLUMN voucher_type TEXT")
    ensure_column(conn, "cash_vouchers", "voucher_no", "ALTER TABLE cash_vouchers ADD COLUMN voucher_no TEXT")
    ensure_column(conn, "cash_vouchers", "voucher_date", "ALTER TABLE cash_vouchers ADD COLUMN voucher_date TEXT")
    ensure_column(conn, "cash_vouchers", "party_name", "ALTER TABLE cash_vouchers ADD COLUMN party_name TEXT")
    ensure_column(conn, "cash_vouchers", "party_type", "ALTER TABLE cash_vouchers ADD COLUMN party_type TEXT")
    ensure_column(conn, "cash_vouchers", "party_id", "ALTER TABLE cash_vouchers ADD COLUMN party_id INTEGER")
    ensure_column(conn, "cash_vouchers", "liquidity_account_code", "ALTER TABLE cash_vouchers ADD COLUMN liquidity_account_code TEXT")
    ensure_column(conn, "cash_vouchers", "counter_account_code", "ALTER TABLE cash_vouchers ADD COLUMN counter_account_code TEXT")
    ensure_column(conn, "cash_vouchers", "amount", "ALTER TABLE cash_vouchers ADD COLUMN amount REAL DEFAULT 0")
    ensure_column(conn, "cash_vouchers", "description", "ALTER TABLE cash_vouchers ADD COLUMN description TEXT")
    ensure_column(conn, "cash_vouchers", "signature_name", "ALTER TABLE cash_vouchers ADD COLUMN signature_name TEXT")
    ensure_column(conn, "cash_vouchers", "status", "ALTER TABLE cash_vouchers ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "cash_vouchers", "journal_id", "ALTER TABLE cash_vouchers ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "cash_vouchers", "reversed_journal_id", "ALTER TABLE cash_vouchers ADD COLUMN reversed_journal_id INTEGER")
    ensure_column(conn, "cash_vouchers", "employee_trans_type", "ALTER TABLE cash_vouchers ADD COLUMN employee_trans_type TEXT")
    ensure_column(conn, "cash_vouchers", "advance_id", "ALTER TABLE cash_vouchers ADD COLUMN advance_id INTEGER")
    ensure_column(conn, "cash_vouchers", "custody_request_id", "ALTER TABLE cash_vouchers ADD COLUMN custody_request_id INTEGER")
    ensure_column(conn, "cash_vouchers", "source_type", "ALTER TABLE cash_vouchers ADD COLUMN source_type TEXT")
    ensure_column(conn, "cash_vouchers", "source_id", "ALTER TABLE cash_vouchers ADD COLUMN source_id INTEGER")
    ensure_column(conn, "cash_vouchers", "expense_payment_source", "ALTER TABLE cash_vouchers ADD COLUMN expense_payment_source TEXT")
    ensure_column(conn, "cash_vouchers", "expense_employee_id", "ALTER TABLE cash_vouchers ADD COLUMN expense_employee_id INTEGER")
    ensure_column(conn, "cash_vouchers", "created_at", "ALTER TABLE cash_vouchers ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")
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


def route_base(voucher_type: str) -> str:
    return "/ui/accounting/cash-receipts" if voucher_type == "receipt" else "/ui/accounting/cash-payments"


def voucher_title(lang: str, voucher_type: str) -> str:
    return tr(lang, "Cash Receipt Voucher", "ط³ظ†ط¯ ظ‚ط¨ط¶ ظ†ظ‚ط¯ظٹ") if voucher_type == "receipt" else tr(lang, "Cash Payment Voucher", "ط³ظ†ط¯ طµط±ظپ ظ†ظ‚ط¯ظٹ")


def voucher_list_title(lang: str, voucher_type: str) -> str:
    return tr(lang, "Cash Receipts", "ط³ظ†ط¯ط§طھ ط§ظ„ظ‚ط¨ط¶") if voucher_type == "receipt" else tr(lang, "Cash Payments", "ط³ظ†ط¯ط§طھ ط§ظ„طµط±ظپ")


def party_label(lang: str, voucher_type: str) -> str:
    return tr(lang, "Received From", "ط§ط³طھظ„ظ…ظ†ط§ ظ…ظ†") if voucher_type == "receipt" else tr(lang, "Paid To", "طھظ… ط§ظ„طµط±ظپ ط¥ظ„ظ‰")


def signature_label(lang: str, voucher_type: str) -> str:
    return tr(lang, "Received By / Depositor", "ط§ط³ظ… ط§ظ„ظ…ط³ظ„ظ…") if voucher_type == "receipt" else tr(lang, "Received By", "ط§ط³ظ… ط§ظ„ظ…ط³طھظ„ظ…")


def next_voucher_no(voucher_type: str) -> str:
    prefix = "CRV" if voucher_type == "receipt" else "CPV"
    conn = get_conn()
    row = conn.execute(
        """
        SELECT voucher_no
        FROM cash_vouchers
        WHERE voucher_type = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (voucher_type,),
    ).fetchone()
    conn.close()
    if not row or not row["voucher_no"]:
        return f"{prefix}-0001"
    try:
        num = int(str(row["voucher_no"]).split("-")[-1])
    except Exception:
        num = 0
    return f"{prefix}-{num + 1:04d}"


def liquidity_account_options(selected_code=""):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT code, name
        FROM accounts
        WHERE COALESCE(is_active,1)=1
          AND COALESCE(is_group,0)=0
          AND COALESCE(allow_posting,1)=1
        ORDER BY code, name
        """
    ).fetchall()
    conn.close()
    default_cash = safe(get_setting_value("default_cash_account", "111100"))
    default_bank = safe(get_setting_value("default_bank_account", "111150"))
    html = '<option value="">-- Select Liquidity Account --</option>'
    for row in rows:
        code = safe(row["code"])
        name = safe(row["name"]).lower()
        if code not in (default_cash, default_bank) and "cash" not in name and "bank" not in name:
            continue
        sel = "selected" if code == safe(selected_code) else ""
        html += f'<option value="{code}" {sel}>{code} - {safe(row["name"])}</option>'
    return html


def counter_account_options(selected_code=""):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT code, name
        FROM accounts
        WHERE COALESCE(is_active,1)=1
          AND COALESCE(is_group,0)=0
          AND COALESCE(allow_posting,1)=1
        ORDER BY code, name
        """
    ).fetchall()
    conn.close()
    html = '<option value="">-- Select Account --</option>'
    for row in rows:
        code = safe(row["code"])
        sel = "selected" if code == safe(selected_code) else ""
        html += f'<option value="{code}" {sel}>{code} - {safe(row["name"])}</option>'
    return html


def account_display(code):
    if not code:
        return ""
    conn = get_conn()
    row = conn.execute("SELECT code, name FROM accounts WHERE code = ? LIMIT 1", (safe(code),)).fetchone()
    conn.close()
    if row:
        return f"{safe(row['code'])} - {safe(row['name'])}"
    return safe(code)


def get_partner_rows(party_type: str):
    party_type = safe(party_type).lower()
    if party_type == "employee":
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT id, code, name, '' AS account_code
            FROM employees
            WHERE COALESCE(is_active,1)=1
            ORDER BY name
            """
        ).fetchall()
        conn.close()
        return rows
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, code, name, account_code
        FROM partners
        WHERE LOWER(COALESCE(partner_type,'')) = ?
          AND COALESCE(is_active,1)=1
        ORDER BY name
        """,
        (safe(party_type).lower(),),
    ).fetchall()
    conn.close()
    return rows


def linked_party_options(lang: str, party_type: str, selected_id=""):
    selected_id = safe(selected_id)
    if party_type == "employee":
        conn = get_conn()
        rows = conn.execute("SELECT id, code, name FROM employees WHERE COALESCE(is_active,1)=1 ORDER BY name").fetchall()
        conn.close()
        html = f'<option value="">-- {tr(lang, "Select Employee", "ط§ط®طھط± ط§ظ„ظ…ظˆط¸ظپ")} --</option>'
        for row in rows:
            option_label = f"{safe(row['code'])} - {safe(row['name'])}" if safe(row["code"]) else safe(row["name"])
            sel = "selected" if selected_id == str(row["id"]) else ""
            html += f'<option value="{row["id"]}" {sel}>{option_label}</option>'
        return html

    label = tr(lang, "Customer", "ط¹ظ…ظٹظ„") if party_type == "customer" else tr(lang, "Vendor", "ظ…ظˆط±ط¯")
    html = f'<option value="">-- Select {label} --</option>'
    for row in get_partner_rows(party_type):
        option_label = f"{safe(row['code'])} - {safe(row['name'])}" if safe(row["code"]) else safe(row["name"])
        sel = "selected" if selected_id == str(row["id"]) else ""
        html += f'<option value="{row["id"]}" {sel}>{option_label}</option>'
    return html


def employee_options_html(selected_id=""):
    selected_id = safe(selected_id)
    conn = get_conn()
    rows = conn.execute("SELECT id, code, name FROM employees WHERE COALESCE(is_active,1)=1 ORDER BY name").fetchall()
    conn.close()
    html = '<option value="">-- Select Employee --</option>'
    for row in rows:
        label = f"{safe(row['code'])} - {safe(row['name'])}" if safe(row["code"]) else safe(row["name"])
        sel = "selected" if selected_id == str(row["id"]) else ""
        html += f'<option value="{row["id"]}" {sel}>{label}</option>'
    return html


@router.get("/ui/accounting/api/employee-advances/{employee_id}")
def get_employee_advances_api(request: Request, employee_id: int):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, advance_no, amount, installment_amount, advance_date, notes, status
        FROM employee_advances
        WHERE employee_id = ?
          AND LOWER(COALESCE(status, 'active')) = 'active'
        ORDER BY advance_date DESC
        """,
        (employee_id,),
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "no": r["advance_no"],
            "amount": r["amount"],
            "installment": r["installment_amount"],
            "date": r["advance_date"],
            "notes": r["notes"],
            "status": "pending",
        }
        for r in rows
    ]


@router.get("/ui/accounting/api/employee-custody-requests/{employee_id}")
def get_employee_custody_requests_api(request: Request, employee_id: int):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, request_no, request_date, amount, notes, status
        FROM employee_custody_requests
        WHERE employee_id = ?
          AND LOWER(COALESCE(status, 'active')) = 'active'
          AND NOT EXISTS (
              SELECT 1
              FROM cash_vouchers v
              WHERE COALESCE(v.custody_request_id, 0) = employee_custody_requests.id
                AND LOWER(COALESCE(v.voucher_type,'')) = 'payment'
                AND LOWER(COALESCE(v.employee_trans_type,'')) = 'custody'
                AND LOWER(COALESCE(v.status,'')) <> 'reversed'
          )
        ORDER BY request_date DESC, id DESC
        """,
        (employee_id,),
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "no": r["request_no"],
            "amount": r["amount"],
            "date": r["request_date"],
            "notes": r["notes"],
            "status": "pending",
        }
        for r in rows
    ]


def get_partner(conn, party_type: str, party_id):
    party_type = safe(party_type).lower()
    party_id = safe_int(party_id)
    if party_type not in ("customer", "vendor") or party_id <= 0:
        return None
    return conn.execute(
        """
        SELECT *
        FROM partners
        WHERE id = ?
          AND LOWER(COALESCE(partner_type,'')) = ?
        LIMIT 1
        """,
        (party_id, party_type),
    ).fetchone()


def default_counter_account_for_party(party_type: str, partner_row=None):
    if partner_row and safe(partner_row["account_code"]):
        return safe(partner_row["account_code"])
    if safe(party_type).lower() == "customer":
        return safe(get_setting_value("customer_control_account", "112100"))
    if safe(party_type).lower() == "vendor":
        return safe(get_setting_value("vendor_control_account", "211100"))
    return ""


def get_voucher(conn, voucher_id: int):
    return conn.execute("SELECT * FROM cash_vouchers WHERE id = ? LIMIT 1", (voucher_id,)).fetchone()


def voucher_journal_status(conn, voucher) -> str:
    if not voucher or not voucher["journal_id"]:
        return ""
    row = conn.execute("SELECT COALESCE(status,'') AS status FROM journal_entries WHERE id = ? LIMIT 1", (voucher["journal_id"],)).fetchone()
    return safe(row["status"]).lower() if row else ""


def voucher_can_modify(conn, voucher) -> bool:
    if not voucher:
        return False
    if safe(voucher["status"]).lower() == "reversed":
        return False
    return voucher_journal_status(conn, voucher) in ("", "draft", "pending_final_post")


def voucher_display_status(conn, voucher) -> str:
    status = safe(voucher["status"]).lower()
    if status == "reversed":
        return "reversed"
    journal_status = voucher_journal_status(conn, voucher)
    if journal_status == "posted":
        return "posted"
    if journal_status == "pending_final_post":
        return "pending_final_post"
    return "draft"


def voucher_payment_type(voucher):
    if not voucher:
        return ""
    voucher_type = safe(voucher["voucher_type"]).lower()
    party_type = safe(voucher["party_type"]).lower()
    if voucher_type == "receipt" and party_type == "customer" and safe_int(voucher["party_id"]) > 0:
        return "cash_receipt"
    if voucher_type == "payment" and party_type == "vendor" and safe_int(voucher["party_id"]) > 0:
        return "cash_payment"
    return ""


def allocation_rows_for_voucher(conn, voucher):
    payment_type = voucher_payment_type(voucher)
    if not payment_type:
        return []
    rows = get_payment_allocations(conn, payment_type, voucher["id"])
    result = []
    for row in rows:
        item = dict(row)
        if safe(row["document_type"]) == "customer_invoice":
            doc = conn.execute(
                "SELECT invoice_no AS doc_no, invoice_date AS doc_date, net_amount FROM customer_invoices WHERE id = ? LIMIT 1",
                (row["document_id"],),
            ).fetchone()
            item["open_url"] = f"/ui/accounting/customer-invoices/{row['document_id']}/view"
        elif safe(row["document_type"]) == "vendor_bill":
            doc = conn.execute(
                "SELECT bill_no AS doc_no, bill_date AS doc_date, net_amount FROM vendor_bills WHERE id = ? LIMIT 1",
                (row["document_id"],),
            ).fetchone()
            item["open_url"] = f"/ui/accounting/vendor-bills/{row['document_id']}/view"
        else:
            doc = None
            item["open_url"] = "#"
        item["doc_no"] = safe(doc["doc_no"]) if doc else ""
        item["doc_date"] = safe(doc["doc_date"]) if doc else ""
        item["doc_total"] = doc["net_amount"] if doc else 0
        result.append(item)
    return result


def validate_voucher(voucher_date, party_name, party_type, party_id, liquidity_account_code, counter_account_code, amount, source_type=""):
    if not safe(voucher_date):
        raise Exception("Voucher date is required")
    if not safe(party_name):
        raise Exception("Party name is required")
    if safe(party_type).lower() in ("customer", "vendor") and safe_int(party_id) <= 0:
        raise Exception("Linked customer/vendor is required")
    if not safe(liquidity_account_code):
        raise Exception("Liquidity account is required")
    if safe(source_type).lower() not in ("expense", "payroll_salary_payment", "employee_grant", "custody_return_request") and not safe(counter_account_code):
        raise Exception("Counter account is required")
    if q2(amount) <= Decimal("0.00"):
        raise Exception("Amount must be greater than zero")


def employee_custody_account_code():
    return safe(get_setting_value("employee_custody_account", "1020504")) or "1020504"


def employee_custody_balance(conn, employee_id: int):
    account_code = employee_custody_account_code()
    row = conn.execute(
        """
        SELECT COALESCE(SUM(jl.debit - jl.credit), 0) AS balance
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE LOWER(COALESCE(je.status,'')) = 'posted'
          AND COALESCE(jl.partner_type,'') = 'employee'
          AND jl.partner_id = ?
          AND COALESCE(jl.account_code,'') = ?
        """,
        (employee_id, account_code),
    ).fetchone()
    return q2(row["balance"] if row else 0)


def build_lines(voucher, conn=None):
    amount = q2(voucher["amount"])
    description = safe(voucher["description"]) or f"{safe(voucher['voucher_no'])} - {safe(voucher['party_name'])}"
    if safe(voucher["source_type"]).lower() == "expense" and safe_int(voucher["source_id"]) > 0:
        local_conn = conn or get_conn()
        try:
            expense = local_conn.execute("SELECT * FROM expenses WHERE id = ? LIMIT 1", (safe_int(voucher["source_id"]),)).fetchone()
            expense_lines = local_conn.execute(
                "SELECT * FROM expense_lines WHERE expense_id = ? ORDER BY line_no, id",
                (safe_int(voucher["source_id"]),),
            ).fetchall()
        finally:
            if conn is None:
                local_conn.close()
        if not expense:
            raise Exception("Linked expense not found")
        lines = []
        total = Decimal("0.00")
        for row in expense_lines:
            line_amount = q2(row["amount"])
            if line_amount <= Decimal("0.00"):
                continue
            line_desc = safe(row["line_description"]) or safe(expense["description"]) or description
            lines.append({
                "description": line_desc,
                "account_code": safe(row["account_code"]),
                "debit": line_amount,
                "credit": Decimal("0.00"),
                "partner_type": None,
                "partner_id": None,
            })
            total += line_amount
        if total <= Decimal("0.00"):
            raise Exception("Linked expense has no valid lines")
        credit_partner_type = None
        credit_partner_id = None
        if safe(voucher["expense_payment_source"]).lower() == "custody" and safe_int(voucher["expense_employee_id"]) > 0:
            credit_partner_type = "employee"
            credit_partner_id = safe_int(voucher["expense_employee_id"])
        if total != amount:
            amount = total
        lines.append({
            "description": description,
            "account_code": safe(voucher["liquidity_account_code"]),
            "debit": Decimal("0.00"),
            "credit": total,
            "partner_type": credit_partner_type,
            "partner_id": credit_partner_id,
        })
        return lines

    if safe(voucher["source_type"]).lower() == "payroll_salary_payment" and safe_int(voucher["source_id"]) > 0:
        payroll_payable = safe(voucher["counter_account_code"]) or safe(get_setting_value("payroll_payable_account", "201020108"))
        credit_partner_type = None
        credit_partner_id = None
        if safe(voucher["expense_payment_source"]).lower() == "custody" and safe_int(voucher["expense_employee_id"]) > 0:
            credit_partner_type = "employee"
            credit_partner_id = safe_int(voucher["expense_employee_id"])
        return [
            {
                "description": description,
                "account_code": payroll_payable,
                "debit": amount,
                "credit": Decimal("0.00"),
                "partner_type": None,
                "partner_id": None,
            },
            {
                "description": description,
                "account_code": safe(voucher["liquidity_account_code"]),
                "debit": Decimal("0.00"),
                "credit": amount,
                "partner_type": credit_partner_type,
                "partner_id": credit_partner_id,
            },
        ]

    if safe(voucher["source_type"]).lower() == "employee_grant" and safe_int(voucher["source_id"]) > 0:
        local_conn = conn or get_conn()
        try:
            grant = local_conn.execute("SELECT * FROM employee_grants WHERE id = ? LIMIT 1", (safe_int(voucher["source_id"]),)).fetchone()
            grant_lines = local_conn.execute(
                "SELECT * FROM employee_grant_lines WHERE grant_id = ? ORDER BY employee_code, employee_name, id",
                (safe_int(voucher["source_id"]),),
            ).fetchall()
        finally:
            if conn is None:
                local_conn.close()
        if not grant:
            raise Exception("Linked employee grant not found")
        grant_account = safe(voucher["counter_account_code"]) or safe(get_setting_value("employee_grant_expense_account", get_setting_value("payroll_bonus_account", "60105")))
        lines = []
        total = Decimal("0.00")
        for row in grant_lines:
            line_amount = q2(row["grant_amount"])
            if line_amount <= Decimal("0.00"):
                continue
            employee_name = safe(row["employee_name"])
            line_desc = f"{description} - {employee_name}" if employee_name else description
            lines.append({
                "description": line_desc,
                "account_code": grant_account,
                "debit": line_amount,
                "credit": Decimal("0.00"),
                "partner_type": "employee",
                "partner_id": safe_int(row["employee_id"]) or None,
            })
            total += line_amount
        if total <= Decimal("0.00"):
            raise Exception("Linked employee grant has no valid lines")
        lines.append({
            "description": description,
            "account_code": safe(voucher["liquidity_account_code"]),
            "debit": Decimal("0.00"),
            "credit": total,
            "partner_type": None,
            "partner_id": None,
        })
        return lines

    party_type = safe(voucher["party_type"]).lower()
    partner_type = party_type if party_type in ("customer", "vendor", "employee") else None
    partner_id = safe_int(voucher["party_id"]) or None
    if safe(voucher["voucher_type"]) == "receipt":
        return [
            {"description": description, "account_code": safe(voucher["liquidity_account_code"]), "debit": amount, "credit": Decimal("0.00"), "partner_type": None, "partner_id": None},
            {"description": description, "account_code": safe(voucher["counter_account_code"]), "debit": Decimal("0.00"), "credit": amount, "partner_type": partner_type, "partner_id": partner_id},
        ]
    credit_partner_type = None
    credit_partner_id = None
    if safe(voucher["expense_payment_source"]).lower() == "custody" and safe_int(voucher["expense_employee_id"]) > 0:
        credit_partner_type = "employee"
        credit_partner_id = safe_int(voucher["expense_employee_id"])
    return [
        {"description": description, "account_code": safe(voucher["counter_account_code"]), "debit": amount, "credit": Decimal("0.00"), "partner_type": partner_type, "partner_id": partner_id},
        {"description": description, "account_code": safe(voucher["liquidity_account_code"]), "debit": Decimal("0.00"), "credit": amount, "partner_type": credit_partner_type, "partner_id": credit_partner_id},
    ]


def create_draft_journal(conn, voucher_id: int):
    voucher = get_voucher(conn, voucher_id)
    if not voucher:
        raise Exception("Voucher not found")
    journal_id = create_journal_entry(
        conn=conn,
        entry_date=safe(voucher["voucher_date"]),
        description=safe(voucher["description"]) or f"{safe(voucher['voucher_no'])} - {safe(voucher['party_name'])}",
        reference=safe(voucher["voucher_no"]),
        source_type="cash_voucher",
        source_id=voucher_id,
        lines=build_lines(voucher, conn),
    )
    conn.execute("UPDATE cash_vouchers SET journal_id = ?, status = 'draft' WHERE id = ?", (journal_id, voucher_id))
    return journal_id


def amount_in_words(amount) -> str:
    return f"{money(amount)} EGP"


def party_type_select_html(lang: str, selected="other"):
    selected = safe(selected).lower() or "other"
    options = [
        ("other", "Other", "ط£ط®ط±ظ‰"),
        ("customer", "Customer", "ط¹ظ…ظٹظ„"),
        ("vendor", "Vendor", "ظ…ظˆط±ط¯"),
        ("employee", "Employee", "ظ…ظˆط¸ظپ"),
    ]
    html = ""
    for value, en, ar in options:
        sel = "selected" if selected == value else ""
        html += f'<option value="{value}" {sel}>{tr(lang, en, ar)}</option>'
    return html


def render_form(lang: str, voucher_type: str, action_url: str, values=None, error=""):
    values = values or {}
    error_html = f'<div class="msg error">{error}</div>' if error else ""
    party_type = safe(values.get("party_type")).lower() or "other"
    manual_party_display = "block" if party_type not in ("customer", "vendor", "employee") else "none"
    customer_display = "block" if party_type == "customer" else "none"
    vendor_display = "block" if party_type == "vendor" else "none"
    employee_display = "block" if party_type == "employee" else "none"
    
    trans_type = safe(values.get("employee_trans_type")).lower()
    advance_select_display = "block" if party_type == "employee" and trans_type == "advance" else "none"
    custody_select_display = "block" if party_type == "employee" and trans_type == "custody" else "none"
    employee_counter_account = (
        employee_custody_account_code()
        if trans_type == "custody"
        else safe(get_setting_value("employee_advance_account", "121200"))
    )
    counter_select_display = "block" if party_type == "other" else "none"
    employee_counter_display = "none"
    source_type = safe(values.get("source_type")).lower()
    source_id = safe(values.get("source_id"))
    is_custody_return_receipt = voucher_type == "receipt" and source_type == "custody_return_request" and bool(source_id)
    if not safe(values.get("voucher_date")):
        values["voucher_date"] = date.today().isoformat()
    is_expense_payment = source_type == "expense" and bool(source_id)
    show_payment_from = voucher_type == "payment" and source_type not in ("payroll_salary_payment", "employee_grant") and not (party_type == "employee" and trans_type in ("advance", "custody"))
    expense_payment_source = safe(values.get("expense_payment_source") or "liquidity").lower()
    if expense_payment_source in ("cash", "bank"):
        expense_payment_source = "liquidity"
    if expense_payment_source not in ("liquidity", "custody"):
        expense_payment_source = "liquidity"
    expense_employee_id = safe(values.get("expense_employee_id"))
    expense_liquidity_selected = "selected" if expense_payment_source == "liquidity" else ""
    expense_custody_selected = "selected" if expense_payment_source == "custody" else ""
    expense_account_display = "none" if show_payment_from and expense_payment_source == "custody" else "block"
    expense_employee_display = "block" if show_payment_from and expense_payment_source == "custody" else "none"
    source_notice = ""
    if is_expense_payment:
        party_type = "other"
        manual_party_display = "block"
        customer_display = "none"
        vendor_display = "none"
        employee_display = "none"
        counter_select_display = "none"
        employee_counter_display = "none"
    if is_custody_return_receipt:
        party_type = "employee"
        manual_party_display = "none"
        customer_display = "none"
        vendor_display = "none"
        employee_display = "block"
        advance_select_display = "none"
        custody_select_display = "none"
        counter_select_display = "none"
        employee_counter_display = "none"
        employee_counter_account = employee_custody_account_code()
    counter_account_fields = "" if is_expense_payment else f"""
                <div class="col" id="counter_account_wrap" style="display:{'none' if is_expense_payment else counter_select_display};">
                    <label>{tr(lang, 'Counter Account', 'ط·آ§ط¸â€‍ط·آ­ط·آ³ط·آ§ط·آ¨ ط·آ§ط¸â€‍ط¸â€¦ط¸â€ڑط·آ§ط·آ¨ط¸â€‍')}</label>
                    <select id="counter_account_code_select" name="counter_account_code" required>
                        {counter_account_options(values.get('counter_account_code'))}
                    </select>
                </div>
                <div class="col" id="employee_counter_wrap" style="display:{'none' if is_expense_payment else employee_counter_display};">
                    <label>{tr(lang, 'Counter Account', 'ط·آ§ط¸â€‍ط·آ­ط·آ³ط·آ§ط·آ¨ ط·آ§ط¸â€‍ط¸â€¦ط¸â€ڑط·آ§ط·آ¨ط¸â€‍')}</label>
                    <input id="employee_counter_account_value" value="{employee_counter_account}" readonly>
                </div>
    """
    attachments = values.get("attachments") or []
    existing_attachment_html = attachment_gallery(attachments) if attachments else ""
    attachment_required = "required" if voucher_type == "payment" and not attachments else ""
    attachment_html = ""
    if voucher_type == "payment":
        attachment_html = f"""
            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{tr(lang, 'Voucher Attachments', 'مرفقات السند')}</label>
                    <input type="file" name="voucher_attachments" accept=".pdf,image/*" multiple {attachment_required}>
                </div>
            </div>
            {existing_attachment_html}
        """

    return f"""
    <div class="card">
        {error_html}
        <h2>{voucher_title(lang, voucher_type)}</h2>
        {source_notice}
        <form method="post" action="{action_url}" enctype="multipart/form-data">
            <input type="hidden" name="source_type" value="{source_type}">
            <input type="hidden" name="source_id" value="{source_id}">
            {'<input type="hidden" name="party_type" value="employee"><input type="hidden" name="employee_trans_type" value="custody_return"><input type="hidden" name="counter_account_code" value="' + employee_custody_account_code() + '">' if is_custody_return_receipt else ''}
            {'<input type="hidden" name="party_type" value="other"><input type="hidden" name="counter_account_code" value="EXPENSE-LINES">' if is_expense_payment else ''}
            <div class="row">
                <div class="col">
                    <label>{tr(lang, 'Voucher No', 'ط±ظ‚ظ… ط§ظ„ط³ظ†ط¯')}</label>
                    <input name="voucher_no" value="{safe(values.get('voucher_no') or next_voucher_no(voucher_type))}" readonly>
                </div>
                <div class="col">
                    <label>{tr(lang, 'Date', 'ط§ظ„طھط§ط±ظٹط®')}</label>
                    <input type="date" name="voucher_date" value="{safe(values.get('voucher_date'))}" required>
                </div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col" id="manual_party_wrap" style="display:{manual_party_display};">
                    <label>{party_label(lang, voucher_type)}</label>
                    <input name="party_name" value="{safe(values.get('party_name'))}">
                </div>
                <div class="col" style="display:{"none" if (is_expense_payment or is_custody_return_receipt) else "block"};">
                    <label>{tr(lang, 'Party Type', 'ظ†ظˆط¹ ط§ظ„ط·ط±ظپ')}</label>
                    <select name="party_type" id="party_type" onchange="toggleLinkedPartyFields()" {"disabled" if is_expense_payment else ""}>
                        {party_type_select_html(lang, party_type)}
                    </select>
                </div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col" id="customer_party_wrap" style="display:{customer_display};">
                    <label>{tr(lang, 'Linked Customer', 'ط§ظ„ط¹ظ…ظٹظ„ ط§ظ„ظ…ط±طھط¨ط·')}</label>
                    <select name="customer_id" id="customer_id" onchange="syncLinkedPartyName()">
                        {linked_party_options(lang, 'customer', values.get('party_id') if party_type == 'customer' else '')}
                    </select>
                </div>
                <div class="col" id="vendor_party_wrap" style="display:{vendor_display};">
                    <label>{tr(lang, 'Linked Vendor', 'ط§ظ„ظ…ظˆط±ط¯ ط§ظ„ظ…ط±طھط¨ط·')}</label>
                    <select name="vendor_id" id="vendor_id" onchange="syncLinkedPartyName()">
                        {linked_party_options(lang, 'vendor', values.get('party_id') if party_type == 'vendor' else '')}
                    </select>
                </div>
                <div class="col" id="employee_party_wrap" style="display:{employee_display};">
                    <label>{tr(lang, 'Linked Employee', 'ط§ظ„ظ…ظˆط¸ظپ ط§ظ„ظ…ط±طھط¨ط·')}</label>
                    <div style="display:flex; gap:8px;">
                        <select name="employee_id" id="employee_id" style="flex:1;" onchange="syncLinkedPartyName(); updateEmployeeAdvances();">
                            {linked_party_options(lang, 'employee', values.get('party_id') if party_type == 'employee' else '')}
                        </select>
                        <a id="employee_statement_link" class="btn blue" style="padding: 8px 12px; display: {employee_display};" href="#" target="_blank">
                            {tr(lang, "Statement", "ظƒط´ظپ ط§ظ„ط­ط³ط§ط¨")}
                        </a>
                    </div>
                </div>
            </div>
            
            <div id="employee_extra_fields" style="display:{'none' if is_custody_return_receipt else employee_display}; border:1px solid #eee; padding:10px; border-radius:4px; margin-top:14px; background:#f9f9f9;">
                <div class="row">
                    <div class="col">
                        <label>{tr(lang, 'Transaction Type', 'ظ†ظˆط¹ ط§ظ„ط¹ظ…ظ„ظٹط©')}</label>
                        <select name="employee_trans_type" id="employee_trans_type" onchange="updateEmployeeAdvances()">
                            <option value="" {"selected" if trans_type not in ("advance", "custody", "custody_return") else ""}>-- {tr(lang, "Select Transaction Type", "ط§ط®طھط± ظ†ظˆط¹ ط§ظ„ط¹ظ…ظ„ظٹط©")} --</option>
                            <option value="advance" {"selected" if trans_type == "advance" else ""}>{tr(lang, "Advance", "ط³ظ„ظپط©")}</option>
                            <option value="custody" {"selected" if trans_type == "custody" else ""}>{tr(lang, "Custody", "ط¹ظ‡ط¯ط©")}</option>
                            <option value="custody_return" {"selected" if trans_type == "custody_return" else ""}>{tr(lang, "Custody Return", "رد عهدة")}</option>
                        </select>
                    </div>
                    <div class="col" id="advance_select_wrap" style="display:{advance_select_display};">
                        <label>{tr(lang, 'Select Advance Request', 'ط§ط®طھط± ط·ظ„ط¨ ط§ظ„ط³ظ„ظپط©')}</label>
                        <select name="advance_id" id="advance_id" onchange="syncAdvanceAmount()">
                            <option value="">-- {tr(lang, "Select Advance", "ط§ط®طھط± ط§ظ„ط³ظ„ظپط©")} --</option>
                        </select>
                    </div>
                    <div class="col" id="custody_select_wrap" style="display:{custody_select_display};">
                        <label>{tr(lang, 'Select Custody Request', 'ط§ط®طھط± ط·ظ„ط¨ ط§ظ„ط¹ظ‡ط¯ط©')}</label>
                        <select name="custody_request_id" id="custody_request_id" onchange="syncCustodyAmount()">
                            <option value="">-- {tr(lang, "Select Custody Request", "ط§ط®طھط± ط·ظ„ط¨ ط§ظ„ط¹ظ‡ط¯ط©")} --</option>
                        </select>
                    </div>
                </div>
            </div>

            {f'''
            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{tr(lang, 'Payment From', 'ظ…طµط¯ط± ط§ظ„طµط±ظپ')}</label>
                    <select name="expense_payment_source" id="expense_payment_source" onchange="toggleExpensePaymentSource()">
                        <option value="liquidity" {expense_liquidity_selected}>{tr(lang, 'Liquidity', 'ط³ظٹظˆظ„ط©')}</option>
                        <option value="custody" {expense_custody_selected}>{tr(lang, 'Employee Custody', 'ط¹ظ‡ط¯ط© ظ…ظˆط¸ظپ')}</option>
                    </select>
                </div>
                <div class="col" id="expense_employee_wrap" style="display:{expense_employee_display};">
                    <label>{tr(lang, 'Custody Employee', 'ظ…ظˆط¸ظپ ط§ظ„ط¹ظ‡ط¯ط©')}</label>
                    <select name="expense_employee_id" id="expense_employee_id">
                        {employee_options_html(expense_employee_id)}
                    </select>
                </div>
            </div>
            ''' if show_payment_from else ''}

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{tr(lang, 'Cash / Bank Account', 'ط­ط³ط§ط¨ ط§ظ„ط®ط²ظ†ط© / ط§ظ„ط¨ظ†ظƒ')}</label>
                    <select name="liquidity_account_code" id="liquidity_account_code" style="display:{expense_account_display};" {"required" if not (show_payment_from and expense_payment_source == "custody") else ""}>
                        {liquidity_account_options(values.get('liquidity_account_code') or safe(get_setting_value('default_cash_account', '111100')))}
                    </select>
                </div>
                <div class="col" id="counter_account_wrap" style="display:{counter_select_display};">
                    <label>{tr(lang, 'Counter Account', 'ط§ظ„ط­ط³ط§ط¨ ط§ظ„ظ…ظ‚ط§ط¨ظ„')}</label>
                    <select id="counter_account_code_select" name="counter_account_code" {"required" if counter_select_display == "block" and not is_expense_payment else "disabled"}>
                        {counter_account_options(values.get('counter_account_code'))}
                    </select>
                </div>
                <div class="col" id="employee_counter_wrap" style="display:{employee_counter_display};">
                    <label>{tr(lang, 'Counter Account', 'ط§ظ„ط­ط³ط§ط¨ ط§ظ„ظ…ظ‚ط§ط¨ظ„')}</label>
                    <input id="employee_counter_account_value" value="{employee_counter_account}" readonly>
                </div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{tr(lang, 'Amount', 'ط§ظ„ظ…ط¨ظ„ط؛')}</label>
                    <input type="number" step="0.01" min="0" name="amount" value="{safe(values.get('amount'))}" {"readonly style='background:#f3f4f6;'" if source_type in ("expense", "custody_return_request") else ""} required>
                </div>
                <div class="col">
                    <label>{signature_label(lang, voucher_type)}</label>
                    <input name="signature_name" value="{safe(values.get('signature_name'))}">
                </div>
            </div>
            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{tr(lang, 'Description', 'ط§ظ„ط¨ظٹط§ظ†')}</label>
                    <input name="description" value="{safe(values.get('description'))}">
                </div>
            </div>
            {attachment_html}
            <div style="margin-top:18px;">
                <button class="btn green" type="submit">{tr(lang, 'Save Draft', 'ط­ظپط¸ ظƒظ…ط³ظˆط¯ط©')}</button>
                <a class="btn gray" href="{route_base(voucher_type)}">{tr(lang, 'Back', 'ط±ط¬ظˆط¹')}</a>
            </div>
        </form>
        <script>
        const isExpensePayment = {str(is_expense_payment).lower()};
        function toggleExpensePaymentSource() {{
            const source = document.getElementById('expense_payment_source')?.value || 'liquidity';
            const employeeWrap = document.getElementById('expense_employee_wrap');
            const accountSelect = document.getElementById('liquidity_account_code');
            const isCustody = source === 'custody';
            if (employeeWrap) employeeWrap.style.display = isCustody ? 'block' : 'none';
            if (accountSelect) {{
                accountSelect.style.display = isCustody ? 'none' : 'block';
                accountSelect.required = !isCustody;
            }}
        }}

        function toggleLinkedPartyFields() {{
            if ({str(is_custody_return_receipt).lower()}) return;
            if (isExpensePayment) return;
            const type = document.getElementById('party_type')?.value || 'other';
            const transType = document.getElementById('employee_trans_type')?.value || '';
            const manualWrap = document.getElementById('manual_party_wrap');
            const customerWrap = document.getElementById('customer_party_wrap');
            const vendorWrap = document.getElementById('vendor_party_wrap');
            const employeeWrap = document.getElementById('employee_party_wrap');
            const employeeExtra = document.getElementById('employee_extra_fields');
            const employeeStatementLink = document.getElementById('employee_statement_link');
            const counterWrap = document.getElementById('counter_account_wrap');
            const employeeCounterWrap = document.getElementById('employee_counter_wrap');
            const counterSelect = document.getElementById('counter_account_code_select');
            const employeeCounterValue = document.getElementById('employee_counter_account_value');

            if (manualWrap) manualWrap.style.display = (type === 'customer' || type === 'vendor' || type === 'employee') ? 'none' : 'block';
            if (customerWrap) customerWrap.style.display = type === 'customer' ? 'block' : 'none';
            if (vendorWrap) vendorWrap.style.display = type === 'vendor' ? 'block' : 'none';
            if (employeeWrap) employeeWrap.style.display = type === 'employee' ? 'block' : 'none';
            if (employeeExtra) employeeExtra.style.display = type === 'employee' ? 'block' : 'none';
            if (employeeStatementLink) employeeStatementLink.style.display = type === 'employee' ? 'block' : 'none';
            const showCounterAccount = type === 'other';
            if (counterWrap) counterWrap.style.display = showCounterAccount ? 'block' : 'none';
            if (employeeCounterWrap) employeeCounterWrap.style.display = 'none';
            if (counterSelect) {{
                counterSelect.required = showCounterAccount;
                counterSelect.disabled = !showCounterAccount;
            }}
            syncEmployeeAmountLock(type, transType);
            
            if (type === 'employee') updateEmployeeAdvances();
        }}
        function syncLinkedPartyName() {{
            const type = document.getElementById('party_type')?.value || '';
            const target = document.querySelector('input[name="party_name"]');
            const statementLink = document.getElementById('employee_statement_link');
            if (!target) return;
            let selector = null;
            if (type === 'customer') selector = document.getElementById('customer_id');
            if (type === 'vendor') selector = document.getElementById('vendor_id');
            if (type === 'employee') selector = document.getElementById('employee_id');
            if (!selector) return;
            const option = selector.options[selector.selectedIndex];
            if (option && option.value) {{
                target.value = option.text;
                if (type === 'employee' && statementLink) {{
                    statementLink.href = `/ui/accounting/partner-ledger?partner_type=employee&partner_id=${{option.value}}`;
                }}
            }}
        }}
        
        async function updateEmployeeAdvances() {{
            const empId = document.getElementById('employee_id')?.value;
            const transType = document.getElementById('employee_trans_type')?.value;
            const advanceWrap = document.getElementById('advance_select_wrap');
            const advanceSelect = document.getElementById('advance_id');
            const custodyWrap = document.getElementById('custody_select_wrap');
            const custodySelect = document.getElementById('custody_request_id');
            const preSelectedAdvanceId = "{safe(values.get('advance_id'))}";
            const preSelectedCustodyId = "{safe(values.get('custody_request_id'))}";
            syncEmployeeAmountLock('employee', transType);
            
            if (transType === 'advance' && empId) {{
                advanceWrap.style.display = 'block';
                custodyWrap.style.display = 'none';
                try {{
                    const res = await fetch(`/ui/accounting/api/employee-advances/${{empId}}`);
                    const data = await res.json();
                    advanceSelect.innerHTML = '<option value="">-- {tr(lang, "Select Advance", "ط§ط®طھط± ط§ظ„ط³ظ„ظپط©")} --</option>';
                    data.forEach(adv => {{
                        const opt = document.createElement('option');
                        opt.value = adv.id;
                        const installmentText = adv.installment > 0 ? ` - {tr(lang, "Installment", "القسط")}: ${{adv.installment}}` : '';
                        const statusText = adv.status === 'pending' ? ' [Pending]' : '';
                        opt.text = `${{adv.no}} (${{adv.amount}} EGP)${{installmentText}} - ${{adv.date}}${{statusText}}`;
                        opt.dataset.amount = adv.amount;
                        opt.dataset.no = adv.no;
                        opt.dataset.notes = adv.notes || '';
                        if (String(adv.id) === preSelectedAdvanceId) {{
                            opt.selected = true;
                        }}
                        advanceSelect.appendChild(opt);
                    }});
                    if (preSelectedAdvanceId) syncAdvanceAmount();
                }} catch(e) {{ console.error(e); }}
            }} else if (transType === 'custody' && empId) {{
                advanceWrap.style.display = 'none';
                custodyWrap.style.display = 'block';
                try {{
                    const res = await fetch(`/ui/accounting/api/employee-custody-requests/${{empId}}`);
                    const data = await res.json();
                    custodySelect.innerHTML = '<option value="">-- {tr(lang, "Select Custody Request", "ط§ط®طھط± ط·ظ„ط¨ ط§ظ„ط¹ظ‡ط¯ط©")} --</option>';
                    data.forEach(req => {{
                        const opt = document.createElement('option');
                        opt.value = req.id;
                        const statusText = req.status === 'pending' ? ' [Pending]' : '';
                        opt.text = `${{req.no}} (${{req.amount}} EGP) - ${{req.date}}${{statusText}}`;
                        opt.dataset.amount = req.amount;
                        opt.dataset.no = req.no;
                        opt.dataset.notes = req.notes || '';
                        if (String(req.id) === preSelectedCustodyId) {{
                            opt.selected = true;
                        }}
                        custodySelect.appendChild(opt);
                    }});
                    if (preSelectedCustodyId) syncCustodyAmount();
                }} catch(e) {{ console.error(e); }}
            }} else {{
                advanceWrap.style.display = 'none';
                custodyWrap.style.display = 'none';
            }}
        }}
        
        function syncAdvanceAmount() {{
            const advanceSelect = document.getElementById('advance_id');
            const amountInput = document.querySelector('input[name="amount"]');
            const descInput = document.querySelector('input[name="description"]');
            const empSelect = document.getElementById('employee_id');
            const option = advanceSelect.options[advanceSelect.selectedIndex];
            
            if (option && option.dataset.amount && amountInput) {{
                amountInput.value = option.dataset.amount;
                if (descInput && empSelect && option.value !== '') {{
                    const empName = empSelect.options[empSelect.selectedIndex].text;
                    const advNo = option.dataset.no;
                    const notes = option.dataset.notes;
                    if (descInput.value === '') {{
                        let desc = `{tr(lang, "Disburse advance no.", "صرف سلفة رقم")} ${{advNo}} {tr(lang, "to employee", "للموظف")} ${{empName}}`;
                        if (notes) desc += ` (${{notes}})`;
                        descInput.value = desc;
                    }}
                }}
            }}
        }}

        function syncCustodyAmount() {{
            const custodySelect = document.getElementById('custody_request_id');
            const amountInput = document.querySelector('input[name="amount"]');
            const descInput = document.querySelector('input[name="description"]');
            const empSelect = document.getElementById('employee_id');
            const option = custodySelect.options[custodySelect.selectedIndex];
            
            if (option && option.dataset.amount && amountInput) {{
                amountInput.value = option.dataset.amount;
                if (descInput && empSelect && option.value !== '') {{
                    const empName = empSelect.options[empSelect.selectedIndex].text;
                    const reqNo = option.dataset.no;
                    const notes = option.dataset.notes;
                    if (descInput.value === '') {{
                        let desc = `{tr(lang, "Disburse custody no.", "صرف عهدة رقم")} ${{reqNo}} {tr(lang, "to employee", "للموظف")} ${{empName}}`;
                        if (notes) desc += ` (${{notes}})`;
                        descInput.value = desc;
                    }}
                }}
            }}
        }}

        function syncEmployeeAmountLock(partyType, transType) {{
            const amountInput = document.querySelector('input[name="amount"]');
            if (!amountInput) return;
            const isEmployeeRequest = partyType === 'employee' && (transType === 'advance' || transType === 'custody');
            amountInput.readOnly = isEmployeeRequest;
            if (isEmployeeRequest) {{
                amountInput.style.background = '#f3f4f6';
                amountInput.placeholder = "{tr(lang, 'Auto from selected request', 'طھظ„ظ‚ط§ط¦ظٹ ظ…ظ† ط§ظ„ط·ظ„ط¨ ط§ظ„ظ…ط®طھط§ط±')}";
            }} else {{
                amountInput.style.background = '';
                amountInput.placeholder = '';
            }}
        }}

        toggleLinkedPartyFields();
        toggleExpensePaymentSource();
        </script>
    </div>
    """


def list_page(request: Request, voucher_type: str):
    if not accounting_allowed(request, "view"):
        return HTMLResponse("Permission denied", status_code=403)
    lang = get_lang(request)
    conn = get_conn()
    rows = conn.execute("SELECT * FROM cash_vouchers WHERE voucher_type = ? ORDER BY id DESC", (voucher_type,)).fetchall()
    body = ""
    status_ar = {"draft": "ظ…ط³ظˆط¯ط©", "pending_final_post": "ظ…ظ†طھط¸ط± ط§ظ„طھط±ط­ظٹظ„ ط§ظ„ظ†ظ‡ط§ط¦ظٹ", "posted": "ظ…ط±ط­ظ„", "reversed": "ظ…ط¹ظƒظˆط³"}
    for row in rows:
        status = voucher_display_status(conn, row)
        status_cls = "green" if status == "posted" else ("red" if status == "reversed" else "orange")
        draft_actions = ""
        if voucher_can_modify(conn, row):
            draft_actions = f"""
                <a class="btn gray" href="{route_base(voucher_type)}/{row['id']}/edit">Edit</a>
                <form method="post" action="{route_base(voucher_type)}/{row['id']}/delete" style="display:inline;"
                      onsubmit="return confirm('Delete this draft voucher?');">
                    <button class="btn red" type="submit">Delete</button>
                </form>
            """
        body += f"""
        <tr>
            <td>{safe(row['voucher_no'])}</td>
            <td>{safe(row['voucher_date'])}</td>
            <td>{safe(row['party_name'])}</td>
            <td>{safe(row['party_type']).title()}</td>
            <td>{money(row['amount'])}</td>
            <td><span class="status-chip {status_cls}">{tr(lang, status.title(), status_ar.get(status, status))}</span></td>
            <td>
                <a class="btn blue" href="{route_base(voucher_type)}/{row['id']}">Open</a>
                {draft_actions}
            </td>
        </tr>
        """
    if not body:
        body = f"<tr><td colspan='7' style='text-align:center;'>{tr(lang, 'No vouchers found.', 'ظ„ط§ طھظˆط¬ط¯ ط³ظ†ط¯ط§طھ ظ…ط³ط¬ظ„ط©.')}</td></tr>"
    conn.close()
    html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>{voucher_list_title(lang, voucher_type)}</h2>
            <a class="btn green" href="{route_base(voucher_type)}/new">+ {tr(lang, 'New Voucher', 'ط³ظ†ط¯ ط¬ط¯ظٹط¯')}</a>
        </div>
    </div>
    <div class="card">
        <table>
            <tr>
                <th>{tr(lang, 'No', 'ط§ظ„ط±ظ‚ظ…')}</th>
                <th>{tr(lang, 'Date', 'ط§ظ„طھط§ط±ظٹط®')}</th>
                <th>{party_label(lang, voucher_type)}</th>
                <th>{tr(lang, 'Party Type', 'ظ†ظˆط¹ ط§ظ„ط·ط±ظپ')}</th>
                <th>{tr(lang, 'Amount', 'ط§ظ„ظ…ط¨ظ„ط؛')}</th>
                <th>{tr(lang, 'Status', 'ط§ظ„ط­ط§ظ„ط©')}</th>
                <th>{tr(lang, 'Action', 'ط§ظ„ط¥ط¬ط±ط§ط،')}</th>
            </tr>
            {body}
        </table>
    </div>
    """
    return HTMLResponse(render_page(voucher_list_title(lang, voucher_type), html, lang, current_path=request.url.path))


@router.get("/ui/accounting/cash-receipts", response_class=HTMLResponse)
def cash_receipts_list(request: Request):
    return list_page(request, "receipt")


@router.get("/ui/accounting/cash-payments", response_class=HTMLResponse)
def cash_payments_list(request: Request):
    return list_page(request, "payment")


@router.get("/ui/accounting/cash-receipts/new", response_class=HTMLResponse)
def cash_receipts_new(request: Request):
    if not accounting_allowed(request, "create"):
        return HTMLResponse("Permission denied", status_code=403)
    lang = get_lang(request)
    params = request.query_params
    values = {
        "party_type": params.get("party_type"),
        "party_id": params.get("employee_id") or params.get("customer_id") or params.get("vendor_id"),
        "employee_trans_type": params.get("employee_trans_type"),
        "advance_id": params.get("advance_id"),
        "custody_request_id": params.get("custody_request_id"),
        "amount": params.get("amount"),
        "source_type": params.get("source_type"),
        "source_id": params.get("source_id") or params.get("expense_id"),
        "expense_payment_source": params.get("expense_payment_source") or "liquidity",
        "expense_employee_id": params.get("expense_employee_id"),
    }
    if safe(values.get("source_type")).lower() == "custody_return_request" and safe_int(values.get("source_id")) > 0:
        conn = get_conn()
        req = conn.execute(
            "SELECT * FROM employee_custody_return_requests WHERE id = ? LIMIT 1",
            (safe_int(values.get("source_id")),),
        ).fetchone()
        if req:
            emp = conn.execute("SELECT code, name FROM employees WHERE id = ? LIMIT 1", (safe_int(req["employee_id"]),)).fetchone()
            emp_name = safe(emp["name"]) if emp else ""
            emp_code = safe(emp["code"]) if emp else ""
            emp_label = f"{emp_code} - {emp_name}" if emp_code else emp_name
            values["party_type"] = "employee"
            values["party_id"] = safe(req["employee_id"])
            values["employee_trans_type"] = "custody_return"
            values["amount"] = str(float(req["amount"] or 0))
            values["counter_account_code"] = employee_custody_account_code()
            values["party_name"] = emp_name
            values["description"] = values.get("description") or f"{tr(lang, 'Receive custody return no.', 'استلام رد عهدة رقم')} {safe(req['request_no'])} - {emp_label}"
        conn.close()
    return HTMLResponse(render_page(voucher_title(lang, "receipt"), render_form(lang, "receipt", request.url.path, values), lang, current_path=request.url.path))


@router.get("/ui/accounting/cash-payments/new", response_class=HTMLResponse)
def cash_payments_new(request: Request):
    if not accounting_allowed(request, "create"):
        return HTMLResponse("Permission denied", status_code=403)
    lang = get_lang(request)
    params = request.query_params
    values = {
        "party_type": params.get("party_type"),
        "party_id": params.get("employee_id") or params.get("customer_id") or params.get("vendor_id"),
        "employee_trans_type": params.get("employee_trans_type"),
        "advance_id": params.get("advance_id"),
        "custody_request_id": params.get("custody_request_id"),
        "amount": params.get("amount"),
        "source_type": params.get("source_type"),
        "source_id": params.get("source_id") or params.get("expense_id"),
        "expense_payment_source": params.get("expense_payment_source") or "liquidity",
        "expense_employee_id": params.get("expense_employee_id"),
    }
    if safe(values.get("source_type")).lower() == "expense" and safe_int(values.get("source_id")) > 0:
        conn = get_conn()
        expense = conn.execute("SELECT * FROM expenses WHERE id = ? LIMIT 1", (safe_int(values.get("source_id")),)).fetchone()
        conn.close()
        if expense:
            values["party_type"] = values.get("party_type") or "other"
            values["party_name"] = values.get("party_name") or f"Expense {safe(expense['expense_no'])}"
            values["amount"] = str(float(expense["total_amount"] or 0))
            values["description"] = values.get("description") or f"Pay expense {safe(expense['expense_no'])} - {safe(expense['description'])}"
    return HTMLResponse(render_page(voucher_title(lang, "payment"), render_form(lang, "payment", request.url.path, values), lang, current_path=request.url.path))


def normalize_party_data(
    conn,
    voucher_type: str,
    party_type: str,
    party_name: str,
    customer_id: str,
    vendor_id: str,
    employee_id: str,
    employee_trans_type: str,
    counter_account_code: str,
):
    party_type = safe(party_type).lower() or "other"
    party_id = 0
    partner = None

    if party_type == "customer":
        party_id = safe_int(customer_id)
        partner = get_partner(conn, "customer", party_id)
        if not partner:
            raise Exception("Customer is required")
        party_name = safe(partner["name"])
        counter_account_code = default_counter_account_for_party("customer", partner)
        if voucher_type != "receipt":
            raise Exception("Customer-linked cash vouchers should be created as cash receipts")

    elif party_type == "vendor":
        party_id = safe_int(vendor_id)
        partner = get_partner(conn, "vendor", party_id)
        if not partner:
            raise Exception("Vendor is required")
        party_name = safe(partner["name"])
        counter_account_code = default_counter_account_for_party("vendor", partner)
        if voucher_type != "payment":
            raise Exception("Vendor-linked cash vouchers should be created as cash payments")

    elif party_type == "employee":
        party_id = safe_int(employee_id)
        emp = conn.execute("SELECT name FROM employees WHERE id = ?", (party_id,)).fetchone()
        if not emp:
            raise Exception("Employee is required")
        party_name = safe(emp["name"])
        trans_type = safe(employee_trans_type).lower() or "advance"
        if trans_type not in ("advance", "custody", "custody_return"):
            raise Exception("Please select employee transaction type (Advance/Custody).")
        if trans_type == "custody_return":
            if voucher_type != "receipt":
                raise Exception("Custody returns should be created as cash receipts.")
            counter_account_code = employee_custody_account_code()
            if not counter_account_code:
                raise Exception("Please set Employee Custody Account in configuration.")
        elif trans_type == "custody":
            counter_account_code = employee_custody_account_code()
            if not counter_account_code:
                raise Exception("Please set Employee Custody Account in configuration.")
        else:
            counter_account_code = safe(get_setting_value("employee_advance_account", "121200"))
            if not counter_account_code:
                raise Exception("Please set Employee Advance Account in configuration.")

    return party_type, party_id, safe(party_name), safe(counter_account_code)


def save_voucher(
    request: Request,
    voucher_type: str,
    voucher_no: str,
    voucher_date: str,
    party_name: str,
    party_type: str,
    customer_id: str,
    vendor_id: str,
    employee_id: str,
    employee_trans_type: str,
    advance_id: str,
    custody_request_id: str,
    liquidity_account_code: str,
    counter_account_code: str,
    amount: str,
    description: str,
    signature_name: str,
    source_type: str = "",
    source_id: str = "",
    expense_payment_source: str = "",
    expense_employee_id: str = "",
    voucher_attachments=None,
):
    if not accounting_allowed(request, "create"):
        return HTMLResponse("Permission denied", status_code=403)
    lang = get_lang(request)
    values = {
        "voucher_no": voucher_no,
        "voucher_date": voucher_date,
        "party_name": party_name,
        "party_type": party_type,
        "party_id": customer_id if safe(party_type).lower() == "customer" else (vendor_id if safe(party_type).lower() == "vendor" else employee_id),
        "employee_trans_type": employee_trans_type,
        "advance_id": advance_id,
        "custody_request_id": custody_request_id,
        "liquidity_account_code": liquidity_account_code,
        "counter_account_code": counter_account_code,
        "amount": amount,
        "description": description,
        "signature_name": signature_name,
        "source_type": source_type,
        "source_id": source_id,
        "expense_payment_source": expense_payment_source,
        "expense_employee_id": expense_employee_id,
    }
    conn = None
    try:
        conn = get_conn()
        new_attachments = save_voucher_uploads(voucher_attachments)
        if voucher_type == "payment" and not new_attachments:
            raise Exception("Attachment is required for cash payment vouchers.")
        source_type = safe(source_type).lower()
        selected_source_id = safe_int(source_id)
        if source_type == "custody_return_request":
            if voucher_type != "receipt":
                raise Exception("Custody return requests can only be received from cash receipts.")
            req = conn.execute(
                """
                SELECT *
                FROM employee_custody_return_requests
                WHERE id = ?
                  AND LOWER(COALESCE(status, 'active')) = 'active'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM cash_vouchers v
                      WHERE LOWER(COALESCE(v.source_type,'')) = 'custody_return_request'
                        AND COALESCE(v.source_id, 0) = employee_custody_return_requests.id
                        AND LOWER(COALESCE(v.voucher_type,'')) = 'receipt'
                        AND LOWER(COALESCE(v.status,'')) <> 'reversed'
                  )
                LIMIT 1
                """,
                (selected_source_id,),
            ).fetchone()
            if not req:
                raise Exception("Selected custody return request is not pending.")
            party_type = "employee"
            employee_id = str(req["employee_id"] or "")
            employee_trans_type = "custody_return"
            amount = str(float(req["amount"] or 0))
            counter_account_code = employee_custody_account_code()
            description = safe(description) or f"{tr(lang, 'Receive custody return no.', 'استلام رد عهدة رقم')} {safe(req['request_no'])}"
            if employee_custody_balance(conn, safe_int(employee_id)) < q2(amount):
                raise Exception(f"Employee custody balance is not enough. Available: {money(employee_custody_balance(conn, safe_int(employee_id)))}")
        party_type, party_id, party_name, counter_account_code = normalize_party_data(
            conn,
            voucher_type,
            party_type,
            party_name,
            customer_id,
            vendor_id,
            employee_id,
            employee_trans_type,
            counter_account_code,
        )
        selected_advance_id = None
        selected_custody_request_id = None
        expense_payment_source = safe(expense_payment_source).lower() or "liquidity"
        if expense_payment_source in ("cash", "bank"):
            expense_payment_source = "liquidity"
        if expense_payment_source not in ("liquidity", "custody"):
            expense_payment_source = "liquidity"
        selected_expense_employee_id = safe_int(expense_employee_id)

        if source_type == "expense":
            if voucher_type != "payment":
                raise Exception("Expenses can only be paid from cash payments.")
            expense_row = conn.execute(
                """
                SELECT *
                FROM expenses
                WHERE id = ?
                  AND LOWER(COALESCE(status, 'draft')) IN ('draft', 'pending_payment')
                  AND NOT EXISTS (
                      SELECT 1 FROM cash_vouchers v
                      WHERE LOWER(COALESCE(v.source_type, '')) = 'expense'
                        AND COALESCE(v.source_id, 0) = expenses.id
                        AND LOWER(COALESCE(v.status, '')) <> 'reversed'
                  )
                LIMIT 1
                """,
                (selected_source_id,),
            ).fetchone()
            if not expense_row:
                raise Exception("Selected expense is not pending for payment.")
            amount = str(float(expense_row["total_amount"] or 0))
            party_type = "other"
            party_id = 0
            party_name = safe(party_name) or f"Expense {safe(expense_row['expense_no'])}"
            description = safe(description) or f"Pay expense {safe(expense_row['expense_no'])} - {safe(expense_row['description'])}"
            counter_account_code = safe(counter_account_code) or "EXPENSE-LINES"
            if expense_payment_source == "custody":
                if selected_expense_employee_id <= 0:
                    raise Exception("Please select the employee whose custody will pay this expense.")
                emp = conn.execute("SELECT id FROM employees WHERE id = ? LIMIT 1", (selected_expense_employee_id,)).fetchone()
                if not emp:
                    raise Exception("Selected custody employee was not found.")
                liquidity_account_code = employee_custody_account_code()
                if not liquidity_account_code:
                    raise Exception("Please set Employee Custody Account in configuration.")
                available = employee_custody_balance(conn, selected_expense_employee_id)
                if available < q2(amount):
                    raise Exception(f"Employee custody balance is not enough. Available: {money(available)}")

        if source_type != "expense" and voucher_type == "payment" and party_type in ("vendor", "other") and expense_payment_source == "custody":
            if selected_expense_employee_id <= 0:
                raise Exception("Please select the employee whose custody will pay this vendor.")
            emp = conn.execute("SELECT id FROM employees WHERE id = ? LIMIT 1", (selected_expense_employee_id,)).fetchone()
            if not emp:
                raise Exception("Selected custody employee was not found.")
            liquidity_account_code = employee_custody_account_code()
            if employee_custody_balance(conn, selected_expense_employee_id) < q2(amount):
                raise Exception(f"Employee custody balance is not enough. Available: {money(employee_custody_balance(conn, selected_expense_employee_id))}")

        # For employee disbursements, amount must come from the selected request
        # (advance or custody request), not manual input.
        if party_type == "employee":
            trans_type = safe(employee_trans_type).lower()
            if trans_type == "advance":
                selected_advance_id = safe_int(advance_id)
                if selected_advance_id <= 0:
                    raise Exception("Please select an advance request.")
                advance_row = conn.execute(
                    """
                    SELECT id, amount
                    FROM employee_advances
                    WHERE id = ?
                      AND employee_id = ?
                      AND LOWER(COALESCE(status, 'active')) = 'active'
                    LIMIT 1
                    """,
                    (selected_advance_id, party_id),
                ).fetchone()
                if not advance_row:
                    raise Exception("Selected advance request is not pending for this employee.")
                amount = str(float(advance_row["amount"] or 0))
            elif trans_type == "custody":
                selected_custody_request_id = safe_int(custody_request_id)
                if selected_custody_request_id <= 0:
                    raise Exception("Please select a custody request.")
                custody_row = conn.execute(
                    """
                    SELECT id, amount
                    FROM employee_custody_requests
                    WHERE id = ?
                      AND employee_id = ?
                      AND LOWER(COALESCE(status, 'active')) = 'active'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM cash_vouchers v
                          WHERE COALESCE(v.custody_request_id, 0) = employee_custody_requests.id
                            AND LOWER(COALESCE(v.voucher_type,'')) = 'payment'
                            AND LOWER(COALESCE(v.employee_trans_type,'')) = 'custody'
                            AND LOWER(COALESCE(v.status,'')) <> 'reversed'
                      )
                    LIMIT 1
                    """,
                    (selected_custody_request_id, party_id),
                ).fetchone()
                if not custody_row:
                    raise Exception("Selected custody request is not pending for this employee.")
                amount = str(float(custody_row["amount"] or 0))

        validate_voucher(voucher_date, party_name, party_type, party_id, liquidity_account_code, counter_account_code, amount, source_type)
        cur = conn.execute(
            """
            INSERT INTO cash_vouchers (
                voucher_type, voucher_no, voucher_date, party_name, party_type, party_id,
                liquidity_account_code, counter_account_code, amount, description, signature_name, 
                employee_trans_type, advance_id, custody_request_id, source_type, source_id,
                expense_payment_source, expense_employee_id, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
            """,
            (
                voucher_type,
                safe(voucher_no) or next_voucher_no(voucher_type),
                safe(voucher_date),
                party_name,
                party_type,
                party_id if party_id > 0 else None,
                safe(liquidity_account_code),
                counter_account_code,
                float(q2(amount)),
                safe(description),
                safe(signature_name),
                employee_trans_type if party_type == "employee" else None,
                selected_advance_id if party_type == "employee" and safe(employee_trans_type).lower() == "advance" else None,
                selected_custody_request_id if party_type == "employee" and safe(employee_trans_type).lower() == "custody" else None,
                source_type or None,
                selected_source_id if source_type else None,
                expense_payment_source if (source_type == "expense" or voucher_type == "payment") else None,
                selected_expense_employee_id if expense_payment_source == "custody" else None,
            ),
        )
        voucher_id = cur.lastrowid
        insert_voucher_attachments(conn, voucher_id, new_attachments)
        create_draft_journal(conn, voucher_id)
        conn.commit()
        conn.close()
        return RedirectResponse(f"{route_base(voucher_type)}/{voucher_id}", status_code=302)
    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        return HTMLResponse(
            render_page(voucher_title(lang, voucher_type), render_form(lang, voucher_type, request.url.path, values, str(e)), lang, current_path=request.url.path),
            status_code=400,
        )


@router.post("/ui/accounting/cash-receipts/new")
def create_cash_receipt(
    request: Request,
    voucher_no: str = Form(""),
    voucher_date: str = Form(""),
    party_name: str = Form(""),
    party_type: str = Form(""),
    customer_id: str = Form(""),
    vendor_id: str = Form(""),
    employee_id: str = Form(""),
    employee_trans_type: str = Form(""),
    advance_id: str = Form(""),
    custody_request_id: str = Form(""),
    liquidity_account_code: str = Form(""),
    counter_account_code: str = Form(""),
    amount: str = Form("0"),
    description: str = Form(""),
    signature_name: str = Form(""),
    source_type: str = Form(""),
    source_id: str = Form(""),
    expense_payment_source: str = Form(""),
    expense_employee_id: str = Form(""),
    voucher_attachments: list[UploadFile] = File(None),
):
    return save_voucher(request, "receipt", voucher_no, voucher_date, party_name, party_type, customer_id, vendor_id, employee_id, employee_trans_type, advance_id, custody_request_id, liquidity_account_code, counter_account_code, amount, description, signature_name, source_type, source_id, expense_payment_source, expense_employee_id, voucher_attachments)


@router.post("/ui/accounting/cash-payments/new")
def create_cash_payment(
    request: Request,
    voucher_no: str = Form(""),
    voucher_date: str = Form(""),
    party_name: str = Form(""),
    party_type: str = Form(""),
    customer_id: str = Form(""),
    vendor_id: str = Form(""),
    employee_id: str = Form(""),
    employee_trans_type: str = Form(""),
    advance_id: str = Form(""),
    custody_request_id: str = Form(""),
    liquidity_account_code: str = Form(""),
    counter_account_code: str = Form(""),
    amount: str = Form("0"),
    description: str = Form(""),
    signature_name: str = Form(""),
    source_type: str = Form(""),
    source_id: str = Form(""),
    expense_payment_source: str = Form(""),
    expense_employee_id: str = Form(""),
    voucher_attachments: list[UploadFile] = File(None),
):
    return save_voucher(request, "payment", voucher_no, voucher_date, party_name, party_type, customer_id, vendor_id, employee_id, employee_trans_type, advance_id, custody_request_id, liquidity_account_code, counter_account_code, amount, description, signature_name, source_type, source_id, expense_payment_source, expense_employee_id, voucher_attachments)


def render_allocation_card(lang: str, conn, voucher):
    payment_type = voucher_payment_type(voucher)
    if not payment_type:
        return ""

    total_allocated = get_allocated_total_for_payment(conn, payment_type, voucher["id"])
    unallocated = get_payment_unallocated_amount(conn, payment_type, voucher["id"])
    rows = allocation_rows_for_voucher(conn, voucher)

    body = ""
    for row in rows:
        body += f"""
        <tr>
            <td>{safe(row['doc_no'])}</td>
            <td>{safe(row['doc_date'])}</td>
            <td>{money(row['doc_total'])}</td>
            <td>{money(row['allocated_amount'])}</td>
            <td><a class="btn blue" href="{safe(row['open_url'])}">{tr(lang, 'Open', 'ظپطھط­')}</a></td>
        </tr>
        """
    if not body:
        body = f"<tr><td colspan='5' style='text-align:center;'>{tr(lang, 'No allocations yet.', 'ظ„ط§ طھظˆط¬ط¯ طھط³ظˆظٹط§طھ ط¨ط¹ط¯.')}</td></tr>"

    label = tr(lang, "Available for allocation", "ط§ظ„ظ…طھط§ط­ ظ„ظ„طھط³ظˆظٹط©")
    return f"""
    <div class="card" style="margin-top:18px;">
        <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:10px;">
            <span class="chip">{tr(lang, 'Allocated', 'ط§ظ„ظ…ط®طµطµ')}: {money(total_allocated)}</span>
            <span class="chip">{label}: {money(unallocated)}</span>
        </div>
        <table>
            <tr>
                <th>{tr(lang, 'Document No', 'ط±ظ‚ظ… ط§ظ„ظ…ط³طھظ†ط¯')}</th>
                <th>{tr(lang, 'Date', 'ط§ظ„طھط§ط±ظٹط®')}</th>
                <th>{tr(lang, 'Document Total', 'ط¥ط¬ظ…ط§ظ„ظٹ ط§ظ„ظ…ط³طھظ†ط¯')}</th>
                <th>{tr(lang, 'Allocated', 'ط§ظ„ظ…ط®طµطµ')}</th>
                <th>{tr(lang, 'Action', 'ط§ظ„ط¥ط¬ط±ط§ط،')}</th>
            </tr>
            {body}
        </table>
    </div>
    """


def open_voucher_page(request: Request, voucher_id: int, voucher_type: str):
    if not accounting_allowed(request, "view"):
        return HTMLResponse("Permission denied", status_code=403)
    lang = get_lang(request)
    conn = get_conn()
    voucher = get_voucher(conn, voucher_id)
    if not voucher or safe(voucher["voucher_type"]) != voucher_type:
        conn.close()
        return HTMLResponse(tr(lang, "Voucher not found", "ط§ظ„ط³ظ†ط¯ ط؛ظٹط± ظ…ظˆط¬ظˆط¯"), status_code=404)

    status = voucher_display_status(conn, voucher)
    status_cls = "green" if status == "posted" else ("red" if status == "reversed" else "orange")
    status_ar = {"draft": "ظ…ط³ظˆط¯ط©", "pending_final_post": "ظ…ظ†طھط¸ط± ط§ظ„طھط±ط­ظٹظ„ ط§ظ„ظ†ظ‡ط§ط¦ظٹ", "posted": "ظ…ط±ط­ظ„", "reversed": "ظ…ط¹ظƒظˆط³"}

    post_btn = ""
    if status == "draft" and voucher_journal_status(conn, voucher) in ("", "draft") and accounting_allowed(request, "post"):
        post_btn = f"<form method='post' action='{route_base(voucher_type)}/{voucher_id}/post' style='display:inline;'><button class='btn green' type='submit'>{tr(lang, 'Send to Journal', 'طھط±ط­ظٹظ„ ظ„ظ„ط¬ظˆط±ظ†ط§ظ„')}</button></form>"
    draft_manage_btns = ""
    if voucher_can_modify(conn, voucher):
        draft_manage_btns = f"""
            <a class="btn blue" href="{route_base(voucher_type)}/{voucher_id}/edit">Edit</a>
            <form method="post" action="{route_base(voucher_type)}/{voucher_id}/delete" style="display:inline;"
                  onsubmit="return confirm('Delete this draft voucher?');">
                <button class="btn red" type="submit">Delete</button>
            </form>
        """

    allocation_html = render_allocation_card(lang, conn, voucher) if status == "posted" else ""
    attachments_html = attachment_gallery(load_voucher_attachments(conn, voucher_id))
    show_counter_account = (
        safe(voucher["party_type"]).lower() == "other"
        and safe(voucher["source_type"]).lower() not in ("expense", "employee_grant")
    )
    counter_account_html = (
        f"<p><b>{tr(lang, 'Counter Account:', 'ط§ظ„ط­ط³ط§ط¨ ط§ظ„ظ…ظ‚ط§ط¨ظ„:')}</b> {account_display(voucher['counter_account_code'])}</p>"
        if show_counter_account
        else ""
    )
    conn.close()

    html = f"""
    <div class="card">
        <h2>{voucher_title(lang, voucher_type)} {safe(voucher['voucher_no'])}</h2>
        <p><b>{tr(lang, 'Date:', 'ط§ظ„طھط§ط±ظٹط®:')}</b> {safe(voucher['voucher_date'])}</p>
        <p><b>{party_label(lang, voucher_type)}:</b> {safe(voucher['party_name'])}</p>
        <p><b>{tr(lang, 'Party Type:', 'ظ†ظˆط¹ ط§ظ„ط·ط±ظپ:')}</b> {safe(voucher['party_type']).title()}</p>
        <p><b>{tr(lang, 'Cash / Bank Account:', 'ط­ط³ط§ط¨ ط§ظ„ظ†ظ‚ط¯ظٹط© / ط§ظ„ط¨ظ†ظƒ:')}</b> {account_display(voucher['liquidity_account_code'])}</p>
        {counter_account_html}
        <p><b>{tr(lang, 'Amount:', 'ط§ظ„ظ…ط¨ظ„ط؛:')}</b> {money(voucher['amount'])}</p>
        <p><b>{tr(lang, 'Description:', 'ط§ظ„ط¨ظٹط§ظ†:')}</b> {safe(voucher['description'])}</p>
        <p><b>{signature_label(lang, voucher_type)}:</b> {safe(voucher['signature_name'])}</p>
        <p><b>{tr(lang, 'Status:', 'ط§ظ„ط­ط§ظ„ط©:')}</b> <span class="status-chip {status_cls}">{tr(lang, status.title(), status_ar.get(status, status))}</span></p>
        <p><b>{tr(lang, 'Journal ID:', 'ط±ظ‚ظ… ط§ظ„ظ‚ظٹط¯:')}</b> {safe(voucher['journal_id'])}</p>
        <p><b>{tr(lang, 'Reverse Journal ID:', 'ط±ظ‚ظ… ظ‚ظٹط¯ ط§ظ„ط¹ظƒط³:')}</b> {safe(voucher['reversed_journal_id'])}</p>
        <div style="margin-top:18px;display:flex;gap:8px;flex-wrap:wrap;">
            <a class="btn blue" href="{route_base(voucher_type)}/{voucher_id}/print" target="_blank">{tr(lang, 'Print', 'ط·ط¨ط§ط¹ط©')}</a>
            {draft_manage_btns}
            {post_btn}
            <a class="btn gray" href="{route_base(voucher_type)}">{tr(lang, 'Back', 'ط±ط¬ظˆط¹')}</a>
        </div>
    </div>
    {attachments_html}
    {allocation_html}
    """
    return HTMLResponse(render_page(voucher_title(lang, voucher_type), html, lang, current_path=request.url.path))


@router.get("/ui/accounting/cash-receipts/{voucher_id}", response_class=HTMLResponse)
def open_cash_receipt(request: Request, voucher_id: int):
    return open_voucher_page(request, voucher_id, "receipt")


@router.get("/ui/accounting/cash-payments/{voucher_id}", response_class=HTMLResponse)
def open_cash_payment(request: Request, voucher_id: int):
    return open_voucher_page(request, voucher_id, "payment")


def remove_draft_voucher_journal(conn, voucher):
    journal_id = safe_int(voucher["journal_id"])
    if not journal_id:
        return
    journal = conn.execute("SELECT id, status FROM journal_entries WHERE id = ? LIMIT 1", (journal_id,)).fetchone()
    if journal and safe(journal["status"]).lower() not in ("draft", "pending_final_post"):
        raise Exception("Voucher journal is not draft and cannot be deleted from here")
    conn.execute("DELETE FROM journal_lines WHERE journal_id = ?", (journal_id,))
    conn.execute("DELETE FROM journal_entries WHERE id = ?", (journal_id,))
    conn.execute("UPDATE cash_vouchers SET journal_id = NULL WHERE id = ?", (voucher["id"],))


def reset_draft_voucher_source(conn, voucher):
    if safe(voucher["party_type"]).lower() == "employee" and safe(voucher["employee_trans_type"]).lower() == "advance" and safe_int(voucher["advance_id"]) > 0:
        conn.execute("UPDATE employee_advances SET status = 'active' WHERE id = ?", (safe_int(voucher["advance_id"]),))
    if safe(voucher["party_type"]).lower() == "employee" and safe(voucher["employee_trans_type"]).lower() == "custody" and safe_int(voucher["custody_request_id"]) > 0:
        conn.execute("UPDATE employee_custody_requests SET status = 'active' WHERE id = ?", (safe_int(voucher["custody_request_id"]),))
    if safe(voucher["source_type"]).lower() == "expense" and safe_int(voucher["source_id"]) > 0:
        conn.execute(
            "UPDATE expenses SET status = 'pending_payment', journal_id = NULL, reversed_journal_id = NULL, payment_account_code = NULL WHERE id = ?",
            (safe_int(voucher["source_id"]),),
        )
    if safe(voucher["source_type"]).lower() == "employee_grant" and safe_int(voucher["source_id"]) > 0:
        conn.execute(
            "UPDATE employee_grants SET status = 'draft', payment_voucher_id = NULL WHERE id = ?",
            (safe_int(voucher["source_id"]),),
        )
    if safe(voucher["source_type"]).lower() == "custody_return_request" and safe_int(voucher["source_id"]) > 0:
        conn.execute(
            "UPDATE employee_custody_return_requests SET status = 'active' WHERE id = ?",
            (safe_int(voucher["source_id"]),),
        )


def edit_voucher_page(request: Request, voucher_id: int, voucher_type: str):
    if not accounting_allowed(request, "edit"):
        return HTMLResponse("Permission denied", status_code=403)
    lang = get_lang(request)
    conn = get_conn()
    try:
        voucher = get_voucher(conn, voucher_id)
        if not voucher or safe(voucher["voucher_type"]) != voucher_type:
            return HTMLResponse(tr(lang, "Voucher not found", "السند غير موجود"), status_code=404)
        if not voucher_can_modify(conn, voucher):
            return RedirectResponse(f"{route_base(voucher_type)}?msg=" + quote("Final posted vouchers cannot be edited."), status_code=302)
        values = dict(voucher)
        values["attachments"] = load_voucher_attachments(conn, voucher_id)
        return HTMLResponse(render_page(voucher_title(lang, voucher_type), render_form(lang, voucher_type, f"{route_base(voucher_type)}/{voucher_id}/edit", values), lang, current_path=request.url.path))
    finally:
        conn.close()


def update_voucher(
    request: Request,
    voucher_id: int,
    voucher_type: str,
    voucher_no: str,
    voucher_date: str,
    party_name: str,
    party_type: str,
    customer_id: str,
    vendor_id: str,
    employee_id: str,
    employee_trans_type: str,
    advance_id: str,
    custody_request_id: str,
    liquidity_account_code: str,
    counter_account_code: str,
    amount: str,
    description: str,
    signature_name: str,
    source_type: str = "",
    source_id: str = "",
    expense_payment_source: str = "",
    expense_employee_id: str = "",
    voucher_attachments=None,
):
    if not accounting_allowed(request, "edit"):
        return HTMLResponse("Permission denied", status_code=403)
    lang = get_lang(request)
    conn = get_conn()
    try:
        voucher = get_voucher(conn, voucher_id)
        if not voucher or safe(voucher["voucher_type"]) != voucher_type:
            return HTMLResponse(tr(lang, "Voucher not found", "السند غير موجود"), status_code=404)
        if not voucher_can_modify(conn, voucher):
            return RedirectResponse(f"{route_base(voucher_type)}?msg=" + quote("Final posted vouchers cannot be edited."), status_code=302)
        existing_attachments = load_voucher_attachments(conn, voucher_id)
        new_attachments = save_voucher_uploads(voucher_attachments)
        if voucher_type == "payment" and not existing_attachments and not new_attachments:
            raise Exception("Attachment is required for cash payment vouchers.")
        source_type = safe(source_type).lower()
        selected_source_id = safe_int(source_id)
        expense_payment_source = safe(expense_payment_source).lower() or "liquidity"
        if expense_payment_source in ("cash", "bank"):
            expense_payment_source = "liquidity"
        if expense_payment_source not in ("liquidity", "custody"):
            expense_payment_source = "liquidity"
        selected_expense_employee_id = safe_int(expense_employee_id)

        if source_type == "expense" and selected_source_id > 0:
            expense_row = conn.execute("SELECT * FROM expenses WHERE id = ? LIMIT 1", (selected_source_id,)).fetchone()
            if not expense_row:
                raise Exception("Linked expense not found")
            party_type = "other"
            party_name = f"Expense {safe(expense_row['expense_no'])}"
            counter_account_code = "EXPENSE-LINES"
            amount = str(float(expense_row["total_amount"] or 0))
            description = safe(description) or f"Pay expense {safe(expense_row['expense_no'])} - {safe(expense_row['description'])}"
            if expense_payment_source == "custody":
                if selected_expense_employee_id <= 0:
                    raise Exception("Please select the custody employee.")
                liquidity_account_code = employee_custody_account_code()
                available = employee_custody_balance(conn, selected_expense_employee_id)
                if available < q2(amount):
                    raise Exception(f"Employee custody balance is not enough. Available: {money(available)}")

        if source_type == "custody_return_request" and selected_source_id > 0:
            if voucher_type != "receipt":
                raise Exception("Custody return requests can only be received from cash receipts.")
            req = conn.execute(
                """
                SELECT *
                FROM employee_custody_return_requests
                WHERE id = ?
                  AND (
                        LOWER(COALESCE(status, 'active')) = 'active'
                        OR EXISTS (
                            SELECT 1 FROM cash_vouchers v
                            WHERE v.id = ?
                              AND LOWER(COALESCE(v.source_type,'')) = 'custody_return_request'
                              AND COALESCE(v.source_id, 0) = employee_custody_return_requests.id
                        )
                  )
                LIMIT 1
                """,
                (selected_source_id, voucher_id),
            ).fetchone()
            if not req:
                raise Exception("Selected custody return request is not pending.")
            party_type = "employee"
            employee_id = str(req["employee_id"] or "")
            employee_trans_type = "custody_return"
            amount = str(float(req["amount"] or 0))
            counter_account_code = employee_custody_account_code()
            description = safe(description) or f"{tr(lang, 'Receive custody return no.', 'استلام رد عهدة رقم')} {safe(req['request_no'])}"
            available = employee_custody_balance(conn, safe_int(employee_id))
            if available < q2(amount):
                raise Exception(f"Employee custody balance is not enough. Available: {money(available)}")

        party_type, party_id, party_name, counter_account_code = normalize_party_data(
            conn, voucher_type, party_type, party_name, customer_id, vendor_id, employee_id, employee_trans_type, counter_account_code
        )
        if source_type != "expense" and voucher_type == "payment" and party_type in ("vendor", "other") and expense_payment_source == "custody":
            if selected_expense_employee_id <= 0:
                raise Exception("Please select the employee whose custody will pay this vendor.")
            available = employee_custody_balance(conn, selected_expense_employee_id)
            if available < q2(amount):
                raise Exception(f"Employee custody balance is not enough. Available: {money(available)}")
            liquidity_account_code = employee_custody_account_code()
        validate_voucher(voucher_date, party_name, party_type, party_id, liquidity_account_code, counter_account_code, amount, source_type)
        remove_draft_voucher_journal(conn, voucher)
        conn.execute(
            """
            UPDATE cash_vouchers
            SET voucher_no = ?, voucher_date = ?, party_name = ?, party_type = ?, party_id = ?,
                liquidity_account_code = ?, counter_account_code = ?, amount = ?, description = ?,
                signature_name = ?, employee_trans_type = ?, advance_id = ?, custody_request_id = ?,
                source_type = ?, source_id = ?, expense_payment_source = ?, expense_employee_id = ?
            WHERE id = ?
            """,
            (
                safe(voucher_no) or safe(voucher["voucher_no"]),
                safe(voucher_date),
                party_name,
                party_type,
                party_id,
                safe(liquidity_account_code),
                safe(counter_account_code),
                float(q2(amount)),
                safe(description),
                safe(signature_name),
                safe(employee_trans_type) if party_type == "employee" else None,
                safe_int(advance_id) if party_type == "employee" and safe(employee_trans_type).lower() == "advance" else None,
                safe_int(custody_request_id) if party_type == "employee" and safe(employee_trans_type).lower() == "custody" else None,
                source_type or None,
                selected_source_id if source_type else None,
                expense_payment_source if (source_type == "expense" or voucher_type == "payment") else None,
                selected_expense_employee_id if expense_payment_source == "custody" else None,
                voucher_id,
            ),
        )
        insert_voucher_attachments(conn, voucher_id, new_attachments)
        create_draft_journal(conn, voucher_id)
        conn.commit()
        return RedirectResponse(f"{route_base(voucher_type)}/{voucher_id}", status_code=302)
    except Exception as e:
        conn.rollback()
        values = {
            "voucher_no": voucher_no,
            "voucher_date": voucher_date,
            "party_name": party_name,
            "party_type": party_type,
            "party_id": employee_id or customer_id or vendor_id,
            "employee_trans_type": employee_trans_type,
            "advance_id": advance_id,
            "custody_request_id": custody_request_id,
            "liquidity_account_code": liquidity_account_code,
            "counter_account_code": counter_account_code,
            "amount": amount,
            "description": description,
            "signature_name": signature_name,
            "source_type": source_type,
            "source_id": source_id,
            "expense_payment_source": expense_payment_source,
            "expense_employee_id": expense_employee_id,
            "attachments": load_voucher_attachments(conn, voucher_id),
        }
        return HTMLResponse(render_page(voucher_title(lang, voucher_type), render_form(lang, voucher_type, f"{route_base(voucher_type)}/{voucher_id}/edit", values, escape(str(e))), lang, current_path=request.url.path), status_code=400)
    finally:
        conn.close()


def delete_draft_voucher(request: Request, voucher_id: int, voucher_type: str):
    if not accounting_allowed(request, "delete"):
        return HTMLResponse("Permission denied", status_code=403)
    conn = get_conn()
    try:
        voucher = get_voucher(conn, voucher_id)
        if not voucher or safe(voucher["voucher_type"]) != voucher_type:
            return RedirectResponse(
                f"{route_base(voucher_type)}?msg=" + quote("Voucher is already deleted or not found."),
                status_code=302,
            )
        if not voucher_can_modify(conn, voucher):
            return RedirectResponse(f"{route_base(voucher_type)}?msg=" + quote("Final posted vouchers cannot be deleted."), status_code=302)
        remove_draft_voucher_journal(conn, voucher)
        reset_draft_voucher_source(conn, voucher)
        conn.execute("DELETE FROM cash_voucher_attachments WHERE voucher_id = ?", (voucher_id,))
        conn.execute("DELETE FROM cash_vouchers WHERE id = ?", (voucher_id,))
        conn.commit()
        return RedirectResponse(f"{route_base(voucher_type)}?msg=" + quote("Draft voucher deleted."), status_code=302)
    except Exception as e:
        conn.rollback()
        return HTMLResponse(str(e), status_code=400)
    finally:
        conn.close()


@router.get("/ui/accounting/cash-receipts/{voucher_id}/edit", response_class=HTMLResponse)
def edit_cash_receipt(request: Request, voucher_id: int):
    return edit_voucher_page(request, voucher_id, "receipt")


@router.get("/ui/accounting/cash-payments/{voucher_id}/edit", response_class=HTMLResponse)
def edit_cash_payment(request: Request, voucher_id: int):
    return edit_voucher_page(request, voucher_id, "payment")


@router.post("/ui/accounting/cash-receipts/{voucher_id}/edit")
def update_cash_receipt(
    request: Request,
    voucher_id: int,
    voucher_no: str = Form(""),
    voucher_date: str = Form(""),
    party_name: str = Form(""),
    party_type: str = Form("other"),
    customer_id: str = Form(""),
    vendor_id: str = Form(""),
    employee_id: str = Form(""),
    employee_trans_type: str = Form(""),
    advance_id: str = Form(""),
    custody_request_id: str = Form(""),
    liquidity_account_code: str = Form(""),
    counter_account_code: str = Form(""),
    amount: str = Form("0"),
    description: str = Form(""),
    signature_name: str = Form(""),
    source_type: str = Form(""),
    source_id: str = Form(""),
    expense_payment_source: str = Form(""),
    expense_employee_id: str = Form(""),
    voucher_attachments: list[UploadFile] = File(None),
):
    return update_voucher(request, voucher_id, "receipt", voucher_no, voucher_date, party_name, party_type, customer_id, vendor_id, employee_id, employee_trans_type, advance_id, custody_request_id, liquidity_account_code, counter_account_code, amount, description, signature_name, source_type, source_id, expense_payment_source, expense_employee_id, voucher_attachments)


@router.post("/ui/accounting/cash-payments/{voucher_id}/edit")
def update_cash_payment(
    request: Request,
    voucher_id: int,
    voucher_no: str = Form(""),
    voucher_date: str = Form(""),
    party_name: str = Form(""),
    party_type: str = Form("other"),
    customer_id: str = Form(""),
    vendor_id: str = Form(""),
    employee_id: str = Form(""),
    employee_trans_type: str = Form(""),
    advance_id: str = Form(""),
    custody_request_id: str = Form(""),
    liquidity_account_code: str = Form(""),
    counter_account_code: str = Form(""),
    amount: str = Form("0"),
    description: str = Form(""),
    signature_name: str = Form(""),
    source_type: str = Form(""),
    source_id: str = Form(""),
    expense_payment_source: str = Form(""),
    expense_employee_id: str = Form(""),
    voucher_attachments: list[UploadFile] = File(None),
):
    return update_voucher(request, voucher_id, "payment", voucher_no, voucher_date, party_name, party_type, customer_id, vendor_id, employee_id, employee_trans_type, advance_id, custody_request_id, liquidity_account_code, counter_account_code, amount, description, signature_name, source_type, source_id, expense_payment_source, expense_employee_id, voucher_attachments)


@router.post("/ui/accounting/cash-receipts/{voucher_id}/delete")
def delete_cash_receipt(request: Request, voucher_id: int):
    return delete_draft_voucher(request, voucher_id, "receipt")


@router.post("/ui/accounting/cash-payments/{voucher_id}/delete")
def delete_cash_payment(request: Request, voucher_id: int):
    return delete_draft_voucher(request, voucher_id, "payment")


def allocated_documents_for_voucher(conn, voucher):
    payment_type = voucher_payment_type(voucher)
    if not payment_type:
        return []
    return [dict(row) for row in get_payment_allocations(conn, payment_type, voucher["id"])]


def refresh_documents_after_allocation_delete(conn, allocated_rows):
    for row in allocated_rows:
        if safe(row["document_type"]) == "customer_invoice":
            refresh_customer_invoice_payment_status(conn, row["document_id"])
        elif safe(row["document_type"]) == "vendor_bill":
            refresh_vendor_bill_payment_status(conn, row["document_id"])


def set_voucher_status(request: Request, voucher_id: int, voucher_type: str, action: str):
    if not accounting_allowed(request, "post"):
        return HTMLResponse("Permission denied", status_code=403)
    conn = get_conn()
    voucher = get_voucher(conn, voucher_id)
    if not voucher or safe(voucher["voucher_type"]) != voucher_type:
        conn.close()
        return RedirectResponse(
            f"{route_base(voucher_type)}?msg=" + quote("Voucher is already deleted or not found."),
            status_code=302,
        )
    try:
        if action == "post":
            journal_status = voucher_journal_status(conn, voucher)
            if safe(voucher["status"]).lower() == "reversed" or journal_status == "posted":
                raise Exception("Only unposted vouchers can be sent to journal")
            if not safe_int(voucher["journal_id"]):
                create_draft_journal(conn, voucher_id)
                voucher = get_voucher(conn, voucher_id)
                journal_status = voucher_journal_status(conn, voucher)
            if journal_status in ("", "draft"):
                submit_journal_for_final_post(conn, voucher["journal_id"])
            conn.execute("UPDATE cash_vouchers SET status = 'pending_final_post' WHERE id = ?", (voucher_id,))
        else:
            if voucher_display_status(conn, voucher) != "posted":
                raise Exception("Only posted vouchers can be reversed")
            allocated_rows = allocated_documents_for_voucher(conn, voucher)
            payment_type = voucher_payment_type(voucher)
            if payment_type:
                delete_payment_allocations(conn, payment_type, voucher["id"])
            refresh_documents_after_allocation_delete(conn, allocated_rows)
            reverse_id = reverse_journal_entry(conn, voucher["journal_id"])
            conn.execute("UPDATE cash_vouchers SET status = 'reversed', reversed_journal_id = ? WHERE id = ?", (reverse_id, voucher_id))
            
            # Revert employee advance status if linked
            if safe(voucher["party_type"]).lower() == "employee" and safe(voucher["employee_trans_type"]).lower() == "advance" and voucher["advance_id"]:
                conn.execute("UPDATE employee_advances SET status = 'active' WHERE id = ?", (voucher["advance_id"],))
            if safe(voucher["party_type"]).lower() == "employee" and safe(voucher["employee_trans_type"]).lower() == "custody" and safe_int(voucher["custody_request_id"]) > 0:
                conn.execute("UPDATE employee_custody_requests SET status = 'active' WHERE id = ?", (voucher["custody_request_id"],))
            if safe(voucher["source_type"]).lower() == "expense" and safe_int(voucher["source_id"]) > 0:
                conn.execute(
                    """
                    UPDATE expenses
                    SET status = 'pending_payment',
                        reversed_journal_id = ?
                    WHERE id = ?
                    """,
                    (reverse_id, safe_int(voucher["source_id"])),
                )
            if safe(voucher["source_type"]).lower() == "employee_grant" and safe_int(voucher["source_id"]) > 0:
                conn.execute(
                    "UPDATE employee_grants SET status = 'draft', payment_voucher_id = NULL WHERE id = ?",
                    (safe_int(voucher["source_id"]),),
                )
            if safe(voucher["source_type"]).lower() == "custody_return_request" and safe_int(voucher["source_id"]) > 0:
                conn.execute(
                    "UPDATE employee_custody_return_requests SET status = 'active' WHERE id = ?",
                    (safe_int(voucher["source_id"]),),
                )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(str(e), status_code=400)
    conn.close()
    return RedirectResponse(f"{route_base(voucher_type)}/{voucher_id}", status_code=302)


@router.post("/ui/accounting/cash-receipts/{voucher_id}/post")
def post_cash_receipt(request: Request, voucher_id: int):
    return set_voucher_status(request, voucher_id, "receipt", "post")


@router.post("/ui/accounting/cash-payments/{voucher_id}/post")
def post_cash_payment(request: Request, voucher_id: int):
    return set_voucher_status(request, voucher_id, "payment", "post")


@router.post("/ui/accounting/cash-receipts/{voucher_id}/reverse")
def reverse_cash_receipt(request: Request, voucher_id: int):
    return set_voucher_status(request, voucher_id, "receipt", "reverse")


@router.post("/ui/accounting/cash-payments/{voucher_id}/reverse")
def reverse_cash_payment(request: Request, voucher_id: int):
    return set_voucher_status(request, voucher_id, "payment", "reverse")


def print_voucher_page(request: Request, voucher_id: int, voucher_type: str):
    if not accounting_allowed(request, "view"):
        return HTMLResponse("Permission denied", status_code=403)
    lang = get_lang(request)
    conn = get_conn()
    voucher = get_voucher(conn, voucher_id)
    conn.close()
    if not voucher or safe(voucher["voucher_type"]) != voucher_type:
        return HTMLResponse(tr(lang, "Voucher not found", "ط§ظ„ط³ظ†ط¯ ط؛ظٹط± ظ…ظˆط¬ظˆط¯"), status_code=404)
    html = f"""
    <!DOCTYPE html>
    <html lang="{lang}" dir="{'rtl' if lang == 'ar' else 'ltr'}">
    <head>
        <meta charset="UTF-8">
        <title>{voucher_title(lang, voucher_type)}</title>
        <style>
            body {{ font-family: Arial, sans-serif; color: #17355c; padding: 24px; }}
            .sheet {{ max-width: 900px; margin: 0 auto; border: 1px solid #dfe6f1; border-radius: 16px; padding: 28px; }}
            .grid {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 14px; }}
            .box {{ border:1px solid #dfe6f1; border-radius: 12px; padding: 12px 14px; min-height: 74px; }}
            .label {{ font-size:12px; color:#678; margin-bottom:6px; }}
            .value {{ font-size:18px; font-weight:700; }}
            .wide {{ grid-column:1 / -1; }}
            .signatures {{ display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:24px; margin-top:48px; }}
            .sig-line {{ border-top:1px solid #1e3556; margin-top:54px; padding-top:8px; text-align:center; }}
            .print-bar {{ margin-bottom:16px; }}
            @media print {{ .print-bar {{ display:none; }} body {{ padding:0; }} .sheet {{ border:none; }} }}
        </style>
    </head>
    <body>
        <div class="print-bar"><button onclick="window.print()">{tr(lang, 'Print', 'ط·ط¨ط§ط¹ط©')}</button></div>
        <div class="sheet">
            <h1>{voucher_title(lang, voucher_type)}</h1>
            <div class="grid">
                <div class="box"><div class="label">{tr(lang, 'Voucher No', 'ط±ظ‚ظ… ط§ظ„ط³ظ†ط¯')}</div><div class="value">{safe(voucher['voucher_no'])}</div></div>
                <div class="box"><div class="label">{tr(lang, 'Date', 'ط§ظ„طھط§ط±ظٹط®')}</div><div class="value">{safe(voucher['voucher_date'])}</div></div>
                <div class="box"><div class="label">{party_label(lang, voucher_type)}</div><div class="value">{safe(voucher['party_name'])}</div></div>
                <div class="box"><div class="label">{tr(lang, 'Party Type', 'ظ†ظˆط¹ ط§ظ„ط·ط±ظپ')}</div><div class="value">{safe(voucher['party_type']).title()}</div></div>
                <div class="box"><div class="label">{tr(lang, 'Cash / Bank Account', 'ط­ط³ط§ط¨ ط§ظ„ظ†ظ‚ط¯ظٹط© / ط§ظ„ط¨ظ†ظƒ')}</div><div class="value">{account_display(voucher['liquidity_account_code'])}</div></div>
                <div class="box"><div class="label">{tr(lang, 'Counter Account', 'ط§ظ„ط­ط³ط§ط¨ ط§ظ„ظ…ظ‚ط§ط¨ظ„')}</div><div class="value">{account_display(voucher['counter_account_code'])}</div></div>
                <div class="box"><div class="label">{tr(lang, 'Amount', 'ط§ظ„ظ…ط¨ظ„ط؛')}</div><div class="value">{money(voucher['amount'])}</div></div>
                <div class="box"><div class="label">{signature_label(lang, voucher_type)}</div><div class="value">{safe(voucher['signature_name'])}</div></div>
                <div class="box wide"><div class="label">{tr(lang, 'Description', 'ط§ظ„ط¨ظٹط§ظ†')}</div><div class="value">{safe(voucher['description'])}</div></div>
                <div class="box wide"><div class="label">{tr(lang, 'Amount in Words', 'ط§ظ„ظ…ط¨ظ„ط؛ ظƒطھط§ط¨ط©')}</div><div class="value">{amount_in_words(voucher['amount'])}</div></div>
            </div>
            <div class="signatures">
                <div class="sig-line">{tr(lang, 'Prepared By', 'ط§ظ„ظ…ط­ط§ط³ط¨')}</div>
                <div class="sig-line">{signature_label(lang, voucher_type)}</div>
                <div class="sig-line">{tr(lang, 'Approved By', 'ط§ظ„ط§ط¹طھظ…ط§ط¯')}</div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)


@router.get("/ui/accounting/cash-receipts/{voucher_id}/print", response_class=HTMLResponse)
def print_cash_receipt(request: Request, voucher_id: int):
    return print_voucher_page(request, voucher_id, "receipt")


@router.get("/ui/accounting/cash-payments/{voucher_id}/print", response_class=HTMLResponse)
def print_cash_payment(request: Request, voucher_id: int):
    return print_voucher_page(request, voucher_id, "payment")
