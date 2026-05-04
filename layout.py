import inspect
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from auth import can


_I18N_JS_PATH = Path(__file__).resolve().parent / "static" / "js" / "i18n.js"
_I18N_JS_VERSION = str(int(_I18N_JS_PATH.stat().st_mtime_ns)) if _I18N_JS_PATH.exists() else "1"


_NAV_LABELS_AR = {
    "Accounting": "الحسابات",
    "HR": "الموارد البشرية",
    "Inventory": "المخازن",
    "Purchasing": "المشتريات",
    "Sales": "المبيعات",
    "Operations": "التشغيل",
    "Users": "المستخدمون",
}


_MOJIBAKE_AR_RE = re.compile(r"[\u00a0-\u00ff\u0600-\u06ff\u201a-\u201e\u2020-\u2026\u02c6\u2030]+")


def _repair_arabic_mojibake(text):
    if not isinstance(text, str) or not text:
        return text

    def fix_match(match):
        value = match.group(0)
        try:
            fixed = value.encode("cp1256").decode("utf-8")
        except Exception:
            return value
        return fixed if fixed else value

    return _MOJIBAKE_AR_RE.sub(fix_match, text)


def _request_lang(request, fallback="en"):
    lang = (fallback or "en").strip().lower()
    try:
        query_lang = (request.query_params.get("lang") or "").strip().lower()
        if query_lang in {"ar", "en"}:
            return query_lang
    except Exception:
        pass
    try:
        cookie_lang = (request.cookies.get("ui_lang") or "").strip().lower()
        if cookie_lang in {"ar", "en"}:
            return cookie_lang
    except Exception:
        pass
    return lang if lang in {"ar", "en"} else "en"


def _with_lang_url(href, lang):
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return href
    if href.startswith("http://") or href.startswith("https://") or href.startswith("/static/"):
        return href
    try:
        parts = urlsplit(href)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if lang == "ar":
            query["lang"] = "ar"
        else:
            query.pop("lang", None)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return href


def _request_from_stack():
    try:
        for frame_info in inspect.stack()[1:10]:
            request = frame_info.frame.f_locals.get("request")
            if request is not None and (hasattr(request, "session") or hasattr(request, "query_params")):
                return request
    except Exception:
        return None
    return None


def _sidebar_menu_items(request, current_path="", lang=None):
    lang = _request_lang(request, lang or "en")

    items = [
        ("accounting", "/ui/accounting", "/static/icons/nav-accounting.svg", "Accounting"),
        ("hr", "/ui/hr", "/static/icons/nav-hr.svg", "HR"),
        ("inventory", "/ui/inventory", "/static/icons/nav-inventory.svg", "Inventory"),
        ("purchasing", "/ui/purchasing", "/static/icons/nav-purchasing.svg", "Purchasing"),
        ("sales", "/ui/sales", "/static/icons/nav-sales.svg", "Sales"),
        ("operations", "/ui/operations", "/static/icons/nav-projects.svg", "Operations"),
        ("users", "/ui/system/users", "/static/icons/employees.svg", "Users"),
    ]

    html = []
    for module_code, href, icon, label in items:
        visible = True if request is None else can(request, module_code, "view")
        if not visible:
            continue
        
        full_href = _with_lang_url(href, lang)
        display_label = _NAV_LABELS_AR.get(label, label) if lang == "ar" else label
        
        if module_code == "operations":
            active = "/operations" in current_path or "/projects" in current_path
        else:
            active = href in current_path
        
        html.append(
            f"""
                    <a class="menu-item {'active' if active else ''}" href="{full_href}" style="position:relative; z-index:10;">
                        <span class="menu-icon"><img src="{icon}" alt="{display_label}"></span>
                        <span class="menu-text">{display_label}</span>
                    </a>
            """
        )
    return "".join(html)


def _module_home_link(current_path: str):
    path = str(current_path or "")
    if path.startswith("/ui/accounting"):
        return "/ui/accounting"
    if path.startswith("/ui/hr"):
        return "/ui/hr"
    if path.startswith("/ui/inventory"):
        return "/ui/inventory"
    if path.startswith("/ui/purchasing"):
        return "/ui/purchasing"
    if path.startswith("/ui/sales"):
        return "/ui/sales"
    if path.startswith("/ui/operations"):
        return "/ui/operations"
    if path.startswith("/ui/system"):
        return "/ui/system/users"
    return ""


def render_page(title, content, lang="en", current_path=""):
    request = _request_from_stack()
    lang = _request_lang(request, lang)
    title = _repair_arabic_mojibake(str(title or ""))
    content = _repair_arabic_mojibake(str(content or ""))
    sidebar_items_html = _sidebar_menu_items(request, current_path, lang)
    module_home_href = _with_lang_url(_module_home_link(current_path), lang)
    show_module_back = bool(module_home_href and current_path and current_path != module_home_href)
    module_back_label = "الرجوع للموديول" if lang == "ar" else "Back to Module"
    language_label = "English" if lang == "ar" else "عربي"
    home_label = "الرئيسية" if lang == "ar" else "Home"
    logout_label = "تسجيل الخروج" if lang == "ar" else "Logout"
    assistant_label = "المساعد" if lang == "ar" else "Assistant"
    assistant_placeholder = "اسأل..." if lang == "ar" else "Ask..."
    assistant_send = "إرسال" if lang == "ar" else "Send"
    done_label = "تم." if lang == "ar" else "Done."
    failed_label = "حصل خطأ، حاول مرة أخرى." if lang == "ar" else "Something went wrong. Try again."
    home_href = _with_lang_url("/", lang)
    logo_href = _with_lang_url("/ui/accounting", lang)
    return f"""
    <!DOCTYPE html>
    <html lang="{lang if lang in ['en', 'ar'] else 'en'}" dir="{'rtl' if lang == 'ar' else 'ltr'}">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>{title}</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
                font-family: Arial, sans-serif;
            }}
            :root {{
                --brand-blue-1: #052861;
                --brand-blue-2: #0a3a8f;
                --brand-blue-3: #0e4fb8;
                --brand-cyan: #20d3f3;
                --brand-glow: rgba(32, 211, 243, 0.22);
                --surface-white: rgba(255, 255, 255, 0.98);
                --mesh-dots: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='220' height='120' viewBox='0 0 220 120'%3E%3Cg fill='%23b9f4ff' fill-opacity='0.78'%3E%3Ccircle cx='18' cy='92' r='1.25'/%3E%3Ccircle cx='30' cy='86' r='1.2'/%3E%3Ccircle cx='42' cy='79' r='1.18'/%3E%3Ccircle cx='54' cy='73' r='1.15'/%3E%3Ccircle cx='66' cy='67' r='1.12'/%3E%3Ccircle cx='78' cy='61' r='1.1'/%3E%3Ccircle cx='90' cy='56' r='1.08'/%3E%3Ccircle cx='102' cy='51' r='1.05'/%3E%3Ccircle cx='114' cy='47' r='1.02'/%3E%3Ccircle cx='126' cy='44' r='1'/%3E%3Ccircle cx='138' cy='42' r='0.98'/%3E%3Ccircle cx='150' cy='41' r='0.96'/%3E%3Ccircle cx='162' cy='41' r='0.94'/%3E%3Ccircle cx='174' cy='42' r='0.92'/%3E%3Ccircle cx='186' cy='45' r='0.9'/%3E%3Ccircle cx='198' cy='49' r='0.88'/%3E%3Ccircle cx='210' cy='54' r='0.86'/%3E%3Ccircle cx='22' cy='104' r='1.1'/%3E%3Ccircle cx='34' cy='98' r='1.08'/%3E%3Ccircle cx='46' cy='91' r='1.05'/%3E%3Ccircle cx='58' cy='85' r='1.02'/%3E%3Ccircle cx='70' cy='79' r='1'/%3E%3Ccircle cx='82' cy='73' r='0.98'/%3E%3Ccircle cx='94' cy='68' r='0.96'/%3E%3Ccircle cx='106' cy='63' r='0.94'/%3E%3Ccircle cx='118' cy='59' r='0.92'/%3E%3Ccircle cx='130' cy='56' r='0.9'/%3E%3Ccircle cx='142' cy='54' r='0.88'/%3E%3Ccircle cx='154' cy='53' r='0.86'/%3E%3Ccircle cx='166' cy='53' r='0.84'/%3E%3Ccircle cx='178' cy='55' r='0.82'/%3E%3Ccircle cx='190' cy='58' r='0.8'/%3E%3Ccircle cx='202' cy='62' r='0.78'/%3E%3Ccircle cx='214' cy='67' r='0.76'/%3E%3C/g%3E%3C/svg%3E");
                --mesh-wave: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='900' height='420' viewBox='0 0 900 420'%3E%3Cdefs%3E%3Cfilter id='g'%3E%3CfeGaussianBlur stdDeviation='3'/%3E%3C/filter%3E%3C/defs%3E%3Cpath d='M20 290 C180 228 294 134 432 152 C560 168 672 270 880 236' fill='none' stroke='%2325dbf6' stroke-opacity='0.84' stroke-width='2.4'/%3E%3Cpath d='M0 320 C148 252 278 174 428 188 C578 202 686 292 900 256' fill='none' stroke='%2362ebff' stroke-opacity='0.38' stroke-width='1.4'/%3E%3Cpath d='M54 266 C206 208 318 124 442 142 C566 160 694 252 850 222' fill='none' stroke='%2393f4ff' stroke-opacity='0.2' stroke-width='1' filter='url(%23g)'/%3E%3C/svg%3E");
            }}
            body {{
                background:
                    radial-gradient(circle at 18% 18%, rgba(47, 191, 255, 0.14), transparent 26%),
                    linear-gradient(135deg, #06245a 0%, #0a327d 52%, #041f4d 100%);
                color: #183153;
                min-height: 100vh;
            }}
            html[dir="rtl"] body {{
                text-align: right;
            }}
            a {{ text-decoration: none; }}
            .app {{
                display: flex;
                min-height: 100vh;
                position: relative;
                overflow: hidden;
            }}
            html[dir="rtl"] .app {{
                flex-direction: row-reverse;
            }}
            .sidebar {{
                width: 250px;
                min-width: 250px;
                flex: 0 0 250px;
                background:
                    radial-gradient(circle at top left, rgba(58, 202, 255, 0.14), transparent 30%),
                    linear-gradient(180deg, #062b67 0%, #0a3b91 52%, #05265f 100%);
                color: #fff;
                padding: 18px 14px;
                position: sticky;
                top: 0;
                height: 100vh;
                overflow-y: auto;
                overflow-x: hidden;
                box-shadow: 18px 0 40px rgba(2, 15, 42, 0.24);
                z-index: 3;
                isolation: isolate;
            }}
            .sidebar::before {{
                content: "";
                position: absolute;
                inset: 0;
                background:
                    var(--mesh-dots) left -12px bottom 68px / 220px 120px no-repeat,
                    var(--mesh-wave) left -20px bottom -12px / 360px 180px no-repeat;
                opacity: 0.58;
                pointer-events: none;
                z-index: 0;
            }}
            .sidebar::after {{
                content: "";
                position: absolute;
                inset: 0;
                background:
                    radial-gradient(circle at 22% 80%, rgba(34, 217, 248, 0.2), transparent 18%),
                    linear-gradient(180deg, transparent 0%, transparent 56%, rgba(255,255,255,0.02) 100%);
                opacity: 0.78;
                pointer-events: none;
                z-index: 0;
            }}
            .logo-box {{
                text-align: center;
                padding: 8px 8px 18px 8px;
                border-bottom: 1px solid rgba(255,255,255,0.12);
                margin-bottom: 18px;
                position: relative;
                z-index: 1;
            }}
            .logo-link {{
                display: block;
                color: inherit;
            }}
            .logo-img {{
                width: 126%;
                max-width: 126%;
                height: auto;
                display: block;
                margin: 0 auto 12px auto;
                margin-left: -13%;
                object-fit: contain;
                filter: drop-shadow(0 8px 18px rgba(0,0,0,0.22));
            }}
            .logo-title {{
                font-size: 0px;
                font-weight: 800;
                letter-spacing: 0.3px;
                margin-top: 4px;
            }}
            .logo-sub {{
                font-size: 0px;
                opacity: 0.9;
                margin-top: 4px;
            }}
            .menu {{
                display: flex;
                flex-direction: column;
                gap: 10px;
                position: relative;
                z-index: 1;
            }}
            .menu-item {{
                display: flex;
                align-items: center;
                gap: 12px;
                padding: 12px 14px;
                border-radius: 16px;
                color: #eaf0ff;
                font-weight: 700;
                transition: 0.2s ease;
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.03);
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
            }}
            .menu-item:hover {{
                background: rgba(255,255,255,0.12);
                border-color: rgba(255,255,255,0.09);
            }}
            .menu-item.active {{
                background: linear-gradient(135deg, #174cb7 0%, #1d67e2 58%, #1dbbe8 100%);
                box-shadow:
                    inset 0 0 0 1px rgba(255,255,255,0.1),
                    0 12px 22px rgba(14, 79, 184, 0.24);
            }}
            .menu-icon {{
                width: 26px;
                height: 26px;
                border-radius: 50%;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                background: rgba(255,255,255,0.15);
                color: #d7deea;
                font-size: 13px;
                font-weight: 800;
                flex-shrink: 0;
            }}
            .menu-icon img {{
                width: 16px;
                height: 16px;
                object-fit: contain;
                display: block;
            }}
            .main {{
                flex: 1;
                padding: 22px;
                position: relative;
                z-index: 1;
                background:
                    radial-gradient(circle at 85% 54%, rgba(28, 210, 244, 0.18), transparent 18%),
                    radial-gradient(circle at 76% 70%, rgba(56, 176, 255, 0.1), transparent 24%),
                    linear-gradient(135deg, rgba(7, 41, 99, 0.98) 0%, rgba(11, 54, 130, 0.98) 56%, rgba(6, 38, 95, 0.98) 100%);
                overflow: hidden;
            }}
            .main::before {{
                content: "";
                position: absolute;
                inset: 0;
                background:
                    var(--mesh-dots) right 54px bottom 86px / 360px 196px no-repeat,
                    var(--mesh-wave) right -10px bottom -18px / 640px 320px no-repeat;
                opacity: 0.88;
                pointer-events: none;
            }}
            .main::after {{
                content: "";
                position: absolute;
                inset: 0;
                background:
                    radial-gradient(circle at 78% 74%, rgba(54, 213, 243, 0.24), transparent 16%),
                    radial-gradient(circle at 90% 62%, rgba(116, 238, 255, 0.1), transparent 22%);
                opacity: 0.94;
                pointer-events: none;
            }}
            .topbar {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                background: rgba(255,255,255,0.98);
                border: 1px solid rgba(223, 230, 241, 0.92);
                border-radius: 22px;
                padding: 14px 18px;
                margin-bottom: 18px;
                box-shadow: 0 12px 28px rgba(4, 18, 53, 0.16);
                position: relative;
                z-index: 2;
            }}
            .topbar h1 {{
                font-size: 20px;
                font-weight: 800;
                color: #13315c;
            }}
            .topbar-actions {{
                display: flex;
                align-items: center;
                gap: 10px;
                flex-wrap: wrap;
            }}
            html[dir="rtl"] .topbar-actions {{
                flex-direction: row-reverse;
            }}
            .topbar-link {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                min-width: 96px;
                height: 40px;
                padding: 0 14px;
                border-radius: 12px;
                background: #2a67ea;
                color: #fff;
                font-size: 14px;
                font-weight: 800;
                border: 1px solid #2a67ea;
                box-shadow: 0 8px 18px rgba(42, 103, 234, 0.16);
            }}
            .topbar-link:hover {{
                background: #1f57cf;
                border-color: #1f57cf;
            }}
            .topbar-link.secondary {{
                background: #f5f7fb;
                color: #244267;
                border-color: #dfe6f1;
                box-shadow: none;
            }}
            .topbar-link.secondary:hover {{
                background: #eaf0f8;
                border-color: #d3ddea;
            }}
            .topbar-badge {{
                width: 40px;
                height: 40px;
                border-radius: 12px;
                background: #f5f7fb;
                border: 1px solid #dfe6f1;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-size: 18px;
            }}
            .topbar-badge img {{
                width: 20px;
                height: 20px;
                object-fit: contain;
                display: block;
            }}
            .assistant-fab {{
                position: fixed;
                right: 22px;
                bottom: 22px;
                z-index: 999;
                width: 58px;
                height: 58px;
                border-radius: 18px;
                border: 0;
                background: #1f57cf;
                color: #fff;
                font-size: 22px;
                font-weight: 900;
                box-shadow: 0 18px 34px rgba(4, 18, 53, 0.28);
                cursor: pointer;
            }}
            html[dir="rtl"] .assistant-fab {{
                right: auto;
                left: 22px;
            }}
            .assistant-panel {{
                position: fixed;
                right: 22px;
                bottom: 92px;
                z-index: 999;
                width: min(420px, calc(100vw - 36px));
                height: min(560px, calc(100vh - 130px));
                background: #fff;
                border: 1px solid #dfe6f1;
                border-radius: 18px;
                box-shadow: 0 24px 55px rgba(4, 18, 53, 0.25);
                display: none;
                overflow: hidden;
            }}
            html[dir="rtl"] .assistant-panel {{
                right: auto;
                left: 22px;
            }}
            .assistant-panel.open {{
                display: flex;
                flex-direction: column;
            }}
            .assistant-head {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 14px 16px;
                background: #f5f7fb;
                border-bottom: 1px solid #dfe6f1;
                color: #13315c;
                font-weight: 900;
            }}
            .assistant-close {{
                border: 0;
                background: transparent;
                color: #244267;
                font-size: 22px;
                cursor: pointer;
            }}
            .assistant-messages {{
                flex: 1;
                padding: 14px;
                overflow-y: auto;
                background: #fbfdff;
            }}
            .assistant-message {{
                max-width: 88%;
                padding: 10px 12px;
                border-radius: 14px;
                margin-bottom: 10px;
                line-height: 1.45;
                font-size: 14px;
                color: #183153;
                background: #eef2f8;
            }}
            .assistant-message.user {{
                margin-left: auto;
                background: #2a67ea;
                color: #fff;
            }}
            html[dir="rtl"] .assistant-message.user {{
                margin-left: 0;
                margin-right: auto;
            }}
            .assistant-message .btn {{
                margin-top: 8px;
                padding: 8px 12px;
            }}
            .assistant-message ol {{
                margin: 8px 0 0 0;
                padding-inline-start: 20px;
                line-height: 1.65;
            }}
            .assistant-message li {{
                margin-bottom: 4px;
            }}
            .assistant-form {{
                display: flex;
                gap: 8px;
                padding: 12px;
                border-top: 1px solid #dfe6f1;
                background: #fff;
            }}
            .assistant-form input {{
                min-width: 0;
            }}
            .assistant-form button {{
                white-space: nowrap;
            }}
            .card {{
                background: var(--surface-white);
                border: 1px solid rgba(223, 230, 241, 0.92);
                border-radius: 22px;
                padding: 18px;
                margin-bottom: 18px;
                box-shadow: 0 14px 30px rgba(4, 18, 53, 0.12);
                position: relative;
                z-index: 2;
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
            }}
            .sub-title {{
                font-size: 18px;
                font-weight: 800;
                color: #13315c;
            }}
            .card-grid {{
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 16px;
                margin-top: 14px;
            }}
            .module-card {{
                background: linear-gradient(180deg, rgba(255,255,255,0.96) 0%, rgba(249, 252, 255, 0.98) 100%);
                border: 1px solid rgba(223, 230, 241, 0.92);
                border-radius: 18px;
                padding: 22px 16px;
                text-align: center;
                transition: 0.2s ease;
                color: #183153;
            }}
            .module-card:hover {{
                transform: translateY(-3px);
                box-shadow: 0 16px 30px rgba(8, 31, 84, 0.16);
                border-color: #cfd8ea;
            }}
            .module-card-icon {{
                width: 58px;
                height: 58px;
                margin: 0 auto 12px auto;
                display: flex;
                align-items: center;
                justify-content: center;
            }}
            .module-card-icon img {{
                width: 54px;
                height: 54px;
                object-fit: contain;
                display: block;
            }}
            .module-card-title {{
                font-size: 16px;
                font-weight: 800;
                margin-bottom: 6px;
                color: #16335d;
            }}
            .module-card-sub {{
                font-size: 13px;
                color: #6d809c;
                line-height: 1.4;
            }}
            .toolbar {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 12px;
                flex-wrap: wrap;
            }}
            html[dir="rtl"] .toolbar,
            html[dir="rtl"] .row,
            html[dir="rtl"] .list-header,
            html[dir="rtl"] .action-strip,
            html[dir="rtl"] .filter-actions,
            html[dir="rtl"] .table-summary,
            html[dir="rtl"] .audit-item-head {{
                flex-direction: row-reverse;
            }}
            .row {{
                display: flex;
                flex-wrap: wrap;
                gap: 16px;
            }}
            .col {{
                flex: 1 1 220px;
                min-width: 220px;
            }}
            .btn {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                padding: 10px 16px;
                border-radius: 10px;
                border: 1px solid transparent;
                font-size: 14px;
                font-weight: 700;
                cursor: pointer;
                text-decoration: none;
            }}
            .btn.green {{ background: #1f9d55; color: #fff; }}
            .btn.blue {{ background: #2a67ea; color: #fff; }}
            .btn.gray {{ background: #eef2f8; color: #244267; border-color: #dfe6f1; }}
            .btn.orange {{ background: #f59e0b; color: #fff; }}
            .btn.purple {{ background: #7c3aed; color: #fff; }}
            .btn.red {{ background: #dc2626; color: #fff; }}
            .msg {{
                padding: 14px 16px;
                border-radius: 12px;
                margin-bottom: 14px;
                font-size: 14px;
                font-weight: 700;
            }}
            .msg.error {{ background: #fdecec; color: #b42318; border: 1px solid #f6c7c4; }}
            .msg.success {{ background: #e8f7ec; color: #217a3c; border: 1px solid #ccebd5; }}
            .form-grid {{
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 16px;
            }}
            .form-group {{
                display: flex;
                flex-direction: column;
                gap: 7px;
            }}
            .form-group label {{
                font-size: 13px;
                font-weight: 700;
                color: #3a567d;
            }}
            input, select, textarea {{
                width: 100%;
                padding: 12px 14px;
                border: 1px solid #d5deea;
                border-radius: 12px;
                background: #fff;
                color: #183153;
                font-size: 14px;
                outline: none;
            }}
            textarea {{
                min-height: 110px;
                resize: vertical;
            }}
            .searchable-wrapper {{
                position: relative;
                width: 100%;
            }}
            .searchable-input {{
                width: 100%;
                padding: 12px 14px;
                border: 1px solid #d5deea;
                border-radius: 12px;
                background: #fff;
                color: #183153;
                font-size: 14px;
                outline: none;
            }}
            .searchable-dropdown {{
                position: absolute;
                top: calc(100% + 4px);
                left: 0;
                right: 0;
                background: #fff;
                border: 1px solid #d5deea;
                border-radius: 12px;
                max-height: 240px;
                overflow-y: auto;
                box-shadow: 0 12px 24px rgba(15, 35, 95, 0.12);
                z-index: 9999;
                display: none;
            }}
            .searchable-item,
            .searchable-empty {{
                padding: 10px 12px;
                font-size: 14px;
                color: #183153;
            }}
            .searchable-item {{
                cursor: pointer;
                border-bottom: 1px solid #eef2f8;
            }}
            .searchable-item:hover {{
                background: #f5f7fb;
            }}
            .searchable-empty {{
                color: #6d809c;
            }}
            .form-actions {{
                display: flex;
                gap: 10px;
                justify-content: flex-end;
                margin-top: 18px;
                flex-wrap: wrap;
            }}
            .filters {{
                display: flex;
                flex-direction: column;
                gap: 14px;
            }}
            .tabs {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-bottom: 18px;
            }}
            .tab {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 10px 16px;
                border-radius: 999px;
                border: 1px solid #d8e2f0;
                background: rgba(255,255,255,0.9);
                color: #4e6483;
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 0.2px;
                box-shadow: 0 4px 14px rgba(15, 35, 95, 0.05);
                transition: 0.2s ease;
            }}
            .tab:hover {{
                transform: translateY(-1px);
                border-color: #bfcde2;
                color: #1b4da1;
            }}
            .tab.active {{
                color: #fff;
                border-color: #1b57d0;
                background: linear-gradient(135deg, #1b57d0 0%, #0f3f9c 100%);
                box-shadow: 0 10px 24px rgba(27, 87, 208, 0.24);
            }}
            .report-hero {{
                display: grid;
                grid-template-columns: 1.6fr 1fr;
                gap: 18px;
                padding: 24px;
                border-radius: 22px;
                margin-bottom: 18px;
                border: 1px solid #dfe6f1;
                background:
                    radial-gradient(circle at top right, rgba(44, 103, 234, 0.16), transparent 34%),
                    linear-gradient(135deg, #ffffff 0%, #f6f9ff 100%);
                box-shadow: 0 14px 34px rgba(15, 35, 95, 0.08);
            }}
            .report-hero-kicker {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 6px 10px;
                border-radius: 999px;
                background: #eaf1ff;
                color: #1b57d0;
                font-size: 12px;
                font-weight: 800;
                margin-bottom: 12px;
            }}
            .report-hero h2 {{
                font-size: 28px;
                line-height: 1.1;
                color: #13315c;
                margin-bottom: 10px;
            }}
            .report-hero p {{
                color: #58708f;
                font-size: 15px;
                line-height: 1.7;
                max-width: 720px;
            }}
            .report-grid {{
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 16px;
            }}
            .report-card {{
                display: block;
                padding: 18px;
                border-radius: 18px;
                border: 1px solid #dfe6f1;
                background: linear-gradient(180deg, #ffffff 0%, #fbfcff 100%);
                box-shadow: 0 8px 24px rgba(15, 35, 95, 0.05);
                transition: 0.22s ease;
                color: inherit;
            }}
            .report-card:hover {{
                transform: translateY(-3px);
                border-color: #c8d5e7;
                box-shadow: 0 16px 32px rgba(15, 35, 95, 0.1);
            }}
            .report-card-kicker {{
                font-size: 11px;
                font-weight: 800;
                color: #5f7da8;
                text-transform: uppercase;
                letter-spacing: 0.9px;
                margin-bottom: 10px;
            }}
            .report-card-title {{
                font-size: 21px;
                font-weight: 800;
                color: #16335d;
                margin-bottom: 8px;
            }}
            .report-card-desc {{
                font-size: 14px;
                line-height: 1.6;
                color: #667d99;
                min-height: 44px;
            }}
            .report-card-link {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                margin-top: 16px;
                font-size: 13px;
                font-weight: 800;
                color: #1b57d0;
            }}
            .kpi-grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 12px;
            }}
            .kpi-card {{
                padding: 16px;
                border-radius: 18px;
                border: 1px solid #dfe6f1;
                background: rgba(255,255,255,0.92);
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.75);
            }}
            .kpi-label {{
                font-size: 12px;
                font-weight: 800;
                color: #6883a6;
                text-transform: uppercase;
                letter-spacing: 0.8px;
                margin-bottom: 8px;
            }}
            .kpi-value {{
                font-size: 24px;
                font-weight: 900;
                color: #14325d;
                margin-bottom: 6px;
            }}
            .kpi-note {{
                font-size: 13px;
                line-height: 1.5;
                color: #6b7f9a;
            }}
            .section-note {{
                font-size: 13px;
                color: #6d809c;
                margin-top: 4px;
                line-height: 1.5;
            }}
            .table-summary {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                align-items: center;
            }}
            .summary-pill {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 8px 12px;
                border-radius: 999px;
                background: #f5f8fd;
                border: 1px solid #dfe6f1;
                color: #355377;
                font-size: 12px;
                font-weight: 800;
            }}
            .list-shell {{
                display: flex;
                flex-direction: column;
                gap: 18px;
            }}
            .list-header {{
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                gap: 16px;
                flex-wrap: wrap;
            }}
            .list-title h2 {{
                font-size: 26px;
                color: #13315c;
                margin-bottom: 6px;
            }}
            .list-title p {{
                font-size: 14px;
                color: #6b7f9a;
                line-height: 1.6;
            }}
            .filter-grid {{
                display: grid;
                grid-template-columns: 1.4fr repeat(4, minmax(0, 1fr));
                gap: 12px;
                align-items: end;
            }}
            .filter-actions {{
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
                justify-content: flex-end;
                margin-top: 14px;
            }}
            .action-strip {{
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
                align-items: center;
            }}
            .action-btn {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-width: 40px;
                height: 36px;
                padding: 0 12px;
                border-radius: 10px;
                border: 1px solid #dfe6f1;
                background: #fff;
                color: #214570;
                font-size: 12px;
                font-weight: 800;
                cursor: pointer;
                box-shadow: 0 4px 10px rgba(15, 35, 95, 0.04);
            }}
            .action-btn.blue {{
                background: #eef4ff;
                border-color: #d7e4ff;
                color: #1b57d0;
            }}
            .action-btn.green {{
                background: #ebf9f0;
                border-color: #d2efdc;
                color: #19733e;
            }}
            .action-btn.red {{
                background: #fff1f1;
                border-color: #ffd9d9;
                color: #c62828;
            }}
            .status-chip {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 7px 12px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 800;
                text-transform: capitalize;
                white-space: nowrap;
            }}
            .status-chip.green {{
                background: #eaf8ee;
                color: #19733e;
            }}
            .status-chip.orange {{
                background: #fff5e8;
                color: #b86b00;
            }}
            .status-chip.red {{
                background: #fff0f0;
                color: #cb2f2f;
            }}
            .status-chip.gray {{
                background: #f3f6fb;
                color: #637b99;
            }}
            .status-chip.blue {{
                background: #edf4ff;
                color: #1b57d0;
            }}
            .number-cell {{
                text-align: right;
                white-space: nowrap;
                font-variant-numeric: tabular-nums;
            }}
            html[dir="rtl"] .menu-item {{
                flex-direction: row-reverse;
            }}
            .doc-no {{
                font-weight: 800;
                color: #16335d;
            }}
            .doc-party {{
                font-weight: 700;
                color: #22436c;
            }}
            .table-wrap {{
                width: 100%;
                overflow-x: auto;
                border-radius: 14px;
                border: 1px solid #e2e8f2;
            }}
            .custom-filter-bar {{
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                justify-content: space-between;
                gap: 10px;
                padding: 12px;
                margin: 0 0 10px 0;
                border: 1px solid #e2e8f2;
                border-radius: 12px;
                background: #f8fbff;
            }}
            .custom-filter-title {{
                font-weight: 800;
                color: #0a2f63;
            }}
            .custom-filter-actions {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                align-items: center;
            }}
            .custom-filter-builder {{
                display: none;
                width: 100%;
                grid-template-columns: minmax(150px, 1fr) minmax(140px, 0.8fr) minmax(180px, 1fr) auto auto;
                gap: 8px;
                align-items: center;
                margin-top: 8px;
            }}
            .custom-filter-bar.open .custom-filter-builder {{
                display: grid;
            }}
            .custom-filter-builder select,
            .custom-filter-builder input {{
                width: 100%;
                min-height: 40px;
                border: 1px solid #d5e0ee;
                border-radius: 10px;
                padding: 8px 10px;
                color: #12315d;
                background: #fff;
            }}
            .custom-filter-chips {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                width: 100%;
            }}
            .custom-filter-chip {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 7px 10px;
                border: 1px solid #cfe0fb;
                border-radius: 999px;
                background: #eef5ff;
                color: #12315d;
                font-weight: 700;
                font-size: 13px;
            }}
            .custom-filter-chip button {{
                width: 22px;
                height: 22px;
                border: 0;
                border-radius: 999px;
                cursor: pointer;
                background: #dbeafe;
                color: #12315d;
                font-weight: 900;
            }}
            .custom-filter-empty-row td {{
                text-align: center;
                color: #637694;
                font-weight: 700;
                padding: 18px;
            }}
            @media (max-width: 900px) {{
                .custom-filter-builder {{
                    grid-template-columns: 1fr;
                }}
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: #fff;
                min-width: 900px;
            }}
            th {{
                text-align: left;
                background: #f5f7fb;
                color: #244267;
                font-size: 13px;
                font-weight: 800;
                padding: 14px 14px;
                border-bottom: 1px solid #e2e8f2;
                white-space: nowrap;
            }}
            th.sortable-th {{
                cursor: pointer;
                user-select: none;
                position: relative;
                transition: background 0.18s ease, color 0.18s ease;
            }}
            th.sortable-th:hover {{
                background: #eaf1fb;
                color: #143a71;
            }}
            th.resizable-th {{
                position: relative;
            }}
            .column-resizer {{
                position: absolute;
                top: 0;
                inset-inline-end: -6px;
                width: 14px;
                height: 100%;
                cursor: col-resize;
                user-select: none;
                touch-action: none;
                z-index: 3;
            }}
            .column-resizer::after {{
                content: "";
                position: absolute;
                top: 20%;
                bottom: 20%;
                inset-inline-end: 4px;
                width: 3px;
                border-radius: 999px;
                background: rgba(27, 87, 208, 0.35);
                transition: background 0.15s ease;
            }}
            th:hover .column-resizer::after,
            th.resizing .column-resizer::after {{
                background: rgba(27, 87, 208, 0.85);
            }}
            th.sortable-th .sort-label {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
            }}
            th.sortable-th .sort-indicator {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 18px;
                height: 18px;
                border-radius: 50%;
                background: rgba(27, 87, 208, 0.08);
                color: #6c82a1;
                font-size: 11px;
                font-weight: 900;
                transition: all 0.18s ease;
            }}
            th.sortable-th.sorted-asc .sort-indicator,
            th.sortable-th.sorted-desc .sort-indicator {{
                background: rgba(27, 87, 208, 0.14);
                color: #1b57d0;
            }}
            html[dir="rtl"] th,
            html[dir="rtl"] td,
            html[dir="rtl"] .list-title,
            html[dir="rtl"] .sub-title,
            html[dir="rtl"] .card,
            html[dir="rtl"] .topbar h1 {{
                text-align: right;
            }}
            td {{
                padding: 14px 14px;
                border-bottom: 1px solid #edf1f7;
                font-size: 14px;
                color: #1f3657;
                vertical-align: middle;
            }}
            tbody tr:hover td {{
                background: #f9fbff;
            }}
            .summary-row td {{
                background: #f3f7ff;
                font-weight: 800;
                color: #17345f;
            }}
            .table-pagination {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 14px;
                flex-wrap: wrap;
                padding: 14px 4px 2px 4px;
                color: #234268;
            }}
            .table-pagination[hidden] {{
                display: none !important;
            }}
            .table-pagination-group {{
                display: inline-flex;
                align-items: center;
                gap: 10px;
                flex-wrap: wrap;
            }}
            .table-pagination-label {{
                font-size: 13px;
                font-weight: 700;
                color: #315173;
            }}
            .table-pagination-select {{
                min-width: 94px;
                height: 38px;
                border-radius: 12px;
                border: 1px solid #d6e1ef;
                background: #ffffff;
                color: #17345f;
                padding: 0 12px;
                font-size: 13px;
                font-weight: 800;
                outline: none;
                box-shadow: 0 6px 16px rgba(19, 49, 92, 0.08);
            }}
            .table-pagination-select option {{
                color: #17345f;
                background: #ffffff;
            }}
            .table-pagination-info {{
                font-size: 13px;
                font-weight: 700;
                color: #5f7795;
            }}
            .table-pagination-buttons {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                flex-wrap: wrap;
            }}
            .table-pagination-btn {{
                min-width: 38px;
                height: 38px;
                padding: 0 12px;
                border-radius: 12px;
                border: 1px solid #d7e3f1;
                background: #ffffff;
                color: #1d406e;
                font-size: 13px;
                font-weight: 800;
                cursor: pointer;
                transition: transform 0.16s ease, background 0.16s ease, border-color 0.16s ease;
                box-shadow: 0 8px 18px rgba(15, 53, 106, 0.08);
            }}
            .table-pagination-btn:hover:not(:disabled) {{
                background: #eef5ff;
                border-color: #bfd3ee;
                transform: translateY(-1px);
            }}
            .table-pagination-btn.active {{
                background: linear-gradient(135deg, #1b57d0 0%, #23c9ea 100%);
                border-color: rgba(27, 87, 208, 0.16);
                color: #ffffff;
                box-shadow: 0 10px 18px rgba(14, 79, 184, 0.22);
            }}
            .table-pagination-btn:disabled {{
                cursor: default;
                opacity: 0.5;
                color: #9aaec8;
                background: #f4f7fb;
            }}
            .table-pagination-dots {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-width: 24px;
                color: #7c93b0;
                font-weight: 800;
            }}
            .text-right {{
                text-align: right;
            }}
            .muted {{
                color: #6d809c;
            }}
            .empty-state {{
                text-align: center;
                color: #6d809c;
                padding: 28px 16px;
            }}
            .badge {{
                display: inline-flex;
                align-items: center;
                padding: 6px 10px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 800;
            }}
            .badge.green {{ background: #e8f7ec; color: #217a3c; }}
            .badge.red {{ background: #fdecec; color: #b42318; }}
            .badge.orange {{ background: #fff3df; color: #b86b00; }}
            .audit-list {{
                display: flex;
                flex-direction: column;
                gap: 12px;
            }}
            .audit-item {{
                background: #fbfcff;
                border: 1px solid #dfe6f1;
                border-radius: 14px;
                padding: 14px 16px;
            }}
            .audit-item-head {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 12px;
                flex-wrap: wrap;
                margin-bottom: 6px;
            }}
            .audit-item-title {{
                font-size: 15px;
                font-weight: 800;
                color: #16335d;
            }}
            .audit-item-meta {{
                font-size: 12px;
                color: #6d809c;
                font-weight: 700;
            }}
            .audit-note {{
                font-size: 13px;
                color: #47607f;
                line-height: 1.5;
            }}
            .audit-item-user {{
                margin-top: 8px;
                font-size: 12px;
                color: #6d809c;
                font-weight: 700;
            }}
            .page-tabs {{
                display: flex;
                gap: 14px;
                border-bottom: 1px solid #e2e8f2;
                margin-bottom: 16px;
                flex-wrap: wrap;
            }}
            .page-tab {{
                padding: 10px 4px 12px 4px;
                color: #5f7390;
                font-size: 14px;
                font-weight: 700;
                border-bottom: 3px solid transparent;
            }}
            .page-tab.active {{
                color: #1b57d0;
                border-bottom-color: #1b57d0;
            }}
            .modal-shell {{
                position: fixed;
                inset: 0;
                background: rgba(8, 20, 45, 0.52);
                backdrop-filter: blur(6px);
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 22px;
                z-index: 10000;
                animation: fadeIn 0.2s ease;
            }}
            .modal-card {{
                width: min(560px, 100%);
                border-radius: 24px;
                background:
                    radial-gradient(circle at top right, rgba(42, 103, 234, 0.12), transparent 34%),
                    linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
                border: 1px solid #dfe6f1;
                box-shadow: 0 28px 60px rgba(15, 35, 95, 0.24);
                overflow: hidden;
                animation: slideUp 0.22s ease;
            }}
            .modal-head {{
                padding: 22px 24px 10px 24px;
            }}
            .modal-kicker {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 7px 12px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 800;
                margin-bottom: 14px;
            }}
            .modal-kicker.error {{
                background: #fff1f1;
                color: #c62828;
            }}
            .modal-kicker.warning {{
                background: #fff6e7;
                color: #b86b00;
            }}
            .modal-kicker.info {{
                background: #eef4ff;
                color: #1b57d0;
            }}
            .modal-title {{
                font-size: 28px;
                line-height: 1.1;
                color: #13315c;
                font-weight: 900;
                margin-bottom: 10px;
            }}
            .modal-body {{
                padding: 0 24px 24px 24px;
                color: #5d7492;
                font-size: 15px;
                line-height: 1.7;
            }}
            .modal-actions {{
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
                margin-top: 18px;
            }}
            .modal-close {{
                position: absolute;
                top: 16px;
                right: 16px;
                width: 40px;
                height: 40px;
                border-radius: 12px;
                border: 1px solid #dfe6f1;
                background: rgba(255,255,255,0.82);
                color: #355377;
                font-size: 20px;
                font-weight: 800;
                cursor: pointer;
            }}
            .modal-card-wrap {{
                position: relative;
            }}
            @keyframes fadeIn {{
                from {{ opacity: 0; }}
                to {{ opacity: 1; }}
            }}
            @keyframes slideUp {{
                from {{
                    opacity: 0;
                    transform: translateY(16px) scale(0.98);
                }}
                to {{
                    opacity: 1;
                    transform: translateY(0) scale(1);
                }}
            }}
            @media (max-width: 1300px) {{
                .card-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
                .form-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
                .report-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
                .report-hero {{ grid-template-columns: 1fr; }}
                .filter-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
            }}
            @media (max-width: 900px) {{
                .sidebar {{
                    width: 88px;
                    min-width: 88px;
                    flex-basis: 88px;
                    padding: 18px 10px;
                }}
                .logo-box .logo-title,
                .logo-box .logo-sub,
                .menu-text {{
                    display: none;
                }}
                .logo-img {{
                    width: 66px;
                    margin-bottom: 0;
                    margin-left: auto;
                }}
                .menu-item {{
                    justify-content: center;
                    padding: 12px;
                }}
                .main {{
                    padding: 14px;
                }}
                .main::after {{
                    background:
                        radial-gradient(circle at 78% 78%, rgba(54, 213, 243, 0.22), transparent 18%),
                        radial-gradient(circle at 92% 66%, rgba(116, 238, 255, 0.08), transparent 24%);
                }}
                .card-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
                .form-grid {{ grid-template-columns: 1fr; }}
                .report-grid {{ grid-template-columns: 1fr; }}
                .kpi-grid {{ grid-template-columns: 1fr; }}
                .col {{ min-width: 100%; }}
                .filter-grid {{ grid-template-columns: 1fr; }}
            }}
            @media (max-width: 620px) {{
                .card-grid {{ grid-template-columns: 1fr; }}
                .report-hero {{
                    padding: 18px;
                    border-radius: 18px;
                }}
                .report-hero h2 {{
                    font-size: 22px;
                }}
                .modal-title {{
                    font-size: 22px;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="app">
            <aside class="sidebar">
                <div class="logo-box">
                    <a class="logo-link" href="{logo_href}">
                        <img src="/static/logo6.png" alt="System Logo" class="logo-img">
                        <div class="logo-title">ULTRA POWER</div>
                        <div class="logo-sub">Premium One</div>
                    </a>
                </div>
                <div class="menu">
                    {sidebar_items_html}
                </div>
            </aside>
            <main class="main">
                <div class="topbar">
                    <h1>{title}</h1>
                    <div class="topbar-actions">
                        {'<a class="topbar-link secondary" href="' + module_home_href + '" id="moduleBackLink">' + module_back_label + '</a>' if show_module_back else ''}
                        <button class="topbar-link secondary" type="button" id="langToggleBtn">{language_label}</button>
                        <a class="topbar-link" href="{home_href}" id="homeLink">{home_label}</a>
                        <a class="topbar-link secondary" href="/logout" id="logoutLink">{logout_label}</a>
                        <div class="topbar-badge"><img src="/static/icons/nav-notification.svg" alt="Alerts"></div>
                    </div>
                </div>
                {content}
            </main>
        </div>
        <button class="assistant-fab" type="button" id="assistantFab" title="{assistant_label}">AI</button>
        <div class="assistant-panel" id="assistantPanel">
            <div class="assistant-head">
                <span>{assistant_label}</span>
                <button class="assistant-close" type="button" id="assistantClose">أ—</button>
            </div>
            <div class="assistant-messages" id="assistantMessages"></div>
            <form class="assistant-form" id="assistantForm">
                <input id="assistantInput" autocomplete="off" placeholder="{assistant_placeholder}" />
                <button class="btn green" type="submit">{assistant_send}</button>
            </form>
        </div>
        <script src="/static/js/searchable.js"></script>
        <script src="/static/js/i18n.js?v={_I18N_JS_VERSION}"></script>
        <script>
            window.erpCurrentPath = {json.dumps(current_path or "")};
            window.erpUiLang = {json.dumps(lang)};
            try {{
                localStorage.setItem('ui_lang', window.erpUiLang);
                document.cookie = 'ui_lang=' + window.erpUiLang + '; path=/; max-age=31536000; SameSite=Lax';
            }} catch (e) {{}}
            window.closePageModal = function() {{
                document.querySelectorAll('[data-page-modal]').forEach(function(el) {{
                    el.remove();
                }});
            }};

            function toggleUiLanguage() {{
                const params = new URLSearchParams(window.location.search);
                const current = (window.currentUiLang ? window.currentUiLang() : (params.get('lang') === 'ar' ? 'ar' : 'en'));
                const next = current === 'ar' ? 'en' : 'ar';
                try {{
                    localStorage.setItem('ui_lang', next);
                    document.cookie = 'ui_lang=' + next + '; path=/; max-age=31536000; SameSite=Lax';
                }} catch (e) {{}}
                params.set('lang', next);
                window.location.search = params.toString();
            }}

            document.addEventListener('DOMContentLoaded', function() {{
                const toggleBtn = document.getElementById('langToggleBtn');
                if (toggleBtn) {{
                    toggleBtn.addEventListener('click', toggleUiLanguage);
                }}

                const assistantFab = document.getElementById('assistantFab');
                const assistantPanel = document.getElementById('assistantPanel');
                const assistantClose = document.getElementById('assistantClose');
                const assistantForm = document.getElementById('assistantForm');
                const assistantInput = document.getElementById('assistantInput');
                const assistantMessages = document.getElementById('assistantMessages');

                function assistantAddMessage(text, kind, action) {{
                    if (!assistantMessages) return;
                    const bubble = document.createElement('div');
                    bubble.className = 'assistant-message ' + (kind || 'bot');
                    const span = document.createElement('div');
                    span.textContent = text;
                    bubble.appendChild(span);
                    if (action && Array.isArray(action.steps) && action.steps.length) {{
                        const list = document.createElement('ol');
                        action.steps.forEach(function(step) {{
                            const item = document.createElement('li');
                            item.textContent = step;
                            list.appendChild(item);
                        }});
                        bubble.appendChild(list);
                    }}
                    if (action && action.href) {{
                        const link = document.createElement('a');
                        link.className = 'btn blue';
                        link.href = action.href;
                        link.textContent = action.label || 'Open';
                        bubble.appendChild(link);
                    }}
                    assistantMessages.appendChild(bubble);
                    assistantMessages.scrollTop = assistantMessages.scrollHeight;
                }}

                if (assistantFab && assistantPanel) {{
                    assistantFab.addEventListener('click', function() {{
                        assistantPanel.classList.toggle('open');
                        if (assistantPanel.classList.contains('open')) {{
                            setTimeout(function() {{ assistantInput && assistantInput.focus(); }}, 50);
                        }}
                    }});
                }}
                if (assistantClose && assistantPanel) {{
                    assistantClose.addEventListener('click', function() {{
                        assistantPanel.classList.remove('open');
                    }});
                }}
                if (assistantForm) {{
                    assistantForm.addEventListener('submit', async function(event) {{
                        event.preventDefault();
                        const message = (assistantInput && assistantInput.value || '').trim();
                        if (!message) return;
                        assistantAddMessage(message, 'user');
                        assistantInput.value = '';
                        try {{
                            const response = await fetch('/ui/assistant/chat', {{
                                method: 'POST',
                                headers: {{ 'Content-Type': 'application/json' }},
                                body: JSON.stringify({{
                                    message: message,
                                    current_path: window.erpCurrentPath || window.location.pathname,
                                    lang: window.currentUiLang ? window.currentUiLang() : document.documentElement.lang || 'en'
                                }})
                            }});
                            const data = await response.json();
                            assistantAddMessage(data.reply || {json.dumps(done_label)}, 'bot', data.action);
                            if (data.redirect && data.action && data.action.href) {{
                                window.location.href = data.action.href;
                            }}
                        }} catch (e) {{
                            assistantAddMessage({json.dumps(failed_label)}, 'bot');
                        }}
                    }});
                }}

                function parseSortValue(raw) {{
                    const text = (raw || '').replace(/\\s+/g, ' ').trim();
                    if (!text) return {{ type: 'text', value: '' }};

                    const isoDate = text.match(/^(\\d{{4}})[\\/\\-](\\d{{1,2}})[\\/\\-](\\d{{1,2}})$/);
                    if (isoDate) {{
                        return {{
                            type: 'date',
                            value: new Date(Number(isoDate[1]), Number(isoDate[2]) - 1, Number(isoDate[3])).getTime()
                        }};
                    }}

                    const usDate = text.match(/^(\\d{{1,2}})[\\/\\-](\\d{{1,2}})[\\/\\-](\\d{{4}})$/);
                    if (usDate) {{
                        return {{
                            type: 'date',
                            value: new Date(Number(usDate[3]), Number(usDate[1]) - 1, Number(usDate[2])).getTime()
                        }};
                    }}

                    const numberText = text.replace(/[^0-9.\\-]/g, '');
                    if (numberText && /^-?\\d+(\\.\\d+)?$/.test(numberText)) {{
                        return {{ type: 'number', value: Number(numberText) }};
                    }}

                    return {{ type: 'text', value: text.toLowerCase() }};
                }}

                function cellText(row, index) {{
                    const cells = row.querySelectorAll('td');
                    if (!cells[index]) return '';
                    return cells[index].innerText || cells[index].textContent || '';
                }}

                function enableTableColumnResizing(root) {{
                    const tables = (root || document).querySelectorAll('.table-wrap table, .card table');
                    tables.forEach(function(table) {{
                        const headerRow = table.querySelector('tr');
                        if (!headerRow || table.dataset.resizeReady === '1') return;
                        const headers = Array.from(headerRow.querySelectorAll('th'));
                        if (headers.length < 2) return;

                        table.dataset.resizeReady = '1';
                        table.style.tableLayout = 'fixed';
                        const tableWidth = table.getBoundingClientRect().width;
                        if (tableWidth > 0) table.style.width = tableWidth + 'px';

                        headers.forEach(function(th) {{
                            if (th.querySelector('.column-resizer')) return;
                            th.classList.add('resizable-th');
                            const currentWidth = th.getBoundingClientRect().width;
                            if (currentWidth > 0) th.style.width = currentWidth + 'px';
                            const handle = document.createElement('span');
                            handle.className = 'column-resizer';
                            handle.addEventListener('mousedown', function(event) {{
                                event.preventDefault();
                                event.stopPropagation();
                                const startX = event.clientX;
                                const startWidth = th.getBoundingClientRect().width;
                                th.classList.add('resizing');

                                function onMove(moveEvent) {{
                                    const delta = moveEvent.clientX - startX;
                                    th.style.width = Math.max(80, startWidth + delta) + 'px';
                                }}

                                function onUp() {{
                                    th.classList.remove('resizing');
                                    document.removeEventListener('mousemove', onMove);
                                    document.removeEventListener('mouseup', onUp);
                                }}

                                document.addEventListener('mousemove', onMove);
                                document.addEventListener('mouseup', onUp);
                            }});
                            th.appendChild(handle);
                        }});
                    }});
                }}

                function tablePaginationLabels() {{
                    const isArabic = document.documentElement.getAttribute('dir') === 'rtl' || document.documentElement.lang === 'ar';
                    return isArabic
                        ? {{
                            rows: '\u0639\u062f\u062f \u0627\u0644\u0635\u0641\u0648\u0641',
                            all: '\u0627\u0644\u0643\u0644',
                            previous: '\u0627\u0644\u0633\u0627\u0628\u0642',
                            next: '\u0627\u0644\u062a\u0627\u0644\u064a',
                            page: '\u0635\u0641\u062d\u0629',
                            showing: '\u0639\u0631\u0636',
                            of: '\u0645\u0646',
                            entries: '\u0635\u0641'
                        }}
                        : {{
                            rows: 'Rows',
                            all: 'All',
                            previous: 'Prev',
                            next: 'Next',
                            page: 'Page',
                            showing: 'Showing',
                            of: 'of',
                            entries: 'rows'
                        }};
                }}

                function getTableDataRows(table) {{
                    return Array.from(table.querySelectorAll('tr')).filter(function(row, idx) {{
                        return idx > 0 && row.querySelectorAll('td').length > 0 && !row.classList.contains('custom-filter-empty-row');
                    }});
                }}

                function tableAllowsPagination(table) {{
                    if (!table || table.dataset.noPagination === '1' || table.closest('[data-no-pagination="1"]')) return false;
                    if (table.querySelector('tbody input, tbody select, tbody textarea, tbody button, tbody [contenteditable="true"]')) return false;
                    return getTableDataRows(table).length > 0;
                }}

                function tableAllowsCustomFilters(table) {{
                    if (!table || table.dataset.noCustomFilter === '1' || table.closest('[data-no-custom-filter="1"]')) return false;
                    if (table.querySelector('tbody input, tbody select, tbody textarea, tbody [contenteditable="true"]')) return false;
                    return getTableDataRows(table).length > 0;
                }}

                function customFilterLabels() {{
                    const isArabic = document.documentElement.getAttribute('dir') === 'rtl' || document.documentElement.lang === 'ar';
                    return isArabic
                        ? {{
                            title: 'فلتر مخصص',
                            addFilter: '+ إضافة فلتر',
                            hideFilter: 'إخفاء الفلتر',
                            apply: 'تطبيق',
                            clear: 'مسح',
                            column: 'العمود',
                            operator: 'الشرط',
                            value: 'القيمة',
                            contains: 'يحتوي على',
                            equals: 'يساوي',
                            starts: 'يبدأ بـ',
                            ends: 'ينتهي بـ',
                            greater: 'أكبر من',
                            less: 'أقل من',
                            empty: 'فارغ',
                            notEmpty: 'غير فارغ',
                            noResults: 'لا توجد نتائج مطابقة للفلاتر.',
                            allColumns: 'كل الأعمدة'
                        }}
                        : {{
                            title: 'Custom Filter',
                            addFilter: '+ Add Filter',
                            hideFilter: 'Hide Filter',
                            apply: 'Apply',
                            clear: 'Clear',
                            column: 'Column',
                            operator: 'Condition',
                            value: 'Value',
                            contains: 'Contains',
                            equals: 'Equals',
                            starts: 'Starts with',
                            ends: 'Ends with',
                            greater: 'Greater than',
                            less: 'Less than',
                            empty: 'Is empty',
                            notEmpty: 'Is not empty',
                            noResults: 'No rows match the current filters.',
                            allColumns: 'All columns'
                        }};
                }}

                function normalizeFilterText(value) {{
                    return (value || '').replace(/\\s+/g, ' ').trim();
                }}

                function parseFilterNumber(value) {{
                    const cleaned = normalizeFilterText(value).replace(/,/g, '').replace(/[^0-9.\\-]/g, '');
                    if (!cleaned || !/^-?\\d+(\\.\\d+)?$/.test(cleaned)) return null;
                    return Number(cleaned);
                }}

                function getTableHeaders(table) {{
                    const headerRow = table.querySelector('tr');
                    if (!headerRow) return [];
                    return Array.from(headerRow.querySelectorAll('th')).map(function(th, index) {{
                        const clone = th.cloneNode(true);
                        clone.querySelectorAll('.sort-indicator, .column-resizer').forEach(function(el) {{ el.remove(); }});
                        const label = normalizeFilterText(clone.innerText || clone.textContent || ('Column ' + (index + 1)));
                        return {{ index: index, label: label || ('Column ' + (index + 1)) }};
                    }}).filter(function(item) {{
                        const lower = item.label.toLowerCase();
                        return lower && lower !== 'action' && lower !== 'actions' && item.label !== 'الإجراء' && item.label !== 'الإجراءات';
                    }});
                }}

                function rowMatchesCustomFilters(row, filters) {{
                    if (!filters.length) return true;
                    return filters.every(function(filter) {{
                        const text = normalizeFilterText(cellText(row, filter.column)).toLowerCase();
                        const rawText = normalizeFilterText(cellText(row, filter.column));
                        const value = normalizeFilterText(filter.value).toLowerCase();
                        if (filter.operator === 'empty') return rawText === '';
                        if (filter.operator === 'not_empty') return rawText !== '';
                        if (filter.operator === 'equals') return text === value;
                        if (filter.operator === 'starts') return text.startsWith(value);
                        if (filter.operator === 'ends') return text.endsWith(value);
                        if (filter.operator === 'greater' || filter.operator === 'less') {{
                            const rowNumber = parseFilterNumber(rawText);
                            const filterNumber = parseFilterNumber(filter.value);
                            if (rowNumber === null || filterNumber === null) return false;
                            return filter.operator === 'greater' ? rowNumber > filterNumber : rowNumber < filterNumber;
                        }}
                        return text.includes(value);
                    }});
                }}

                function renderFilterChips(table, chips, filters, labels) {{
                    chips.innerHTML = '';
                    filters.forEach(function(filter, idx) {{
                        const header = getTableHeaders(table).find(function(item) {{ return item.index === filter.column; }});
                        const chip = document.createElement('span');
                        chip.className = 'custom-filter-chip';
                        const opLabel = labels[filter.operator] || labels.contains;
                        const filterValue = (filter.operator === 'empty' || filter.operator === 'not_empty') ? '' : ': ' + filter.value;
                        chip.appendChild(document.createTextNode((header ? header.label : labels.column) + ' - ' + opLabel + filterValue));
                        const removeBtn = document.createElement('button');
                        removeBtn.type = 'button';
                        removeBtn.textContent = '×';
                        removeBtn.addEventListener('click', function() {{
                            filters.splice(idx, 1);
                            applyTableCustomFilters(table, filters);
                            renderFilterChips(table, chips, filters, labels);
                        }});
                        chip.appendChild(removeBtn);
                        chips.appendChild(chip);
                    }});
                }}

                function applyTableCustomFilters(table, filters) {{
                    const rows = getTableDataRows(table).filter(function(row) {{
                        return !row.classList.contains('summary-row') && row.dataset.sortFixed !== 'bottom' && row.dataset.customFilterHidden !== '1';
                    }});
                    let visibleCount = 0;
                    rows.forEach(function(row) {{
                        const visible = rowMatchesCustomFilters(row, filters);
                        row.dataset.customFilterHidden = visible ? '0' : '1';
                        row.style.display = visible ? '' : 'none';
                        if (visible) visibleCount += 1;
                    }});

                    let emptyRow = table.querySelector('tr.custom-filter-empty-row');
                    if (!emptyRow) {{
                        emptyRow = document.createElement('tr');
                        emptyRow.className = 'custom-filter-empty-row';
                        const td = document.createElement('td');
                        td.colSpan = Math.max(1, (table.querySelectorAll('tr:first-child th').length || 1));
                        emptyRow.appendChild(td);
                        const tbody = table.tBodies[0] || table;
                        tbody.appendChild(emptyRow);
                    }}
                    emptyRow.querySelector('td').textContent = customFilterLabels().noResults;
                    emptyRow.style.display = filters.length && visibleCount === 0 ? '' : 'none';

                    table.dataset.page = '1';
                    refreshTablePagination(table);
                }}

                function enableTableCustomFilters(root) {{
                    const tables = (root || document).querySelectorAll('.table-wrap table, .card table');
                    tables.forEach(function(table) {{
                        if (table.dataset.customFilterReady === '1') return;
                        if (!tableAllowsCustomFilters(table)) return;
                        const headers = getTableHeaders(table);
                        if (headers.length < 2) return;

                        table.dataset.customFilterReady = '1';
                        const labels = customFilterLabels();
                        const filters = [];

                        const bar = document.createElement('div');
                        bar.className = 'custom-filter-bar';

                        const title = document.createElement('div');
                        title.className = 'custom-filter-title';
                        title.textContent = labels.title;

                        const actions = document.createElement('div');
                        actions.className = 'custom-filter-actions';
                        const toggle = document.createElement('button');
                        toggle.type = 'button';
                        toggle.className = 'btn gray';
                        toggle.textContent = labels.addFilter;
                        toggle.addEventListener('click', function() {{
                            bar.classList.toggle('open');
                            toggle.textContent = bar.classList.contains('open') ? labels.hideFilter : labels.addFilter;
                        }});
                        actions.appendChild(toggle);

                        const builder = document.createElement('div');
                        builder.className = 'custom-filter-builder';

                        const columnSelect = document.createElement('select');
                        headers.forEach(function(header) {{
                            const option = document.createElement('option');
                            option.value = String(header.index);
                            option.textContent = header.label;
                            columnSelect.appendChild(option);
                        }});

                        const operatorSelect = document.createElement('select');
                        [
                            ['contains', labels.contains],
                            ['equals', labels.equals],
                            ['starts', labels.starts],
                            ['ends', labels.ends],
                            ['greater', labels.greater],
                            ['less', labels.less],
                            ['empty', labels.empty],
                            ['not_empty', labels.notEmpty]
                        ].forEach(function(item) {{
                            const option = document.createElement('option');
                            option.value = item[0];
                            option.textContent = item[1];
                            operatorSelect.appendChild(option);
                        }});

                        const valueInput = document.createElement('input');
                        valueInput.type = 'text';
                        valueInput.placeholder = labels.value;

                        operatorSelect.addEventListener('change', function() {{
                            const noValue = operatorSelect.value === 'empty' || operatorSelect.value === 'not_empty';
                            valueInput.disabled = noValue;
                            if (noValue) valueInput.value = '';
                        }});

                        const applyBtn = document.createElement('button');
                        applyBtn.type = 'button';
                        applyBtn.className = 'btn blue';
                        applyBtn.textContent = labels.apply;
                        const clearBtn = document.createElement('button');
                        clearBtn.type = 'button';
                        clearBtn.className = 'btn gray';
                        clearBtn.textContent = labels.clear;

                        const chips = document.createElement('div');
                        chips.className = 'custom-filter-chips';

                        applyBtn.addEventListener('click', function() {{
                            const operator = operatorSelect.value;
                            const needsValue = operator !== 'empty' && operator !== 'not_empty';
                            const value = normalizeFilterText(valueInput.value);
                            if (needsValue && !value) return;
                            filters.push({{
                                column: Number(columnSelect.value),
                                operator: operator,
                                value: value
                            }});
                            valueInput.value = '';
                            applyTableCustomFilters(table, filters);
                            renderFilterChips(table, chips, filters, labels);
                        }});

                        valueInput.addEventListener('keydown', function(event) {{
                            if (event.key === 'Enter') {{
                                event.preventDefault();
                                applyBtn.click();
                            }}
                        }});

                        clearBtn.addEventListener('click', function() {{
                            filters.splice(0, filters.length);
                            applyTableCustomFilters(table, filters);
                            renderFilterChips(table, chips, filters, labels);
                        }});

                        builder.appendChild(columnSelect);
                        builder.appendChild(operatorSelect);
                        builder.appendChild(valueInput);
                        builder.appendChild(applyBtn);
                        builder.appendChild(clearBtn);

                        bar.appendChild(title);
                        bar.appendChild(actions);
                        bar.appendChild(builder);
                        bar.appendChild(chips);

                        const host = table.closest('.table-wrap, .card') || table.parentNode;
                        host.insertAdjacentElement('beforebegin', bar);
                    }});
                }}

                function buildPageList(totalPages, currentPage) {{
                    if (totalPages <= 7) {{
                        return Array.from({{ length: totalPages }}, function(_, idx) {{ return idx + 1; }});
                    }}

                    const pages = [1];
                    const start = Math.max(2, currentPage - 1);
                    const end = Math.min(totalPages - 1, currentPage + 1);

                    if (start > 2) pages.push('dots-left');
                    for (let i = start; i <= end; i += 1) pages.push(i);
                    if (end < totalPages - 1) pages.push('dots-right');
                    pages.push(totalPages);
                    return pages;
                }}

                function createPaginationContainer(table) {{
                    const container = document.createElement('div');
                    container.className = 'table-pagination';

                    const sizeGroup = document.createElement('div');
                    sizeGroup.className = 'table-pagination-group';

                    const sizeLabel = document.createElement('span');
                    sizeLabel.className = 'table-pagination-label';
                    sizeLabel.dataset.paginationRole = 'rows-label';

                    const sizeSelect = document.createElement('select');
                    sizeSelect.className = 'table-pagination-select';
                    sizeSelect.dataset.paginationRole = 'size-select';

                    [6, 10, 25, 50, 'all'].forEach(function(value) {{
                        const option = document.createElement('option');
                        option.value = String(value);
                        option.textContent = value === 'all' ? tablePaginationLabels().all : String(value);
                        sizeSelect.appendChild(option);
                    }});

                    const info = document.createElement('span');
                    info.className = 'table-pagination-info';
                    info.dataset.paginationRole = 'info';

                    sizeGroup.appendChild(sizeLabel);
                    sizeGroup.appendChild(sizeSelect);
                    sizeGroup.appendChild(info);

                    const navGroup = document.createElement('div');
                    navGroup.className = 'table-pagination-buttons';
                    navGroup.dataset.paginationRole = 'buttons';

                    container.appendChild(sizeGroup);
                    container.appendChild(navGroup);

                    const host = table.closest('.table-wrap, .card') || table.parentNode;
                    host.insertAdjacentElement('afterend', container);

                    sizeSelect.addEventListener('change', function() {{
                        table.dataset.pageSize = sizeSelect.value;
                        table.dataset.page = '1';
                        refreshTablePagination(table);
                    }});

                    table._paginationContainer = container;
                    return container;
                }}

                function refreshTablePagination(table) {{
                    if (!tableAllowsPagination(table)) return;

                    const rows = getTableDataRows(table);
                    const fixedBottomRows = rows.filter(function(row) {{
                        return row.classList.contains('summary-row') || row.dataset.sortFixed === 'bottom';
                    }});
                    const paginatedRows = rows.filter(function(row) {{
                        return !row.classList.contains('summary-row') && row.dataset.sortFixed !== 'bottom' && row.dataset.customFilterHidden !== '1';
                    }});
                    rows.forEach(function(row) {{
                        if (row.dataset.customFilterHidden === '1') row.style.display = 'none';
                    }});

                    if (!paginatedRows.length) return;

                    let container = table._paginationContainer;
                    if (!container || !container.isConnected) {{
                        container = createPaginationContainer(table);
                    }}

                    const labels = tablePaginationLabels();
                    const select = container.querySelector('[data-pagination-role="size-select"]');
                    const rowsLabel = container.querySelector('[data-pagination-role="rows-label"]');
                    const info = container.querySelector('[data-pagination-role="info"]');
                    const buttons = container.querySelector('[data-pagination-role="buttons"]');

                    rowsLabel.textContent = labels.rows;

                    let pageSizeValue = table.dataset.pageSize || '6';
                    if (![ '6', '10', '25', '50', 'all' ].includes(pageSizeValue)) {{
                        pageSizeValue = '6';
                        table.dataset.pageSize = pageSizeValue;
                    }}
                    select.value = pageSizeValue;
                    Array.from(select.options).forEach(function(option) {{
                        option.textContent = option.value === 'all' ? labels.all : option.value;
                    }});

                    const pageSize = pageSizeValue === 'all' ? paginatedRows.length : Number(pageSizeValue);
                    const totalPages = Math.max(1, Math.ceil(paginatedRows.length / Math.max(pageSize, 1)));
                    let currentPage = Number(table.dataset.page || '1');
                    if (!Number.isFinite(currentPage) || currentPage < 1) currentPage = 1;
                    if (currentPage > totalPages) currentPage = totalPages;
                    table.dataset.page = String(currentPage);

                    const startIndex = pageSizeValue === 'all' ? 0 : (currentPage - 1) * pageSize;
                    const endIndex = pageSizeValue === 'all' ? paginatedRows.length : startIndex + pageSize;

                    paginatedRows.forEach(function(row, idx) {{
                        row.style.display = idx >= startIndex && idx < endIndex ? '' : 'none';
                    }});
                    fixedBottomRows.forEach(function(row) {{
                        row.style.display = '';
                    }});

                    const shownFrom = paginatedRows.length ? startIndex + 1 : 0;
                    const shownTo = pageSizeValue === 'all' ? paginatedRows.length : Math.min(endIndex, paginatedRows.length);
                    info.textContent = paginatedRows.length
                        ? labels.showing + ' ' + shownFrom + '-' + shownTo + ' ' + labels.of + ' ' + paginatedRows.length
                        : labels.showing + ' 0';

                    buttons.innerHTML = '';
                    container.hidden = paginatedRows.length <= 6 && pageSizeValue === '6';

                    if (container.hidden) {{
                        return;
                    }}

                    function addButton(text, targetPage, opts) {{
                        const btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = 'table-pagination-btn' + (opts && opts.active ? ' active' : '');
                        btn.textContent = text;
                        btn.disabled = !!(opts && opts.disabled);
                        btn.addEventListener('click', function() {{
                            if (btn.disabled) return;
                            table.dataset.page = String(targetPage);
                            refreshTablePagination(table);
                        }});
                        buttons.appendChild(btn);
                    }}

                    addButton(labels.previous, Math.max(1, currentPage - 1), {{ disabled: currentPage === 1 }});

                    buildPageList(totalPages, currentPage).forEach(function(token) {{
                        if (String(token).indexOf('dots') === 0) {{
                            const dots = document.createElement('span');
                            dots.className = 'table-pagination-dots';
                            dots.textContent = '...';
                            buttons.appendChild(dots);
                            return;
                        }}
                        addButton(String(token), token, {{ active: token === currentPage }});
                    }});

                    addButton(labels.next, Math.min(totalPages, currentPage + 1), {{ disabled: currentPage === totalPages }});
                }}

                function makeTablesSortable(root) {{
                    const tables = (root || document).querySelectorAll('.table-wrap table, .card table');
                    tables.forEach(function(table) {{
                        if (!tableAllowsPagination(table) && table.dataset.sortReady === '1') return;
                        if (table.dataset.sortReady === '1') return;
                        const headerRow = table.querySelector('tr');
                        if (!headerRow) return;
                        const headers = Array.from(headerRow.querySelectorAll('th'));
                        if (!headers.length) return;

                        const bodyRows = Array.from(table.querySelectorAll('tr')).filter(function(row, idx) {{
                            return idx > 0 && row.querySelectorAll('td').length > 0;
                        }});
                        if (bodyRows.length < 2) return;

                        table.dataset.sortReady = '1';

                        headers.forEach(function(th, index) {{
                            if (th.hasAttribute('data-no-sort')) return;
                            const original = th.innerHTML.trim();
                            th.classList.add('sortable-th');
                            th.innerHTML = '<span class=\"sort-label\">' + original + '<span class=\"sort-indicator\">\u2195</span></span>';

                            th.addEventListener('click', function() {{
                                const currentDir = th.dataset.sortDir === 'asc' ? 'asc' : (th.dataset.sortDir === 'desc' ? 'desc' : '');
                                const nextDir = currentDir === 'asc' ? 'desc' : 'asc';

                                headers.forEach(function(other) {{
                                    if (other !== th) {{
                                        other.dataset.sortDir = '';
                                        other.classList.remove('sorted-asc', 'sorted-desc');
                                        const indicator = other.querySelector('.sort-indicator');
                                        if (indicator) indicator.textContent = '\u2195';
                                    }}
                                }});

                                th.dataset.sortDir = nextDir;
                                th.classList.remove('sorted-asc', 'sorted-desc');
                                th.classList.add(nextDir === 'asc' ? 'sorted-asc' : 'sorted-desc');
                                const indicator = th.querySelector('.sort-indicator');
                                if (indicator) indicator.textContent = nextDir === 'asc' ? '\u2191' : '\u2193';

                                const rows = Array.from(table.querySelectorAll('tr')).filter(function(row, idx) {{
                                    return idx > 0 && row.querySelectorAll('td').length > 0;
                                }});
                                const fixedBottomRows = rows.filter(function(row) {{
                                    return row.classList.contains('summary-row') || row.dataset.sortFixed === 'bottom';
                                }});
                                const sortableRows = rows.filter(function(row) {{
                                    return !row.classList.contains('summary-row') && row.dataset.sortFixed !== 'bottom' && row.dataset.customFilterHidden !== '1';
                                }});

                                sortableRows.sort(function(a, b) {{
                                    const av = parseSortValue(cellText(a, index));
                                    const bv = parseSortValue(cellText(b, index));
                                    let result = 0;

                                    if ((av.type === 'number' || av.type === 'date') && av.type === bv.type) {{
                                        result = av.value - bv.value;
                                    }} else {{
                                        result = String(av.value).localeCompare(String(bv.value), undefined, {{ numeric: true, sensitivity: 'base' }});
                                    }}

                                    return nextDir === 'asc' ? result : -result;
                                }});

                                const anchorParent = rows[0] ? rows[0].parentNode : null;
                                if (!anchorParent) return;
                                sortableRows.concat(fixedBottomRows).forEach(function(row) {{
                                    anchorParent.appendChild(row);
                                }});
                                refreshTablePagination(table);
                            }});
                        }});
                    }});
                }}

                makeTablesSortable(document);
                enableTableColumnResizing(document);
                enableTableCustomFilters(document);
                document.querySelectorAll('.table-wrap table, .card table').forEach(function(table) {{
                    if (tableAllowsPagination(table)) {{
                        if (!table.dataset.pageSize) table.dataset.pageSize = '6';
                        if (!table.dataset.page) table.dataset.page = '1';
                        refreshTablePagination(table);
                    }}
                }});
            }});
        </script>
    </body>
    </html>
    """
