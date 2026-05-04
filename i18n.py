translations = {
    "en": {
        "app_name": "Premium One ERP",
        "configuration": "Configuration",
        "accounts": "Accounts",
        "journals": "Journals",
        "customers": "Customers",
        "vendors": "Vendors",
        "employees": "Employees",
        "petty_cash": "Petty Cash",
        "expenses": "Expenses",
        "statement": "Statement",
        "customer_invoices": "Customer Invoices",
        "customer_payments": "Customer Payments",
    },
    "ar": {
        "app_name": "Premium One ERP",
        "configuration": "الإعدادات",
        "accounts": "الحسابات",
        "journals": "قيود اليومية",
        "customers": "العملاء",
        "vendors": "الموردون",
        "employees": "الموظفون",
        "petty_cash": "العهدة",
        "expenses": "المصروفات",
        "statement": "كشف الحساب",
        "customer_invoices": "فواتير العملاء",
        "customer_payments": "تحصيلات العملاء",
    },
}


def t(key, lang="en"):
    lang = lang if lang in translations else "en"
    return translations.get(lang, {}).get(key, translations["en"].get(key, key))


def get_lang(request):
    try:
        lang = (request.query_params.get("lang") or "").strip().lower()
        if lang in ["ar", "en"]:
            return lang
    except Exception:
        pass
    try:
        lang = (request.cookies.get("ui_lang") or "").strip().lower()
        if lang in ["ar", "en"]:
            return lang
    except Exception:
        pass
    return "en"
