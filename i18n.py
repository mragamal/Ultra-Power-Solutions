import json
import re
from functools import lru_cache
from pathlib import Path


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
    "ط§", "ط¨", "ط©", "ط±", "ط£", "ط¥", "ط¹", "ط³", "طµ", "ط¯", "طھ",
    "ظ„", "ظ…", "ظˆ", "ظٹ", "ظ†", "ظپ", "ظ‚", "ظ‡", "ظƒ", "طŒ",
    "ط·آ§", "ط¸â€‍", "ط¸â€¦", "ط·آ¨", "ط·آ©", "ط·آ±", "ط·آ£", "ط·آ¥",
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


STATIC_UI_AR = {
    "Operations Workflow Guide": "دليل دورة التشغيل",
    "Use the current Operations screens in the same order below. Customer-owned stock is tracking only; company spare parts remain company inventory.": "استخدم شاشات التشغيل الحالية بنفس الترتيب. مخزون العميل للمتابعة التشغيلية فقط، أما قطع الغيار المملوكة للشركة فتظل داخل مخزون الشركة.",
    "Workshop Repair - Orange Operation": "تصليح الورشة - تشغيل أورانج",
    "Field Service - Technical Visit / Swap / Install": "خدمة المواقع - زيارة فنية / سواب / تركيب",
    "Orange Planning": "تخطيط أورانج",
    "Receive Faulty Modules": "استلام الموديولات العطلانة",
    "Customer Warehouses -> Custody Movements -> Receive From Customer as Faulty": "مخازن العميل -> حركات عهدة العميل -> استلام من العميل كعطلان",
    "Create Ticket": "إنشاء تذكرة",
    "Tickets -> select Orange + fault/site/request details": "التذاكر -> اختيار أورانج + بيانات العطل أو الموقع أو الطلب",
    "Create Work Order": "إنشاء أمر شغل",
    "Work Orders -> Workflow Workshop Repair + Orange Operation warehouse + requested qty": "أوامر الشغل -> دورة تصليح الورشة + مخزن تشغيل أورانج + الكمية المطلوبة",
    "Issue To Maintenance": "صرف للصيانة",
    "Open WO -> Issue To Repair; modules become Under Repair": "فتح أمر الشغل -> صرف للتصليح؛ تتحول الموديولات إلى تحت الصيانة",
    "Repair And Partial Return": "الصيانة والرجوع الجزئي",
    "WO -> Return From Repair; repaired qty becomes Working": "أمر الشغل -> رجوع من الصيانة؛ الكمية المصلحة تتحول إلى شغال",
    "Progress": "الموقف",
    "Requested, issued, completed, remaining, and activity log": "المطلوب والمنصرف والمنتهي والمتبقي وسجل النشاط",
    "Open Work Order": "فتح أمر شغل",
    "Ticket -> Work Order -> Workflow Field Service + action repair/swap/install/technical visit": "تذكرة -> أمر شغل -> دورة خدمة مواقع + تصليح/سواب/تركيب/زيارة فنية",
    "Assign Resources": "توزيع الموارد",
    "Region, trip ticket, vehicle, vehicle rates, technician reports": "المنطقة وأمر الرحلة والسيارة وتسعير السيارة وتقارير الفني",
    "Issue Materials": "صرف الخامات",
    "Customer warehouse first; shortage later from company warehouse workflow": "من مخزن العميل أولا، والنقص لاحقا من مخزن الشركة",
    "Swap": "سواب",
    "Working unit becomes Installed; removed faulty unit goes back as Faulty": "الوحدة الشغالة تصبح مركبة، والوحدة العطلانة الراجعة تدخل كعطلان",
    "Closure": "الإغلاق",
    "Engineer closes WO with completed qty, actual actions, and notes": "المهندس يغلق أمر الشغل بالكمية المنتهية والأعمال الفعلية والملاحظات",
    "Incentive": "حافز الفني",
    "Action price + region allowance are tracked on the WO and reports": "سعر العمل + بدل المنطقة يتم تسجيلهم على أمر الشغل والتقارير",
    "Project Request": "طلب المشروع",
    "Ticket includes site code and job type from the official request": "التذكرة تشمل كود الموقع ونوع العمل من الطلب الرسمي",
    "Standard Kit": "الخامات القياسية",
    "Action Catalog materials represent the PM-defined standard kit": "خامات كتالوج الأعمال تمثل الخامات القياسية المحددة من مدير المشروع",
    "Planning Warehouse": "مخزن التخطيط",
    "Warehouse department Planning is separate from Operation": "مخزن قسم التخطيط منفصل عن التشغيل",
    "Rollout Approval": "اعتماد الرولاوت",
    "Open WO -> Rollout Confirm; notes are mandatory before execution": "فتح أمر الشغل -> تأكيد الرولاوت؛ الملاحظات إلزامية قبل التنفيذ",
    "Technician Execution": "تنفيذ الفني",
    "Materials issued to technician, returned to same source, then WO is closed": "الخامات تصرف للفني، والمرتجع يرجع لنفس المصدر، ثم يتم إغلاق أمر الشغل",
    "Audit": "المراجعة",
    "Every movement, approval, and closure is written to Activity Log": "كل حركة واعتماد وإغلاق يتم تسجيله في سجل النشاط",
    "Activity Log": "سجل النشاط",
    "Master Data": "البيانات الأساسية",
    "Customer Stock": "أرصدة مخازن العميل",
    "Customer Warehouses": "مخازن العميل",
    "Custody Movements": "حركات عهدة العميل",
    "Trip Tickets": "أوامر الرحلات",
    "Pricing Versions": "إصدارات التسعير",
    "Contracts": "العقود",
    "Tickets": "التذاكر",
    "Workflow Guide": "دليل دورة التشغيل",
    "Work Orders": "أوامر الشغل",
    "Rental Offices": "مكاتب التأجير",
    "Vehicles": "السيارات",
    "Contract Companies": "شركات التعاقد",
    "Fault Types": "أنواع الأعطال",
    "Regions": "المناطق",
    "Action Catalog": "كتالوج الأعمال",
    "Vehicle Rates": "تسعير السيارات",
}


@lru_cache(maxsize=1)
def _client_exact_translations():
    path = Path(__file__).resolve().parent / "static" / "js" / "i18n.js"
    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    match = re.search(r"const\s+exact\s*=\s*\{(.*?)\n\s*\};", source, re.S)
    if not match:
        return {}
    entries = {}
    for raw_key, raw_value in re.findall(r'"((?:\\.|[^"\\])*)"\s*:\s*"((?:\\.|[^"\\])*)"', match.group(1)):
        try:
            key = json.loads(f'"{raw_key}"')
            value = json.loads(f'"{raw_value}"')
        except Exception:
            continue
        entries[key] = value
    return entries


def translate_static_ui(text, lang="en"):
    if lang != "ar" or not isinstance(text, str) or not text:
        return text
    output = fix_mojibake(text)
    entries = {}
    entries.update(_client_exact_translations())
    entries.update(STATIC_UI_AR)
    for source, target in sorted(entries.items(), key=lambda item: len(item[0]), reverse=True):
        output = output.replace(source, target)
    return output
