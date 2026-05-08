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
    get_payment_unallocated_amount,
    refresh_vendor_bill_payment_status,
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
from modules.purchasing.workflow import ensure_workflow_tables, po_billable_lines, po_billable_summary

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
        "vendor_bill_prefix": "VBILL",
        "vendor_control_account": "211100",
        "input_vat_account": "201020110",
        "wht_payable_account": "214200",
    }
    return fallback.get(key, default)


# =========================================================
# DB SCHEMA + MIGRATION
# =========================================================
def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendor_bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_no TEXT,
            bill_date TEXT,
            due_date TEXT,
            source_po_id INTEGER,
            vendor_id INTEGER,
            vendor_name TEXT,
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
        CREATE TABLE IF NOT EXISTS vendor_bill_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL,
            po_id INTEGER,
            po_line_id INTEGER,
            line_no INTEGER DEFAULT 1,
            item_description TEXT,
            account_code TEXT,
            qty REAL DEFAULT 0,
            unit_price REAL DEFAULT 0,
            line_amount REAL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS partners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            partner_type TEXT DEFAULT 'vendor',
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendor_bill_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL,
            file_url TEXT NOT NULL,
            file_name TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fixed_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            category_id INTEGER,
            purchase_date TEXT,
            in_service_date TEXT,
            cost REAL DEFAULT 0,
            salvage_value REAL DEFAULT 0,
            status TEXT DEFAULT 'draft',
            acquisition_account_code TEXT,
            offset_account_code TEXT,
            acquisition_journal_id INTEGER,
            disposal_journal_id INTEGER,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    ensure_column(conn, "vendor_bills", "bill_no", "ALTER TABLE vendor_bills ADD COLUMN bill_no TEXT")
    ensure_column(conn, "vendor_bills", "bill_date", "ALTER TABLE vendor_bills ADD COLUMN bill_date TEXT")
    ensure_column(conn, "vendor_bills", "due_date", "ALTER TABLE vendor_bills ADD COLUMN due_date TEXT")
    ensure_column(conn, "vendor_bills", "source_po_id", "ALTER TABLE vendor_bills ADD COLUMN source_po_id INTEGER")
    ensure_column(conn, "vendor_bills", "vendor_id", "ALTER TABLE vendor_bills ADD COLUMN vendor_id INTEGER")
    ensure_column(conn, "vendor_bills", "vendor_name", "ALTER TABLE vendor_bills ADD COLUMN vendor_name TEXT")
    ensure_column(conn, "vendor_bills", "description", "ALTER TABLE vendor_bills ADD COLUMN description TEXT")
    ensure_column(conn, "vendor_bills", "attachment_url", "ALTER TABLE vendor_bills ADD COLUMN attachment_url TEXT")
    ensure_column(conn, "vendor_bills", "attachment_name", "ALTER TABLE vendor_bills ADD COLUMN attachment_name TEXT")
    ensure_column(conn, "vendor_bills", "payment_term_days", "ALTER TABLE vendor_bills ADD COLUMN payment_term_days INTEGER DEFAULT 0")
    ensure_column(conn, "vendor_bills", "subtotal", "ALTER TABLE vendor_bills ADD COLUMN subtotal REAL DEFAULT 0")
    ensure_column(conn, "vendor_bills", "vat_rate", "ALTER TABLE vendor_bills ADD COLUMN vat_rate REAL DEFAULT 14")
    ensure_column(conn, "vendor_bills", "vat_amount", "ALTER TABLE vendor_bills ADD COLUMN vat_amount REAL DEFAULT 0")
    ensure_column(conn, "vendor_bills", "wht_rate", "ALTER TABLE vendor_bills ADD COLUMN wht_rate REAL DEFAULT 0")
    ensure_column(conn, "vendor_bills", "wht_amount", "ALTER TABLE vendor_bills ADD COLUMN wht_amount REAL DEFAULT 0")
    ensure_column(conn, "vendor_bills", "total_amount", "ALTER TABLE vendor_bills ADD COLUMN total_amount REAL DEFAULT 0")
    ensure_column(conn, "vendor_bills", "net_amount", "ALTER TABLE vendor_bills ADD COLUMN net_amount REAL DEFAULT 0")
    ensure_column(conn, "vendor_bills", "payment_status", "ALTER TABLE vendor_bills ADD COLUMN payment_status TEXT DEFAULT 'unpaid'")
    ensure_column(conn, "vendor_bills", "status", "ALTER TABLE vendor_bills ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "vendor_bills", "journal_id", "ALTER TABLE vendor_bills ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "vendor_bills", "reversed_journal_id", "ALTER TABLE vendor_bills ADD COLUMN reversed_journal_id INTEGER")
    ensure_column(conn, "vendor_bills", "created_at", "ALTER TABLE vendor_bills ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "vendor_bill_lines", "line_no", "ALTER TABLE vendor_bill_lines ADD COLUMN line_no INTEGER DEFAULT 1")
    ensure_column(conn, "vendor_bill_lines", "po_id", "ALTER TABLE vendor_bill_lines ADD COLUMN po_id INTEGER")
    ensure_column(conn, "vendor_bill_lines", "po_line_id", "ALTER TABLE vendor_bill_lines ADD COLUMN po_line_id INTEGER")
    ensure_column(conn, "vendor_bill_lines", "item_description", "ALTER TABLE vendor_bill_lines ADD COLUMN item_description TEXT")
    ensure_column(conn, "vendor_bill_lines", "account_code", "ALTER TABLE vendor_bill_lines ADD COLUMN account_code TEXT")
    ensure_column(conn, "vendor_bill_lines", "qty", "ALTER TABLE vendor_bill_lines ADD COLUMN qty REAL DEFAULT 0")
    ensure_column(conn, "vendor_bill_lines", "unit_price", "ALTER TABLE vendor_bill_lines ADD COLUMN unit_price REAL DEFAULT 0")
    ensure_column(conn, "vendor_bill_lines", "line_amount", "ALTER TABLE vendor_bill_lines ADD COLUMN line_amount REAL DEFAULT 0")
    ensure_column(conn, "vendor_bill_lines", "asset_category_id", "ALTER TABLE vendor_bill_lines ADD COLUMN asset_category_id INTEGER")
    ensure_column(conn, "vendor_bill_lines", "fixed_asset_id", "ALTER TABLE vendor_bill_lines ADD COLUMN fixed_asset_id INTEGER")

    ensure_column(conn, "fixed_assets", "source_vendor_bill_id", "ALTER TABLE fixed_assets ADD COLUMN source_vendor_bill_id INTEGER")
    ensure_column(conn, "fixed_assets", "source_vendor_bill_line_id", "ALTER TABLE fixed_assets ADD COLUMN source_vendor_bill_line_id INTEGER")

    conn.commit()
    conn.close()


# =========================================================
# MASTER HELPERS
# =========================================================
def next_bill_no():
    prefix = get_setting_value("vendor_bill_prefix", "VBILL")
    conn = get_conn()
    row = conn.execute("""
        SELECT bill_no
        FROM vendor_bills
        WHERE COALESCE(bill_no, '') <> ''
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["bill_no"]:
        return f"{prefix}-0000001"

    last = str(row["bill_no"])
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0

    return f"{prefix}-{num + 1:07d}"


def vendor_rows():
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, code, name, payment_term_days, account_code
        FROM partners
        WHERE partner_type = 'vendor'
          AND COALESCE(is_active, 1) = 1
        ORDER BY name
    """).fetchall()
    conn.close()
    return rows


def vendor_options(selected_id=None):
    html = "<option value=''>-- Select Vendor --</option>"
    for r in vendor_rows():
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


def asset_category_options(selected_id=""):
    rows = []
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT id, code, name
            FROM asset_categories
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY code, name, id
        """).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    html = '<option value="">No Asset</option>'
    for row in rows:
        row_id = safe(row["id"])
        label = f"{safe(row['code'])} - {safe(row['name'])}".strip(" -")
        selected = "selected" if row_id == safe(selected_id) else ""
        html += f'<option value="{row_id}" {selected}>{label}</option>'
    return html


def next_asset_code_for_conn(conn):
    row = conn.execute("""
        SELECT code
        FROM fixed_assets
        WHERE COALESCE(code, '') <> ''
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    if not row or not safe(row["code"]):
        return "FA-00001"
    last = safe(row["code"])
    prefix = "FA"
    number = 0
    if "-" in last:
        prefix, raw = last.rsplit("-", 1)
        try:
            number = int(raw)
        except Exception:
            number = 0
    return f"{prefix}-{number + 1:05d}"


def sync_vendor_bill_fixed_assets(conn, bill_id: int, final_posted: bool = False):
    bill = conn.execute("SELECT * FROM vendor_bills WHERE id = ? LIMIT 1", (bill_id,)).fetchone()
    if not bill:
        return

    lines = conn.execute("""
        SELECT *
        FROM vendor_bill_lines
        WHERE bill_id = ?
        ORDER BY line_no, id
    """, (bill_id,)).fetchall()

    seen_asset_ids = []
    for line in lines:
        category_id = safe_int(line["asset_category_id"], 0)
        line_amount = to_decimal(line["line_amount"])
        if category_id <= 0 or line_amount <= Decimal("0"):
            continue

        category = conn.execute("SELECT * FROM asset_categories WHERE id = ? LIMIT 1", (category_id,)).fetchone()
        if not category:
            continue

        asset_id = safe_int(line["fixed_asset_id"], 0)
        asset = None
        if asset_id > 0:
            asset = conn.execute("SELECT * FROM fixed_assets WHERE id = ? LIMIT 1", (asset_id,)).fetchone()

        asset_name = safe(line["item_description"]) or f"{safe(bill['bill_no'])} line {safe(line['line_no'])}"
        status = "running" if final_posted else "draft"
        acquisition_account = safe(line["account_code"]) or safe(category["asset_account_code"])
        notes = f"Created from vendor bill {safe(bill['bill_no'])}"

        if asset:
            conn.execute("""
                UPDATE fixed_assets
                SET name = ?,
                    category_id = ?,
                    purchase_date = ?,
                    in_service_date = COALESCE(NULLIF(in_service_date, ''), ?),
                    cost = ?,
                    status = ?,
                    acquisition_account_code = ?,
                    offset_account_code = ?,
                    acquisition_journal_id = ?,
                    source_vendor_bill_id = ?,
                    source_vendor_bill_line_id = ?,
                    notes = ?
                WHERE id = ?
            """, (
                asset_name,
                category_id,
                safe(bill["bill_date"]),
                safe(bill["bill_date"]),
                float(line_amount),
                status,
                acquisition_account,
                safe(get_setting_value("vendor_control_account", "211100")),
                bill["journal_id"],
                bill_id,
                line["id"],
                notes,
                asset_id,
            ))
        else:
            cur = conn.execute("""
                INSERT INTO fixed_assets (
                    code, name, category_id, purchase_date, in_service_date,
                    cost, salvage_value, status, acquisition_account_code,
                    offset_account_code, acquisition_journal_id,
                    source_vendor_bill_id, source_vendor_bill_line_id, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
            """, (
                next_asset_code_for_conn(conn),
                asset_name,
                category_id,
                safe(bill["bill_date"]),
                safe(bill["bill_date"]),
                float(line_amount),
                status,
                acquisition_account,
                safe(get_setting_value("vendor_control_account", "211100")),
                bill["journal_id"],
                bill_id,
                line["id"],
                notes,
            ))
            asset_id = cur.lastrowid
            conn.execute("UPDATE vendor_bill_lines SET fixed_asset_id = ? WHERE id = ?", (asset_id, line["id"]))

        seen_asset_ids.append(asset_id)

    if seen_asset_ids:
        placeholders = ",".join("?" for _ in seen_asset_ids)
        conn.execute(f"""
            DELETE FROM fixed_assets
            WHERE source_vendor_bill_id = ?
              AND id NOT IN ({placeholders})
              AND LOWER(COALESCE(status, 'draft')) = 'draft'
        """, [bill_id, *seen_asset_ids])
    else:
        conn.execute("""
            DELETE FROM fixed_assets
            WHERE source_vendor_bill_id = ?
              AND LOWER(COALESCE(status, 'draft')) = 'draft'
        """, (bill_id,))


def get_vendor(conn, vendor_id: int):
    return conn.execute("""
        SELECT *
        FROM partners
        WHERE id = ?
          AND partner_type = 'vendor'
        LIMIT 1
    """, (vendor_id,)).fetchone()


def vendor_bill_can_edit_before_final(conn, row) -> bool:
    doc_status = safe(row["status"]).lower()
    if doc_status == "draft":
        return True
    if doc_status != "posted" or not row["journal_id"]:
        return False
    journal = conn.execute("SELECT status FROM journal_entries WHERE id = ? LIMIT 1", (row["journal_id"],)).fetchone()
    return bool(journal and safe(journal["status"]).lower() == "pending_final_post")


def vendor_bill_journal_final_posted(conn, row) -> bool:
    if not row["journal_id"]:
        return False
    journal = conn.execute("SELECT status FROM journal_entries WHERE id = ? LIMIT 1", (row["journal_id"],)).fetchone()
    return bool(journal and safe(journal["status"]).lower() == "posted")


def calc_due_date(bill_date: str, payment_term_days) -> str:
    try:
        bill_date_obj = datetime.fromisoformat(safe(bill_date)).date()
        return (bill_date_obj + timedelta(days=safe_int(payment_term_days, 0))).isoformat()
    except Exception:
        return safe(bill_date)


def available_vendor_cash_payments(conn, vendor_id: int):
    result = []

    rows = conn.execute(
        """
        SELECT *
        FROM cash_vouchers
        WHERE LOWER(COALESCE(voucher_type,'')) = 'payment'
          AND LOWER(COALESCE(party_type,'')) = 'vendor'
          AND COALESCE(party_id, 0) = ?
          AND LOWER(COALESCE(status,'')) = 'posted'
        ORDER BY voucher_date DESC, id DESC
        """,
        (vendor_id,),
    ).fetchall()
    for row in rows:
        unapplied = get_payment_unallocated_amount(conn, "cash_payment", row["id"])
        if unapplied > Decimal("0.00"):
            label = f"{safe(row['voucher_no'])} | {safe(row['voucher_date'])} | Cash Payment | Available {money(unapplied)}"
            result.append(("cash_payment", row, unapplied, label))

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
          AND LOWER(COALESCE(l.partner_type,'')) = 'vendor'
          AND COALESCE(l.partner_id, 0) = ?
          AND COALESCE(l.debit, 0) > COALESCE(l.credit, 0)
        ORDER BY j.entry_date DESC, j.id DESC, COALESCE(l.line_no,0), l.id DESC
        """,
        (vendor_id,),
    ).fetchall()
    for row in opening_rows:
        unapplied = get_payment_unallocated_amount(conn, "vendor_opening_journal", row["id"])
        if unapplied > Decimal("0.00"):
            label = f"{safe(row['entry_no'])} | {safe(row['entry_date'])} | Opening Journal | Available {money(unapplied)}"
            result.append(("vendor_opening_journal", row, unapplied, label))

    return result


def default_purchase_account():
    return (
        safe(get_setting_value("purchase_account", ""))
        or safe(get_setting_value("expense_account", ""))
        or "510000"
    )


def purchase_po_row(conn, po_id: int):
    if not get_columns(conn, "purchase_orders"):
        return None
    try:
        return conn.execute(
            """
            SELECT *
            FROM purchase_orders
            WHERE id = ?
            LIMIT 1
            """,
            (po_id,),
        ).fetchone()
    except Exception:
        return None


def purchase_po_no(conn, po_id: int):
    row = purchase_po_row(conn, po_id)
    return safe(row["po_no"]) if row else ""


def purchase_vendor_name(conn, vendor_id):
    if not vendor_id:
        return ""

    # Prefer purchasing vendor master if available.
    try:
        row = conn.execute(
            """
            SELECT code, name
            FROM vendors
            WHERE id = ?
            LIMIT 1
            """,
            (vendor_id,),
        ).fetchone()
        if row and safe(row["name"]):
            code = safe(row["code"])
            return f"{code} - {safe(row['name'])}" if code else safe(row["name"])
    except Exception:
        pass

    try:
        row = conn.execute(
            """
            SELECT code, name
            FROM partners
            WHERE id = ?
              AND partner_type = 'vendor'
            LIMIT 1
            """,
            (vendor_id,),
        ).fetchone()
        if row and safe(row["name"]):
            code = safe(row["code"])
            return f"{code} - {safe(row['name'])}" if code else safe(row["name"])
    except Exception:
        pass

    return ""


def purchase_item_name(conn, item_id):
    if not item_id:
        return ""
    table_name = None
    cols = []
    for candidate in ["items", "inventory_items"]:
        c = get_columns(conn, candidate)
        if c:
            table_name = candidate
            cols = c
            break
    if not table_name:
        return ""

    name_col = "name" if "name" in cols else ("item_name" if "item_name" in cols else None)
    code_col = "code" if "code" in cols else ("item_code" if "item_code" in cols else ("sku" if "sku" in cols else None))
    if not name_col:
        return ""

    row = conn.execute(
        f"""
        SELECT {name_col} AS item_name, {code_col if code_col else "''"} AS item_code
        FROM {table_name}
        WHERE id = ?
        LIMIT 1
        """,
        (item_id,),
    ).fetchone()
    if not row:
        return ""

    name = safe(row["item_name"])
    code = safe(row["item_code"])
    if not name:
        return ""
    return f"{code} - {name}" if code else name


def build_bill_lines_from_po(conn, po_id: int):
    lines = []
    account_code = default_purchase_account()
    for line in po_billable_lines(conn, po_id):
        desc = safe(line["description"]) or purchase_item_name(conn, line["item_id"]) or f"PO Line {line['po_line_id']}"
        qty_dec = Decimal(str(line["pending_qty"])).quantize(Decimal("1.0000000"), rounding=ROUND_HALF_UP)
        price_dec = Decimal(str(line["unit_price"])).quantize(Decimal("1.0000000"), rounding=ROUND_HALF_UP)
        lines.append(
            {
                "line_no": int(line["line_no"] or 0),
                "item_description": desc,
                "account_code": account_code,
                "qty": qty_dec,
                "unit_price": price_dec,
                "line_amount": (qty_dec * price_dec),
                "po_line_id": int(line["po_line_id"]),
            }
        )
    return lines


# =========================================================
# LINE / TOTAL HELPERS
# =========================================================
def normalize_lines_from_form(form):
    descriptions = form.getlist("line_description")
    account_codes = form.getlist("line_account_code")
    qtys = form.getlist("line_qty")
    prices = form.getlist("line_unit_price")
    po_line_ids = form.getlist("line_po_line_id")
    asset_category_ids = form.getlist("line_asset_category_id")

    lines = []
    max_len = max(len(descriptions), len(account_codes), len(qtys), len(prices), len(po_line_ids), len(asset_category_ids), 0)

    for i in range(max_len):
        desc = safe(descriptions[i]) if i < len(descriptions) else ""
        account_code = safe(account_codes[i]) if i < len(account_codes) else ""
        qty = to_decimal(qtys[i] if i < len(qtys) else "0")
        unit_price = to_decimal(prices[i] if i < len(prices) else "0")
        po_line_id = safe_int(po_line_ids[i] if i < len(po_line_ids) else 0, 0)
        asset_category_id = safe_int(asset_category_ids[i] if i < len(asset_category_ids) else 0, 0)

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
            "po_line_id": po_line_id if po_line_id > 0 else None,
            "asset_category_id": asset_category_id if asset_category_id > 0 else None,
        })

    return lines


def calculate_bill_totals(lines, vat_rate, wht_rate):
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
def build_vendor_bill_journal_lines(bill, lines_rows, vendor):
    vendor_account = safe(vendor["account_code"]) or safe(get_setting_value("vendor_control_account", "211100"))
    input_vat_account = safe(get_setting_value("input_vat_account", "114100"))
    wht_payable_account = safe(get_setting_value("wht_payable_account", "214200"))

    if not vendor_account:
        raise Exception("Vendor control account is missing")

    posting_lines = []

    for line in lines_rows:
        account_code = safe(line["account_code"])
        if not account_code:
            raise Exception(f"Account is required on bill line #{line['line_no']}")

        amount = Decimal(str(line["line_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
        if amount <= Decimal("0"):
            continue

        posting_lines.append({
            "description": safe(line["item_description"]) or f"Vendor bill line {line['line_no']}",
            "account_code": account_code,
            "debit": amount,
            "credit": Decimal("0.00"),
            "partner_type": "vendor",
            "partner_id": bill["vendor_id"],
        })

    vat_amount = Decimal(str(bill["vat_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    if vat_amount > Decimal("0"):
        posting_lines.append({
            "description": f"Input VAT for {bill['bill_no']}",
            "account_code": input_vat_account,
            "debit": vat_amount,
            "credit": Decimal("0.00"),
            "partner_type": "vendor",
            "partner_id": bill["vendor_id"],
        })

    net_amount = Decimal(str(bill["net_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    posting_lines.append({
        "description": f"Vendor payable for {bill['bill_no']}",
        "account_code": vendor_account,
        "debit": Decimal("0.00"),
        "credit": net_amount,
        "partner_type": "vendor",
        "partner_id": bill["vendor_id"],
    })

    wht_amount = Decimal(str(bill["wht_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    if wht_amount > Decimal("0"):
        posting_lines.append({
            "description": f"WHT payable for {bill['bill_no']}",
            "account_code": wht_payable_account,
            "debit": Decimal("0.00"),
            "credit": wht_amount,
            "partner_type": "vendor",
            "partner_id": bill["vendor_id"],
        })

    total_debit = sum((Decimal(str(x["debit"])) for x in posting_lines), Decimal("0")).quantize(Decimal("1.00"))
    total_credit = sum((Decimal(str(x["credit"])) for x in posting_lines), Decimal("0")).quantize(Decimal("1.00"))

    if total_debit != total_credit:
        raise Exception(f"Journal not balanced: DR={total_debit}, CR={total_credit}")

    return posting_lines


def create_vendor_bill_draft_journal(conn, bill_id: int):
    bill = conn.execute("""
        SELECT *
        FROM vendor_bills
        WHERE id = ?
        LIMIT 1
    """, (bill_id,)).fetchone()

    if not bill:
        raise Exception("Vendor bill not found")

    vendor = get_vendor(conn, bill["vendor_id"])
    if not vendor:
        raise Exception("Vendor not found")

    lines_rows = conn.execute("""
        SELECT *
        FROM vendor_bill_lines
        WHERE bill_id = ?
        ORDER BY line_no, id
    """, (bill_id,)).fetchall()

    if not lines_rows:
        raise Exception("Vendor bill has no lines")

    journal_lines = build_vendor_bill_journal_lines(bill, lines_rows, vendor)

    journal_id = create_journal_entry(
        conn=conn,
        entry_date=bill["bill_date"],
        description=f"Vendor Bill {bill['bill_no']} - {bill['vendor_name']}",
        reference=bill["bill_no"],
        source_type="vendor_bill",
        source_id=bill["id"],
        lines=journal_lines,
    )

    conn.execute("""
        UPDATE vendor_bills
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
        linked_asset = ""
        if safe(line.get("fixed_asset_id", "")):
            linked_asset = f"<a class='btn gray' href='/ui/accounting/fixed-assets/{safe(line.get('fixed_asset_id'))}'>Asset</a>"
        body += f"""
        <tr>
            <td>{idx}</td>
            <td>
                <input type="text" name="line_description" value="{safe(line.get('item_description', ''))}" placeholder="Description" {read_attr}>
                <input type="hidden" name="line_po_line_id" value="{safe(line.get('po_line_id', ''))}">
            </td>
            <td>
                <select name="line_account_code" class="line-account" {select_disabled}>
                    {account_options(safe(line.get('account_code', '')))}
                </select>
                {"<input type='hidden' name='line_account_code' value='%s'>" % safe(line.get('account_code', '')) if readonly else ""}
            </td>
            <td>
                <select name="line_asset_category_id" class="line-asset-category" {select_disabled}>
                    {asset_category_options(safe(line.get('asset_category_id', '')))}
                </select>
                {"<input type='hidden' name='line_asset_category_id' value='%s'>" % safe(line.get('asset_category_id', '')) if readonly else ""}
                {linked_asset}
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


def vendor_bill_form(values=None, row_id=None, lines=None, readonly=False):
    values = values or {}
    lines = lines or []

    bill_date = safe(values.get("bill_date", ""))
    payment_term_days = safe_int(values.get("payment_term_days", 0), 0)
    due_date = safe(values.get("due_date", "")) or calc_due_date(bill_date, payment_term_days)

    action = f"/ui/accounting/vendor-bills/{row_id}/edit" if row_id else "/ui/accounting/vendor-bills/new"
    form_title = "View Vendor Bill" if readonly else ("Edit Vendor Bill" if row_id else "New Vendor Bill")
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
    source_po_id = safe(values.get("source_po_id", ""))
    source_po_label = safe(values.get("source_po_no", "")) or source_po_id
    source_po_hint = ""
    if source_po_id:
        source_po_hint = f"""
        <div class="msg ok" style="margin-bottom:12px;">
            Source PO: {source_po_label} | Lines are loaded from received quantities ready for billing.
        </div>
        """

    account_options_html = account_options().replace("\\", "\\\\").replace("`", "\\`")
    asset_category_options_html = asset_category_options().replace("\\", "\\\\").replace("`", "\\`")

    return f"""
    {upload_card}
    <div class="card">
        <h2>{form_title}</h2>
        {ai_notice_html}
        {source_po_hint}

        <form method="post" action="{action}" enctype="multipart/form-data">
            <input type="hidden" name="source_po_id" value="{source_po_id}">
            <div class="form-grid">
                <div class="form-group">
                    <label>Bill No</label>
                    <input type="text" name="bill_no" value="{safe(values.get('bill_no', next_bill_no()))}" required {text_readonly}>
                </div>

                <div class="form-group">
                    <label>Bill Date</label>
                    <input type="date" id="bill_date" name="bill_date" value="{bill_date}" {"readonly" if readonly else "required"}>
                </div>

                <div class="form-group">
                    <label>Vendor</label>
                    <select id="vendor_id" name="vendor_id" {select_disabled} {"required" if not readonly else ""}>
                        {vendor_options(values.get('vendor_id', ''))}
                    </select>
                    {"<input type='hidden' name='vendor_id' value='%s'>" % safe(values.get('vendor_id', '')) if readonly else ""}
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
                            <th style="width:220px;">Fixed Asset</th>
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
                <a class="btn gray" href="/ui/accounting/vendor-bills">Back</a>
            </div>
        </form>
    </div>
    {attachments_html}

    <script>
    (function() {{
        const isReadonly = {"true" if readonly else "false"};
        const defaultAccountOptions = `{account_options_html}`;
        const defaultAssetCategoryOptions = `{asset_category_options_html}`;

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

        function updateFromVendor() {{
            const vendorSelect = document.getElementById("vendor_id");
            const termInput = document.getElementById("payment_term_days");
            const billDateInput = document.getElementById("bill_date");
            const dueDateInput = document.getElementById("due_date");

            if (!vendorSelect || !termInput || !billDateInput || !dueDateInput) return;

            const selected = vendorSelect.options[vendorSelect.selectedIndex];
            const term = selected ? (selected.getAttribute("data-payment-term") || "0") : "0";

            termInput.value = term;
            dueDateInput.value = addDaysToDate(billDateInput.value, term);
        }}

        function updateDueDateOnly() {{
            const termInput = document.getElementById("payment_term_days");
            const billDateInput = document.getElementById("bill_date");
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
                "<td><input type='text' name='line_description' placeholder='Description'><input type='hidden' name='line_po_line_id' value=''></td>" +
                "<td><select name='line_account_code' class='line-account'>" + defaultAccountOptions + "</select></td>" +
                "<td><select name='line_asset_category_id' class='line-asset-category'>" + defaultAssetCategoryOptions + "</select></td>" +
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
                alert("Vendor bill must contain at least one line.");
                return;
            }}

            btn.closest("tr").remove();
            renumberLines();
            recalcTotals();
        }}

        document.addEventListener("DOMContentLoaded", function() {{
            const vendorSelect = document.getElementById("vendor_id");
            const billDateInput = document.getElementById("bill_date");
            const vatRateInput = document.getElementById("vat_rate");
            const whtRateInput = document.getElementById("wht_rate");

            if (vendorSelect && !isReadonly) vendorSelect.addEventListener("change", updateFromVendor);
            if (billDateInput && !isReadonly) billDateInput.addEventListener("change", updateDueDateOnly);
            if (vatRateInput && !isReadonly) vatRateInput.addEventListener("input", recalcTotals);
            if (whtRateInput && !isReadonly) whtRateInput.addEventListener("input", recalcTotals);

            document.querySelectorAll("#lines-table tbody tr").forEach(bindLineEvents);

            if (vendorSelect && vendorSelect.value) {{
                updateFromVendor();
            }} else {{
                updateDueDateOnly();
            }}

            renumberLines();
            recalcTotals();
        }});
    }})();
    </script>
    """


def load_bill_lines(conn, bill_id: int):
    return conn.execute("""
        SELECT *
        FROM vendor_bill_lines
        WHERE bill_id = ?
        ORDER BY line_no, id
    """, (bill_id,)).fetchall()


def load_bill_attachments(conn, bill_id: int):
    try:
        rows = conn.execute("""
            SELECT file_url, file_name
            FROM vendor_bill_attachments
            WHERE bill_id = ?
            ORDER BY id
        """, (bill_id,)).fetchall()
    except Exception:
        rows = []
    if rows:
        return [dict(r) for r in rows]
    row = conn.execute("""
        SELECT attachment_url AS file_url, attachment_name AS file_name
        FROM vendor_bills
        WHERE id = ?
          AND COALESCE(attachment_url, '') <> ''
        LIMIT 1
    """, (bill_id,)).fetchone()
    return [dict(row)] if row else []


def vendor_filter_options(selected_id=""):
    html = "<option value=''>All Vendors</option>"
    for r in vendor_rows():
        selected = "selected" if str(selected_id or "") == str(r["id"]) else ""
        label = f"{safe(r['code'])} - {safe(r['name'])}" if safe(r["code"]) else safe(r["name"])
        html += f"<option value='{r['id']}' {selected}>{label}</option>"
    return html


def bill_status_chip(doc_status: str, payment_status: str):
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


def vendor_bill_tabs(active_key="list"):
    tabs = [
        ("Bills", "/ui/accounting/vendor-bills", "list"),
        ("New Bill", "/ui/accounting/vendor-bills/new", "new"),
        ("Vendor Payments", "/ui/accounting/vendor-payments", "payments"),
        ("Vendor Statement", "/ui/accounting/vendor-statement", "statement"),
        ("Aging Report", "/ui/accounting/aging?partner_type=vendor", "aging"),
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
ensure_workflow_tables()


@router.get("/ui/accounting/vendor-bills", response_class=HTMLResponse)
def list_vendor_bills(
    request: Request,
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    vendor_id: str = "",
    payment_status: str = "",
    status: str = "",
):
    can_create_perm = accounting_allowed(request, "create")
    can_edit_perm = accounting_allowed(request, "edit")
    can_post_perm = accounting_allowed(request, "post")
    conn = get_conn()
    sql = """
        SELECT
            id, bill_no, bill_date, due_date, vendor_id, vendor_name,
            subtotal, vat_amount, wht_amount, total_amount, net_amount,
            payment_status, status, journal_id, reversed_journal_id
        FROM vendor_bills
        WHERE 1 = 1
    """
    params = []

    if safe(search):
        sql += " AND (LOWER(COALESCE(bill_no,'')) LIKE ? OR LOWER(COALESCE(vendor_name,'')) LIKE ?)"
        like_value = f"%{safe(search).lower()}%"
        params.extend([like_value, like_value])

    if safe(date_from):
        sql += " AND COALESCE(bill_date,'') >= ?"
        params.append(safe(date_from))

    if safe(date_to):
        sql += " AND COALESCE(bill_date,'') <= ?"
        params.append(safe(date_to))

    if safe(vendor_id):
        sql += " AND COALESCE(vendor_id, 0) = ?"
        params.append(safe_int(vendor_id, 0))

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
        paid_amount = Decimal(str(get_allocated_total_for_document(conn, "vendor_bill", r["id"]) or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
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

        if vendor_bill_can_edit_before_final(conn, r) and can_edit_perm:
            edit_btn = f"<a class='action-btn green' href='/ui/accounting/vendor-bills/{r['id']}/edit'>Edit</a>"
        if doc_state == "draft" and can_post_perm:
            post_btn = (
                f"<form method='post' action='/ui/accounting/vendor-bills/{r['id']}/post' style='display:inline;'>"
                f"<button class='action-btn green' type='submit'>Post</button></form>"
            )

        rows_html += f"""
        <tr>
            <td><span class="doc-no">{safe(r['bill_no'])}</span></td>
            <td><span class="doc-party">{safe(r['vendor_name'])}</span></td>
            <td>{safe(r['bill_date'])}</td>
            <td>{safe(r['due_date'])}</td>
            <td class="number-cell">{money(doc_total, 2)}</td>
            <td class="number-cell">{money(paid_amount, 2)}</td>
            <td class="number-cell">{money(balance_amount, 2)}</td>
            <td>{bill_status_chip(r['status'], r['payment_status'])}</td>
            <td>
                <div class="action-strip">
                    <a class="action-btn blue" href="/ui/accounting/vendor-bills/{r['id']}/view">View</a>
                    {edit_btn}
                    {post_btn}
                </div>
            </td>
        </tr>
        """

    if not rows_html:
        rows_html = "<tr><td colspan='9' class='empty-state'>No vendor bills found for the selected filters.</td></tr>"

    ready_rows_html = ""
    try:
        po_rows = conn.execute(
            """
            SELECT id, po_no, po_date, vendor_id, status
            FROM purchase_orders
            WHERE LOWER(COALESCE(status, '')) IN ('partial_received', 'received')
            ORDER BY id DESC
            LIMIT 40
            """
        ).fetchall()
    except Exception:
        po_rows = []

    for po in po_rows:
        billable = po_billable_summary(conn, po["id"])
        if billable["line_count"] <= 0:
            continue
        ready_rows_html += f"""
        <tr>
            <td>{safe(po['po_no'])}</td>
            <td>{purchase_vendor_name(conn, po['vendor_id'])}</td>
            <td>{safe(po['po_date'])}</td>
            <td>{safe(po['status'])}</td>
            <td class="number-cell">{money(billable['qty'], 2)}</td>
            <td>{billable['line_count']}</td>
            <td>
                <div class="action-strip">
                    <a class="action-btn gray" href="/ui/purchasing/purchase-orders/{po['id']}">Open PO</a>
                    <a class="action-btn green" href="/ui/accounting/vendor-bills/new?po_id={po['id']}">Create Bill</a>
                </div>
            </td>
        </tr>
        """

    if not ready_rows_html:
        ready_rows_html = "<tr><td colspan='7' class='empty-state'>No received PO quantities pending billing.</td></tr>"

    content = f"""
    <div class="list-shell">
        <div class="card">
            <div class="list-header">
                <div class="list-title">
                    <h2>Vendor Bills</h2>
                    <p>Track payables, due dates, paid amounts, and outstanding vendor balances with the same clean invoice style.</p>
                </div>
                {"<a class='btn blue' href='/ui/accounting/vendor-bills/new'>+ New Bill</a>" if can_create_perm else ""}
            </div>
            <div style="margin-top:16px;">
                {vendor_bill_tabs("list")}
            </div>
        </div>

        <div class="card">
            <div class="toolbar" style="margin-bottom:14px;">
                <div>
                    <h3 class="sub-title">Ready From Warehouse</h3>
                    <div class="section-note">POs with received quantities not billed yet.</div>
                </div>
            </div>
            <div class="table-wrap">
                <table>
                    <tr>
                        <th>PO #</th>
                        <th>Vendor</th>
                        <th>PO Date</th>
                        <th>PO Status</th>
                        <th class="text-right">Ready Qty</th>
                        <th>Lines</th>
                        <th>Actions</th>
                    </tr>
                    {ready_rows_html}
                </table>
            </div>
        </div>

        <div class="card">
            <div class="toolbar" style="margin-bottom:14px;">
                <div>
                    <h3 class="sub-title">Filters</h3>
                    <div class="section-note">Search by bill number or vendor, then narrow the list by date range and status.</div>
                </div>
                <div class="table-summary">
                    <span class="summary-pill">Bills: {len(rows)}</span>
                    <span class="summary-pill">Paid: {paid_count}</span>
                    <span class="summary-pill">Partial: {partial_count}</span>
                    <span class="summary-pill">Unpaid: {unpaid_count}</span>
                </div>
            </div>

            <form method="get">
                <div class="filter-grid">
                    <div class="form-group">
                        <label>Search</label>
                        <input type="text" name="search" value="{safe(search)}" placeholder="Search bills...">
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
                        <label>Vendor</label>
                        <select name="vendor_id">
                            {vendor_filter_options(vendor_id)}
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
                    <a class="btn gray" href="/ui/accounting/vendor-bills">Clear</a>
                </div>
            </form>
        </div>

        <div class="card">
            <div class="toolbar" style="margin-bottom:16px;">
                <div>
                    <h3 class="sub-title">Bills</h3>
                    <div class="section-note">A matching vendor bill view with totals, settled amount, open balance, and fast actions.</div>
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
                        <th>Bill #</th>
                        <th>Vendor</th>
                        <th>Bill Date</th>
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
    return HTMLResponse(render_page("Vendor Bills", content, current_path=str(request.url.path)))


@router.get("/ui/accounting/vendor-bills/new", response_class=HTMLResponse)
def new_vendor_bill(request: Request, vendor_id: str = "", po_id: str = ""):
    if not accounting_allowed(request, "create"):
        return permission_denied("You do not have permission to create vendor bills.", "ليس لديك صلاحية إنشاء فواتير الموردين.")
    today = datetime.today().date().isoformat()
    selected_vendor_id = safe_int(vendor_id, 0)
    selected_po_id = safe_int(po_id, 0)

    payment_term_days = 0
    lines = [
        {
            "item_description": "",
            "account_code": "",
            "qty": Decimal("1"),
            "unit_price": Decimal("0"),
            "line_amount": Decimal("0"),
            "po_line_id": None,
        }
    ]
    desc = ""
    source_po_no = ""

    conn = get_conn()

    if selected_po_id > 0:
        po = purchase_po_row(conn, selected_po_id)
        if not po:
            conn.close()
            return HTMLResponse("PO not found for billing.", status_code=404)

        selected_vendor_id = safe_int(po["vendor_id"], 0)
        lines_from_po = build_bill_lines_from_po(conn, selected_po_id)
        if not lines_from_po:
            conn.close()
            return HTMLResponse(
                "This PO has no received quantities pending billing.",
                status_code=400,
            )
        lines = lines_from_po
        desc = f"Vendor bill from PO {safe(po['po_no'])}"
        source_po_no = safe(po["po_no"])

    if selected_vendor_id:
        vendor = get_vendor(conn, selected_vendor_id)
        if vendor:
            payment_term_days = safe_int(vendor["payment_term_days"], 0)

    conn.close()

    values = {
        "bill_no": next_bill_no(),
        "bill_date": today,
        "vendor_id": selected_vendor_id if selected_vendor_id > 0 else "",
        "source_po_id": selected_po_id if selected_po_id > 0 else "",
        "source_po_no": source_po_no,
        "payment_term_days": payment_term_days,
        "due_date": calc_due_date(today, payment_term_days),
        "vat_rate": 14,
        "wht_rate": 0,
        "description": desc,
        "status": "draft",
        "payment_status": "unpaid",
    }
    return HTMLResponse(render_page("New Vendor Bill", vendor_bill_form(values, lines=lines), current_path=str(request.url.path)))


@router.post("/ui/accounting/vendor-bills/ai-upload", response_class=HTMLResponse)
async def vendor_bill_ai_upload(request: Request, file: UploadFile = File(...)):
    if not accounting_allowed(request, "create"):
        return permission_denied("You do not have permission to create vendor bills.", "ظ„ظٹط³ ظ„ط¯ظٹظƒ طµظ„ط§ط­ظٹط© ط¥ظ†ط´ط§ط، ظپظˆط§طھظٹط± ط§ظ„ظ…ظˆط±ط¯ظٹظ†.")
    today = datetime.today().date().isoformat()
    file_bytes = await file.read()
    file_url, file_name = save_uploaded_invoice(file.filename or "", file_bytes)
    try:
        extracted = parse_invoice_upload(file.filename or "", file.content_type or "", file_bytes, "vendor")
    except Exception as e:
        values = {
            "bill_no": next_bill_no(),
            "bill_date": today,
            "vendor_id": "",
            "source_po_id": "",
            "source_po_no": "",
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
            "account_code": default_purchase_account(),
            "qty": Decimal("1"),
            "unit_price": Decimal("0"),
            "line_amount": Decimal("0"),
            "po_line_id": None,
        }]
        return HTMLResponse(render_page("New Vendor Bill", vendor_bill_form(values, lines=lines), current_path=str(request.url.path)))

    conn = get_conn()
    try:
        vendor_id = find_partner_id(conn, "vendor", extracted.get("party_name", ""))
    finally:
        conn.close()
    if vendor_id <= 0:
        content = missing_partner_card(
            "vendor",
            extracted.get("party_name", ""),
            "/ui/accounting/vendor-bills/ai-upload",
        )
        return HTMLResponse(render_page("Vendor Not Found", content, current_path=str(request.url.path)))

    bill_date = safe(extracted.get("invoice_date")) or today
    source_no = safe(extracted.get("invoice_no"))
    description = safe(extracted.get("description"))
    if source_no:
        description = f"{description} | Source invoice {source_no}" if description else f"Source invoice {source_no}"
    default_account = default_purchase_account()
    lines = []
    for line in extracted.get("lines", []):
        line = dict(line)
        line["account_code"] = default_account
        line["po_line_id"] = None
        lines.append(line)
    totals = calculate_bill_totals(lines, extracted.get("vat_rate", 14), extracted.get("wht_rate", 0))
    values = {
        "bill_no": next_bill_no(),
        "bill_date": bill_date,
        "vendor_id": vendor_id if vendor_id > 0 else "",
        "source_po_id": "",
        "source_po_no": "",
        "payment_term_days": 0,
        "due_date": safe(extracted.get("due_date")) or bill_date,
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
    return HTMLResponse(render_page("New Vendor Bill", vendor_bill_form(values, lines=lines), current_path=str(request.url.path)))


@router.post("/ui/accounting/vendor-bills/new")
async def create_vendor_bill(request: Request):
    if not accounting_allowed(request, "create"):
        return permission_denied("You do not have permission to create vendor bills.", "ليس لديك صلاحية إنشاء فواتير الموردين.")
    form = await request.form()

    bill_no = safe(form.get("bill_no"))
    bill_date = safe(form.get("bill_date"))
    source_po_id = safe_int(form.get("source_po_id"))
    vendor_id = safe_int(form.get("vendor_id"))
    vat_rate = to_decimal(form.get("vat_rate"), "14")
    wht_rate = to_decimal(form.get("wht_rate"), "0")
    description = safe(form.get("description"))
    new_attachments = await attachments_from_form(form)
    if not new_attachments:
        return HTMLResponse("Invoice attachment is required before saving.", status_code=400)
    attachment_url = new_attachments[0]["file_url"]
    attachment_name = new_attachments[0]["file_name"]

    conn = get_conn()
    vendor = get_vendor(conn, vendor_id)

    if not vendor:
        conn.close()
        return HTMLResponse("Vendor not found", status_code=400)

    actual_term = safe_int(vendor["payment_term_days"], 0)
    final_due_date = calc_due_date(bill_date, actual_term)

    lines = normalize_lines_from_form(form)
    if not lines:
        conn.close()
        return HTMLResponse("Vendor bill must contain at least one line", status_code=400)

    for line in lines:
        if not safe(line["account_code"]):
            conn.close()
            return HTMLResponse("Account is required on all bill lines", status_code=400)

    totals = calculate_bill_totals(lines, vat_rate, wht_rate)

    try:
        cur = conn.execute("""
            INSERT INTO vendor_bills (
                bill_no, bill_date, due_date,
                source_po_id,
                vendor_id, vendor_name, description,
                attachment_url, attachment_name,
                payment_term_days, subtotal, vat_rate, vat_amount,
                wht_rate, wht_amount, total_amount, net_amount,
                payment_status, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unpaid', 'draft')
        """, (
            bill_no,
            bill_date,
            final_due_date,
            source_po_id if source_po_id > 0 else None,
            vendor_id,
            safe(vendor["name"]),
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

        for line in lines:
            conn.execute("""
                INSERT INTO vendor_bill_lines (
                    bill_id, po_id, po_line_id, line_no, item_description, account_code,
                    asset_category_id, qty, unit_price, line_amount
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bill_id,
                source_po_id if source_po_id > 0 else None,
                line.get("po_line_id"),
                line["line_no"],
                line["item_description"],
                line["account_code"],
                line.get("asset_category_id"),
                float(line["qty"]),
                float(line["unit_price"]),
                float(line["line_amount"]),
            ))

        for item in new_attachments:
            conn.execute("""
                INSERT INTO vendor_bill_attachments (bill_id, file_url, file_name)
                VALUES (?, ?, ?)
            """, (bill_id, item["file_url"], item["file_name"]))

        create_vendor_bill_draft_journal(conn, bill_id)
        sync_vendor_bill_fixed_assets(conn, bill_id)
        safe_log_action(
            "vendor_bill",
            bill_id,
            "Created",
            done_by=actor_name_from_request(request),
            notes=f"Draft vendor bill created for {safe(vendor['name'])} | Total: {totals['net_amount']}",
            conn=conn,
        )

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Save error: {safe(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/vendor-bills/{bill_id}/view", status_code=303)


@router.get("/ui/accounting/vendor-bills/{row_id}/edit", response_class=HTMLResponse)
def edit_vendor_bill(request: Request, row_id: int):
    if not accounting_allowed(request, "edit"):
        return permission_denied("You do not have permission to edit vendor bills.", "ليس لديك صلاحية تعديل فواتير الموردين.")
    conn = get_conn()
    row = conn.execute("""
        SELECT *
        FROM vendor_bills
        WHERE id = ?
        LIMIT 1
    """, (row_id,)).fetchone()

    if not row:
        conn.close()
        return HTMLResponse("Vendor bill not found", status_code=404)

    if not vendor_bill_can_edit_before_final(conn, row):
        conn.close()
        return HTMLResponse("Only draft or pre-final-post vendor bills can be edited", status_code=400)

    lines = load_bill_lines(conn, row_id)
    values = dict(row)
    values["attachments"] = load_bill_attachments(conn, row_id)
    conn.close()

    return HTMLResponse(
        render_page(
            "Edit Vendor Bill",
            vendor_bill_form(values, row_id=row_id, lines=[dict(x) for x in lines]),
            current_path=str(request.url.path),
        )
    )


@router.post("/ui/accounting/vendor-bills/{row_id}/edit")
async def update_vendor_bill(request: Request, row_id: int):
    if not accounting_allowed(request, "edit"):
        return permission_denied("You do not have permission to edit vendor bills.", "ليس لديك صلاحية تعديل فواتير الموردين.")
    form = await request.form()

    bill_no = safe(form.get("bill_no"))
    bill_date = safe(form.get("bill_date"))
    source_po_id = safe_int(form.get("source_po_id"))
    vendor_id = safe_int(form.get("vendor_id"))
    vat_rate = to_decimal(form.get("vat_rate"), "14")
    wht_rate = to_decimal(form.get("wht_rate"), "0")
    description = safe(form.get("description"))
    new_attachments = await attachments_from_form(form)
    attachment_url = safe(form.get("attachment_url"))
    attachment_name = safe(form.get("attachment_name"))

    conn = get_conn()

    existing = conn.execute("""
        SELECT *
        FROM vendor_bills
        WHERE id = ?
        LIMIT 1
    """, (row_id,)).fetchone()

    if not existing:
        conn.close()
        return HTMLResponse("Vendor bill not found", status_code=404)

    if not vendor_bill_can_edit_before_final(conn, existing):
        conn.close()
        return HTMLResponse("Only draft or pre-final-post vendor bills can be edited", status_code=400)

    existing_attachments = load_bill_attachments(conn, row_id)
    if new_attachments:
        attachment_url = new_attachments[0]["file_url"]
        attachment_name = new_attachments[0]["file_name"]
    if not attachment_url and existing_attachments:
        attachment_url = safe(existing_attachments[0].get("file_url"))
        attachment_name = safe(existing_attachments[0].get("file_name"))
    if not attachment_url and not new_attachments:
        conn.close()
        return HTMLResponse("Invoice attachment is required before saving.", status_code=400)

    vendor = get_vendor(conn, vendor_id)
    if not vendor:
        conn.close()
        return HTMLResponse("Vendor not found", status_code=400)

    actual_term = safe_int(vendor["payment_term_days"], 0)
    final_due_date = calc_due_date(bill_date, actual_term)

    lines = normalize_lines_from_form(form)
    if not lines:
        conn.close()
        return HTMLResponse("Vendor bill must contain at least one line", status_code=400)

    for line in lines:
        if not safe(line["account_code"]):
            conn.close()
            return HTMLResponse("Account is required on all bill lines", status_code=400)

    totals = calculate_bill_totals(lines, vat_rate, wht_rate)

    try:
        conn.execute("""
            UPDATE vendor_bills
            SET bill_no = ?,
                bill_date = ?,
                due_date = ?,
                source_po_id = ?,
                vendor_id = ?,
                vendor_name = ?,
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
            bill_no,
            bill_date,
            final_due_date,
            source_po_id if source_po_id > 0 else None,
            vendor_id,
            safe(vendor["name"]),
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

        conn.execute("DELETE FROM vendor_bill_lines WHERE bill_id = ?", (row_id,))
        conn.execute("""
            DELETE FROM fixed_assets
            WHERE source_vendor_bill_id = ?
              AND LOWER(COALESCE(status, 'draft')) = 'draft'
        """, (row_id,))

        for line in lines:
            conn.execute("""
                INSERT INTO vendor_bill_lines (
                    bill_id, po_id, po_line_id, line_no, item_description, account_code,
                    asset_category_id, qty, unit_price, line_amount
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row_id,
                source_po_id if source_po_id > 0 else None,
                line.get("po_line_id"),
                line["line_no"],
                line["item_description"],
                line["account_code"],
                line.get("asset_category_id"),
                float(line["qty"]),
                float(line["unit_price"]),
                float(line["line_amount"]),
            ))

        for item in new_attachments:
            conn.execute("""
                INSERT INTO vendor_bill_attachments (bill_id, file_url, file_name)
                VALUES (?, ?, ?)
            """, (row_id, item["file_url"], item["file_name"]))

        old_journal_id = existing["journal_id"]
        if old_journal_id:
            delete_draft_journal_entry(conn, old_journal_id)

        create_vendor_bill_draft_journal(conn, row_id)
        sync_vendor_bill_fixed_assets(conn, row_id)
        if safe(existing["status"]).lower() == "posted":
            refreshed = conn.execute("SELECT journal_id FROM vendor_bills WHERE id = ?", (row_id,)).fetchone()
            if refreshed and refreshed["journal_id"]:
                submit_journal_for_final_post(conn, refreshed["journal_id"])
        safe_log_action(
            "vendor_bill",
            row_id,
            "Updated",
            done_by=actor_name_from_request(request),
            notes=f"Draft vendor bill updated for {safe(vendor['name'])} | Total: {totals['net_amount']}",
            conn=conn,
        )

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Update error: {safe(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/vendor-bills/{row_id}/view", status_code=303)


@router.post("/ui/accounting/vendor-bills/{row_id}/post")
def post_vendor_bill(request: Request, row_id: int):
    if not accounting_allowed(request, "post"):
        return permission_denied("You do not have permission to post vendor bills.", "ليس لديك صلاحية ترحيل فواتير الموردين.")
    conn = get_conn()
    try:
        bill = conn.execute("""
            SELECT *
            FROM vendor_bills
            WHERE id = ?
            LIMIT 1
        """, (row_id,)).fetchone()

        if not bill:
            raise Exception("Vendor bill not found")

        if safe(bill["status"]).lower() != "draft":
            raise Exception("Only draft vendor bills can be posted")

        if not bill["journal_id"]:
            create_vendor_bill_draft_journal(conn, row_id)
            bill = conn.execute("SELECT * FROM vendor_bills WHERE id = ?", (row_id,)).fetchone()

        submit_journal_for_final_post(conn, bill["journal_id"])

        conn.execute("""
            UPDATE vendor_bills
            SET status = 'posted'
            WHERE id = ?
        """, (row_id,))
        safe_log_action(
            "vendor_bill",
            row_id,
            "Posted",
            done_by=actor_name_from_request(request),
            notes=f"Bill {safe(bill['bill_no'])} moved to posted and journal is waiting final post.",
            conn=conn,
        )

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Post error: {safe(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/vendor-bills/{row_id}/view", status_code=303)


@router.post("/ui/accounting/vendor-bills/{row_id}/reverse")
def reverse_vendor_bill(request: Request, row_id: int):
    if not accounting_allowed(request, "post"):
        return permission_denied("You do not have permission to reverse vendor bills.", "ليس لديك صلاحية عكس فواتير الموردين.")
    conn = get_conn()
    try:
        bill = conn.execute("""
            SELECT *
            FROM vendor_bills
            WHERE id = ?
            LIMIT 1
        """, (row_id,)).fetchone()

        if not bill:
            raise Exception("Vendor bill not found")

        if safe(bill["status"]).lower() != "posted":
            raise Exception("Only posted vendor bills can be reversed")

        if bill["reversed_journal_id"]:
            raise Exception("Vendor bill already reversed")

        if not bill["journal_id"]:
            raise Exception("Posted vendor bill has no journal")

        reverse_id = reverse_journal_entry(conn, bill["journal_id"])

        conn.execute("""
            UPDATE vendor_bills
            SET status = 'reversed',
                reversed_journal_id = ?,
                payment_status = 'cancelled'
            WHERE id = ?
        """, (reverse_id, row_id))
        safe_log_action(
            "vendor_bill",
            row_id,
            "Reversed",
            done_by=actor_name_from_request(request),
            notes=f"Bill {safe(bill['bill_no'])} reversed and payment status moved to Cancelled.",
            conn=conn,
        )

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reverse error: {safe(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/vendor-bills/{row_id}/view", status_code=303)


@router.post("/ui/accounting/vendor-bills/{row_id}/allocate-cash")
async def allocate_vendor_cash_payment(request: Request, row_id: int):
    if not accounting_allowed(request, "edit"):
        return permission_denied("You do not have permission to allocate vendor cash payments.", "ليس لديك صلاحية تخصيص سندات صرف الموردين.")
    form = await request.form()
    source_ref = safe(form.get("voucher_id"))
    payment_type = "cash_payment"
    voucher_id = safe_int(source_ref)
    if ":" in source_ref:
        payment_type, raw_id = source_ref.split(":", 1)
        voucher_id = safe_int(raw_id)
    allocated_amount = to_decimal(form.get("allocated_amount"), "0")
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM vendor_bills WHERE id = ? LIMIT 1", (row_id,)).fetchone()
        if not row:
            raise Exception("Vendor bill not found")
        create_payment_allocation(conn, payment_type, voucher_id, "vendor_bill", row_id, allocated_amount)
        refresh_vendor_bill_payment_status(conn, row_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Allocation error: {safe(e)}", status_code=400)
    conn.close()
    return RedirectResponse(f"/ui/accounting/vendor-bills/{row_id}/view", status_code=303)


@router.get("/ui/accounting/vendor-bills/{row_id}/view", response_class=HTMLResponse)
def view_vendor_bill(request: Request, row_id: int):
    lang = get_lang(request)
    can_edit_perm = accounting_allowed(request, "edit")
    can_post_perm = accounting_allowed(request, "post")
    conn = get_conn()
    row = conn.execute("""
        SELECT *
        FROM vendor_bills
        WHERE id = ?
        LIMIT 1
    """, (row_id,)).fetchone()

    if not row:
        conn.close()
        return HTMLResponse("Vendor bill not found", status_code=404)

    lines = load_bill_lines(conn, row_id)
    cash_payments = available_vendor_cash_payments(conn, safe_int(row["vendor_id"]))
    paid_amount = Decimal(str(get_allocated_total_for_document(conn, "vendor_bill", row_id) or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
    open_amount = Decimal(str(row["net_amount"] or 0)).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP) - paid_amount
    if open_amount < Decimal("0.00"):
        open_amount = Decimal("0.00")

    content = vendor_bill_form(
        values={**dict(row), "attachments": load_bill_attachments(conn, row_id)},
        row_id=row_id,
        lines=[dict(x) for x in lines],
        readonly=True,
    )

    extra_buttons = ""
    if vendor_bill_can_edit_before_final(conn, row) and can_edit_perm:
        extra_buttons += f"<a class='btn green' href='/ui/accounting/vendor-bills/{row_id}/edit'>Edit</a>"
    if safe(row["status"]).lower() == "draft" and can_post_perm:
        extra_buttons += (
            f"<form method='post' action='/ui/accounting/vendor-bills/{row_id}/post' style='display:inline;'>"
            f"<button class='btn green' type='submit'>Post</button></form>"
        )

    journal_is_final = vendor_bill_journal_final_posted(conn, row)

    cash_allocations_html = ""
    if safe(row["status"]).lower() == "posted" and journal_is_final and not row["reversed_journal_id"] and cash_payments:
        options = ""
        total_available = Decimal("0.00")
        for source_type, voucher, unapplied, option_label in cash_payments:
            total_available += unapplied
            options += f"<option value='{source_type}:{voucher['id']}'>{option_label}</option>"
        cash_allocations_html = f"""
        <div class="card" style="margin-top:20px;">
            <h3>{'Vendor Cash Balance' if lang != 'ar' else 'رصيد المورد غير المخصص'}</h3>
            <p><b>{'Bill Open Amount' if lang != 'ar' else 'المتبقي على الفاتورة'}:</b> {money(open_amount)}</p>
            <p><b>{'Available Unallocated Payments' if lang != 'ar' else 'المتاح من سندات الصرف غير المخصصة'}:</b> {money(total_available)}</p>
            <form method="post" action="/ui/accounting/vendor-bills/{row_id}/allocate-cash" style="margin-top:14px;">
                <div class="row">
                    <div class="col">
                        <label>{'Cash Payment Voucher' if lang != 'ar' else 'سند الصرف'}</label>
                        <select name="voucher_id" required>{options}</select>
                    </div>
                    <div class="col">
                        <label>{'Allocate Amount' if lang != 'ar' else 'مبلغ التخصيص'}</label>
                        <input type="number" step="0.01" min="0.01" name="allocated_amount" value="{money(open_amount).replace(',', '')}" required>
                    </div>
                </div>
                <div style="margin-top:14px;">
                    <button class="btn blue" type="submit">{'Allocate to Bill' if lang != 'ar' else 'تخصيص على فاتورة المورد'}</button>
                </div>
            </form>
        </div>
        """

    extra = f"""
    <div class="card" style="margin-top:20px;">
        <h3>{'Vendor Bill Summary' if lang != 'ar' else 'ملخص فاتورة المورد'}</h3>
        <p><b>{'Source PO ID' if lang != 'ar' else 'رقم أمر الشراء'}:</b> {safe(row['source_po_id'])}</p>
        <p><b>{'Journal ID' if lang != 'ar' else 'رقم القيد'}:</b> {safe(row['journal_id'])}</p>
        <p><b>{'Reverse Journal ID' if lang != 'ar' else 'رقم قيد العكس'}:</b> {safe(row['reversed_journal_id'])}</p>
        <p><b>{'Paid / Allocated' if lang != 'ar' else 'المدفوع / المخصص'}:</b> {money(paid_amount)}</p>
        <p><b>{'Open Amount' if lang != 'ar' else 'المتبقي'}:</b> {money(open_amount)}</p>
    </div>
    {cash_allocations_html}

    <div class="form-actions" style="margin-top:16px;">
        {extra_buttons}
        {"<a class='btn blue' href='/ui/purchasing/purchase-orders/%s'>Open Source PO</a>" % safe(row['source_po_id']) if safe(row['source_po_id']) else ""}
        <a class="btn gray" href="/ui/accounting/vendor-bills">{'Back to Vendor Bills' if lang != 'ar' else 'الرجوع لفواتير الموردين'}</a>
    </div>
    """

    conn.close()
    return HTMLResponse(render_page("View Vendor Bill", content + extra + render_audit_log_card("vendor_bill", row_id), lang, current_path=str(request.url.path)))
