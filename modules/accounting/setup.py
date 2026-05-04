from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page
from i18n import get_lang
from modules.accounting.chart_generator import (
    generate_chart,
    apply_chart_to_db,
    get_default_account_mapping,
)

router = APIRouter()


def tr(lang: str, en: str, ar: str) -> str:
    return ar if lang == "ar" else en


# =========================================================
# DB HELPERS
# =========================================================
def column_exists(conn, table_name, column_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(r["name"] == column_name for r in rows)


def table_exists(conn, table_name):
    row = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
    """, (table_name,)).fetchone()
    return row is not None


def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_setup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT,
            company_name_ar TEXT,
            logo_path TEXT,
            base_currency TEXT,
            fiscal_year_start TEXT,
            business_type TEXT,
            country TEXT,
            city TEXT,
            address TEXT,
            phone TEXT,
            email TEXT,
            tax_no TEXT,
            use_inventory INTEGER DEFAULT 0,
            use_cost_centers INTEGER DEFAULT 0,
            use_projects INTEGER DEFAULT 0,
            use_branches INTEGER DEFAULT 0,
            use_vat INTEGER DEFAULT 0,
            use_wht INTEGER DEFAULT 0,
            use_petty_cash INTEGER DEFAULT 0,
            include_fixed_assets INTEGER DEFAULT 1,
            include_receivables_payables INTEGER DEFAULT 1,
            is_initialized INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    alter_statements = [
        ("company_name", "ALTER TABLE system_setup ADD COLUMN company_name TEXT"),
        ("company_name_ar", "ALTER TABLE system_setup ADD COLUMN company_name_ar TEXT"),
        ("logo_path", "ALTER TABLE system_setup ADD COLUMN logo_path TEXT"),
        ("base_currency", "ALTER TABLE system_setup ADD COLUMN base_currency TEXT"),
        ("fiscal_year_start", "ALTER TABLE system_setup ADD COLUMN fiscal_year_start TEXT"),
        ("business_type", "ALTER TABLE system_setup ADD COLUMN business_type TEXT"),
        ("country", "ALTER TABLE system_setup ADD COLUMN country TEXT"),
        ("city", "ALTER TABLE system_setup ADD COLUMN city TEXT"),
        ("address", "ALTER TABLE system_setup ADD COLUMN address TEXT"),
        ("phone", "ALTER TABLE system_setup ADD COLUMN phone TEXT"),
        ("email", "ALTER TABLE system_setup ADD COLUMN email TEXT"),
        ("tax_no", "ALTER TABLE system_setup ADD COLUMN tax_no TEXT"),
        ("use_inventory", "ALTER TABLE system_setup ADD COLUMN use_inventory INTEGER DEFAULT 0"),
        ("use_cost_centers", "ALTER TABLE system_setup ADD COLUMN use_cost_centers INTEGER DEFAULT 0"),
        ("use_projects", "ALTER TABLE system_setup ADD COLUMN use_projects INTEGER DEFAULT 0"),
        ("use_branches", "ALTER TABLE system_setup ADD COLUMN use_branches INTEGER DEFAULT 0"),
        ("use_vat", "ALTER TABLE system_setup ADD COLUMN use_vat INTEGER DEFAULT 0"),
        ("use_wht", "ALTER TABLE system_setup ADD COLUMN use_wht INTEGER DEFAULT 0"),
        ("use_petty_cash", "ALTER TABLE system_setup ADD COLUMN use_petty_cash INTEGER DEFAULT 0"),
        ("include_fixed_assets", "ALTER TABLE system_setup ADD COLUMN include_fixed_assets INTEGER DEFAULT 1"),
        ("include_receivables_payables", "ALTER TABLE system_setup ADD COLUMN include_receivables_payables INTEGER DEFAULT 1"),
        ("is_initialized", "ALTER TABLE system_setup ADD COLUMN is_initialized INTEGER DEFAULT 0"),
        ("created_at", "ALTER TABLE system_setup ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP"),
        ("updated_at", "ALTER TABLE system_setup ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP"),
    ]

    for col, stmt in alter_statements:
        if not column_exists(conn, "system_setup", col):
            conn.execute(stmt)

    conn.commit()
    conn.close()


ensure_tables()


def to_int_flag(v):
    try:
        return 1 if int(v or 0) == 1 else 0
    except Exception:
        return 0


def yes_no(v, lang="en"):
    return tr(lang, "Yes", "نعم") if int(v or 0) == 1 else tr(lang, "No", "لا")


# =========================================================
# SETTINGS HELPERS
# =========================================================
def get_setup_row():
    conn = get_conn()
    row = conn.execute("""
        SELECT *
        FROM system_setup
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()
    return row


def get_setting(conn, key_name: str, default_value: str = ""):
    row = conn.execute("""
        SELECT value
        FROM settings
        WHERE key = ?
        LIMIT 1
    """, (key_name,)).fetchone()
    if row and row["value"] is not None:
        return str(row["value"]).strip()
    return default_value


def set_setting(conn, key_name: str, value: str):
    existing = conn.execute("""
        SELECT key
        FROM settings
        WHERE key = ?
        LIMIT 1
    """, (key_name,)).fetchone()

    if existing:
        conn.execute("""
            UPDATE settings
            SET value = ?
            WHERE key = ?
        """, (value or "", key_name))
    else:
        conn.execute("""
            INSERT INTO settings (key, value)
            VALUES (?, ?)
        """, (key_name, value or ""))


# =========================================================
# CHART SAFETY HELPERS
# =========================================================
def account_count(conn) -> int:
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()
        return int(row["c"] or 0)
    except Exception:
        return 0


def posted_journal_count(conn) -> int:
    try:
        row = conn.execute("""
            SELECT COUNT(*) AS c
            FROM journal_entries
            WHERE LOWER(COALESCE(status,'')) = 'posted'
        """).fetchone()
        return int(row["c"] or 0)
    except Exception:
        return 0


def can_replace_chart(conn):
    return posted_journal_count(conn) == 0


def wipe_chart_accounts(conn):
    """
    Safe only before transactions exist.
    """
    if not can_replace_chart(conn):
        raise Exception("Cannot replace chart because posted journal entries already exist.")

    conn.execute("DELETE FROM accounts")


def save_default_account_settings(conn, mapping: dict):
    """
    Normalize the generated chart defaults into the keys used by the rest of the system.
    """
    key_map = {
        "customer_control_account": mapping.get("customer_control_account", ""),
        "vendor_control_account": mapping.get("vendor_control_account", ""),
        "sales_revenue_account_code": mapping.get("sales_revenue_account_code", ""),
        "input_vat_account": mapping.get("input_vat_account", ""),
        "output_vat_account": mapping.get("output_vat_account", ""),
        "wht_receivable_account": mapping.get("wht_receivable_account", ""),
        "wht_payable_account": mapping.get("wht_payable_account", ""),
        "default_cash_account": mapping.get("default_cash_account", "111100"),
        "default_bank_account": mapping.get("default_bank_account", "111150"),
        "default_petty_cash_account": mapping.get("default_petty_cash_account", "111200"),
        "customer_invoice_prefix": mapping.get("customer_invoice_prefix", "INV"),
        "vendor_bill_prefix": mapping.get("vendor_bill_prefix", "VBILL"),
        "customer_payment_prefix": mapping.get("customer_payment_prefix", "CP"),
        "vendor_payment_prefix": mapping.get("vendor_payment_prefix", "VP"),
        "journal_prefix": mapping.get("journal_prefix", "JV"),
    }

    for k, v in key_map.items():
        if v:
            set_setting(conn, k, str(v))


# =========================================================
# FORM / PREVIEW HELPERS
# =========================================================
def checked_attr(v):
    return "checked" if int(v or 0) == 1 else ""


def selected_attr(actual, expected):
    return "selected" if str(actual or "") == str(expected) else ""


def business_type_options(selected_value="service", lang="en"):
    return f"""
        <option value="service" {selected_attr(selected_value, "service")}>{tr(lang, "Service", "خدمي")}</option>
        <option value="trading" {selected_attr(selected_value, "trading")}>{tr(lang, "Trading", "تجاري")}</option>
        <option value="manufacturing" {selected_attr(selected_value, "manufacturing")}>{tr(lang, "Manufacturing", "تصنيعي")}</option>
    """


def currency_options(selected_value="EGP"):
    return f"""
        <option value="EGP" {selected_attr(selected_value, "EGP")}>EGP</option>
        <option value="SAR" {selected_attr(selected_value, "SAR")}>SAR</option>
        <option value="USD" {selected_attr(selected_value, "USD")}>USD</option>
        <option value="EUR" {selected_attr(selected_value, "EUR")}>EUR</option>
    """


def get_form_defaults(source=None):
    source = source or {}
    return {
        "company_name": source.get("company_name", ""),
        "company_name_ar": source.get("company_name_ar", ""),
        "logo_path": source.get("logo_path", "/static/logo.png"),
        "base_currency": source.get("base_currency", "EGP"),
        "fiscal_year_start": source.get("fiscal_year_start", ""),
        "business_type": source.get("business_type", "service"),
        "country": source.get("country", "Egypt"),
        "city": source.get("city", ""),
        "address": source.get("address", ""),
        "phone": source.get("phone", ""),
        "email": source.get("email", ""),
        "tax_no": source.get("tax_no", ""),
        "use_inventory": int(source.get("use_inventory", 0) or 0),
        "use_cost_centers": int(source.get("use_cost_centers", 0) or 0),
        "use_projects": int(source.get("use_projects", 0) or 0),
        "use_branches": int(source.get("use_branches", 0) or 0),
        "use_vat": int(source.get("use_vat", 0) or 0),
        "use_wht": int(source.get("use_wht", 0) or 0),
        "use_petty_cash": int(source.get("use_petty_cash", 0) or 0),
        "include_fixed_assets": int(source.get("include_fixed_assets", 1) or 0),
        "include_receivables_payables": int(source.get("include_receivables_payables", 1) or 0),
    }


def render_preview_table(rows, lang="en"):
    if not rows:
        return f"""
        <div class="card">
            <h3>{tr(lang, "Chart Preview", "معاينة شجرة الحسابات")}</h3>
            <p>{tr(lang, "No preview generated yet.", "لا توجد معاينة متاحة حتى الآن.")}</p>
        </div>
        """

    body = ""
    for r in rows:
        body += f"""
        <tr>
            <td>{r.get('code','')}</td>
            <td>{r.get('name','')}</td>
            <td>{r.get('type','')}</td>
            <td>{r.get('parent_code','')}</td>
            <td>{yes_no(r.get('is_group',0), lang)}</td>
            <td>{yes_no(r.get('is_active',1), lang)}</td>
        </tr>
        """

    return f"""
    <div class="card">
        <h3>{tr(lang, "Chart Preview", "معاينة شجرة الحسابات")}</h3>
        <p><b>{tr(lang, "Total Accounts:", "إجمالي الحسابات:")}</b> {len(rows)}</p>
        <table>
            <tr>
                <th>{tr(lang, "Code", "الكود")}</th>
                <th>{tr(lang, "Name", "الاسم")}</th>
                <th>{tr(lang, "Type", "النوع")}</th>
                <th>{tr(lang, "Parent Code", "كود الأب")}</th>
                <th>{tr(lang, "Group", "مجموعة")}</th>
                <th>{tr(lang, "Active", "نشط")}</th>
            </tr>
            {body}
        </table>
    </div>
    """


def render_setup_form(
    request: Request,
    values=None,
    error_message: str = "",
    success_message: str = "",
    preview_rows=None,
    preview_errors=None,
):
    lang = get_lang(request)
    row = get_setup_row()
    db_values = get_form_defaults(dict(row) if row else {})
    values = get_form_defaults(values or db_values)

    is_initialized = int(row["is_initialized"] or 0) if row else 0

    conn = get_conn()
    current_account_count = account_count(conn)
    replace_allowed = can_replace_chart(conn)
    current_posted = posted_journal_count(conn)
    conn.close()

    status_badge = (
        f'<span style="padding:6px 12px;border-radius:999px;background:#dcfce7;color:#166534;font-weight:700;">{tr(lang, "Initialized", "مفعل")}</span>'
        if is_initialized
        else f'<span style="padding:6px 12px;border-radius:999px;background:#fef3c7;color:#92400e;font-weight:700;">{tr(lang, "Not Initialized", "غير مفعل")}</span>'
    )

    logo_preview = ""
    if str(values["logo_path"]).strip():
        logo_preview = f"""
            <div style="margin-top:10px;">
            <div style="font-size:13px;color:#6b7280;margin-bottom:8px;">{tr(lang, "Logo Preview", "معاينة الشعار")}</div>
            <div style="width:110px;height:110px;border:1px solid #e5e7eb;border-radius:14px;background:#fff;display:flex;align-items:center;justify-content:center;overflow:hidden;padding:8px;">
                <img src="{values["logo_path"]}" alt="Logo" style="max-width:100%;max-height:100%;object-fit:contain;" onerror="this.style.display='none'">
            </div>
        </div>
        """

    alert_html = ""
    if error_message:
        alert_html += f"""
        <div class="card" style="border-left:4px solid #dc2626;">
            <div style="color:#991b1b;font-weight:700;">{error_message}</div>
        </div>
        """
    if success_message:
        alert_html += f"""
        <div class="card" style="border-left:4px solid #16a34a;">
            <div style="color:#166534;font-weight:700;">{success_message}</div>
        </div>
        """

    preview_error_html = ""
    if preview_errors:
        items = "".join([f"<li>{e}</li>" for e in preview_errors])
        preview_error_html = f"""
        <div class="card" style="border-left:4px solid #dc2626;">
            <h3>{tr(lang, "Preview Errors", "أخطاء المعاينة")}</h3>
            <ul>{items}</ul>
        </div>
        """

    chart_status = f"""
    <div class="card">
        <h3>{tr(lang, "Chart Status", "حالة شجرة الحسابات")}</h3>
        <p><b>{tr(lang, "Accounts Count:", "عدد الحسابات:")}</b> {current_account_count}</p>
        <p><b>{tr(lang, "Posted Journal Entries:", "عدد القيود المرحلة:")}</b> {current_posted}</p>
        <p><b>{tr(lang, "Replace Chart Allowed:", "السماح باستبدال الشجرة:")}</b> {tr(lang, "Yes", "نعم") if replace_allowed else tr(lang, "No", "لا")}</p>
    </div>
    """

    content = f"""
    {alert_html}

    <div class="card">
        <div class="toolbar">
            <div>
                <div class="section-title">{tr(lang, "System Setup", "تهيئة النظام")}</div>
                <div class="muted">{tr(lang, "Company profile + chart generator + feature-based chart application.", "بيانات الشركة + مولد شجرة الحسابات + تطبيق الخصائص على الدليل المحاسبي.")}</div>
            </div>
            <div>{status_badge}</div>
        </div>

        <form method="post" action="/ui/accounting/setup/save">
            <div class="section-title" style="margin-top:18px;">{tr(lang, "Company Information", "بيانات الشركة")}</div>
            <div class="form-grid">
                <div class="form-group">
                    <label>{tr(lang, "Company Name", "اسم الشركة")}</label>
                    <input type="text" name="company_name" value="{values["company_name"]}">
                </div>

                <div class="form-group">
                    <label>{tr(lang, "Company Name (Arabic)", "اسم الشركة بالعربية")}</label>
                    <input type="text" name="company_name_ar" value="{values["company_name_ar"]}">
                </div>

                <div class="form-group">
                    <label>{tr(lang, "Logo Path", "مسار الشعار")}</label>
                    <input type="text" name="logo_path" value="{values["logo_path"]}">
                    {logo_preview}
                </div>

                <div class="form-group">
                    <label>{tr(lang, "Base Currency", "العملة الأساسية")}</label>
                    <select name="base_currency">
                        {currency_options(values["base_currency"])}
                    </select>
                </div>

                <div class="form-group">
                    <label>{tr(lang, "Fiscal Year Start", "بداية السنة المالية")}</label>
                    <input type="date" name="fiscal_year_start" value="{values["fiscal_year_start"]}">
                </div>

                <div class="form-group">
                    <label>{tr(lang, "Business Type", "نوع النشاط")}</label>
                    <select name="business_type">
                        {business_type_options(values["business_type"], lang)}
                    </select>
                </div>

                <div class="form-group">
                    <label>{tr(lang, "Country", "الدولة")}</label>
                    <input type="text" name="country" value="{values["country"]}">
                </div>

                <div class="form-group">
                    <label>{tr(lang, "City", "المدينة")}</label>
                    <input type="text" name="city" value="{values["city"]}">
                </div>

                <div class="form-group">
                    <label>{tr(lang, "Address", "العنوان")}</label>
                    <input type="text" name="address" value="{values["address"]}">
                </div>

                <div class="form-group">
                    <label>{tr(lang, "Phone", "الهاتف")}</label>
                    <input type="text" name="phone" value="{values["phone"]}">
                </div>

                <div class="form-group">
                    <label>{tr(lang, "Email", "البريد الإلكتروني")}</label>
                    <input type="text" name="email" value="{values["email"]}">
                </div>

                <div class="form-group">
                    <label>{tr(lang, "Tax No", "الرقم الضريبي")}</label>
                    <input type="text" name="tax_no" value="{values["tax_no"]}">
                </div>
            </div>

            <div class="section-title" style="margin-top:24px;">{tr(lang, "Chart Options", "خيارات الشجرة")}</div>
            <div class="form-grid">
                <div class="form-group">
                    <label><input type="checkbox" name="use_inventory" value="1" {checked_attr(values["use_inventory"])}> {tr(lang, "Inventory", "المخازن")}</label>
                </div>

                <div class="form-group">
                    <label><input type="checkbox" name="use_cost_centers" value="1" {checked_attr(values["use_cost_centers"])}> {tr(lang, "Cost Centers", "مراكز التكلفة")}</label>
                </div>

                <div class="form-group">
                    <label><input type="checkbox" name="use_projects" value="1" {checked_attr(values["use_projects"])}> {tr(lang, "Projects", "المشروعات")}</label>
                </div>

                <div class="form-group">
                    <label><input type="checkbox" name="use_branches" value="1" {checked_attr(values["use_branches"])}> {tr(lang, "Branches", "الفروع")}</label>
                </div>

                <div class="form-group">
                    <label><input type="checkbox" name="use_vat" value="1" {checked_attr(values["use_vat"])}> VAT</label>
                </div>

                <div class="form-group">
                    <label><input type="checkbox" name="use_wht" value="1" {checked_attr(values["use_wht"])}> WHT</label>
                </div>

                <div class="form-group">
                    <label><input type="checkbox" name="use_petty_cash" value="1" {checked_attr(values["use_petty_cash"])}> {tr(lang, "Petty Cash", "العهدة النقدية")}</label>
                </div>

                <div class="form-group">
                    <label><input type="checkbox" name="include_fixed_assets" value="1" {checked_attr(values["include_fixed_assets"])}> {tr(lang, "Fixed Assets", "الأصول الثابتة")}</label>
                </div>

                <div class="form-group">
                    <label><input type="checkbox" name="include_receivables_payables" value="1" {checked_attr(values["include_receivables_payables"])}> {tr(lang, "Receivables / Payables", "المدينون / الدائنون")}</label>
                </div>
            </div>

            <div class="form-actions" style="margin-top:18px;">
                <button class="btn green" type="submit">{tr(lang, "Save Setup", "حفظ التهيئة")}</button>
                <a class="btn gray" href="/ui/accounting/setup?lang={lang}">{tr(lang, "Refresh", "تحديث")}</a>
            </div>
        </form>
    </div>

    {chart_status}

    <div class="card">
        <h3>{tr(lang, "Chart Generator", "مولد شجرة الحسابات")}</h3>

        <form method="post" action="/ui/accounting/setup/preview-chart">
            <input type="hidden" name="company_name" value="{values["company_name"]}">
            <input type="hidden" name="company_name_ar" value="{values["company_name_ar"]}">
            <input type="hidden" name="logo_path" value="{values["logo_path"]}">
            <input type="hidden" name="base_currency" value="{values["base_currency"]}">
            <input type="hidden" name="fiscal_year_start" value="{values["fiscal_year_start"]}">
            <input type="hidden" name="business_type" value="{values["business_type"]}">
            <input type="hidden" name="country" value="{values["country"]}">
            <input type="hidden" name="city" value="{values["city"]}">
            <input type="hidden" name="address" value="{values["address"]}">
            <input type="hidden" name="phone" value="{values["phone"]}">
            <input type="hidden" name="email" value="{values["email"]}">
            <input type="hidden" name="tax_no" value="{values["tax_no"]}">
            <input type="hidden" name="use_inventory" value="{values["use_inventory"]}">
            <input type="hidden" name="use_cost_centers" value="{values["use_cost_centers"]}">
            <input type="hidden" name="use_projects" value="{values["use_projects"]}">
            <input type="hidden" name="use_branches" value="{values["use_branches"]}">
            <input type="hidden" name="use_vat" value="{values["use_vat"]}">
            <input type="hidden" name="use_wht" value="{values["use_wht"]}">
            <input type="hidden" name="use_petty_cash" value="{values["use_petty_cash"]}">
            <input type="hidden" name="include_fixed_assets" value="{values["include_fixed_assets"]}">
            <input type="hidden" name="include_receivables_payables" value="{values["include_receivables_payables"]}">

            <div class="form-actions">
                <button class="btn blue" type="submit">{tr(lang, "Preview Chart", "معاينة الشجرة")}</button>
            </div>
        </form>

        <form method="post" action="/ui/accounting/setup/apply-chart" style="margin-top:12px;">
            <input type="hidden" name="business_type" value="{values["business_type"]}">
            <input type="hidden" name="use_inventory" value="{values["use_inventory"]}">
            <input type="hidden" name="use_cost_centers" value="{values["use_cost_centers"]}">
            <input type="hidden" name="use_projects" value="{values["use_projects"]}">
            <input type="hidden" name="use_branches" value="{values["use_branches"]}">
            <input type="hidden" name="use_vat" value="{values["use_vat"]}">
            <input type="hidden" name="use_wht" value="{values["use_wht"]}">
            <input type="hidden" name="use_petty_cash" value="{values["use_petty_cash"]}">
            <input type="hidden" name="include_fixed_assets" value="{values["include_fixed_assets"]}">
            <input type="hidden" name="include_receivables_payables" value="{values["include_receivables_payables"]}">

            <div class="form-grid">
                <div class="form-group">
                    <label>{tr(lang, "Apply Mode", "طريقة التطبيق")}</label>
                    <select name="apply_mode">
                        <option value="merge">{tr(lang, "Merge with existing chart", "دمج مع الشجرة الحالية")}</option>
                        <option value="replace">{tr(lang, "Replace existing chart", "استبدال الشجرة الحالية")}</option>
                    </select>
                </div>
            </div>

            <div class="form-actions">
                <button class="btn green" type="submit">{tr(lang, "Apply Chart", "تطبيق الشجرة")}</button>
            </div>
        </form>
    </div>

    {preview_error_html}
    {render_preview_table(preview_rows or [], lang)}
    """

    return HTMLResponse(render_page(tr(lang, "System Setup", "تهيئة النظام"), content, lang, current_path=str(request.url.path)))


# =========================================================
# FORM PARSER
# =========================================================
def collect_form_values(
    company_name="",
    company_name_ar="",
    logo_path="",
    base_currency="EGP",
    fiscal_year_start="",
    business_type="service",
    country="Egypt",
    city="",
    address="",
    phone="",
    email="",
    tax_no="",
    use_inventory=0,
    use_cost_centers=0,
    use_projects=0,
    use_branches=0,
    use_vat=0,
    use_wht=0,
    use_petty_cash=0,
    include_fixed_assets=1,
    include_receivables_payables=1,
):
    return {
        "company_name": company_name or "",
        "company_name_ar": company_name_ar or "",
        "logo_path": logo_path or "",
        "base_currency": base_currency or "EGP",
        "fiscal_year_start": fiscal_year_start or "",
        "business_type": business_type or "service",
        "country": country or "Egypt",
        "city": city or "",
        "address": address or "",
        "phone": phone or "",
        "email": email or "",
        "tax_no": tax_no or "",
        "use_inventory": to_int_flag(use_inventory),
        "use_cost_centers": to_int_flag(use_cost_centers),
        "use_projects": to_int_flag(use_projects),
        "use_branches": to_int_flag(use_branches),
        "use_vat": to_int_flag(use_vat),
        "use_wht": to_int_flag(use_wht),
        "use_petty_cash": to_int_flag(use_petty_cash),
        "include_fixed_assets": to_int_flag(include_fixed_assets if include_fixed_assets is not None else 1),
        "include_receivables_payables": to_int_flag(include_receivables_payables if include_receivables_payables is not None else 1),
    }


# =========================================================
# SAVE SETUP DATA
# =========================================================
def save_setup_data(conn, values: dict):
    existing = conn.execute("""
        SELECT id
        FROM system_setup
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    if existing:
        conn.execute("""
            UPDATE system_setup
            SET
                company_name = ?,
                company_name_ar = ?,
                logo_path = ?,
                base_currency = ?,
                fiscal_year_start = ?,
                business_type = ?,
                country = ?,
                city = ?,
                address = ?,
                phone = ?,
                email = ?,
                tax_no = ?,
                use_inventory = ?,
                use_cost_centers = ?,
                use_projects = ?,
                use_branches = ?,
                use_vat = ?,
                use_wht = ?,
                use_petty_cash = ?,
                include_fixed_assets = ?,
                include_receivables_payables = ?,
                is_initialized = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            values["company_name"],
            values["company_name_ar"],
            values["logo_path"],
            values["base_currency"],
            values["fiscal_year_start"],
            values["business_type"],
            values["country"],
            values["city"],
            values["address"],
            values["phone"],
            values["email"],
            values["tax_no"],
            values["use_inventory"],
            values["use_cost_centers"],
            values["use_projects"],
            values["use_branches"],
            values["use_vat"],
            values["use_wht"],
            values["use_petty_cash"],
            values["include_fixed_assets"],
            values["include_receivables_payables"],
            existing["id"],
        ))
    else:
        conn.execute("""
            INSERT INTO system_setup (
                company_name,
                company_name_ar,
                logo_path,
                base_currency,
                fiscal_year_start,
                business_type,
                country,
                city,
                address,
                phone,
                email,
                tax_no,
                use_inventory,
                use_cost_centers,
                use_projects,
                use_branches,
                use_vat,
                use_wht,
                use_petty_cash,
                include_fixed_assets,
                include_receivables_payables,
                is_initialized
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            values["company_name"],
            values["company_name_ar"],
            values["logo_path"],
            values["base_currency"],
            values["fiscal_year_start"],
            values["business_type"],
            values["country"],
            values["city"],
            values["address"],
            values["phone"],
            values["email"],
            values["tax_no"],
            values["use_inventory"],
            values["use_cost_centers"],
            values["use_projects"],
            values["use_branches"],
            values["use_vat"],
            values["use_wht"],
            values["use_petty_cash"],
            values["include_fixed_assets"],
            values["include_receivables_payables"],
        ))


# =========================================================
# ROUTES
# =========================================================
@router.get("/ui/accounting/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    row = get_setup_row()
    values = get_form_defaults(dict(row) if row else {})
    return render_setup_form(request, values=values)


@router.post("/ui/accounting/setup/save")
def save_setup(
    request: Request,
    company_name: str = Form(""),
    company_name_ar: str = Form(""),
    logo_path: str = Form(""),
    base_currency: str = Form("EGP"),
    fiscal_year_start: str = Form(""),
    business_type: str = Form("service"),
    country: str = Form("Egypt"),
    city: str = Form(""),
    address: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    tax_no: str = Form(""),
    use_inventory: int = Form(0),
    use_cost_centers: int = Form(0),
    use_projects: int = Form(0),
    use_branches: int = Form(0),
    use_vat: int = Form(0),
    use_wht: int = Form(0),
    use_petty_cash: int = Form(0),
    include_fixed_assets: int = Form(1),
    include_receivables_payables: int = Form(1),
):
    values = collect_form_values(
        company_name=company_name,
        company_name_ar=company_name_ar,
        logo_path=logo_path,
        base_currency=base_currency,
        fiscal_year_start=fiscal_year_start,
        business_type=business_type,
        country=country,
        city=city,
        address=address,
        phone=phone,
        email=email,
        tax_no=tax_no,
        use_inventory=use_inventory,
        use_cost_centers=use_cost_centers,
        use_projects=use_projects,
        use_branches=use_branches,
        use_vat=use_vat,
        use_wht=use_wht,
        use_petty_cash=use_petty_cash,
        include_fixed_assets=include_fixed_assets,
        include_receivables_payables=include_receivables_payables,
    )

    conn = get_conn()
    try:
        save_setup_data(conn, values)
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return render_setup_form(request, values=values, error_message=f"{tr(get_lang(request), 'Save failed:', 'فشل الحفظ:')} {str(e)}")

    conn.close()
    return render_setup_form(request, values=values, success_message=tr(get_lang(request), "Setup saved successfully.", "تم حفظ التهيئة بنجاح."))


@router.post("/ui/accounting/setup/preview-chart")
def preview_chart(
    request: Request,
    company_name: str = Form(""),
    company_name_ar: str = Form(""),
    logo_path: str = Form(""),
    base_currency: str = Form("EGP"),
    fiscal_year_start: str = Form(""),
    business_type: str = Form("service"),
    country: str = Form("Egypt"),
    city: str = Form(""),
    address: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    tax_no: str = Form(""),
    use_inventory: int = Form(0),
    use_cost_centers: int = Form(0),
    use_projects: int = Form(0),
    use_branches: int = Form(0),
    use_vat: int = Form(0),
    use_wht: int = Form(0),
    use_petty_cash: int = Form(0),
    include_fixed_assets: int = Form(1),
    include_receivables_payables: int = Form(1),
):
    values = collect_form_values(
        company_name=company_name,
        company_name_ar=company_name_ar,
        logo_path=logo_path,
        base_currency=base_currency,
        fiscal_year_start=fiscal_year_start,
        business_type=business_type,
        country=country,
        city=city,
        address=address,
        phone=phone,
        email=email,
        tax_no=tax_no,
        use_inventory=use_inventory,
        use_cost_centers=use_cost_centers,
        use_projects=use_projects,
        use_branches=use_branches,
        use_vat=use_vat,
        use_wht=use_wht,
        use_petty_cash=use_petty_cash,
        include_fixed_assets=include_fixed_assets,
        include_receivables_payables=include_receivables_payables,
    )

    preview = generate_chart(
        activity_type=values["business_type"],
        use_inventory=bool(values["use_inventory"]),
        use_cost_centers=bool(values["use_cost_centers"]),
        use_projects=bool(values["use_projects"]),
        use_branches=bool(values["use_branches"]),
        use_vat=bool(values["use_vat"]),
        use_wht=bool(values["use_wht"]),
        use_petty_cash=bool(values["use_petty_cash"]),
        include_fixed_assets=bool(values["include_fixed_assets"]),
        include_receivables_payables=bool(values["include_receivables_payables"]),
    )

    return render_setup_form(
        request,
        values=values,
        preview_rows=preview.get("rows", []),
        preview_errors=preview.get("errors", []),
        success_message=tr(get_lang(request), "Preview generated.", "تم إنشاء المعاينة.") if preview.get("ok") else "",
    )


@router.post("/ui/accounting/setup/apply-chart")
def apply_chart(
    request: Request,
    business_type: str = Form("service"),
    use_inventory: int = Form(0),
    use_cost_centers: int = Form(0),
    use_projects: int = Form(0),
    use_branches: int = Form(0),
    use_vat: int = Form(0),
    use_wht: int = Form(0),
    use_petty_cash: int = Form(0),
    include_fixed_assets: int = Form(1),
    include_receivables_payables: int = Form(1),
    apply_mode: str = Form("merge"),
):
    row = get_setup_row()
    values = get_form_defaults(dict(row) if row else {})
    values.update({
        "business_type": business_type or values["business_type"],
        "use_inventory": to_int_flag(use_inventory),
        "use_cost_centers": to_int_flag(use_cost_centers),
        "use_projects": to_int_flag(use_projects),
        "use_branches": to_int_flag(use_branches),
        "use_vat": to_int_flag(use_vat),
        "use_wht": to_int_flag(use_wht),
        "use_petty_cash": to_int_flag(use_petty_cash),
        "include_fixed_assets": to_int_flag(include_fixed_assets if include_fixed_assets is not None else 1),
        "include_receivables_payables": to_int_flag(include_receivables_payables if include_receivables_payables is not None else 1),
    })

    conn = get_conn()
    try:
        if (apply_mode or "").strip().lower() == "replace":
            wipe_chart_accounts(conn)
            conn.commit()

        result = apply_chart_to_db(
            activity_type=values["business_type"],
            use_inventory=bool(values["use_inventory"]),
            use_cost_centers=bool(values["use_cost_centers"]),
            use_projects=bool(values["use_projects"]),
            use_branches=bool(values["use_branches"]),
            use_vat=bool(values["use_vat"]),
            use_wht=bool(values["use_wht"]),
            use_petty_cash=bool(values["use_petty_cash"]),
            include_fixed_assets=bool(values["include_fixed_assets"]),
            include_receivables_payables=bool(values["include_receivables_payables"]),
            overwrite_names_and_types=True,
        )

        if not result.get("ok"):
            conn.close()
            return render_setup_form(
                request,
                values=values,
                preview_rows=result.get("rows", []),
                preview_errors=result.get("errors", []),
                error_message=tr(get_lang(request), "Chart application failed.", "فشل تطبيق الشجرة."),
            )

        mapping = get_default_account_mapping(
            activity_type=values["business_type"],
            use_vat=bool(values["use_vat"]),
            use_wht=bool(values["use_wht"]),
            use_petty_cash=bool(values["use_petty_cash"]),
        )

        save_default_account_settings(conn, mapping)
        save_setup_data(conn, values)
        conn.commit()

        if get_lang(request) == "ar":
            msg = f"تم تطبيق الشجرة بنجاح. مضاف: {result.get('inserted_count',0)}، محدث: {result.get('updated_count',0)}، إجمالي صفوف القالب: {result.get('total_count',0)}"
        else:
            msg = f"Chart applied successfully. Inserted: {result.get('inserted_count',0)}, Updated: {result.get('updated_count',0)}, Total Template Rows: {result.get('total_count',0)}"
        conn.close()
        return render_setup_form(request, values=values, success_message=msg)

    except Exception as e:
        conn.rollback()
        conn.close()
        return render_setup_form(request, values=values, error_message=f"{tr(get_lang(request), 'Apply failed:', 'فشل التطبيق:')} {str(e)}")


@router.get("/ui/accounting/setup/reset", response_class=HTMLResponse)
def reset_setup_warning(request: Request):
    conn = get_conn()
    allow_replace = can_replace_chart(conn)
    posted_count = posted_journal_count(conn)
    conn.close()

    msg = (
        tr(get_lang(request), "Replace is allowed because there are no posted journal entries.", "الاستبدال مسموح لأنه لا توجد قيود يومية مرحلة.")
        if allow_replace else
        (f"تم منع الاستبدال لأن هناك قيود يومية مرحلة بالفعل ({posted_count})." if get_lang(request) == "ar" else f"Replace is blocked because posted journal entries already exist ({posted_count}).")
    )

    content = f"""
    <div class="card">
        <h2>{tr(get_lang(request), "Setup / Chart Reset", "إعادة ضبط التهيئة / الشجرة")}</h2>
        <p>{msg}</p>
        <a class="btn gray" href="/ui/accounting/setup?lang={get_lang(request)}">{tr(get_lang(request), "Back", "رجوع")}</a>
    </div>
    """
    return HTMLResponse(render_page(tr(get_lang(request), "Setup Reset", "إعادة الضبط"), content, get_lang(request), current_path=str(request.url.path)))
