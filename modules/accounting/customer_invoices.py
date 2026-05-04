from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from audit import actor_name_from_request, render_audit_log_card, safe_log_action
from auth import can
from db import get_conn
from i18n import get_lang
from layout import render_page
from modules.accounting.allocation_engine import (
    create_payment_allocation,
    get_allocated_total_for_document,
    get_document_allocations,
    get_payment_unallocated_amount,
    refresh_customer_invoice_payment_status,
)
from modules.accounting.accounting_engine import (
    create_journal_entry,
    post_journal_entry,
    submit_journal_for_final_post,
    reverse_journal_entry,
    delete_draft_journal_entry,
)
from modules.accounting.invoice_ai import (
    ai_upload_card,
    attachment_from_form,
    attachment_gallery,
    attachments_from_form,
    find_partner_id,
    missing_partner_card,
    parse_invoice_upload,
    save_uploaded_invoice,
)

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


def dec_str(value, places=7):
    try:
        d = Decimal(str(value or 0))
    except Exception:
        d = Decimal("0")
    q = Decimal("1." + ("0" * places))
    d = d.quantize(q, rounding=ROUND_HALF_UP)
    return f"{d:.{places}f}"


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


def get_columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [r["name"] for r in rows]


def table_has_column(conn, table_name, column_name):
    return column_name in get_columns(conn, table_name)


def invoice_line_fk_column(conn):
    cols = get_columns(conn, "customer_invoice_lines")
    if "invoice_id" in cols:
        return "invoice_id"
    return "bill_id"


# =========================================================
# SETTINGS
# =========================================================
def get_setting_value(key: str, default=None):
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT value
            FROM accounting_settings
            WHERE key = ?
            LIMIT 1
        """, (key,)).fetchone()
        if row and row["value"] not in [None, ""]:
            return row["value"]
    except Exception:
        pass
    finally:
        conn.close()

    fallback = {
        "customer_invoice_prefix": "INV",
        "customer_control_account": "112100",
        "sales_revenue_account_code": "410000",
        "output_vat_account": "201020110",
        "wht_receivable_account": "1020502",
    }
    return fallback.get(key, default)


# =========================================================
# DB SCHEMA + MIGRATION
# =========================================================
def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT,
            invoice_date TEXT,
            due_date TEXT,
            customer_id INTEGER,
            customer_name TEXT,
            description TEXT,
            attachment_url TEXT,
            attachment_name TEXT,
            payment_term_days INTEGER DEFAULT 0,
            subtotal REAL DEFAULT 0,
            vat_rate REAL DEFAULT 14,
            vat_amount REAL DEFAULT 0,
            wht_rate REAL DEFAULT 0,
            wht_amount REAL DEFAULT 0,
            total_amount REAL DEFAULT 0,
            net_amount REAL DEFAULT 0,
            payment_status TEXT DEFAULT 'unpaid',
            status TEXT DEFAULT 'draft',
            journal_id INTEGER,
            reversed_journal_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_invoice_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL,
            line_no INTEGER DEFAULT 1,
            item_description TEXT,
            account_code TEXT,
            qty REAL DEFAULT 0,
            unit_price REAL DEFAULT 0,
            line_amount REAL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_invoice_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            file_url TEXT NOT NULL,
            file_name TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS partners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            partner_type TEXT DEFAULT 'customer',
            phone TEXT,
            email TEXT,
            address TEXT,
            payment_term_days INTEGER DEFAULT 0,
            opening_balance REAL DEFAULT 0,
            account_code TEXT,
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
            level1 TEXT,
            level2 TEXT,
            statement_type TEXT,
            is_group INTEGER DEFAULT 0,
            allow_posting INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1
        )
    """)

    ensure_column(conn, "customer_invoices", "invoice_no", "ALTER TABLE customer_invoices ADD COLUMN invoice_no TEXT")
    ensure_column(conn, "customer_invoices", "invoice_date", "ALTER TABLE customer_invoices ADD COLUMN invoice_date TEXT")
    ensure_column(conn, "customer_invoices", "due_date", "ALTER TABLE customer_invoices ADD COLUMN due_date TEXT")
    ensure_column(conn, "customer_invoices", "customer_id", "ALTER TABLE customer_invoices ADD COLUMN customer_id INTEGER")
    ensure_column(conn, "customer_invoices", "customer_name", "ALTER TABLE customer_invoices ADD COLUMN customer_name TEXT")
    ensure_column(conn, "customer_invoices", "description", "ALTER TABLE customer_invoices ADD COLUMN description TEXT")
    ensure_column(conn, "customer_invoices", "attachment_url", "ALTER TABLE customer_invoices ADD COLUMN attachment_url TEXT")
    ensure_column(conn, "customer_invoices", "attachment_name", "ALTER TABLE customer_invoices ADD COLUMN attachment_name TEXT")
    ensure_column(conn, "customer_invoices", "payment_term_days", "ALTER TABLE customer_invoices ADD COLUMN payment_term_days INTEGER DEFAULT 0")
    ensure_column(conn, "customer_invoices", "subtotal", "ALTER TABLE customer_invoices ADD COLUMN subtotal REAL DEFAULT 0")
    ensure_column(conn, "customer_invoices", "vat_rate", "ALTER TABLE customer_invoices ADD COLUMN vat_rate REAL DEFAULT 14")
    ensure_column(conn, "customer_invoices", "vat_amount", "ALTER TABLE customer_invoices ADD COLUMN vat_amount REAL DEFAULT 0")
    ensure_column(conn, "customer_invoices", "wht_rate", "ALTER TABLE customer_invoices ADD COLUMN wht_rate REAL DEFAULT 0")
    ensure_column(conn, "customer_invoices", "wht_amount", "ALTER TABLE customer_invoices ADD COLUMN wht_amount REAL DEFAULT 0")
    ensure_column(conn, "customer_invoices", "total_amount", "ALTER TABLE customer_invoices ADD COLUMN total_amount REAL DEFAULT 0")
    ensure_column(conn, "customer_invoices", "net_amount", "ALTER TABLE customer_invoices ADD COLUMN net_amount REAL DEFAULT 0")
    ensure_column(conn, "customer_invoices", "payment_status", "ALTER TABLE customer_invoices ADD COLUMN payment_status TEXT DEFAULT 'unpaid'")
    ensure_column(conn, "customer_invoices", "status", "ALTER TABLE customer_invoices ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "customer_invoices", "journal_id", "ALTER TABLE customer_invoices ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "customer_invoices", "reversed_journal_id", "ALTER TABLE customer_invoices ADD COLUMN reversed_journal_id INTEGER")
    ensure_column(conn, "customer_invoices", "created_at", "ALTER TABLE customer_invoices ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "customer_invoice_lines", "bill_id", "ALTER TABLE customer_invoice_lines ADD COLUMN bill_id INTEGER")
    ensure_column(conn, "customer_invoice_lines", "line_no", "ALTER TABLE customer_invoice_lines ADD COLUMN line_no INTEGER DEFAULT 1")
    ensure_column(conn, "customer_invoice_lines", "item_description", "ALTER TABLE customer_invoice_lines ADD COLUMN item_description TEXT")
    ensure_column(conn, "customer_invoice_lines", "account_code", "ALTER TABLE customer_invoice_lines ADD COLUMN account_code TEXT")
    ensure_column(conn, "customer_invoice_lines", "qty", "ALTER TABLE customer_invoice_lines ADD COLUMN qty REAL DEFAULT 0")
    ensure_column(conn, "customer_invoice_lines", "unit_price", "ALTER TABLE customer_invoice_lines ADD COLUMN unit_price REAL DEFAULT 0")
    ensure_column(conn, "customer_invoice_lines", "line_amount", "ALTER TABLE customer_invoice_lines ADD COLUMN line_amount REAL DEFAULT 0")

    if table_has_column(conn, "customer_invoice_lines", "invoice_id"):
        conn.execute("""
            UPDATE customer_invoice_lines
            SET bill_id = invoice_id
            WHERE bill_id IS NULL
              AND invoice_id IS NOT NULL
        """)

    if table_has_column(conn, "customer_invoice_lines", "description"):
        conn.execute("""
            UPDATE customer_invoice_lines
            SET item_description = description
            WHERE COALESCE(item_description, '') = ''
              AND COALESCE(description, '') <> ''
        """)

    conn.commit()
    conn.close()


# =========================================================
# MASTER HELPERS
# =========================================================
def next_invoice_no():
    prefix = get_setting_value("customer_invoice_prefix", "INV")
    conn = get_conn()
    row = conn.execute("""
        SELECT invoice_no
        FROM customer_invoices
        WHERE COALESCE(invoice_no, '') <> ''
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["invoice_no"]:
        return f"{prefix}-0000001"

    last = str(row["invoice_no"])
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0

    return f"{prefix}-{num + 1:07d}"


def customer_rows():
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, code, name, payment_term_days, account_code
        FROM partners
        WHERE partner_type = 'customer'
          AND COALESCE(is_active, 1) = 1
        ORDER BY name
    """).fetchall()
    conn.close()
    return rows


def customer_options(selected_id=None):
    html = "<option value=''>-- Select Customer --</option>"
    for r in customer_rows():
        selected = "selected" if str(selected_id or "") == str(r["id"]) else ""
        label = f"{safe(r['code'])} - {safe(r['name'])}" if safe(r["code"]) else safe(r["name"])
        html += (
            f"<option value='{r['id']}' "
            f"data-payment-term='{safe_int(r['payment_term_days'], 0)}' "
            f"data-name='{safe(r['name'])}' "
            f"{selected}>{label}</option>"
        )
    return html


def account_options(selected_code=""):
    conn = get_conn()
    rows = conn.execute("""
        SELECT code, name
        FROM accounts
        WHERE COALESCE(is_active, 1) = 1
          AND COALESCE(is_group, 0) = 0
          AND COALESCE(allow_posting, 1) = 1
        ORDER BY code, name
    """).fetchall()
    conn.close()

    html = "<option value=''>-- Select Account --</option>"
    for row in rows:
        sel = "selected" if safe(selected_code) == safe(row["code"]) else ""
        html += f"<option value='{safe(row['code'])}' {sel}>{safe(row['code'])} - {safe(row['name'])}</option>"
    return html


def account_label(conn, code):
    code = safe(code)
    if not code:
        return ""
    row = conn.execute("SELECT code, name FROM accounts WHERE code = ? LIMIT 1", (code,)).fetchone()
    if row:
        return f"{safe(row['code'])} - {safe(row['name'])}"
    return code


def get_customer(conn, customer_id: int):
    return conn.execute("""
        SELECT *
        FROM partners
        WHERE id = ?
          AND partner_type = 'customer'
        LIMIT 1
    """, (customer_id,)).fetchone()


def invoice_can_edit_before_final(conn, row) -> bool:
    doc_status = safe(row["status"]).lower()
    if doc_status == "draft":
        return True
    if doc_status != "posted" or not row["journal_id"]:
        return False
    journal = conn.execute("SELECT status FROM journal_entries WHERE id = ? LIMIT 1", (row["journal_id"],)).fetchone()
    return bool(journal and safe(journal["status"]).lower() == "pending_final_post")


def invoice_journal_final_posted(conn, row) -> bool:
    if not row["journal_id"]:
        return False
    journal = conn.execute("SELECT status FROM journal_entries WHERE id = ? LIMIT 1", (row["journal_id"],)).fetchone()
    return bool(journal and safe(journal["status"]).lower() == "posted")


def calc_due_date(invoice_date: str, payment_term_days) -> str:
    try:
        invoice_date_obj = datetime.fromisoformat(safe(invoice_date)).date()
        return (invoice_date_obj + timedelta(days=safe_int(payment_term_days, 0))).isoformat()
    except Exception:
        return safe(invoice_date)


def available_customer_cash_receipts(conn, customer_id: int):
    result = []

    payment_rows = conn.execute(
        """
        SELECT *
        FROM customer_payments
        WHERE COALESCE(customer_id, 0) = ?
          AND LOWER(COALESCE(status,'')) = 'posted'
        ORDER BY payment_date DESC, id DESC
        """,
        (customer_id,),
    ).fetchall()
    for row in payment_rows:
        unapplied = get_payment_unallocated_amount(conn, "customer_payment", row["id"])
        if unapplied > Decimal("0.00"):
            label = f"{safe(row['payment_no'])} | {safe(row['payment_date'])} | Customer Payment | Available {money(unapplied)}"
            result.append(("customer_payment", row, unapplied, label))

    rows = conn.execute(
        """
        SELECT *
        FROM cash_vouchers
        WHERE LOWER(COALESCE(voucher_type,'')) = 'receipt'
          AND LOWER(COALESCE(party_type,'')) = 'customer'
          AND COALESCE(party_id, 0) = ?
          AND LOWER(COALESCE(status,'')) = 'posted'
        ORDER BY voucher_date DESC, id DESC
        """,
        (customer_id,),
    ).fetchall()
    for row in rows:
        unapplied = get_payment_unallocated_amount(conn, "cash_receipt", row["id"])
        if unapplied > Decimal("0.00"):
            label = f"{safe(row['voucher_no'])} | {safe(row['voucher_date'])} | Cash Receipt | Available {money(unapplied)}"
            result.append(("cash_receipt", row, unapplied, label))

    opening_rows = conn.execute(
        """
        SELECT
            l.id,
            j.entry_date,
            j.entry_no,
            j.reference,
            COALESCE(NULLIF(l.line_description,''), NULLIF(j.description,''), '') AS description
        FROM journal_lines l
        JOIN journal_entries j ON j.id = l.journal_id
        WHERE LOWER(COALESCE(j.status,'')) = 'posted'
          AND LOWER(COALESCE(l.partner_type,'')) = 'customer'
          AND COALESCE(l.partner_id, 0) = ?
          AND COALESCE(l.credit, 0) > COALESCE(l.debit, 0)
        ORDER BY j.entry_date DESC, j.id DESC, COALESCE(l.line_no,0), l.id DESC
        """,
        (customer_id,),
    ).fetchall()
    for row in opening_rows:
        unapplied = get_payment_unallocated_amount(conn, "customer_opening_journal", row["id"])
        if unapplied > Decimal("0.00"):
            label = f"{safe(row['entry_no'])} | {safe(row['entry_date'])} | Opening Journal | Available {money(unapplied)}"
            result.append(("customer_opening_journal", row, unapplied, label))

    return result


# =========================================================
# LINE / TOTAL HELPERS
# =========================================================
def normalize_lines_from_form(form):
    descriptions = form.getlist("line_description")
    account_codes = form.getlist("line_account_code")
    qtys = form.getlist("line_qty")
    prices = form.getlist("line_unit_price")

    lines = []
    max_len = max(len(descriptions), len(account_codes), len(qtys), len(prices), 0)

    for i in range(max_len):
        desc = safe(descriptions[i]) if i < len(descriptions) else ""
        account_code = safe(account_codes[i]) if i < len(account_codes) else ""
        qty = to_decimal(qtys[i] if i < len(qtys) else "0")
        unit_price = to_decimal(prices[i] if i < len(prices) else "0")

        if desc == "" and account_code == "" and qty == Decimal("0") and unit_price == Decimal("0"):
            continue

        line_amount = qty * unit_price

        lines.append({
            "line_no": i + 1,
            "item_description": desc,
            "account_code": account_code,
            "qty": qty,
            "unit_price": unit_price,
            "line_amount": line_amount,
        })

    return lines


def calculate_invoice_totals(lines, vat_rate, wht_rate):
    subtotal = sum((line["line_amount"] for line in lines), Decimal("0"))
    vat_rate_dec = to_decimal(vat_rate)
    wht_rate_dec = to_decimal(wht_rate)

    vat_amount = subtotal * vat_rate_dec / Decimal("100")
    total_amount = subtotal + vat_amount
    wht_amount = subtotal * wht_rate_dec / Decimal("100")
    net_amount = total_amount - wht_amount

    return {
        "subtotal": subtotal.quantize(Decimal("1.00"), rounding=ROUND_HALF_UP),
        "vat_rate": vat_rate_dec,
        "vat_amount": vat_amount.quantize(Decimal("1.00"), rounding=ROUND_HALF_UP),
        "wht_rate": wht_rate_dec,
        "wht_amount": wht_amount.quantize(Decimal("1.00"), rounding=ROUND_HALF_UP),
        "total_amount": total_amount.quantize(Decimal("1.00"), rounding=ROUND_HALF_UP),
        "net_amount": net_amount.quantize(Decimal("1.00"), rounding=ROUND_HALF_UP),
    }


# =========================================================
# JOURNAL VIA ENGINE
# =========================================================
def build_customer_invoice_journal_lines(bill, lines_rows, customer):
    customer_account = safe(customer["account_code"]) or safe(get_setting_value("customer_control_account", "112100"))
    output_vat_account = safe(get_setting_value("output_vat_account", "212100"))
    wht_receivable_account = safe(get_setting_value("wht_receivable_account", "1020502"))

    if not customer_account:
        raise Exception("Customer control account is missing")

    posting_lines = []

    for line in lines_rows:
        account_code = safe(line["account_code"])
        if not account_code:
            raise Exception(f"Account is required on bill line #{line['line_no']}")

        amount = Decimal(str(line["line_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
        if amount <= Decimal("0"):
            continue

        posting_lines.append({
            "description": safe(line["item_description"]) or f"Customer invoice line {line['line_no']}",
            "account_code": account_code,
            "debit": Decimal("0.00"),
            "credit": amount,
            "partner_type": "customer",
            "partner_id": bill["customer_id"],
        })

    vat_amount = Decimal(str(bill["vat_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    if vat_amount > Decimal("0"):
        posting_lines.append({
            "description": f"Output VAT for {bill['invoice_no']}",
            "account_code": output_vat_account,
            "debit": Decimal("0.00"),
            "credit": vat_amount,
            "partner_type": "customer",
            "partner_id": bill["customer_id"],
        })

    net_amount = Decimal(str(bill["net_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    posting_lines.append({
        "description": f"Customer receivable for {bill['invoice_no']}",
        "account_code": customer_account,
        "debit": net_amount,
        "credit": Decimal("0.00"),
        "partner_type": "customer",
        "partner_id": bill["customer_id"],
    })

    wht_amount = Decimal(str(bill["wht_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    if wht_amount > Decimal("0"):
        posting_lines.append({
            "description": f"WHT receivable for {bill['invoice_no']}",
            "account_code": wht_receivable_account,
            "debit": wht_amount,
            "credit": Decimal("0.00"),
            "partner_type": "customer",
            "partner_id": bill["customer_id"],
        })

    total_debit = sum((Decimal(str(x["debit"])) for x in posting_lines), Decimal("0")).quantize(Decimal("1.00"))
    total_credit = sum((Decimal(str(x["credit"])) for x in posting_lines), Decimal("0")).quantize(Decimal("1.00"))

    if total_debit != total_credit:
        raise Exception(f"Journal not balanced: DR={total_debit}, CR={total_credit}")

    return posting_lines


def create_customer_invoice_draft_journal(conn, bill_id: int):
    bill = conn.execute("""
        SELECT *
        FROM customer_invoices
        WHERE id = ?
        LIMIT 1
    """, (bill_id,)).fetchone()

    if not bill:
        raise Exception("Customer invoice not found")

    customer = get_customer(conn, bill["customer_id"])
    if not customer:
        raise Exception("Customer not found")

    fk_col = invoice_line_fk_column(conn)
    lines_rows = conn.execute(f"""
        SELECT *
        FROM customer_invoice_lines
        WHERE {fk_col} = ?
        ORDER BY line_no, id
    """, (bill_id,)).fetchall()

    if not lines_rows:
        raise Exception("Customer invoice has no lines")

    journal_lines = build_customer_invoice_journal_lines(bill, lines_rows, customer)

    journal_id = create_journal_entry(
        conn=conn,
        entry_date=bill["invoice_date"],
        description=f"Customer Invoice {bill['invoice_no']} - {bill['customer_name']}",
        reference=bill["invoice_no"],
        source_type="customer_bill",
        source_id=bill["id"],
        lines=journal_lines,
    )

    conn.execute("""
        UPDATE customer_invoices
        SET journal_id = ?
        WHERE id = ?
    """, (journal_id, bill_id))

    return journal_id


# =========================================================
# UI HELPERS
# =========================================================
def render_lines_table(lines=None, readonly=False):
    lines = lines or [
        {
            "item_description": "",
            "account_code": "",
            "qty": Decimal("1"),
            "unit_price": Decimal("0"),
            "line_amount": Decimal("0"),
        }
    ]

    read_attr = "readonly" if readonly else ""
    select_disabled = "disabled" if readonly else ""
    remove_btn = "" if readonly else '<button type="button" class="btn red" onclick="removeLine(this)">Remove</button>'

    body = ""
    for idx, line in enumerate(lines, start=1):
        body += f"""
        <tr>
            <td>{idx}</td>
            <td>
                <input type="text" name="line_description" value="{safe(line.get('item_description', ''))}" placeholder="Description" {read_attr}>
            </td>
            <td>
                <select name="line_account_code" class="line-account" {select_disabled}>
                    {account_options(safe(line.get('account_code', '')))}
                </select>
                {"<input type='hidden' name='line_account_code' value='%s'>" % safe(line.get('account_code', '')) if readonly else ""}
            </td>
            <td>
                <input type="text" inputmode="decimal" name="line_qty" value="{dec_str(line.get('qty', '1'), 7)}" class="line-qty" {read_attr}>
            </td>
            <td>
                <input type="text" inputmode="decimal" name="line_unit_price" value="{dec_str(line.get('unit_price', '0'), 7)}" class="line-price" {read_attr}>
            </td>
            <td>
                <input type="text" value="{dec_str(line.get('line_amount', '0'), 7)}" class="line-amount" readonly>
            </td>
            <td>{remove_btn}</td>
        </tr>
        """
    return body


def customer_invoice_form(values=None, row_id=None, lines=None, readonly=False):
    values = values or {}
    lines = lines or []

    invoice_date = safe(values.get("invoice_date", ""))
    payment_term_days = safe_int(values.get("payment_term_days", 0), 0)
    due_date = safe(values.get("due_date", "")) or calc_due_date(invoice_date, payment_term_days)

    action = f"/ui/accounting/customer-invoices/{row_id}/edit" if row_id else "/ui/accounting/customer-invoices/new"
    form_title = "View Customer Invoice" if readonly else ("Edit Customer Invoice" if row_id else "New Customer Invoice")
    text_readonly = "readonly" if readonly else ""
    select_disabled = "disabled" if readonly else ""
    add_line_button = "" if readonly else '<button type="button" class="btn green" onclick="addLine()">+ Add Line</button>'
    save_button = "" if readonly else '<button class="btn green" type="submit">Save Draft</button>'
    upload_card = ""
    ai_notice = safe(values.get("ai_notice", ""))
    ai_notice_class = "error" if ai_notice.lower().startswith("ai read failed") else "ok"
    ai_notice_html = f'<div class="msg {ai_notice_class}" style="margin-bottom:12px;">{ai_notice}</div>' if ai_notice else ""
    attachments = values.get("attachments") or []
    attachment_url = safe(values.get("attachment_url", "")) or safe(values.get("ai_file_url", ""))
    attachment_name = safe(values.get("attachment_name", "")) or safe(values.get("ai_file_name", ""))
    if attachment_url and not attachments:
        attachments = [{"file_url": attachment_url, "file_name": attachment_name}]
    attachment_required = "required" if not readonly and not attachments else ""
    attachment_input = ""
    if readonly:
        attachment_input = "<input type='text' value='See attachments below' readonly>" if attachments else "<input type='text' value='No attachment' readonly>"
    else:
        attachment_input = f"""
            <input type="hidden" name="attachment_url" value="{attachment_url}">
            <input type="hidden" name="attachment_name" value="{attachment_name}">
            <input type="file" name="invoice_attachments" accept=".pdf,image/*" multiple {attachment_required}>
        """
    attachments_html = attachment_gallery(attachments)

    account_options_html = account_options().replace("\\", "\\\\").replace("`", "\\`")

    return f"""
    {upload_card}
    <div class="card">
        <h2>{form_title}</h2>
        {ai_notice_html}

        <form method="post" action="{action}" enctype="multipart/form-data">
            <div class="form-grid">
                <div class="form-group">
                    <label>Bill No</label>
                    <input type="text" name="invoice_no" value="{safe(values.get('invoice_no', next_invoice_no()))}" required {text_readonly}>
                </div>

                <div class="form-group">
                    <label>Bill Date</label>
                    <input type="date" id="invoice_date" name="invoice_date" value="{invoice_date}" {"readonly" if readonly else "required"}>
                </div>

                <div class="form-group">
                    <label>Customer</label>
                    <select id="customer_id" name="customer_id" {select_disabled} {"required" if not readonly else ""}>
                        {customer_options(values.get('customer_id', ''))}
                    </select>
                    {"<input type='hidden' name='customer_id' value='%s'>" % safe(values.get('customer_id', '')) if readonly else ""}
                </div>

                <div class="form-group">
                    <label>Payment Term Days</label>
                    <input type="number" id="payment_term_days" name="payment_term_days" value="{payment_term_days}" readonly>
                </div>

                <div class="form-group">
                    <label>Due Date</label>
                    <input type="date" id="due_date" name="due_date" value="{due_date}" readonly>
                </div>

                <div class="form-group">
                    <label>Status</label>
                    <input type="text" value="{safe(values.get('status', 'draft'))}" readonly>
                </div>

                <div class="form-group">
                    <label>Payment Status</label>
                    <input type="text" value="{safe(values.get('payment_status', 'unpaid'))}" readonly>
                </div>

                <div class="form-group">
                    <label>VAT %</label>
                    <input type="number" step="0.01" id="vat_rate" name="vat_rate" value="{safe(values.get('vat_rate', 14))}" {text_readonly}>
                </div>

                <div class="form-group">
                    <label>WHT %</label>
                    <input type="number" step="0.01" id="wht_rate" name="wht_rate" value="{safe(values.get('wht_rate', 0))}" {text_readonly}>
                </div>

                <div class="form-group">
                    <label>Description</label>
                    <input type="text" name="description" value="{safe(values.get('description', ''))}" {text_readonly}>
                </div>

                <div class="form-group" style="grid-column: span 2;">
                    <label>Invoice Attachment</label>
                    {attachment_input}
                </div>
            </div>

            <div style="margin-top:20px;">
                <div class="table-header">
                    <h3>Bill Lines</h3>
                    {add_line_button}
                </div>

                <table id="lines-table">
                    <thead>
                        <tr>
                            <th style="width:60px;">#</th>
                            <th>Description</th>
                            <th>Account</th>
                            <th style="width:140px;">Qty</th>
                            <th style="width:180px;">Unit Price</th>
                            <th style="width:180px;">Line Amount</th>
                            <th style="width:120px;">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {render_lines_table(lines, readonly=readonly)}
                    </tbody>
                </table>
            </div>

            <div style="margin-top:20px; max-width:420px; margin-right:auto; margin-left:0;">
                <table>
                    <tr>
                        <th>Subtotal</th>
                        <td><input type="text" id="subtotal_view" readonly value="{money(values.get('subtotal', 0), 2)}"></td>
                    </tr>
                    <tr>
                        <th>VAT Amount</th>
                        <td><input type="text" id="vat_amount_view" readonly value="{money(values.get('vat_amount', 0), 2)}"></td>
                    </tr>
                    <tr>
                        <th>Total Amount</th>
                        <td><input type="text" id="total_amount_view" readonly value="{money(values.get('total_amount', 0), 2)}"></td>
                    </tr>
                    <tr>
                        <th>WHT Amount</th>
                        <td><input type="text" id="wht_amount_view" readonly value="{money(values.get('wht_amount', 0), 2)}"></td>
                    </tr>
                    <tr>
                        <th>Net Amount</th>
                        <td><input type="text" id="net_amount_view" readonly value="{money(values.get('net_amount', 0), 2)}"></td>
                    </tr>
                </table>
            </div>

            <div class="form-actions">
                {save_button}
                <a class="btn gray" href="/ui/accounting/customer-invoices">Back</a>
            </div>
        </form>
    </div>
    {attachments_html}

    <script>
    (function() {{
        const isReadonly = {"true" if readonly else "false"};
        const defaultAccountOptions = `{account_options_html}`;

        function pad(n) {{
            return String(n).padStart(2, "0");
        }}

        function addDaysToDate(dateStr, days) {{
            if (!dateStr) return "";
            const d = new Date(dateStr + "T00:00:00");
            if (isNaN(d.getTime())) return "";
            d.setDate(d.getDate() + (parseInt(days || 0, 10) || 0));
            return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
        }}

        function sanitizeDecimalTyping(v) {{
            if (v === null || v === undefined) return "";
            v = String(v);
            v = v.replace(/[^0-9.\\-]/g, "");

            if (v.includes("-")) {{
                v = (v.startsWith("-") ? "-" : "") + v.replace(/-/g, "");
            }}

            let firstDot = v.indexOf(".");
            if (firstDot !== -1) {{
                let before = v.substring(0, firstDot + 1);
                let after = v.substring(firstDot + 1).replace(/\\./g, "");
                v = before + after;
            }}

            return v;
        }}

        function parseNum(v) {{
            if (!v) return 0;
            v = String(v);
            if (v === "." || v === "-" || v === "-.") return 0;
            const n = parseFloat(v);
            return isNaN(n) ? 0 : n;
        }}

        function normalizeDecimalField(el, digits) {{
            if (!el) return;
            const raw = sanitizeDecimalTyping(el.value);

            if (raw === "" || raw === "." || raw === "-" || raw === "-.") {{
                el.value = Number(0).toFixed(digits);
                return;
            }}

            const n = parseFloat(raw);
            if (isNaN(n)) {{
                el.value = Number(0).toFixed(digits);
                return;
            }}

            el.value = n.toFixed(digits);
        }}

        function updateFromCustomer() {{
            const customerSelect = document.getElementById("customer_id");
            const termInput = document.getElementById("payment_term_days");
            const billDateInput = document.getElementById("invoice_date");
            const dueDateInput = document.getElementById("due_date");

            if (!customerSelect || !termInput || !billDateInput || !dueDateInput) return;

            const selected = customerSelect.options[customerSelect.selectedIndex];
            const term = selected ? (selected.getAttribute("data-payment-term") || "0") : "0";

            termInput.value = term;
            dueDateInput.value = addDaysToDate(billDateInput.value, term);
        }}

        function updateDueDateOnly() {{
            const termInput = document.getElementById("payment_term_days");
            const billDateInput = document.getElementById("invoice_date");
            const dueDateInput = document.getElementById("due_date");

            if (!termInput || !billDateInput || !dueDateInput) return;
            dueDateInput.value = addDaysToDate(billDateInput.value, termInput.value);
        }}

        function renumberLines() {{
            const rows = document.querySelectorAll("#lines-table tbody tr");
            rows.forEach((row, idx) => {{
                const firstCell = row.querySelector("td");
                if (firstCell) firstCell.textContent = idx + 1;
            }});
        }}

        function recalcTotals() {{
            let subtotal = 0;

            document.querySelectorAll("#lines-table tbody tr").forEach((row) => {{
                const qtyInput = row.querySelector(".line-qty");
                const priceInput = row.querySelector(".line-price");
                const amountInput = row.querySelector(".line-amount");

                const qty = parseNum(qtyInput ? qtyInput.value : 0);
                const price = parseNum(priceInput ? priceInput.value : 0);
                const amount = qty * price;

                if (amountInput) {{
                    amountInput.value = amount.toFixed(7);
                }}

                subtotal += amount;
            }});

            const vatRate = parseNum(document.getElementById("vat_rate")?.value || 0);
            const whtRate = parseNum(document.getElementById("wht_rate")?.value || 0);

            const vatAmount = subtotal * vatRate / 100;
            const totalAmount = subtotal + vatAmount;
            const whtAmount = subtotal * whtRate / 100;
            const netAmount = totalAmount - whtAmount;

            document.getElementById("subtotal_view").value = subtotal.toFixed(2);
            document.getElementById("vat_amount_view").value = vatAmount.toFixed(2);
            document.getElementById("total_amount_view").value = totalAmount.toFixed(2);
            document.getElementById("wht_amount_view").value = whtAmount.toFixed(2);
            document.getElementById("net_amount_view").value = netAmount.toFixed(2);
        }}

        function bindLineEvents(scope) {{
            scope.querySelectorAll(".line-qty, .line-price").forEach((el) => {{
                el.addEventListener("input", function() {{
                    const oldStart = el.selectionStart;
                    const oldLen = el.value.length;
                    const sanitized = sanitizeDecimalTyping(el.value);
                    el.value = sanitized;

                    const newLen = el.value.length;
                    if (oldStart !== null) {{
                        const nextPos = oldStart + (newLen - oldLen);
                        try {{
                            el.setSelectionRange(nextPos, nextPos);
                        }} catch (e) {{}}
                    }}

                    recalcTotals();
                }});

                el.addEventListener("blur", function() {{
                    normalizeDecimalField(el, 7);
                    recalcTotals();
                }});

                el.addEventListener("focus", function() {{
                    if (el.value === "0.0000000") {{
                        el.select();
                    }}
                }});
            }});
        }}

        window.addLine = function() {{
            const tbody = document.querySelector("#lines-table tbody");
            if (!tbody) return;

            const tr = document.createElement("tr");
            tr.innerHTML =
                "<td></td>" +
                "<td><input type='text' name='line_description' placeholder='Description'></td>" +
                "<td><select name='line_account_code' class='line-account'>" + defaultAccountOptions + "</select></td>" +
                "<td><input type='text' inputmode='decimal' name='line_qty' value='1.0000000' class='line-qty'></td>" +
                "<td><input type='text' inputmode='decimal' name='line_unit_price' value='0.0000000' class='line-price'></td>" +
                "<td><input type='text' value='0.0000000' class='line-amount' readonly></td>" +
                "<td><button type='button' class='btn red' onclick='removeLine(this)'>Remove</button></td>";

            tbody.appendChild(tr);
            bindLineEvents(tr);
            renumberLines();
            recalcTotals();
        }}

        window.removeLine = function(btn) {{
            const tbody = document.querySelector("#lines-table tbody");
            if (!tbody) return;

            if (tbody.querySelectorAll("tr").length <= 1) {{
                alert("Customer invoice must contain at least one line.");
                return;
            }}

            btn.closest("tr").remove();
            renumberLines();
            recalcTotals();
        }}

        document.addEventListener("DOMContentLoaded", function() {{
            const customerSelect = document.getElementById("customer_id");
            const billDateInput = document.getElementById("invoice_date");
            const vatRateInput = document.getElementById("vat_rate");
            const whtRateInput = document.getElementById("wht_rate");

            if (customerSelect && !isReadonly) customerSelect.addEventListener("change", updateFromCustomer);
            if (billDateInput && !isReadonly) billDateInput.addEventListener("change", updateDueDateOnly);
            if (vatRateInput && !isReadonly) vatRateInput.addEventListener("input", recalcTotals);
            if (whtRateInput && !isReadonly) whtRateInput.addEventListener("input", recalcTotals);

            renumberLines();
            if (!isReadonly) {{
                document.querySelectorAll("#lines-table tbody tr").forEach(bindLineEvents);

                if (customerSelect && customerSelect.value) {{
                    updateFromCustomer();
                }} else {{
                    updateDueDateOnly();
                }}

                recalcTotals();
            }}
        }});
    }})();
    </script>
    """


def load_invoice_lines(conn, bill_id: int):
    fk_col = invoice_line_fk_column(conn)
    return conn.execute(f"""
        SELECT *
        FROM customer_invoice_lines
        WHERE {fk_col} = ?
        ORDER BY line_no, id
    """, (bill_id,)).fetchall()


def load_invoice_attachments(conn, invoice_id: int):
    try:
        rows = conn.execute("""
            SELECT file_url, file_name
            FROM customer_invoice_attachments
            WHERE invoice_id = ?
            ORDER BY id
        """, (invoice_id,)).fetchall()
    except Exception:
        rows = []
    if rows:
        return [dict(r) for r in rows]
    row = conn.execute("""
        SELECT attachment_url AS file_url, attachment_name AS file_name
        FROM customer_invoices
        WHERE id = ?
          AND COALESCE(attachment_url, '') <> ''
        LIMIT 1
    """, (invoice_id,)).fetchone()
    return [dict(row)] if row else []


def customer_filter_options(selected_id=""):
    html = "<option value=''>All Customers</option>"
    for r in customer_rows():
        selected = "selected" if str(selected_id or "") == str(r["id"]) else ""
        label = f"{safe(r['code'])} - {safe(r['name'])}" if safe(r["code"]) else safe(r["name"])
        html += f"<option value='{r['id']}' {selected}>{label}</option>"
    return html


def invoice_status_chip(doc_status: str, payment_status: str):
    doc_status = safe(doc_status).lower()
    payment_status = safe(payment_status).lower()

    if doc_status == "draft":
        return '<span class="status-chip blue">Draft</span>'
    if doc_status == "reversed":
        return '<span class="status-chip gray">Reversed</span>'
    if payment_status == "paid":
        return '<span class="status-chip green">Paid</span>'
    if payment_status == "partial":
        return '<span class="status-chip orange">Partial</span>'
    if payment_status == "cancelled":
        return '<span class="status-chip gray">Cancelled</span>'
    return '<span class="status-chip red">Unpaid</span>'


def customer_invoice_tabs(active_key="list"):
    tabs = [
        ("Invoices", "/ui/accounting/customer-invoices", "list"),
        ("New Invoice", "/ui/accounting/customer-invoices/new", "new"),
        ("Customer Payments", "/ui/accounting/customer-payments", "payments"),
        ("Customer Statement", "/ui/accounting/customer-statement", "statement"),
        ("Aging Report", "/ui/accounting/aging?partner_type=customer", "aging"),
        ("Settings", "/ui/accounting/config", "settings"),
    ]

    html = '<div class="page-tabs">'
    for label, href, key in tabs:
        cls = "page-tab active" if key == active_key else "page-tab"
        html += f'<a class="{cls}" href="{href}">{label}</a>'
    html += "</div>"
    return html


# =========================================================
# ROUTES
# =========================================================
ensure_tables()


@router.get("/ui/accounting/customer-invoices", response_class=HTMLResponse)
def list_customer_invoices(
    request: Request,
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    customer_id: str = "",
    payment_status: str = "",
    status: str = "",
):
    can_create_perm = accounting_allowed(request, "create")
    can_edit_perm = accounting_allowed(request, "edit")
    can_post_perm = accounting_allowed(request, "post")
    conn = get_conn()
    sql = """
        SELECT
            id, invoice_no, invoice_date, due_date, customer_id, customer_name,
            subtotal, vat_amount, wht_amount, total_amount, net_amount,
            payment_status, status, journal_id, reversed_journal_id
        FROM customer_invoices
        WHERE 1 = 1
    """
    params = []

    if safe(search):
        sql += " AND (LOWER(COALESCE(invoice_no,'')) LIKE ? OR LOWER(COALESCE(customer_name,'')) LIKE ?)"
        like_value = f"%{safe(search).lower()}%"
        params.extend([like_value, like_value])

    if safe(date_from):
        sql += " AND COALESCE(invoice_date,'') >= ?"
        params.append(safe(date_from))

    if safe(date_to):
        sql += " AND COALESCE(invoice_date,'') <= ?"
        params.append(safe(date_to))

    if safe(customer_id):
        sql += " AND COALESCE(customer_id, 0) = ?"
        params.append(safe_int(customer_id, 0))

    if safe(payment_status):
        sql += " AND LOWER(COALESCE(payment_status,'')) = ?"
        params.append(safe(payment_status).lower())

    if safe(status):
        sql += " AND LOWER(COALESCE(status,'')) = ?"
        params.append(safe(status).lower())

    sql += " ORDER BY id DESC"
    rows = conn.execute(sql, params).fetchall()

    rows_html = ""
    total_amount_sum = Decimal("0.00")
    total_paid_sum = Decimal("0.00")
    total_balance_sum = Decimal("0.00")
    paid_count = 0
    partial_count = 0
    unpaid_count = 0

    for r in rows:
        edit_btn = ""
        post_btn = ""
        reverse_btn = ""
        doc_state = safe(r["status"]).lower()
        payment_state = safe(r["payment_status"]).lower()
        paid_amount = Decimal(str(get_allocated_total_for_document(conn, "customer_invoice", r["id"]) or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
        doc_total = Decimal(str(r["net_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
        balance_amount = doc_total - paid_amount
        if balance_amount < Decimal("0.00"):
            balance_amount = Decimal("0.00")

        total_amount_sum += doc_total
        total_paid_sum += paid_amount
        total_balance_sum += balance_amount

        if doc_state == "posted" and payment_state == "paid":
            paid_count += 1
        elif doc_state == "posted" and payment_state == "partial":
            partial_count += 1
        elif doc_state == "posted" and payment_state not in ["paid", "partial"]:
            unpaid_count += 1

        if invoice_can_edit_before_final(conn, r) and can_edit_perm:
            edit_btn = f"<a class='action-btn green' href='/ui/accounting/customer-invoices/{r['id']}/edit'>Edit</a>"
        if doc_state == "draft" and can_post_perm:
            post_btn = (
                f"<form method='post' action='/ui/accounting/customer-invoices/{r['id']}/post' style='display:inline;'>"
                f"<button class='action-btn green' type='submit'>Post</button></form>"
            )

        rows_html += f"""
        <tr>
            <td><span class="doc-no">{safe(r['invoice_no'])}</span></td>
            <td><span class="doc-party">{safe(r['customer_name'])}</span></td>
            <td>{safe(r['invoice_date'])}</td>
            <td>{safe(r['due_date'])}</td>
            <td class="number-cell">{money(doc_total, 2)}</td>
            <td class="number-cell">{money(paid_amount, 2)}</td>
            <td class="number-cell">{money(balance_amount, 2)}</td>
            <td>{invoice_status_chip(r['status'], r['payment_status'])}</td>
            <td>
                <div class="action-strip">
                    <a class="action-btn blue" href="/ui/accounting/customer-invoices/{r['id']}/view">View</a>
                    {edit_btn}
                    {post_btn}
                </div>
            </td>
        </tr>
        """

    if not rows_html:
        rows_html = "<tr><td colspan='9' class='empty-state'>No customer invoices found for the selected filters.</td></tr>"

    content = f"""
    <div class="list-shell">
        <div class="card">
            <div class="list-header">
                <div class="list-title">
                    <h2>Customer Invoices</h2>
                    <p>Track receivables, invoice dates, balances, and collection status with a cleaner invoice workspace.</p>
                </div>
                {"<a class='btn blue' href='/ui/accounting/customer-invoices/new'>+ New Invoice</a>" if can_create_perm else ""}
            </div>
            <div style="margin-top:16px;">
                {customer_invoice_tabs("list")}
            </div>
        </div>

        <div class="card">
            <div class="toolbar" style="margin-bottom:14px;">
                <div>
                    <h3 class="sub-title">Filters</h3>
                    <div class="section-note">Search by invoice number or customer, then narrow by date range and status.</div>
                </div>
                <div class="table-summary">
                    <span class="summary-pill">Invoices: {len(rows)}</span>
                    <span class="summary-pill">Paid: {paid_count}</span>
                    <span class="summary-pill">Partial: {partial_count}</span>
                    <span class="summary-pill">Unpaid: {unpaid_count}</span>
                </div>
            </div>

            <form method="get">
                <div class="filter-grid">
                    <div class="form-group">
                        <label>Search</label>
                        <input type="text" name="search" value="{safe(search)}" placeholder="Search invoices...">
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
                        <label>Customer</label>
                        <select name="customer_id">
                            {customer_filter_options(customer_id)}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Payment Status</label>
                        <select name="payment_status">
                            <option value="" {"selected" if safe(payment_status) == "" else ""}>All Payment Status</option>
                            <option value="unpaid" {"selected" if safe(payment_status).lower() == "unpaid" else ""}>Unpaid</option>
                            <option value="partial" {"selected" if safe(payment_status).lower() == "partial" else ""}>Partial</option>
                            <option value="paid" {"selected" if safe(payment_status).lower() == "paid" else ""}>Paid</option>
                            <option value="cancelled" {"selected" if safe(payment_status).lower() == "cancelled" else ""}>Cancelled</option>
                        </select>
                    </div>
                </div>
                <div class="filter-grid" style="grid-template-columns: repeat(5, minmax(0, 1fr)); margin-top:12px;">
                    <div class="form-group">
                        <label>Document Status</label>
                        <select name="status">
                            <option value="" {"selected" if safe(status) == "" else ""}>All Document Status</option>
                            <option value="draft" {"selected" if safe(status).lower() == "draft" else ""}>Draft</option>
                            <option value="posted" {"selected" if safe(status).lower() == "posted" else ""}>Posted</option>
                            <option value="reversed" {"selected" if safe(status).lower() == "reversed" else ""}>Reversed</option>
                        </select>
                    </div>
                </div>
                <div class="filter-actions">
                    <button class="btn blue" type="submit">Filter</button>
                    <a class="btn gray" href="/ui/accounting/customer-invoices">Clear</a>
                </div>
            </form>
        </div>

        <div class="card">
            <div class="toolbar" style="margin-bottom:16px;">
                <div>
                    <h3 class="sub-title">Invoices</h3>
                    <div class="section-note">A cleaner listing for due dates, amounts collected, outstanding balances, and fast actions.</div>
                </div>
                <div class="table-summary">
                    <span class="summary-pill">Total Amount: {money(total_amount_sum, 2)}</span>
                    <span class="summary-pill">Paid Amount: {money(total_paid_sum, 2)}</span>
                    <span class="summary-pill">Balance: {money(total_balance_sum, 2)}</span>
                </div>
            </div>

            <div class="table-wrap">
                <table>
                    <tr>
                        <th>Invoice #</th>
                        <th>Customer</th>
                        <th>Invoice Date</th>
                        <th>Due Date</th>
                        <th class="text-right">Total Amount</th>
                        <th class="text-right">Paid Amount</th>
                        <th class="text-right">Balance</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                    {rows_html}
                </table>
            </div>
        </div>
    </div>
    """
    conn.close()
    return HTMLResponse(render_page("Customer Invoices", content, current_path=str(request.url.path)))


@router.get("/ui/accounting/customer-invoices/new", response_class=HTMLResponse)
def new_customer_invoice(request: Request, customer_id: str = ""):
    if not accounting_allowed(request, "create"):
        return permission_denied("You do not have permission to create customer invoices.", "ليس لديك صلاحية إنشاء فواتير العملاء.")
    today = datetime.today().date().isoformat()
    selected_customer_id = safe_int(customer_id, 0)

    payment_term_days = 0
    if selected_customer_id:
        conn = get_conn()
        customer = get_customer(conn, selected_customer_id)
        conn.close()
        if customer:
            payment_term_days = safe_int(customer["payment_term_days"], 0)

    values = {
        "invoice_no": next_invoice_no(),
        "invoice_date": today,
        "customer_id": selected_customer_id if selected_customer_id > 0 else "",
        "payment_term_days": payment_term_days,
        "due_date": calc_due_date(today, payment_term_days),
        "vat_rate": 14,
        "wht_rate": 0,
        "description": "",
        "status": "draft",
        "payment_status": "unpaid",
    }
    lines = [
        {
            "item_description": "",
            "account_code": "",
            "qty": Decimal("1"),
            "unit_price": Decimal("0"),
            "line_amount": Decimal("0"),
        }
    ]
    return HTMLResponse(render_page("New Customer Invoice", customer_invoice_form(values, lines=lines), current_path=str(request.url.path)))


@router.post("/ui/accounting/customer-invoices/ai-upload", response_class=HTMLResponse)
async def customer_invoice_ai_upload(request: Request, file: UploadFile = File(...)):
    if not accounting_allowed(request, "create"):
        return permission_denied("You do not have permission to create customer invoices.", "ظ„ظٹط³ ظ„ط¯ظٹظƒ طµظ„ط§ط­ظٹط© ط¥ظ†ط´ط§ط، ظپظˆط§طھظٹط± ط§ظ„ط¹ظ…ظ„ط§ط،.")
    today = datetime.today().date().isoformat()
    file_bytes = await file.read()
    file_url, file_name = save_uploaded_invoice(file.filename or "", file_bytes)
    try:
        extracted = parse_invoice_upload(file.filename or "", file.content_type or "", file_bytes, "customer")
    except Exception as e:
        values = {
            "invoice_no": next_invoice_no(),
            "invoice_date": today,
            "customer_id": "",
            "payment_term_days": 0,
            "due_date": today,
            "vat_rate": 14,
            "wht_rate": 0,
            "description": "",
            "status": "draft",
            "payment_status": "unpaid",
            "ai_notice": "AI is not available now. Use the invoice preview to complete and save the draft.",
            "ai_file_url": file_url,
            "ai_file_name": file_name,
        }
        lines = [{
            "item_description": "",
            "account_code": get_setting_value("sales_revenue_account_code", "410000"),
            "qty": Decimal("1"),
            "unit_price": Decimal("0"),
            "line_amount": Decimal("0"),
        }]
        return HTMLResponse(render_page("New Customer Invoice", customer_invoice_form(values, lines=lines), current_path=str(request.url.path)))

    conn = get_conn()
    try:
        customer_id = find_partner_id(conn, "customer", extracted.get("party_name", ""))
        customer = get_customer(conn, customer_id) if customer_id > 0 else None
    finally:
        conn.close()
    if customer_id <= 0:
        content = missing_partner_card(
            "customer",
            extracted.get("party_name", ""),
            "/ui/accounting/customer-invoices/ai-upload",
        )
        return HTMLResponse(render_page("Customer Not Found", content, current_path=str(request.url.path)))

    invoice_date = safe(extracted.get("invoice_date")) or today
    payment_term_days = safe_int(customer["payment_term_days"], 0) if customer else 0
    source_no = safe(extracted.get("invoice_no"))
    description = safe(extracted.get("description"))
    if source_no:
        description = f"{description} | Source invoice {source_no}" if description else f"Source invoice {source_no}"
    default_account = get_setting_value("sales_revenue_account_code", "410000")
    lines = []
    for line in extracted.get("lines", []):
        line = dict(line)
        line["account_code"] = default_account
        lines.append(line)
    totals = calculate_invoice_totals(lines, extracted.get("vat_rate", 14), extracted.get("wht_rate", 0))
    values = {
        "invoice_no": next_invoice_no(),
        "invoice_date": invoice_date,
        "customer_id": customer_id if customer_id > 0 else "",
        "payment_term_days": payment_term_days,
        "due_date": calc_due_date(invoice_date, payment_term_days),
        "vat_rate": extracted.get("vat_rate", 14),
        "wht_rate": extracted.get("wht_rate", 0),
        "description": description,
        "status": "draft",
        "payment_status": "unpaid",
        "ai_file_url": file_url,
        "ai_file_name": file_name,
        "subtotal": totals["subtotal"],
        "vat_amount": totals["vat_amount"],
        "total_amount": totals["total_amount"],
        "wht_amount": totals["wht_amount"],
        "net_amount": totals["net_amount"],
        "ai_notice": "Invoice was read by AI. Review the fields, then save draft.",
    }
    return HTMLResponse(render_page("New Customer Invoice", customer_invoice_form(values, lines=lines), current_path=str(request.url.path)))


@router.get("/ui/accounting/customer-invoices/ai-upload")
def customer_invoice_ai_upload_redirect():
    return RedirectResponse("/ui/accounting/customer-invoices/new", status_code=302)


@router.post("/ui/accounting/customer-invoices/new")
async def create_customer_bill(request: Request):
    if not accounting_allowed(request, "create"):
        return permission_denied("You do not have permission to create customer invoices.", "ليس لديك صلاحية إنشاء فواتير العملاء.")
    form = await request.form()

    invoice_no = safe(form.get("invoice_no"))
    invoice_date = safe(form.get("invoice_date"))
    customer_id = safe_int(form.get("customer_id"))
    vat_rate = to_decimal(form.get("vat_rate"), "14")
    wht_rate = to_decimal(form.get("wht_rate"), "0")
    description = safe(form.get("description"))
    new_attachments = await attachments_from_form(form)
    if not new_attachments:
        return HTMLResponse("Invoice attachment is required before saving.", status_code=400)
    attachment_url = new_attachments[0]["file_url"]
    attachment_name = new_attachments[0]["file_name"]

    conn = get_conn()
    customer = get_customer(conn, customer_id)

    if not customer:
        conn.close()
        return HTMLResponse("Customer not found", status_code=400)

    actual_term = safe_int(customer["payment_term_days"], 0)
    final_due_date = calc_due_date(invoice_date, actual_term)

    lines = normalize_lines_from_form(form)
    if not lines:
        conn.close()
        return HTMLResponse("Customer invoice must contain at least one line", status_code=400)

    for line in lines:
        if not safe(line["account_code"]):
            conn.close()
            return HTMLResponse("Account is required on all bill lines", status_code=400)

    totals = calculate_invoice_totals(lines, vat_rate, wht_rate)

    try:
        cur = conn.execute("""
            INSERT INTO customer_invoices (
                invoice_no, invoice_date, due_date,
                customer_id, customer_name, description,
                attachment_url, attachment_name,
                payment_term_days, subtotal, vat_rate, vat_amount,
                wht_rate, wht_amount, total_amount, net_amount,
                payment_status, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unpaid', 'draft')
        """, (
            invoice_no,
            invoice_date,
            final_due_date,
            customer_id,
            safe(customer["name"]),
            description,
            attachment_url,
            attachment_name,
            actual_term,
            float(totals["subtotal"]),
            float(totals["vat_rate"]),
            float(totals["vat_amount"]),
            float(totals["wht_rate"]),
            float(totals["wht_amount"]),
            float(totals["total_amount"]),
            float(totals["net_amount"]),
        ))
        bill_id = cur.lastrowid

        fk_col = invoice_line_fk_column(conn)
        for line in lines:
            conn.execute(f"""
                INSERT INTO customer_invoice_lines (
                    {fk_col}, line_no, item_description, account_code, qty, unit_price, line_amount
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                bill_id,
                line["line_no"],
                line["item_description"],
                line["account_code"],
                float(line["qty"]),
                float(line["unit_price"]),
                float(line["line_amount"]),
            ))

        for item in new_attachments:
            conn.execute("""
                INSERT INTO customer_invoice_attachments (invoice_id, file_url, file_name)
                VALUES (?, ?, ?)
            """, (bill_id, item["file_url"], item["file_name"]))

        create_customer_invoice_draft_journal(conn, bill_id)
        safe_log_action(
            "customer_invoice",
            bill_id,
            "Created",
            done_by=actor_name_from_request(request),
            notes=f"Draft customer invoice created for {safe(customer['name'])} | Total: {totals['net_amount']}",
            conn=conn,
        )

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Save error: {safe(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/customer-invoices/{bill_id}/view", status_code=303)


@router.get("/ui/accounting/customer-invoices/{row_id}", response_class=HTMLResponse)
def open_customer_invoice(request: Request, row_id: int):
    return RedirectResponse(f"/ui/accounting/customer-invoices/{row_id}/view", status_code=302)


@router.get("/ui/accounting/customer-invoices/{row_id}/edit", response_class=HTMLResponse)
def edit_customer_invoice(request: Request, row_id: int):
    if not accounting_allowed(request, "edit"):
        return permission_denied("You do not have permission to edit customer invoices.", "ليس لديك صلاحية تعديل فواتير العملاء.")
    conn = get_conn()
    row = conn.execute("""
        SELECT *
        FROM customer_invoices
        WHERE id = ?
        LIMIT 1
    """, (row_id,)).fetchone()

    if not row:
        conn.close()
        return HTMLResponse("Customer invoice not found", status_code=404)

    if not invoice_can_edit_before_final(conn, row):
        conn.close()
        return HTMLResponse("Only draft or pre-final-post customer invoices can be edited", status_code=400)

    lines = load_invoice_lines(conn, row_id)
    values = dict(row)
    values["attachments"] = load_invoice_attachments(conn, row_id)
    conn.close()

    return HTMLResponse(
        render_page(
            "Edit Customer Invoice",
            customer_invoice_form(values, row_id=row_id, lines=[dict(x) for x in lines]),
            current_path=str(request.url.path),
        )
    )


@router.post("/ui/accounting/customer-invoices/{row_id}/edit")
async def update_customer_invoice(request: Request, row_id: int):
    if not accounting_allowed(request, "edit"):
        return permission_denied("You do not have permission to edit customer invoices.", "ليس لديك صلاحية تعديل فواتير العملاء.")
    form = await request.form()

    invoice_no = safe(form.get("invoice_no"))
    invoice_date = safe(form.get("invoice_date"))
    customer_id = safe_int(form.get("customer_id"))
    vat_rate = to_decimal(form.get("vat_rate"), "14")
    wht_rate = to_decimal(form.get("wht_rate"), "0")
    description = safe(form.get("description"))
    new_attachments = await attachments_from_form(form)
    attachment_url = safe(form.get("attachment_url"))
    attachment_name = safe(form.get("attachment_name"))

    conn = get_conn()

    existing = conn.execute("""
        SELECT *
        FROM customer_invoices
        WHERE id = ?
        LIMIT 1
    """, (row_id,)).fetchone()

    if not existing:
        conn.close()
        return HTMLResponse("Customer invoice not found", status_code=404)

    if not invoice_can_edit_before_final(conn, existing):
        conn.close()
        return HTMLResponse("Only draft or pre-final-post customer invoices can be edited", status_code=400)

    existing_attachments = load_invoice_attachments(conn, row_id)
    if new_attachments:
        attachment_url = new_attachments[0]["file_url"]
        attachment_name = new_attachments[0]["file_name"]
    if not attachment_url and existing_attachments:
        attachment_url = safe(existing_attachments[0].get("file_url"))
        attachment_name = safe(existing_attachments[0].get("file_name"))
    if not attachment_url and not new_attachments:
        conn.close()
        return HTMLResponse("Invoice attachment is required before saving.", status_code=400)

    customer = get_customer(conn, customer_id)
    if not customer:
        conn.close()
        return HTMLResponse("Customer not found", status_code=400)

    actual_term = safe_int(customer["payment_term_days"], 0)
    final_due_date = calc_due_date(invoice_date, actual_term)

    lines = normalize_lines_from_form(form)
    if not lines:
        conn.close()
        return HTMLResponse("Customer invoice must contain at least one line", status_code=400)

    for line in lines:
        if not safe(line["account_code"]):
            conn.close()
            return HTMLResponse("Account is required on all bill lines", status_code=400)

    totals = calculate_invoice_totals(lines, vat_rate, wht_rate)

    try:
        conn.execute("""
            UPDATE customer_invoices
            SET invoice_no = ?,
                invoice_date = ?,
                due_date = ?,
                customer_id = ?,
                customer_name = ?,
                description = ?,
                attachment_url = ?,
                attachment_name = ?,
                payment_term_days = ?,
                subtotal = ?,
                vat_rate = ?,
                vat_amount = ?,
                wht_rate = ?,
                wht_amount = ?,
                total_amount = ?,
                net_amount = ?
            WHERE id = ?
        """, (
            invoice_no,
            invoice_date,
            final_due_date,
            customer_id,
            safe(customer["name"]),
            description,
            attachment_url,
            attachment_name,
            actual_term,
            float(totals["subtotal"]),
            float(totals["vat_rate"]),
            float(totals["vat_amount"]),
            float(totals["wht_rate"]),
            float(totals["wht_amount"]),
            float(totals["total_amount"]),
            float(totals["net_amount"]),
            row_id,
        ))

        fk_col = invoice_line_fk_column(conn)
        conn.execute(f"DELETE FROM customer_invoice_lines WHERE {fk_col} = ?", (row_id,))

        for line in lines:
            conn.execute(f"""
                INSERT INTO customer_invoice_lines (
                    {fk_col}, line_no, item_description, account_code, qty, unit_price, line_amount
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                row_id,
                line["line_no"],
                line["item_description"],
                line["account_code"],
                float(line["qty"]),
                float(line["unit_price"]),
                float(line["line_amount"]),
            ))

        for item in new_attachments:
            conn.execute("""
                INSERT INTO customer_invoice_attachments (invoice_id, file_url, file_name)
                VALUES (?, ?, ?)
            """, (row_id, item["file_url"], item["file_name"]))

        old_journal_id = existing["journal_id"]
        if old_journal_id:
            delete_draft_journal_entry(conn, old_journal_id)

        create_customer_invoice_draft_journal(conn, row_id)
        if safe(existing["status"]).lower() == "posted":
            refreshed = conn.execute("SELECT journal_id FROM customer_invoices WHERE id = ?", (row_id,)).fetchone()
            if refreshed and refreshed["journal_id"]:
                submit_journal_for_final_post(conn, refreshed["journal_id"])
        safe_log_action(
            "customer_invoice",
            row_id,
            "Updated",
            done_by=actor_name_from_request(request),
            notes=f"Draft customer invoice updated for {safe(customer['name'])} | Total: {totals['net_amount']}",
            conn=conn,
        )

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Update error: {safe(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/customer-invoices/{row_id}/view", status_code=303)


@router.post("/ui/accounting/customer-invoices/{row_id}/post")
def post_customer_invoice(request: Request, row_id: int):
    if not accounting_allowed(request, "post"):
        return permission_denied("You do not have permission to post customer invoices.", "ليس لديك صلاحية ترحيل فواتير العملاء.")
    conn = get_conn()
    try:
        bill = conn.execute("""
            SELECT *
            FROM customer_invoices
            WHERE id = ?
            LIMIT 1
        """, (row_id,)).fetchone()

        if not bill:
            raise Exception("Customer invoice not found")

        if safe(bill["status"]).lower() != "draft":
            raise Exception("Only draft customer invoices can be posted")

        if not bill["journal_id"]:
            create_customer_invoice_draft_journal(conn, row_id)
            bill = conn.execute("SELECT * FROM customer_invoices WHERE id = ?", (row_id,)).fetchone()

        submit_journal_for_final_post(conn, bill["journal_id"])

        conn.execute("""
            UPDATE customer_invoices
            SET status = 'posted'
            WHERE id = ?
        """, (row_id,))
        safe_log_action(
            "customer_invoice",
            row_id,
            "Posted",
            done_by=actor_name_from_request(request),
            notes=f"Invoice {safe(bill['invoice_no'])} moved to posted and journal is waiting final post.",
            conn=conn,
        )

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Post error: {safe(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/customer-invoices/{row_id}/view", status_code=303)


@router.post("/ui/accounting/customer-invoices/{row_id}/reverse")
def reverse_customer_invoice(request: Request, row_id: int):
    if not accounting_allowed(request, "post"):
        return permission_denied("You do not have permission to reverse customer invoices.", "ليس لديك صلاحية عكس فواتير العملاء.")
    conn = get_conn()
    try:
        bill = conn.execute("""
            SELECT *
            FROM customer_invoices
            WHERE id = ?
            LIMIT 1
        """, (row_id,)).fetchone()

        if not bill:
            raise Exception("Customer invoice not found")

        if safe(bill["status"]).lower() != "posted":
            raise Exception("Only posted customer invoices can be reversed")

        if bill["reversed_journal_id"]:
            raise Exception("Customer invoice already reversed")

        if not bill["journal_id"]:
            raise Exception("Posted customer invoice has no journal")

        reverse_id = reverse_journal_entry(conn, bill["journal_id"])

        conn.execute("""
            UPDATE customer_invoices
            SET status = 'reversed',
                reversed_journal_id = ?,
                payment_status = 'cancelled'
            WHERE id = ?
        """, (reverse_id, row_id))
        safe_log_action(
            "customer_invoice",
            row_id,
            "Reversed",
            done_by=actor_name_from_request(request),
            notes=f"Invoice {safe(bill['invoice_no'])} reversed and payment status moved to Cancelled.",
            conn=conn,
        )

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reverse error: {safe(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/customer-invoices/{row_id}/view", status_code=303)


@router.post("/ui/accounting/customer-invoices/{row_id}/allocate-cash")
async def allocate_customer_cash_receipt(request: Request, row_id: int):
    if not accounting_allowed(request, "edit"):
        return permission_denied("You do not have permission to allocate customer receipts.", "ليس لديك صلاحية تخصيص سندات قبض العملاء.")
    form = await request.form()
    source_ref = safe(form.get("voucher_id"))
    payment_type = "cash_receipt"
    voucher_id = safe_int(source_ref)
    if ":" in source_ref:
        payment_type, raw_id = source_ref.split(":", 1)
        voucher_id = safe_int(raw_id)
    allocated_amount = to_decimal(form.get("allocated_amount"), "0")
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM customer_invoices WHERE id = ? LIMIT 1", (row_id,)).fetchone()
        if not row:
            raise Exception("Customer invoice not found")
        create_payment_allocation(conn, payment_type, voucher_id, "customer_invoice", row_id, allocated_amount)
        refresh_customer_invoice_payment_status(conn, row_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Allocation error: {safe(e)}", status_code=400)
    conn.close()
    return RedirectResponse(f"/ui/accounting/customer-invoices/{row_id}/view", status_code=303)


@router.post("/ui/accounting/customer-invoices/{row_id}/allocations/{allocation_id}/delete")
def delete_customer_invoice_allocation(request: Request, row_id: int, allocation_id: int):
    if not accounting_allowed(request, "edit"):
        return permission_denied("You do not have permission to remove receipt allocations.", "ليس لديك صلاحية إلغاء تخصيص السداد.")
    conn = get_conn()
    try:
        allocation = conn.execute(
            """
            SELECT *
            FROM payment_allocations
            WHERE id = ?
              AND document_type = 'customer_invoice'
              AND document_id = ?
            LIMIT 1
            """,
            (allocation_id, row_id),
        ).fetchone()
        if not allocation:
            raise Exception("Allocation not found")
        conn.execute("DELETE FROM payment_allocations WHERE id = ?", (allocation_id,))
        refresh_customer_invoice_payment_status(conn, row_id)
        safe_log_action(
            "customer_invoice",
            row_id,
            "Receipt Allocation Removed",
            done_by=actor_name_from_request(request),
            notes=f"Removed allocation #{allocation_id} from invoice.",
            conn=conn,
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Remove allocation error: {safe(e)}", status_code=400)
    conn.close()
    return RedirectResponse(f"/ui/accounting/customer-invoices/{row_id}/view", status_code=303)


@router.post("/ui/accounting/customer-invoices/{row_id}/reverse-customer-payment/{payment_id}")
def reverse_customer_payment_from_invoice(request: Request, row_id: int, payment_id: int):
    if not accounting_allowed(request, "post"):
        return permission_denied("You do not have permission to reverse customer payments.", "ليس لديك صلاحية عكس تحصيلات العملاء.")
    conn = get_conn()
    try:
        invoice = conn.execute("SELECT * FROM customer_invoices WHERE id = ? LIMIT 1", (row_id,)).fetchone()
        if not invoice:
            raise Exception("Customer invoice not found")

        linked = conn.execute(
            """
            SELECT id
            FROM customer_payment_allocations
            WHERE payment_id = ?
              AND invoice_id = ?
            LIMIT 1
            """,
            (payment_id, row_id),
        ).fetchone()
        if not linked:
            raise Exception("This payment is not linked to the selected invoice.")

        payment = conn.execute("SELECT * FROM customer_payments WHERE id = ? LIMIT 1", (payment_id,)).fetchone()
        if not payment:
            raise Exception("Payment not found.")
        if safe(payment["status"]).lower() != "posted":
            raise Exception("Only posted payments can be reversed.")
        if payment["reversed_journal_id"]:
            raise Exception("Payment already reversed.")
        if not payment["journal_id"]:
            raise Exception("Payment journal is missing.")

        reverse_id = reverse_journal_entry(conn, payment["journal_id"])
        conn.execute(
            """
            UPDATE customer_payments
            SET status = 'reversed',
                reversed_journal_id = ?
            WHERE id = ?
            """,
            (reverse_id, payment_id),
        )

        allocations = conn.execute("SELECT invoice_id FROM customer_payment_allocations WHERE payment_id = ?", (payment_id,)).fetchall()
        for alloc in allocations:
            inv_id = safe_int(alloc["invoice_id"])
            inv = conn.execute("SELECT net_amount FROM customer_invoices WHERE id = ? LIMIT 1", (inv_id,)).fetchone()
            if not inv:
                continue
            row_total = conn.execute(
                """
                SELECT COALESCE(SUM(a.allocated_amount), 0) AS total_allocated
                FROM customer_payment_allocations a
                JOIN customer_payments p ON p.id = a.payment_id
                WHERE a.invoice_id = ?
                  AND LOWER(COALESCE(p.status,'')) = 'posted'
                """,
                (inv_id,),
            ).fetchone()
            allocated = Decimal(str(row_total["total_allocated"] if row_total else 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
            net_amount = Decimal(str(inv["net_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
            if allocated <= Decimal("0.00"):
                pay_status = "unpaid"
            elif allocated < net_amount:
                pay_status = "partial"
            else:
                pay_status = "paid"
            conn.execute("UPDATE customer_invoices SET payment_status = ? WHERE id = ?", (pay_status, inv_id))

        safe_log_action(
            "customer_payment",
            payment_id,
            "Reversed",
            done_by=actor_name_from_request(request),
            notes=f"Payment {safe(payment['payment_no'])} reversed from customer invoice {safe(invoice['invoice_no'])}.",
            conn=conn,
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reverse error: {safe(e)}", status_code=400)
    conn.close()
    return RedirectResponse(f"/ui/accounting/customer-invoices/{row_id}/view", status_code=303)


@router.get("/ui/accounting/customer-invoices/{row_id}/view", response_class=HTMLResponse)
def view_customer_invoice(request: Request, row_id: int):
    lang = get_lang(request)
    can_edit_perm = accounting_allowed(request, "edit")
    can_post_perm = accounting_allowed(request, "post")
    can_create_perm = accounting_allowed(request, "create")
    conn = get_conn()
    row = conn.execute("""
        SELECT *
        FROM customer_invoices
        WHERE id = ?
        LIMIT 1
    """, (row_id,)).fetchone()

    if not row:
        conn.close()
        return HTMLResponse("Customer invoice not found", status_code=404)

    lines = load_invoice_lines(conn, row_id)
    cash_receipts = available_customer_cash_receipts(conn, safe_int(row["customer_id"]))
    document_allocations = get_document_allocations(conn, "customer_invoice", row_id)
    paid_amount = Decimal(str(get_allocated_total_for_document(conn, "customer_invoice", row_id) or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    open_amount = Decimal(str(row["net_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP) - paid_amount
    if open_amount < Decimal("0.00"):
        open_amount = Decimal("0.00")
    payment_open_amount = open_amount

    content = customer_invoice_form(
        values={**dict(row), "attachments": load_invoice_attachments(conn, row_id)},
        row_id=row_id,
        lines=[dict(x) for x in lines],
        readonly=True,
    )

    extra_buttons = ""
    if invoice_can_edit_before_final(conn, row) and can_edit_perm:
        extra_buttons += f"<a class='btn green' href='/ui/accounting/customer-invoices/{row_id}/edit'>Edit</a>"
    if safe(row["status"]).lower() == "draft" and can_post_perm:
        extra_buttons += (
            f"<form method='post' action='/ui/accounting/customer-invoices/{row_id}/post' style='display:inline;'>"
            f"<button class='btn green' type='submit'>Post</button></form>"
        )

    journal_is_final = invoice_journal_final_posted(conn, row)

    cash_allocations_html = ""
    if safe(row["status"]).lower() == "posted" and not row["reversed_journal_id"] and open_amount > Decimal("0.00"):
        total_available = Decimal("0.00")
        receipt_rows = ""
        for source_type, voucher, unapplied, option_label in cash_receipts:
            total_available += unapplied
            suggested_amount = unapplied if unapplied < open_amount else open_amount
            if source_type == "cash_receipt":
                receipt_no = safe(voucher["voucher_no"])
                receipt_date = safe(voucher["voucher_date"])
                receipt_kind = "Cash Receipt"
                receipt_kind_ar = "سند قبض"
                open_link = f"/ui/accounting/cash-receipts/{voucher['id']}"
            elif source_type == "customer_payment":
                receipt_no = safe(voucher["payment_no"])
                receipt_date = safe(voucher["payment_date"])
                receipt_kind = "Customer Payment"
                receipt_kind_ar = "تحصيل عميل"
                open_link = f"/ui/accounting/customer-payments/{voucher['id']}"
            else:
                receipt_no = safe(voucher["entry_no"])
                receipt_date = safe(voucher["entry_date"])
                receipt_kind = "Opening Journal"
                receipt_kind_ar = "قيد افتتاحي"
                open_link = f"/ui/accounting/journal/{voucher['id']}"
            receipt_rows += f"""
            <tr>
                <td style="padding:6px 8px;">{receipt_no}</td>
                <td style="padding:6px 8px;">{receipt_date}</td>
                <td style="padding:6px 8px;">{receipt_kind if lang != 'ar' else receipt_kind_ar}</td>
                <td style="padding:6px 8px;">{money(unapplied)}</td>
                <td style="padding:6px 8px;">
                    <form method="post" action="/ui/accounting/customer-invoices/{row_id}/allocate-cash" style="display:flex;gap:6px;align-items:center;flex-wrap:nowrap;margin:0;">
                        <input type="hidden" name="voucher_id" value="{source_type}:{voucher['id']}">
                        <input type="number" step="0.01" min="0.01" max="{money(unapplied).replace(',', '')}" name="allocated_amount" value="{money(suggested_amount).replace(',', '')}" required style="width:120px;padding:6px 8px;min-height:34px;">
                        <button class="btn green" type="submit" style="padding:7px 10px;min-height:34px;">{'Pay' if lang != 'ar' else 'سداد'}</button>
                    </form>
                </td>
                <td style="padding:6px 8px;"><a class="btn blue" href="{open_link}" style="padding:7px 10px;min-height:34px;">{'Open' if lang != 'ar' else 'فتح'}</a></td>
            </tr>
            """
        if receipt_rows:
            pay_body = f"""
            <table style="margin-top:8px;">
                <tr>
                    <th style="padding:6px 8px;">{'Payment #' if lang != 'ar' else 'رقم السداد'}</th>
                    <th style="padding:6px 8px;">{'Date' if lang != 'ar' else 'التاريخ'}</th>
                    <th style="padding:6px 8px;">{'Type' if lang != 'ar' else 'النوع'}</th>
                    <th style="padding:6px 8px;">{'Available' if lang != 'ar' else 'المتاح'}</th>
                    <th style="padding:6px 8px;">{'Pay' if lang != 'ar' else 'سداد'}</th>
                    <th style="padding:6px 8px;">{'Open' if lang != 'ar' else 'فتح'}</th>
                </tr>
                {receipt_rows}
            </table>
            """
        else:
            pay_body = f"<p>{'No unallocated receipts found for this customer.' if lang != 'ar' else 'لا توجد سدادات غير مخصصة لهذا العميل.'}</p>"
        cash_allocations_html = f"""
        <div style="margin-top:14px;">
            <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin-bottom:4px;">
                <h3 style="margin:0;font-size:18px;">{'Available Payments' if lang != 'ar' else 'السدادات المتاحة'}</h3>
                <span><b>{'Open' if lang != 'ar' else 'المتبقي'}:</b> {money(open_amount)}</span>
                <span><b>{'Available' if lang != 'ar' else 'المتاح'}:</b> {money(total_available)}</span>
            </div>
            {pay_body}
        </div>
        """

    linked_payment_rows = ""
    for alloc in document_allocations:
        payment_type = safe(alloc["payment_type"])
        payment_id = safe_int(alloc["payment_id"])
        payment_no = f"{payment_type} #{payment_id}"
        payment_date = ""
        payment_amount = ""
        open_href = "#"
        if payment_type == "cash_receipt":
            payment = conn.execute("SELECT * FROM cash_vouchers WHERE id = ? LIMIT 1", (payment_id,)).fetchone()
            if payment:
                payment_no = safe(payment["voucher_no"])
                payment_date = safe(payment["voucher_date"])
                payment_amount = money(payment["amount"])
                open_href = f"/ui/accounting/cash-receipts/{payment_id}"
        elif payment_type == "customer_payment":
            payment = conn.execute("SELECT * FROM customer_payments WHERE id = ? LIMIT 1", (payment_id,)).fetchone()
            if payment:
                payment_no = safe(payment["payment_no"])
                payment_date = safe(payment["payment_date"])
                payment_amount = money(payment["amount"])
                open_href = f"/ui/accounting/customer-payments/{payment_id}"
        elif payment_type == "customer_opening_journal":
            payment = conn.execute(
                """
                SELECT l.id, j.entry_no, j.entry_date, l.debit, l.credit
                FROM journal_lines l
                JOIN journal_entries j ON j.id = l.journal_id
                WHERE l.id = ?
                LIMIT 1
                """,
                (payment_id,),
            ).fetchone()
            if payment:
                payment_no = safe(payment["entry_no"])
                payment_date = safe(payment["entry_date"])
                payment_amount = money(Decimal(str(payment["credit"] or 0)) - Decimal(str(payment["debit"] or 0)))
                open_href = f"/ui/accounting/journal/{payment_id}"
        remove_btn = ""
        if can_edit_perm:
            remove_btn = (
                f"<form method='post' action='/ui/accounting/customer-invoices/{row_id}/allocations/{alloc['id']}/delete' "
                f"style='display:inline;' onsubmit=\"return confirm('Remove this payment from the invoice?');\">"
                f"<button class='btn red' type='submit'>{'Remove' if lang != 'ar' else 'إلغاء الربط'}</button></form>"
            )
        linked_payment_rows += f"""
        <tr>
            <td>{payment_no}</td>
            <td>{payment_date}</td>
            <td>{payment_amount}</td>
            <td>{money(alloc['allocated_amount'])}</td>
            <td><span class="status-chip green">Allocated</span></td>
            <td>
                <a class="btn blue" href="{open_href}">{'Open' if lang != 'ar' else 'فتح'}</a>
                {remove_btn}
            </td>
        </tr>
        """
    if not linked_payment_rows:
        linked_payment_rows = f"<tr><td colspan='6' style='text-align:center;'>{'No receipt allocations linked to this invoice.' if lang != 'ar' else 'لا توجد إيصالات سداد مرتبطة بهذه الفاتورة.'}</td></tr>"

    journal_rows_html = ""
    if row["journal_id"]:
        journal_lines = conn.execute(
            """
            SELECT *
            FROM journal_lines
            WHERE journal_id = ?
            ORDER BY COALESCE(line_no, 0), id
            """,
            (row["journal_id"],),
        ).fetchall()
        for jl in journal_lines:
            journal_rows_html += f"""
            <tr>
                <td>{safe(jl['line_no'])}</td>
                <td>{safe(jl['line_description'])}</td>
                <td>{account_label(conn, jl['account_code'])}</td>
                <td>{money(jl['debit'])}</td>
                <td>{money(jl['credit'])}</td>
            </tr>
            """
    if not journal_rows_html:
        journal_rows_html = f"<tr><td colspan='5' style='text-align:center;'>{'No journal lines found.' if lang != 'ar' else 'لا توجد سطور قيد.'}</td></tr>"
    journal_lines_html = f"""
    <div class="card" style="margin-top:20px;">
        <h3>{'Accounting Entry Lines' if lang != 'ar' else 'سطور القيد المحاسبي'}</h3>
        <table>
            <tr>
                <th>#</th>
                <th>{'Description' if lang != 'ar' else 'البيان'}</th>
                <th>{'Account' if lang != 'ar' else 'الحساب'}</th>
                <th>{'Debit' if lang != 'ar' else 'مدين'}</th>
                <th>{'Credit' if lang != 'ar' else 'دائن'}</th>
            </tr>
            {journal_rows_html}
        </table>
    </div>
    """

    new_payment_btn = ""
    if (
        can_create_perm
        and safe(row["status"]).lower() == "posted"
        and journal_is_final
        and not row["reversed_journal_id"]
        and payment_open_amount > Decimal("0.00")
    ):
        new_payment_btn = (
            f"<a class='btn green' href='/ui/accounting/customer-payments/new?customer_id={safe(row['customer_id'])}"
            f"&customer_name={safe(row['customer_name'])}&invoice_id={row_id}&amount={money(payment_open_amount).replace(',', '')}'>"
            f"{'Record Receipt' if lang != 'ar' else 'سداد / إيصال قبض'}</a>"
        )

    extra = f"""
    <div class="card" style="margin-top:20px;">
        <h3>{'Customer Invoice Summary' if lang != 'ar' else 'ملخص فاتورة العميل'}</h3>
        <p><b>{'Journal ID' if lang != 'ar' else 'رقم القيد'}:</b> {safe(row['journal_id'])}</p>
        <p><b>{'Reverse Journal ID' if lang != 'ar' else 'رقم قيد العكس'}:</b> {safe(row['reversed_journal_id'])}</p>
        <p><b>{'Paid / Allocated' if lang != 'ar' else 'المدفوع / المخصص'}:</b> {money(paid_amount)}</p>
        <p><b>{'Open Amount' if lang != 'ar' else 'المتبقي'}:</b> {money(open_amount)}</p>
    </div>
    {journal_lines_html}
    {cash_allocations_html}
    <div class="card" style="margin-top:20px;">
        <div style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;">
            <h3>{'Receipt Entries on This Invoice' if lang != 'ar' else 'إيصالات السداد المرتبطة بالفاتورة'}</h3>
            {new_payment_btn}
        </div>
        <p><b>{'Invoice Open for Receipts' if lang != 'ar' else 'المتبقي للسداد'}:</b> {money(payment_open_amount)}</p>
        <table>
            <tr>
                <th>{'Receipt #' if lang != 'ar' else 'رقم الإيصال'}</th>
                <th>{'Date' if lang != 'ar' else 'التاريخ'}</th>
                <th>{'Receipt Amount' if lang != 'ar' else 'قيمة الإيصال'}</th>
                <th>{'Allocated to This Invoice' if lang != 'ar' else 'المخصص لهذه الفاتورة'}</th>
                <th>{'Status' if lang != 'ar' else 'الحالة'}</th>
                <th>{'Actions' if lang != 'ar' else 'الإجراءات'}</th>
            </tr>
            {linked_payment_rows}
        </table>
    </div>

    <div class="form-actions" style="margin-top:16px;">
        {extra_buttons}
        <a class="btn gray" href="/ui/accounting/customer-invoices">{'Back to Customer Invoices' if lang != 'ar' else 'الرجوع لفواتير العملاء'}</a>
    </div>
    """

    conn.close()
    return HTMLResponse(render_page("View Customer Invoice", content + extra + render_audit_log_card("customer_invoice", row_id), lang, current_path=str(request.url.path)))
