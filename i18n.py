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

_MOJIBAKE_MARKERS = (
    "ط§", "ظ„", "ظ…", "ط¨", "ط©", "ط±", "ط£", "ط¥", "ظٹ", "ظˆ",
    "ط¹", "ط،", "طŒ", "ظپ", "ظ‚", "ظ‡", "ظ†", "ظƒ", "ط³", "طµ",
    "ط¯", "طھ",
)


def looks_mojibake(text):
    text = str(text or "")
    return any(marker in text for marker in _MOJIBAKE_MARKERS)


def _arabic_count(text):
    return sum(1 for ch in str(text or "") if "\u0600" <= ch <= "\u06ff")


def _mojibake_score(text):
    text = str(text or "")
    score = sum(text.count(marker) for marker in _MOJIBAKE_MARKERS)
    score += sum(1 for ch in text if ch in "\u201a\u201e\u2020\u2021\u02c6\u2030")
    return score


def _try_repair_fragment(text):
    if not isinstance(text, str) or not text:
        return text
    try:
        fixed = text.encode("cp1256").decode("utf-8")
    except Exception:
        return text
    if _arabic_count(fixed) and _mojibake_score(fixed) < _mojibake_score(text):
        return fixed
    return text


def fix_mojibake(text):
    if not isinstance(text, str) or not text:
        return text
    repaired = _try_repair_fragment(text)
    if repaired != text:
        return repaired
    if not looks_mojibake(text):
        return text
    import re

    pattern = re.compile(r"[\u00a0-\u00ff\u0600-\u06ff\u201a-\u201e\u2020-\u2026\u02c6\u2030]+")
    return pattern.sub(lambda match: _try_repair_fragment(match.group(0)), text)


def t(key, lang="en"):
    lang = lang if lang in translations else "en"
    value = translations.get(lang, {}).get(key, translations["en"].get(key, key))
    return fix_mojibake(value) if lang == "ar" else value


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
