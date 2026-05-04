from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from i18n import get_lang
from layout import render_page

router = APIRouter()


def tr(lang: str, en: str, ar: str) -> str:
    return ar if lang == "ar" else en


def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounting_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            value TEXT,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    ensure_column(conn, "accounting_settings", "key", "ALTER TABLE accounting_settings ADD COLUMN key TEXT UNIQUE")
    ensure_column(conn, "accounting_settings", "value", "ALTER TABLE accounting_settings ADD COLUMN value TEXT")
    ensure_column(conn, "accounting_settings", "description", "ALTER TABLE accounting_settings ADD COLUMN description TEXT")
    ensure_column(conn, "accounting_settings", "created_at", "ALTER TABLE accounting_settings ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    conn.commit()
    conn.close()


ensure_tables()


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
        # default accounts
        "default_cash_account": "111100",
        "default_bank_account": "112000",
        "default_customer_account": "112100",
        "customer_control_account": "112100",
        "default_vendor_account": "211100",
        "vendor_control_account": "211100",
          "employee_custody_account": "1020504",
        "petty_cash_account": "111200",
        "sales_revenue_account_code": "410000",
        "purchase_account": "510000",
        "expense_account": "520000",
        "inventory_account": "113000",
        "cogs_account": "510100",
        "input_vat_account": "201020110",
        "output_vat_account": "201020110",
        "wht_receivable_account": "1020502",
        "wht_payable_account": "214200",
        "fixed_asset_account": "121000",
        "accumulated_depreciation_account": "121900",
        "depreciation_expense_account": "530000",

        # prefixes
        "journal_prefix": "JV",
        "customer_payment_prefix": "CP",
        "vendor_payment_prefix": "VP",
        "employee_custody_prefix": "EC",
        "customer_invoice_prefix": "INV",
        "vendor_bill_prefix": "VBILL",
        "expense_prefix": "EXP",
        "petty_cash_prefix": "PC",
    }

    return fallback.get(key, default)


def set_setting_value(key: str, value: str, description: str = ""):
    conn = get_conn()
    existing = conn.execute("""
        SELECT id
        FROM accounting_settings
        WHERE key = ?
        LIMIT 1
    """, (key,)).fetchone()

    if existing:
        conn.execute("""
            UPDATE accounting_settings
            SET value = ?, description = ?
            WHERE key = ?
        """, (value, description, key))
    else:
        conn.execute("""
            INSERT INTO accounting_settings (key, value, description)
            VALUES (?, ?, ?)
        """, (key, value, description))

    conn.commit()
    conn.close()


def all_settings():
    conn = get_conn()
    rows = conn.execute("""
        SELECT key, value, description
        FROM accounting_settings
        ORDER BY key
    """).fetchall()
    conn.close()
    return rows


def account_options(lang="en", selected_code=None):
    conn = get_conn()
    rows = conn.execute("""
        SELECT code, name
        FROM accounts
        WHERE COALESCE(is_active, 1) = 1
        ORDER BY code, name
    """).fetchall()
    conn.close()

    html = f"<option value=''>{tr(lang, '-- Select Account --', '-- اختر الحساب --')}</option>"
    for r in rows:
        selected = "selected" if str(selected_code or "") == str(r["code"] or "") else ""
        html += f"<option value='{r['code']}' {selected}>{r['code']} - {r['name']}</option>"
    return html


@router.get("/ui/accounting/config", response_class=HTMLResponse)
def config_page(request: Request):
    ensure_tables()
    lang = get_lang(request)

    account_fields = [
        ("default_cash_account", tr(lang, "Default Cash Account", "الحساب النقدي الافتراضي")),
        ("default_bank_account", tr(lang, "Default Bank Account", "الحساب البنكي الافتراضي")),
        ("default_customer_account", tr(lang, "Default Customer Account", "حساب العملاء الافتراضي")),
        ("default_vendor_account", tr(lang, "Default Vendor Account", "حساب الموردين الافتراضي")),
        ("employee_custody_account", tr(lang, "Employee Custody Account", "حساب عهد الموظفين")),
        ("employee_advance_account", tr(lang, "Employee Advance Account", "حساب سلف الموظفين")),
        ("petty_cash_account", tr(lang, "Petty Cash Account", "حساب العهدة النقدية")),
        ("sales_revenue_account_code", tr(lang, "Sales Revenue Account", "حساب إيرادات المبيعات")),
        ("purchase_account", tr(lang, "Purchase / Cost Account", "حساب المشتريات / التكلفة")),
        ("expense_account", tr(lang, "Default Expense Account", "حساب المصروفات الافتراضي")),
        ("inventory_account", tr(lang, "Inventory Account", "حساب المخزون")),
        ("cogs_account", tr(lang, "COGS Account", "حساب تكلفة البضاعة المباعة")),
        ("input_vat_account", tr(lang, "Input VAT Account", "حساب ضريبة القيمة المضافة - مدخلات")),
        ("output_vat_account", tr(lang, "Output VAT Account", "حساب ضريبة القيمة المضافة - مخرجات")),
        ("wht_receivable_account", tr(lang, "WHT Receivable Account", "حساب خصم المنبع - مدين")),
        ("wht_payable_account", tr(lang, "WHT Payable Account", "حساب خصم المنبع - دائن")),
        ("fixed_asset_account", tr(lang, "Fixed Asset Account", "حساب الأصول الثابتة")),
        ("accumulated_depreciation_account", tr(lang, "Accumulated Depreciation Account", "حساب مجمع الإهلاك")),
        ("depreciation_expense_account", tr(lang, "Depreciation Expense Account", "حساب مصروف الإهلاك")),
    ]

    payroll_fields = [
        ("payroll_salary_account", tr(lang, "Salary Expense Account", "حساب مصروف المرتب الأساسي")),
        ("payroll_housing_account", tr(lang, "Housing Allowance Expense Account", "حساب مصروف بدل السكن")),
        ("payroll_transport_account", tr(lang, "Transport Allowance Expense Account", "حساب مصروف بدل المواصلات")),
        ("payroll_other_allowance_account", tr(lang, "Other Allowance Expense Account", "حساب مصروف البدلات الأخرى")),
        ("payroll_overtime_account", tr(lang, "Overtime Expense Account", "حساب مصروف العمل الإضافي")),
        ("payroll_bonus_account", tr(lang, "Bonus Expense Account", "حساب مصروف المكافآت")),
        ("payroll_absence_account", tr(lang, "Absence Deduction Account", "حساب خصم الغياب (إيراد)")),
        ("payroll_advance_account", tr(lang, "Advance Deduction Payable Account", "حساب خصم السلف (دائن)")),
        ("payroll_insurance_employee_account", tr(lang, "Employee Insurance Payable Account", "حساب تأمينات الموظفين (دائن)")),
        ("payroll_insurance_employer_account", tr(lang, "Employer Insurance Expense Account", "حساب مصروف تأمينات صاحب العمل")),
        ("payroll_payable_account", tr(lang, "Net Salaries Payable Account", "حساب صافي المرتبات المستحقة")),
        ("payroll_bank_account", tr(lang, "Payroll Bank/Cash Account", "حساب البنك/النقد للصرف")),
    ]

    prefix_fields = [
        ("journal_prefix", tr(lang, "Journal Prefix", "بادئة اليومية")),
        ("customer_payment_prefix", tr(lang, "Customer Payment Prefix", "بادئة تحصيل العميل")),
        ("vendor_payment_prefix", tr(lang, "Vendor Payment Prefix", "بادئة سداد المورد")),
        ("employee_custody_prefix", tr(lang, "Employee Custody Prefix", "بادئة عهدة الموظف")),
        ("customer_invoice_prefix", tr(lang, "Customer Invoice Prefix", "بادئة فاتورة العميل")),
        ("vendor_bill_prefix", tr(lang, "Vendor Bill Prefix", "بادئة فاتورة المورد")),
        ("expense_prefix", tr(lang, "Expense Prefix", "بادئة المصروف")),
        ("petty_cash_prefix", tr(lang, "Petty Cash Prefix", "بادئة العهدة النقدية")),
    ]

    account_html = ""
    for key, label in account_fields:
        value = get_setting_value(key, "")
        account_html += f"""
        <div class="row" style="margin-bottom:12px;">
            <div class="col">
                <label>{label}</label>
                <select id="{key}" name="{key}">
                    {account_options(lang, value)}
                </select>
            </div>
        </div>
        """

    payroll_html = ""
    for key, label in payroll_fields:
        value = get_setting_value(key, "")
        payroll_html += f"""
        <div class="row" style="margin-bottom:12px;">
            <div class="col">
                <label>{label}</label>
                <select id="{key}" name="{key}">
                    {account_options(lang, value)}
                </select>
            </div>
        </div>
        """

    prefix_html = ""
    for key, label in prefix_fields:
        value = get_setting_value(key, "")
        prefix_html += f"""
        <div class="row" style="margin-bottom:12px;">
            <div class="col">
                <label>{label}</label>
                <input type="text" name="{key}" value="{value}">
            </div>
        </div>
        """

    rows_html = ""
    desc_map = {
        "Default Cash Account": tr(lang, "Default Cash Account", "الحساب النقدي الافتراضي"),
        "Default Bank Account": tr(lang, "Default Bank Account", "الحساب البنكي الافتراضي"),
        "Default Customer Account": tr(lang, "Default Customer Account", "حساب العملاء الافتراضي"),
        "Customer Control Account": tr(lang, "Customer Control Account", "حساب مراقبة العملاء"),
        "Default Vendor Account": tr(lang, "Default Vendor Account", "حساب الموردين الافتراضي"),
        "Vendor Control Account": tr(lang, "Vendor Control Account", "حساب مراقبة الموردين"),
        "Employee Custody Account": tr(lang, "Employee Custody Account", "حساب عهد الموظفين"),
        "Petty Cash Account": tr(lang, "Petty Cash Account", "حساب العهدة النقدية"),
        "Sales Revenue Account": tr(lang, "Sales Revenue Account", "حساب إيرادات المبيعات"),
        "Purchase / Cost Account": tr(lang, "Purchase / Cost Account", "حساب المشتريات / التكلفة"),
        "Default Expense Account": tr(lang, "Default Expense Account", "حساب المصروفات الافتراضي"),
        "Inventory Account": tr(lang, "Inventory Account", "حساب المخزون"),
        "COGS Account": tr(lang, "COGS Account", "حساب تكلفة البضاعة المباعة"),
        "Input VAT Account": tr(lang, "Input VAT Account", "حساب ضريبة القيمة المضافة - مدخلات"),
        "Output VAT Account": tr(lang, "Output VAT Account", "حساب ضريبة القيمة المضافة - مخرجات"),
        "WHT Receivable Account": tr(lang, "WHT Receivable Account", "حساب خصم المنبع - مدين"),
        "WHT Payable Account": tr(lang, "WHT Payable Account", "حساب خصم المنبع - دائن"),
        "Fixed Asset Account": tr(lang, "Fixed Asset Account", "حساب الأصول الثابتة"),
        "Accumulated Depreciation Account": tr(lang, "Accumulated Depreciation Account", "حساب مجمع الإهلاك"),
        "Depreciation Expense Account": tr(lang, "Depreciation Expense Account", "حساب مصروف الإهلاك"),
        "Journal Prefix": tr(lang, "Journal Prefix", "بادئة اليومية"),
        "Customer Payment Prefix": tr(lang, "Customer Payment Prefix", "بادئة تحصيل العميل"),
        "Vendor Payment Prefix": tr(lang, "Vendor Payment Prefix", "بادئة سداد المورد"),
        "Employee Custody Prefix": tr(lang, "Employee Custody Prefix", "بادئة عهدة الموظف"),
        "Customer Invoice Prefix": tr(lang, "Customer Invoice Prefix", "بادئة فاتورة العميل"),
        "Vendor Bill Prefix": tr(lang, "Vendor Bill Prefix", "بادئة فاتورة المورد"),
        "Expense Prefix": tr(lang, "Expense Prefix", "بادئة المصروف"),
        "Petty Cash Prefix": tr(lang, "Petty Cash Prefix", "بادئة العهدة النقدية"),
    }
    for r in all_settings():
        rows_html += f"""
        <tr>
            <td>{r['key'] or ''}</td>
            <td>{r['value'] or ''}</td>
            <td>{desc_map.get(r['description'] or '', r['description'] or '')}</td>
        </tr>
        """

    content = f"""
    <div class="card">
        <h2>{tr(lang, "Configuration", "الإعدادات")}</h2>

        <form method="post" action="/ui/accounting/config/save">
            <div class="card">
                <h3>{tr(lang, "Default Accounts", "الحسابات الافتراضية")}</h3>
                {account_html}
            </div>

            <div class="card">
                <h3>{tr(lang, "Payroll Accounts", "حسابات المرتبات")}</h3>
                <p style="font-size:13px; color:#666; margin-bottom:14px;">{tr(lang, "Configure accounts for payroll journal entries. These will be used when posting payroll runs.", "اضبط الحسابات الخاصة بقيود يومية المرتبات. ستستخدم عند ترحيل مسيرات المرتبات.")}</p>
                {payroll_html}
            </div>

            <div class="card">
                <h3>{tr(lang, "Document Prefixes", "بادئات المستندات")}</h3>
                {prefix_html}
            </div>

            <div style="margin-top:16px;">
                <button class="btn green" type="submit">{tr(lang, "Save Configuration", "حفظ الإعدادات")}</button>
            </div>
        </form>
    </div>

    <div class="card">
        <h3>{tr(lang, "Saved Settings", "الإعدادات المحفوظة")}</h3>
        <table>
            <tr>
                <th>{tr(lang, "Key", "المفتاح")}</th>
                <th>{tr(lang, "Value", "القيمة")}</th>
                <th>{tr(lang, "Description", "الوصف")}</th>
            </tr>
            {rows_html}
        </table>
    </div>
    """

    return HTMLResponse(
        render_page(tr(lang, "Configuration", "الإعدادات"), content, lang, current_path=request.url.path)
    )


@router.post("/ui/accounting/config/save")
async def config_save(request: Request):
    form = await request.form()

    fields = {
        "default_cash_account": "Default Cash Account",
        "default_bank_account": "Default Bank Account",
        "default_customer_account": "Default Customer Account",
        "customer_control_account": "Customer Control Account",
        "default_vendor_account": "Default Vendor Account",
        "vendor_control_account": "Vendor Control Account",
        "employee_custody_account": "Employee Custody Account",
        "employee_advance_account": "Employee Advance Account",
        "petty_cash_account": "Petty Cash Account",
        "sales_revenue_account_code": "Sales Revenue Account",
        "purchase_account": "Purchase / Cost Account",
        "expense_account": "Default Expense Account",
        "inventory_account": "Inventory Account",
        "cogs_account": "COGS Account",
        "input_vat_account": "Input VAT Account",
        "output_vat_account": "Output VAT Account",
        "wht_receivable_account": "WHT Receivable Account",
        "wht_payable_account": "WHT Payable Account",
        "fixed_asset_account": "Fixed Asset Account",
        "accumulated_depreciation_account": "Accumulated Depreciation Account",
        "depreciation_expense_account": "Depreciation Expense Account",
        "journal_prefix": "Journal Prefix",
        "customer_payment_prefix": "Customer Payment Prefix",
        "vendor_payment_prefix": "Vendor Payment Prefix",
        "employee_custody_prefix": "Employee Custody Prefix",
        "customer_invoice_prefix": "Customer Invoice Prefix",
        "vendor_bill_prefix": "Vendor Bill Prefix",
        "expense_prefix": "Expense Prefix",
        "petty_cash_prefix": "Petty Cash Prefix",
        "payroll_salary_account": "Salary Expense Account",
        "payroll_housing_account": "Housing Allowance Expense Account",
        "payroll_transport_account": "Transport Allowance Expense Account",
        "payroll_other_allowance_account": "Other Allowance Expense Account",
        "payroll_overtime_account": "Overtime Expense Account",
        "payroll_bonus_account": "Bonus Expense Account",
        "payroll_absence_account": "Absence Deduction Account",
        "payroll_advance_account": "Advance Deduction Payable Account",
        "payroll_insurance_employee_account": "Employee Insurance Payable Account",
        "payroll_insurance_employer_account": "Employer Insurance Expense Account",
        "payroll_payable_account": "Net Salaries Payable Account",
        "payroll_bank_account": "Payroll Bank/Cash Account",
    }

    for key, desc in fields.items():
        value = (form.get(key) or "").strip()
        if value:
            set_setting_value(key, value, desc)

    return RedirectResponse(f"/ui/accounting/config?lang={get_lang(request)}", status_code=302)
