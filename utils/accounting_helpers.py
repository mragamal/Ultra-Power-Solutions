from db import get_conn


def money(x):
    try:
        return f"{float(x or 0):,.2f}"
    except Exception:
        return "0.00"


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name=?
    """, (table_name,)).fetchone()
    return bool(row)


def column_exists(conn, table_name: str, column_name: str) -> bool:
    if not table_exists(conn, table_name):
        return False

    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(c["name"] == column_name for c in cols)


def get_account_row(account_code: str):
    if not account_code:
        return None

    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT code, name, type, is_group, is_active
            FROM accounts
            WHERE code = ?
            LIMIT 1
        """, (account_code,)).fetchone()
        return row
    finally:
        conn.close()


def get_account_name(account_code: str) -> str:
    row = get_account_row(account_code)
    if row:
        return row["name"] or ""
    return ""


def account_display(account_code: str) -> str:
    if not account_code:
        return ""

    row = get_account_row(account_code)
    if row:
        code = row["code"] or ""
        name = row["name"] or ""
        if code and name:
            return f"{code} - {name}"
        return code or name

    return account_code


def get_partner_row(partner_id: int):
    if not partner_id:
        return None

    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT id, code, name, partner_type, phone, email, account_code
            FROM partners
            WHERE id = ?
            LIMIT 1
        """, (partner_id,)).fetchone()
        return row
    finally:
        conn.close()


def partner_display(partner_id: int) -> str:
    row = get_partner_row(partner_id)
    if row:
        code = row["code"] or ""
        name = row["name"] or ""
        if code and name:
            return f"{code} - {name}"
        return code or name
    return ""


def account_options(selected_code=None, include_blank=True, blank_label="-- Select Account --", active_only=True):
    conn = get_conn()
    try:
        sql = """
            SELECT code, name
            FROM accounts
            WHERE 1=1
        """
        params = []

        if active_only and column_exists(conn, "accounts", "is_active"):
            sql += " AND COALESCE(is_active, 1) = 1"

        sql += " ORDER BY code, name"

        rows = conn.execute(sql, params).fetchall()

        html = ""
        if include_blank:
            html += f"<option value=''>{blank_label}</option>"

        for r in rows:
            code = r["code"] or ""
            name = r["name"] or ""
            selected = "selected" if str(selected_code or "") == str(code) else ""
            html += f"<option value='{code}' {selected}>{code} - {name}</option>"

        return html
    finally:
        conn.close()


def partner_options(partner_type=None, selected_id=None, include_blank=True):
    conn = get_conn()
    try:
        sql = """
            SELECT id, code, name, partner_type
            FROM partners
            WHERE 1=1
        """
        params = []

        if partner_type:
            sql += " AND COALESCE(partner_type, '') = ?"
            params.append(partner_type)

        if column_exists(conn, "partners", "is_active"):
            sql += " AND COALESCE(is_active, 1) = 1"

        sql += " ORDER BY name, code"

        rows = conn.execute(sql, params).fetchall()

        label_map = {
            "customer": "-- Select Customer --",
            "vendor": "-- Select Vendor --",
            "employee": "-- Select Employee --",
        }

        html = ""
        if include_blank:
            html += f"<option value=''>{label_map.get(partner_type, '-- Select Partner --')}</option>"

        for r in rows:
            selected = "selected" if str(selected_id or "") == str(r["id"]) else ""
            code = r["code"] or ""
            name = r["name"] or ""
            text = f"{code} - {name}" if code else name
            html += f"<option value='{r['id']}' {selected}>{text}</option>"

        return html
    finally:
        conn.close()


def get_setting_value(key: str, default=None, conn=None):
    external_conn = conn is not None
    if not external_conn:
        conn = get_conn()
    try:
        if not table_exists(conn, "accounting_settings"):
            return default

        row = conn.execute("""
            SELECT value
            FROM accounting_settings
            WHERE key = ?
            LIMIT 1
        """, (key,)).fetchone()

        if row and row["value"] not in [None, ""]:
            return row["value"]

        return default
    except Exception:
        return default
    finally:
        if not external_conn:
            conn.close()


def get_control_account_for_partner_type(partner_type: str) -> str:
    partner_type = (partner_type or "").strip().lower()

    if partner_type == "customer":
        return (
            get_setting_value("default_customer_account", "")
            or get_setting_value("customer_control_account", "")
            or get_setting_value("default_receivables_account", "")
        )

    if partner_type == "vendor":
        return (
            get_setting_value("default_vendor_account", "")
            or get_setting_value("vendor_control_account", "")
            or get_setting_value("default_payables_account", "")
        )

    if partner_type == "employee":
        return (
            get_setting_value("employee_custody_account", "")
            or get_setting_value("default_employee_account", "")
            or get_setting_value("employee_advance_account", "")
        )

    return ""


def get_journal_source_label(source_type: str, source_id):
    if not source_type or not source_id:
        return ""

    labels = {
        "customer_invoice": "Customer Invoice",
        "customer_payment": "Customer Payment",
        "vendor_bill": "Vendor Bill",
        "vendor_payment": "Vendor Payment",
        "expense": "Expense",
        "petty_cash": "Petty Cash",
        "fixed_asset": "Fixed Asset",
        "depreciation": "Depreciation",
    }

    return f"{labels.get(source_type, source_type)} #{source_id}"