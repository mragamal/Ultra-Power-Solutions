import json
import os
from html import escape
from base64 import b64decode
from urllib.parse import quote_plus
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, TimestampSigner
from starlette.middleware.sessions import SessionMiddleware

from auth import can, current_user, default_home_path_for_user, is_logged_in
from audit import safe_log_request_action
from db import init_db
from i18n import fix_mojibake, get_lang
from layout import render_page

init_db()

app = FastAPI(title="Premium One ERP")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "premium-one-erp-session-key")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
session_signer = TimestampSigner(SESSION_SECRET_KEY)


TENANT_RESERVED_PATHS = {
    "ui",
    "login",
    "logout",
    "static",
    "uploads",
    "favicon.ico",
    "api",
}


def split_company_path(path: str):
    path = path or "/"
    parts = [part for part in path.split("/") if part]
    if not parts:
        return "", path

    first = parts[0].strip().lower()
    if first in TENANT_RESERVED_PATHS:
        return "", path

    if len(parts) == 1:
        return first, "/"

    rest = "/" + "/".join(parts[1:])
    if rest == "/" or rest.startswith(("/ui", "/login", "/logout")):
        return first, rest

    return "", path


def apply_company_path(request: Request):
    if request.scope.get("company_path_applied"):
        return request.scope.get("company_prefix", "")

    original_path = request.scope.get("path") or request.url.path or "/"
    company_slug, stripped_path = split_company_path(original_path)
    prefix = f"/{company_slug}" if company_slug else ""

    request.scope["company_path_applied"] = True
    request.scope["company_slug"] = company_slug
    request.scope["company_prefix"] = prefix
    request.scope["original_path"] = original_path

    if prefix:
        request.scope["path"] = stripped_path or "/"
        request.scope["root_path"] = prefix

    return prefix


def prefixed_location(prefix: str, location: str):
    if not prefix or not location or not location.startswith("/"):
        return location
    if location == prefix or location.startswith(prefix + "/"):
        return location
    if location.startswith(("/static", "/uploads", "/favicon.ico")):
        return location
    return prefix + location


def module_for_path(path: str):
    path = path or ""
    rules = [
        ("/ui/settings", "system"),
        ("/ui/assistant", None),
        ("/ui/system/users", "users"),
        ("/ui/accounting/accounts", "accounting.accounts"),
        ("/ui/accounting/journal", "accounting.journal"),
        ("/ui/accounting/customers", "accounting.customers"),
        ("/ui/accounting/partners", "accounting.customers"),
        ("/ui/accounting/customer-invoices", "accounting.customer_invoices"),
        ("/ui/accounting/customer-payments", "accounting.cash_receipts"),
        ("/ui/accounting/cash-receipts", "accounting.cash_receipts"),
        ("/ui/accounting/vendors", "accounting.vendors"),
        ("/ui/accounting/vendor-bills", "accounting.vendor_bills"),
        ("/ui/accounting/vendor-payments", "accounting.vendor_payments"),
        ("/ui/accounting/cash-payments", "accounting.cash_payments"),
        ("/ui/accounting/expenses", "accounting.expenses"),
        ("/ui/accounting/petty-cash", "accounting.petty_cash"),
        ("/ui/accounting/employee-advances", "accounting.employee_advances"),
        ("/ui/accounting/cost-centers", "accounting.cost_centers"),
        ("/ui/accounting/config", "accounting.settings"),
        ("/ui/accounting/setup", "accounting.settings"),
        ("/ui/accounting/fixed-assets", "fixed_assets"),
        ("/ui/accounting/reports", "reports"),
        ("/ui/accounting/general-ledger", "reports"),
        ("/ui/accounting/trial-balance", "reports"),
        ("/ui/accounting/profit-loss", "reports"),
        ("/ui/accounting/balance-sheet", "reports"),
        ("/ui/accounting/partner-ledger", "reports"),
        ("/ui/accounting/aging", "reports"),
        ("/ui/accounting/monthly-dues", "reports"),
        ("/ui/accounting/petty-cash/statement", "reports"),
        ("/ui/hr", "hr"),
        ("/ui/inventory", "inventory"),
        ("/ui/purchasing", "purchasing"),
        ("/ui/sales", "sales"),
        ("/ui/operations", "operations"),
        ("/ui/projects", "operations"),
        ("/ui/accounting", "accounting"),
    ]
    for prefix, module_code in rules:
        if path.startswith(prefix):
            return module_code
    return None


def required_action_for_path(path: str, method: str):
    path = (path or "").lower().rstrip("/")
    method = (method or "").upper()
    if path.startswith(("/static", "/uploads", "/favicon.ico", "/api/assistant")):
        return None

    if method == "GET":
        if path.endswith("/edit"):
            return "edit"
        if path.endswith("/delete"):
            return "delete"
        return None

    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    last_part = path.rsplit("/", 1)[-1]
    if method == "DELETE" or last_part in {"delete", "cancel"}:
        return "delete"

    if last_part in {
        "post",
        "final-post",
        "reverse",
        "unpost",
        "approve",
        "reject",
        "submit-approval",
        "decision",
        "mark-disbursed",
    }:
        return "post"

    if last_part in {"edit", "update", "toggle-active", "password", "permissions", "reset"}:
        return "edit"

    if last_part in {"new", "add", "create", "import", "apply", "save-draft"}:
        return "create"

    if last_part == "save":
        return "write"

    return "write"


def can_write(request: Request, module_code: str) -> bool:
    return any(can(request, module_code, action) for action in ("create", "edit", "delete", "approve", "post"))


def has_required_action(request: Request, module_code: str, action: str) -> bool:
    if not action:
        return True
    if action == "write":
        return can_write(request, module_code)
    if action == "post":
        return can(request, module_code, "post") or can(request, module_code, "approve")
    return can(request, module_code, action)


def hydrate_session_from_cookie(request: Request):
    if request.scope.get("session"):
        return

    raw_cookie = request.cookies.get("session")
    if not raw_cookie:
        request.scope["session"] = {}
        return

    try:
        data = session_signer.unsign(raw_cookie.encode("utf-8"), max_age=14 * 24 * 60 * 60)
        request.scope["session"] = json.loads(b64decode(data))
    except (BadSignature, ValueError, TypeError):
        request.scope["session"] = {}


def should_audit_request(path: str, method: str) -> bool:
    path = path or ""
    method = (method or "").upper()
    if path.startswith(("/static", "/uploads", "/favicon.ico", "/api/assistant")):
        return False
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    if method == "GET" and path.endswith("/edit"):
        return True
    return False


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    apply_company_path(request)
    path = request.scope.get("path") or request.url.path or ""
    open_paths = ["/login", "/logout", "/static", "/favicon.ico"]
    if any(path.startswith(prefix) for prefix in open_paths):
        return await call_next(request)

    hydrate_session_from_cookie(request)

    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=302)

    module_code = module_for_path(path)
    if module_code and not can(request, module_code, "view"):
        return RedirectResponse(default_home_path_for_user(request), status_code=302)
    required_action = required_action_for_path(path, request.method)
    if module_code and required_action and not has_required_action(request, module_code, required_action):
        return HTMLResponse("Permission denied", status_code=403)

    response = await call_next(request)
    if should_audit_request(path, request.method):
        user = current_user(request) or {}
        user_id = int(user.get("user_id") or 0)
        action = "Opened edit screen" if request.method.upper() == "GET" else f"{request.method.upper()} request"
        safe_log_request_action(
            request,
            "system_activity",
            user_id,
            action,
            notes=path,
            module=module_code or "",
            status_code=getattr(response, "status_code", None),
        )
    return response


@app.middleware("http")
async def company_url_guard(request: Request, call_next):
    prefix = apply_company_path(request)
    response = await call_next(request)
    location = response.headers.get("location")
    if location:
        response.headers["location"] = prefixed_location(prefix, location)
    return response


def dashboard_cards(cards):
    html = '<div class="card-grid">'
    for title, href, icon, desc in cards:
        icon_html = f'<img src="{icon}" alt="{title} icon">' if icon.startswith("/static/") else icon
        html += f"""
        <a href="{href}" class="module-card">
            <div class="module-card-icon">{icon_html}</div>
            <div class="module-card-title">{title}</div>
            <div class="module-card-sub">{desc}</div>
        </a>
        """
    html += "</div>"
    return html


def card_section(title, cards):
    return f"""
    <div class="card">
        <h3 class="sub-title">{title}</h3>
        {dashboard_cards(cards)}
    </div>
    """


def t(lang: str, en: str, ar: str) -> str:
    if lang == "ar":
        return fix_mojibake(ar) or ar or en
    return en


ASSISTANT_ACTIONS = [
    {
        "title": "Customer invoice",
        "ar_title": "ظپط§طھظˆط±ط© ط¹ظ…ظٹظ„",
        "href": "/ui/accounting/customer-invoices/new",
        "keywords": ["invoice", "customer invoice", "sales invoice", "ظپط§طھظˆط±ط©", "ط¹ظ…ظٹظ„", "ط¨ظٹط¹", "ظ…ط¨ظٹط¹ط§طھ"],
        "steps": ["Choose the customer.", "Enter invoice date and lines.", "Save draft, review, then post."],
        "ar_steps": ["ط§ط®طھط§ط± ط§ظ„ط¹ظ…ظٹظ„.", "ط§ط¯ط®ظ„ طھط§ط±ظٹط® ط§ظ„ظپط§طھظˆط±ط© ظˆط§ظ„ط¨ظ†ظˆط¯.", "ط§ط­ظپط¸ ظ…ط³ظˆط¯ط©طŒ ط±ط§ط¬ط¹ظ‡ط§طŒ ط«ظ… ط±ط­ظ„ظ‡ط§."],
    },
    {
        "title": "Vendor bill",
        "ar_title": "ظپط§طھظˆط±ط© ظ…ظˆط±ط¯",
        "href": "/ui/accounting/vendor-bills/new",
        "keywords": ["vendor bill", "purchase invoice", "supplier invoice", "ظ…ظˆط±ط¯", "ظ…ط´طھط±ظٹط§طھ", "ظپط§طھظˆط±ط© ظ…ظˆط±ط¯"],
        "steps": ["Choose the vendor.", "Enter bill date and lines.", "Save draft, review, then post."],
        "ar_steps": ["ط§ط®طھط§ط± ط§ظ„ظ…ظˆط±ط¯.", "ط§ط¯ط®ظ„ طھط§ط±ظٹط® ط§ظ„ظپط§طھظˆط±ط© ظˆط§ظ„ط¨ظ†ظˆط¯.", "ط§ط­ظپط¸ ظ…ط³ظˆط¯ط©طŒ ط±ط§ط¬ط¹ظ‡ط§طŒ ط«ظ… ط±ط­ظ„ظ‡ط§."],
    },
    {
        "title": "Account",
        "ar_title": "حساب",
        "href": "/ui/accounting/accounts/new",
        "keywords": ["account", "chart account", "new account", "add account", "حساب", "حساب جديد", "اضيف حساب", "اضافة حساب", "دليل الحسابات"],
        "steps": ["Open the new account screen.", "Enter account code, name, and account type.", "Save the account and use it in transactions."],
        "ar_steps": ["افتح شاشة حساب جديد.", "ادخل كود الحساب واسمه ونوعه.", "احفظ الحساب وبعدها استخدمه في الحركات."],
    },
    {
        "title": "Expense request",
        "ar_title": "ظ…طµط±ظˆظپ",
        "href": "/ui/accounting/expenses/new",
        "keywords": ["expense", "expenses", "ظ…طµط±ظˆظپ", "ظ…طµط±ظˆظپط§طھ"],
        "steps": ["Create the expense request.", "Add expense lines.", "Pay it from Cash Payments when ready."],
        "ar_steps": ["ط§ط¹ظ…ظ„ ط·ظ„ط¨ ط§ظ„ظ…طµط±ظˆظپ.", "ط§ط¶ظپ ط¨ظ†ظˆط¯ ط§ظ„ظ…طµط±ظˆظپ.", "ط§طµط±ظپظ‡ ظ…ظ† ط³ظ†ط¯ط§طھ ط§ظ„طµط±ظپ ط¹ظ†ط¯ ط§ظ„طھظ†ظپظٹط°."],
    },
    {
        "title": "Cash payment",
        "ar_title": "ط³ظ†ط¯ طµط±ظپ",
        "href": "/ui/accounting/cash-payments/new",
        "keywords": ["cash payment", "payment voucher", "pay", "طµط±ظپ", "ط³ظ†ط¯ طµط±ظپ", "ط¯ظپط¹"],
        "steps": ["Select the paid party.", "Choose the cash or bank account.", "Save draft, review, then post."],
        "ar_steps": ["ط§ط®طھط§ط± ط¬ظ‡ط© ط§ظ„طµط±ظپ.", "ط§ط®طھط§ط± ط­ط³ط§ط¨ ط§ظ„ط®ط²ظ†ط© ط£ظˆ ط§ظ„ط¨ظ†ظƒ.", "ط§ط­ظپط¸ ظ…ط³ظˆط¯ط©طŒ ط±ط§ط¬ط¹طŒ ط«ظ… ط±ط­ظ„."],
    },
    {
        "title": "Cash receipt",
        "ar_title": "ط³ظ†ط¯ ظ‚ط¨ط¶",
        "href": "/ui/accounting/cash-receipts/new",
        "keywords": ["cash receipt", "receipt voucher", "receive", "ظ‚ط¨ط¶", "ط³ظ†ط¯ ظ‚ط¨ط¶", "طھط­طµظٹظ„"],
        "steps": ["Select the payer.", "Choose the cash or bank account.", "Save draft, review, then post."],
        "ar_steps": ["ط§ط®طھط§ط± ط¬ظ‡ط© ط§ظ„ظ‚ط¨ط¶.", "ط§ط®طھط§ط± ط­ط³ط§ط¨ ط§ظ„ط®ط²ظ†ط© ط£ظˆ ط§ظ„ط¨ظ†ظƒ.", "ط§ط­ظپط¸ ظ…ط³ظˆط¯ط©طŒ ط±ط§ط¬ط¹طŒ ط«ظ… ط±ط­ظ„."],
    },
    {
        "title": "Vendor payment",
        "ar_title": "ط¯ظپط¹ط© ظ…ظˆط±ط¯",
        "href": "/ui/accounting/vendor-payments/new",
        "keywords": ["vendor payment", "supplier payment", "pay vendor", "ط¯ظپط¹ط© ظ…ظˆط±ط¯", "ط¯ظپط¹ ظ„ظ…ظˆط±ط¯", "ط³ط¯ط§ط¯ ظ…ظˆط±ط¯"],
        "steps": ["Choose the vendor.", "Enter payment amount and date.", "Save draft, review, then post."],
        "ar_steps": ["ط§ط®طھط§ط± ط§ظ„ظ…ظˆط±ط¯.", "ط§ط¯ط®ظ„ طھط§ط±ظٹط® ظˆظ…ط¨ظ„ط؛ ط§ظ„ط¯ظپط¹ط©.", "ط§ط­ظپط¸ ظ…ط³ظˆط¯ط©طŒ ط±ط§ط¬ط¹طŒ ط«ظ… ط±ط­ظ„."],
    },
    {
        "title": "Customer payment",
        "ar_title": "طھط­طµظٹظ„ ط¹ظ…ظٹظ„",
        "href": "/ui/accounting/customer-payments/new",
        "keywords": ["customer payment", "customer receipt", "collect customer", "طھط­طµظٹظ„ ط¹ظ…ظٹظ„", "ظ‚ط¨ط¶ ظ…ظ† ط¹ظ…ظٹظ„", "ط¯ظپط¹ط© ط¹ظ…ظٹظ„"],
        "steps": ["Choose the customer.", "Enter payment amount and date.", "Save draft, review, then post."],
        "ar_steps": ["ط§ط®طھط§ط± ط§ظ„ط¹ظ…ظٹظ„.", "ط§ط¯ط®ظ„ طھط§ط±ظٹط® ظˆظ…ط¨ظ„ط؛ ط§ظ„طھط­طµظٹظ„.", "ط§ط­ظپط¸ ظ…ط³ظˆط¯ط©طŒ ط±ط§ط¬ط¹طŒ ط«ظ… ط±ط­ظ„."],
    },
    {
        "title": "Journal entry",
        "ar_title": "ظ‚ظٹط¯ ظٹظˆظ…ظٹط©",
        "href": "/ui/accounting/journal/new",
        "keywords": ["journal", "entry", "ظ‚ظٹط¯", "ظٹظˆظ…ظٹط©"],
        "steps": ["Enter date and description.", "Add balanced debit and credit lines.", "Save draft, review, then post."],
        "ar_steps": ["ط§ط¯ط®ظ„ ط§ظ„طھط§ط±ظٹط® ظˆط§ظ„ط¨ظٹط§ظ†.", "ط§ط¶ظپ ظ…ط¯ظٹظ† ظˆط¯ط§ط¦ظ† ظ…طھط³ط§ظˆظٹظٹظ†.", "ط§ط­ظپط¸ ظ…ط³ظˆط¯ط©طŒ ط±ط§ط¬ط¹طŒ ط«ظ… ط±ط­ظ„."],
    },
    {
        "title": "Employee advance",
        "ar_title": "ط³ظ„ظپط© ظ…ظˆط¸ظپ",
        "href": "/ui/accounting/employee-advances",
        "keywords": ["advance", "employee advance", "ط³ظ„ظپط©", "ط³ظ„ظپ", "ظ…ظˆط¸ظپ"],
        "steps": ["Open Employee Advances.", "Create or distribute the advance.", "Review installments and payroll deduction."],
        "ar_steps": ["ط§ظپطھط­ ط³ظ„ظپ ط§ظ„ظ…ظˆط¸ظپظٹظ†.", "ط§ط¹ظ…ظ„ ط§ظ„ط³ظ„ظپط© ط£ظˆ ظˆط²ط¹ظ‡ط§.", "ط±ط§ط¬ط¹ ط§ظ„ط£ظ‚ط³ط§ط· ظˆط§ظ„ط®طµظ… ظ…ظ† ط§ظ„ظ…ط±طھط¨ط§طھ."],
    },
    {
        "title": "Employee custody",
        "ar_title": "ط¹ظ‡ط¯ط© ظ…ظˆط¸ظپ",
        "href": "/ui/accounting/petty-cash/custody-request/new",
        "keywords": ["custody", "petty cash", "ط¹ظ‡ط¯ط©", "ط¹ظ‡ط¯"],
        "steps": ["Create the custody request.", "Approve or post it as needed.", "Track custody statement."],
        "ar_steps": ["ط§ط¹ظ…ظ„ ط·ظ„ط¨ ط§ظ„ط¹ظ‡ط¯ط©.", "ط§ط¹طھظ…ط¯ ط£ظˆ ط±ط­ظ„ ط­ط³ط¨ ط§ظ„ط­ط§ظ„ط©.", "طھط§ط¨ط¹ ظƒط´ظپ ط­ط³ط§ط¨ ط§ظ„ط¹ظ‡ط¯ط©."],
    },
    {
        "title": "Fixed asset",
        "ar_title": "ط£طµظ„ ط«ط§ط¨طھ",
        "href": "/ui/accounting/fixed-assets/new-asset",
        "keywords": ["fixed asset", "asset", "add asset", "ط£طµظ„", "ط§طµظ„", "ط£طµظˆظ„", "ط§طµظˆظ„", "ط§طµظ„ ط«ط§ط¨طھ", "ط£طµظ„ ط«ط§ط¨طھ"],
        "steps": ["Open the fixed asset screen.", "Enter asset data and acquisition value.", "Save and post acquisition when ready."],
        "ar_steps": ["ط§ظپطھط­ ط´ط§ط´ط© ط§ظ„ط£طµظ„ ط§ظ„ط«ط§ط¨طھ.", "ط§ط¯ط®ظ„ ط¨ظٹط§ظ†ط§طھ ط§ظ„ط£طµظ„ ظˆظ‚ظٹظ…ط© ط§ظ„ط´ط±ط§ط،.", "ط§ط­ظپط¸ ظˆط±ط­ظ„ ط§ظ„ط§ط³طھط­ظˆط§ط° ظ„ظ…ط§ ظٹظƒظˆظ† ط¬ط§ظ‡ط²."],
    },
    {
        "title": "Payroll",
        "ar_title": "ط§ظ„ظ…ط±طھط¨ط§طھ",
        "href": "/ui/hr/payroll/new",
        "keywords": ["payroll", "salary", "salaries", "ظ…ط±طھط¨ط§طھ", "ط±ط§طھط¨", "ظ…ط³ظٹط±"],
        "steps": ["Create a payroll run.", "Review deductions and advances.", "Post payroll after review."],
        "ar_steps": ["ط§ط¹ظ…ظ„ ظ…ط³ظٹط± ظ…ط±طھط¨ط§طھ.", "ط±ط§ط¬ط¹ ط§ظ„ط®طµظˆظ…ط§طھ ظˆط§ظ„ط³ظ„ظپ.", "ط±ط­ظ„ ط§ظ„ظ…ط±طھط¨ط§طھ ط¨ط¹ط¯ ط§ظ„ظ…ط±ط§ط¬ط¹ط©."],
    },
    {
        "title": "Reports",
        "ar_title": "ط§ظ„طھظ‚ط§ط±ظٹط±",
        "href": "/ui/accounting/reports",
        "keywords": ["report", "reports", "balance", "ledger", "statement", "طھظ‚ط±ظٹط±", "طھظ‚ط§ط±ظٹط±", "ظ…ظٹط²ط§ظ†", "ط£ط³طھط§ط°", "ظƒط´ظپ"],
        "steps": ["Open reports.", "Choose the needed report.", "Set dates and filters, then view or export."],
        "ar_steps": ["ط§ظپطھط­ ط§ظ„طھظ‚ط§ط±ظٹط±.", "ط§ط®طھط§ط± ط§ظ„طھظ‚ط±ظٹط± ط§ظ„ظ…ط·ظ„ظˆط¨.", "ط­ط¯ط¯ ط§ظ„ظپطھط±ط© ظˆط§ظ„ظپظ„ط§طھط± ط«ظ… ط§ط¹ط±ط¶ ط£ظˆ طµط¯ط±."],
    },
]


EXTRA_ASSISTANT_KEYWORDS = {
    "/ui/accounting/customer-invoices/new": [
        "ط¹ظ…ظ„ ظپط§طھظˆط±ط©", "ط§ط¹ظ…ظ„ ظپط§طھظˆط±ط©", "ظپط§طھظˆط±ط© ط¹ظ…ظٹظ„", "ظپط§طھظˆط±ظ‡ ط¹ظ…ظٹظ„", "ظپط§طھظˆط±ط© ط¨ظٹط¹",
        "ظپط§طھظˆط±ظ‡ ط¨ظٹط¹", "ط¨ظٹط¹ ظ„ظ„ط¹ظ…ظٹظ„", "customer bill", "customer inv",
    ],
    "/ui/accounting/vendor-bills/new": [
        "ظپط§طھظˆط±ط© ظ…ظˆط±ط¯", "ظپط§طھظˆط±ظ‡ ظ…ظˆط±ط¯", "ظپط§طھظˆط±ط© ط´ط±ط§ط،", "ظپط§طھظˆط±ظ‡ ط´ط±ط§ط،", "supplier bill", "purchase bill",
    ],
    "/ui/accounting/expenses/new": [
        "ظ…طµط±ظˆظپ", "ظ…طµط±ظˆظپط§طھ", "ط·ظ„ط¨ ظ…طµط±ظˆظپ", "ط§طµط±ظپ ظ…طµط±ظˆظپ", "ط¶ظٹط§ظپط©", "ط¨ظ†ط²ظٹظ†", "ط§ظٹط¬ط§ط±", "ظƒظ‡ط±ط¨ط§", "ظƒظ‡ط±ط¨ط§ط،",
    ],
    "/ui/accounting/cash-payments/new": [
        "ط³ظ†ط¯ طµط±ظپ", "ط§طµط±ظپ", "طµط±ظپ ظ†ظ‚ط¯ظٹ", "ط¯ظپط¹", "ط§ط¯ظپط¹", "payment voucher",
    ],
    "/ui/accounting/cash-receipts/new": [
        "ط³ظ†ط¯ ظ‚ط¨ط¶", "ط§ظ‚ط¨ط¶", "ظ‚ط¨ط¶", "طھط­طµظٹظ„", "ط§ط³طھظ„ط§ظ… ظپظ„ظˆط³", "receipt voucher",
    ],
    "/ui/accounting/vendor-payments/new": [
        "ط¯ظپط¹ط© ظ…ظˆط±ط¯", "ط¯ظپط¹ ظ„ظ…ظˆط±ط¯", "ط³ط¯ط§ط¯ ظ…ظˆط±ط¯", "ظ…ظˆط±ط¯ ظ‡ظٹط§ط®ط¯ ظپظ„ظˆط³", "vendor payment", "supplier payment",
    ],
    "/ui/accounting/customer-payments/new": [
        "طھط­طµظٹظ„ ط¹ظ…ظٹظ„", "ظ‚ط¨ط¶ ظ…ظ† ط¹ظ…ظٹظ„", "ط¯ظپط¹ط© ط¹ظ…ظٹظ„", "ط¹ظ…ظٹظ„ ط¯ظپط¹", "customer payment", "customer receipt",
    ],
    "/ui/accounting/journal/new": ["ظ‚ظٹط¯", "ظ‚ظٹط¯ ظٹظˆظ…ظٹط©", "ظ‚ظٹط¯ ظٹط¯ظˆظٹ", "طھط³ظˆظٹط©"],
    "/ui/accounting/employee-advances": ["ط³ظ„ظپط©", "ط³ظ„ظپظ‡", "ط³ظ„ظپ", "ط³ظ„ظپط© ظ…ظˆط¸ظپ", "ط³ظ„ظپ ط§ظ„ظ…ظˆط¸ظپظٹظ†"],
    "/ui/accounting/petty-cash/custody-request/new": ["ط¹ظ‡ط¯ط©", "ط¹ظ‡ط¯ظ‡", "ط·ظ„ط¨ ط¹ظ‡ط¯ط©", "ط¹ظ‡ط¯ط© ظ…ظˆط¸ظپ"],
    "/ui/hr/payroll/new": ["ظ…ط±طھط¨", "ظ…ط±طھط¨ط§طھ", "ظ…ط³ظٹط±", "ط±ظˆط§طھط¨"],
    "/ui/accounting/reports": ["طھظ‚ط±ظٹط±", "طھظ‚ط§ط±ظٹط±", "ظ…ظٹط²ط§ظ†", "ط§ط³طھط§ط°", "ط£ط³طھط§ط°", "ظƒط´ظپ ط­ط³ط§ط¨", "ط§ط±ط¨ط§ط­", "ط®ط³ط§ط¦ط±"],
    "/ui/hr/employees/new": ["ظ…ظˆط¸ظپ ط¬ط¯ظٹط¯", "ط§ط¶ط§ظپط© ظ…ظˆط¸ظپ", "ط§ط¶ظٹظپ ظ…ظˆط¸ظپ"],
    "/ui/inventory/items/new": ["طµظ†ظپ ط¬ط¯ظٹط¯", "ط§ط¶ط§ظپط© طµظ†ظپ", "ظ…ظ†طھط¬ ط¬ط¯ظٹط¯"],
    "/ui/purchasing/purchase-orders/new": ["ط§ظ…ط± ط´ط±ط§ط،", "ط£ظ…ط± ط´ط±ط§ط،", "purchase order", "po"],
    "/ui/sales/quotations/new": ["ط¹ط±ط¶ ط³ط¹ط±", "quotation", "quote"],
    "/ui/sales/orders/new": ["ط§ظ…ط± ط¨ظٹط¹", "ط£ظ…ط± ط¨ظٹط¹", "sales order"],
}


EXTRA_ASSISTANT_KEYWORDS["/ui/accounting/fixed-assets/new-asset"] = [
    "\u0623\u0635\u0644", "\u0627\u0635\u0644", "\u0623\u0635\u0648\u0644", "\u0627\u0635\u0648\u0644",
    "\u0623\u0635\u0644 \u062b\u0627\u0628\u062a", "\u0627\u0635\u0644 \u062b\u0627\u0628\u062a",
    "\u0627\u0636\u064a\u0641 \u0627\u0635\u0644", "\u0627\u0636\u0627\u0641\u0629 \u0627\u0635\u0644",
    "fixed asset", "add asset",
]
EXTRA_ASSISTANT_KEYWORDS["/ui/accounting/accounts/new"] = [
    "\u062d\u0633\u0627\u0628", "\u062d\u0633\u0627\u0628 \u062c\u062f\u064a\u062f",
    "\u0627\u0636\u064a\u0641 \u062d\u0633\u0627\u0628", "\u0627\u0636\u0627\u0641\u0629 \u062d\u0633\u0627\u0628",
    "\u062f\u0644\u064a\u0644 \u0627\u0644\u062d\u0633\u0627\u0628\u0627\u062a", "add account", "new account",
]

AR_ASSISTANT_TEXT = {
    "/ui/accounting/accounts/new": {
        "title": "\u062d\u0633\u0627\u0628 \u062c\u062f\u064a\u062f",
        "steps": [
            "\u0627\u0641\u062a\u062d \u0634\u0627\u0634\u0629 \u062d\u0633\u0627\u0628 \u062c\u062f\u064a\u062f.",
            "\u0627\u062f\u062e\u0644 \u0643\u0648\u062f \u0627\u0644\u062d\u0633\u0627\u0628 \u0648\u0627\u0633\u0645\u0647.",
            "\u0627\u062e\u062a\u0627\u0631 \u0646\u0648\u0639 \u0627\u0644\u062d\u0633\u0627\u0628 \u0648\u0627\u0644\u062d\u0633\u0627\u0628 \u0627\u0644\u0623\u0628 \u0644\u0648 \u0645\u0648\u062c\u0648\u062f.",
            "\u0627\u062d\u0641\u0638\u0647\u060c \u0648\u0628\u0639\u062f\u0647\u0627 \u0647\u064a\u0638\u0647\u0631 \u0641\u064a \u0627\u0644\u0642\u064a\u0648\u062f \u0648\u0627\u0644\u062d\u0631\u0643\u0627\u062a.",
        ],
    },
    "/ui/accounting/customer-invoices/new": {
        "title": "\u0641\u0627\u062a\u0648\u0631\u0629 \u0639\u0645\u064a\u0644",
        "steps": [
            "\u0627\u0641\u062a\u062d \u0634\u0627\u0634\u0629 \u0641\u0627\u062a\u0648\u0631\u0629 \u0639\u0645\u064a\u0644.",
            "\u0627\u062e\u062a\u0627\u0631 \u0627\u0644\u0639\u0645\u064a\u0644 \u0648\u0627\u0644\u062a\u0627\u0631\u064a\u062e.",
            "\u0627\u0636\u0641 \u0628\u0646\u0648\u062f \u0627\u0644\u0641\u0627\u062a\u0648\u0631\u0629 \u062b\u0645 \u0627\u062d\u0641\u0638 \u0648\u0631\u0627\u062c\u0639 \u0648\u0631\u062d\u0644.",
        ],
    },
    "/ui/accounting/vendor-bills/new": {
        "title": "\u0641\u0627\u062a\u0648\u0631\u0629 \u0645\u0648\u0631\u062f",
        "steps": [
            "\u0627\u0641\u062a\u062d \u0634\u0627\u0634\u0629 \u0641\u0627\u062a\u0648\u0631\u0629 \u0645\u0648\u0631\u062f.",
            "\u0627\u062e\u062a\u0627\u0631 \u0627\u0644\u0645\u0648\u0631\u062f \u0648\u0627\u0644\u062a\u0627\u0631\u064a\u062e.",
            "\u0627\u0636\u0641 \u0627\u0644\u0628\u0646\u0648\u062f \u0648\u0627\u062d\u0641\u0638 \u0643\u0645\u0633\u0648\u062f\u0629 \u062b\u0645 \u0631\u062d\u0644.",
        ],
    },
    "/ui/accounting/expenses/new": {
        "title": "\u0645\u0635\u0631\u0648\u0641",
        "steps": [
            "\u0627\u0641\u062a\u062d \u0634\u0627\u0634\u0629 \u0645\u0635\u0631\u0648\u0641 \u062c\u062f\u064a\u062f.",
            "\u0627\u062f\u062e\u0644 \u0627\u0644\u062a\u0627\u0631\u064a\u062e \u0648\u0627\u0644\u0628\u064a\u0627\u0646.",
            "\u0627\u0636\u0641 \u0628\u0646\u0648\u062f \u0627\u0644\u0645\u0635\u0631\u0648\u0641 \u062b\u0645 \u0627\u062d\u0641\u0638\u0647.",
            "\u0628\u0639\u062f \u0627\u0644\u062d\u0641\u0638 \u0627\u0636\u063a\u0637 Pay \u0644\u062a\u0646\u0641\u064a\u0630 \u0627\u0644\u0635\u0631\u0641.",
        ],
    },
    "/ui/accounting/fixed-assets/new-asset": {
        "title": "\u0623\u0635\u0644 \u062b\u0627\u0628\u062a",
        "steps": [
            "\u0627\u0641\u062a\u062d \u0634\u0627\u0634\u0629 \u0623\u0635\u0644 \u062b\u0627\u0628\u062a \u062c\u062f\u064a\u062f.",
            "\u0627\u062f\u062e\u0644 \u0627\u0633\u0645 \u0627\u0644\u0623\u0635\u0644 \u0648\u0627\u0644\u0643\u0648\u062f \u0648\u0627\u0644\u062a\u0635\u0646\u064a\u0641.",
            "\u0627\u062f\u062e\u0644 \u062a\u0627\u0631\u064a\u062e \u0627\u0644\u0634\u0631\u0627\u0621 \u0648\u0642\u064a\u0645\u0629 \u0627\u0644\u0627\u0633\u062a\u062d\u0648\u0627\u0630.",
            "\u0627\u062d\u0641\u0638 \u0627\u0644\u0623\u0635\u0644 \u0648\u0628\u0639\u062f \u0627\u0644\u0645\u0631\u0627\u062c\u0639\u0629 \u0631\u062d\u0644 \u0627\u0644\u0627\u0633\u062a\u062d\u0648\u0627\u0630.",
        ],
    },
    "/ui/accounting/journal/new": {
        "title": "\u0642\u064a\u062f \u064a\u0648\u0645\u064a\u0629",
        "steps": [
            "\u0627\u0641\u062a\u062d \u0634\u0627\u0634\u0629 \u0642\u064a\u062f \u062c\u062f\u064a\u062f.",
            "\u0627\u062f\u062e\u0644 \u0627\u0644\u062a\u0627\u0631\u064a\u062e \u0648\u0627\u0644\u0628\u064a\u0627\u0646.",
            "\u0627\u0636\u0641 \u0627\u0644\u0645\u062f\u064a\u0646 \u0648\u0627\u0644\u062f\u0627\u0626\u0646 \u0628\u0646\u0641\u0633 \u0627\u0644\u0642\u064a\u0645\u0629.",
            "\u0627\u062d\u0641\u0638 \u0643\u0645\u0633\u0648\u062f\u0629 \u062b\u0645 \u0631\u0627\u062c\u0639 \u0648\u0631\u062d\u0644.",
        ],
    },
}


def assistant_action_by_href(href: str):
    for action in ASSISTANT_ACTIONS:
        if action["href"] == href:
            return action
    return None


def assistant_action_for_current_page(path: str):
    path = path or ""
    best = None
    for action in ASSISTANT_ACTIONS:
        href = action["href"]
        base = href[:-4] if href.endswith("/new") else href
        if path.startswith(base) and (best is None or len(base) > len(best[0])):
            best = (base, action)
    return best[1] if best else None


def assistant_all_keywords(action):
    return list(action["keywords"]) + EXTRA_ASSISTANT_KEYWORDS.get(action["href"], [])


def assistant_score(query: str, action) -> int:
    text = (query or "").strip().lower()
    if not text:
        return 0
    score = 0
    for keyword in assistant_all_keywords(action):
        key = keyword.lower()
        if key and key in text:
            score += 10 + len(key)
    words = [w for w in text.replace("/", " ").replace("-", " ").split() if len(w) > 2]
    for word in words:
        for keyword in assistant_all_keywords(action):
            if word in keyword.lower():
                score += 2
    return score


def assistant_module_for_href(href: str):
    href = href or ""
    if href.startswith("/ui/accounting"):
        return "accounting"
    if href.startswith("/ui/hr"):
        return "hr"
    if href.startswith("/ui/inventory"):
        return "inventory"
    if href.startswith("/ui/purchasing"):
        return "purchasing"
    if href.startswith("/ui/sales"):
        return "sales"
    if href.startswith("/ui/operations") or href.startswith("/ui/projects"):
        return "operations"
    return ""


def assistant_context_module(path: str):
    return assistant_module_for_href(path or "")


def assistant_rank(query: str, current_path: str = ""):
    if not (query or "").strip():
        return [(0, action) for action in ASSISTANT_ACTIONS[:6]]
    context_module = assistant_context_module(current_path)
    ranked = []
    for action in ASSISTANT_ACTIONS:
        score = assistant_score(query, action)
        if score > 0 and context_module and assistant_module_for_href(action["href"]) == context_module:
            score += 3
        ranked.append((score, action))
    ranked = [item for item in ranked if item[0] > 0]
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked or [(0, action) for action in ASSISTANT_ACTIONS[:6]]


def assistant_confident_action(query: str, current_path: str = ""):
    ranked = assistant_rank(query, current_path)
    if not ranked or ranked[0][0] <= 0:
        return None
    top_score, top_action = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0
    if top_score >= 12 and top_score >= second_score + 6:
        return top_action
    return None


def openai_assistant_action(query: str, lang: str):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or not (query or "").strip():
        return None
    actions = [
        {
            "title": action["title"],
            "ar_title": action["ar_title"],
            "href": action["href"],
            "keywords": assistant_all_keywords(action)[:12],
        }
        for action in ASSISTANT_ACTIONS
    ]
    prompt = {
        "task": "Choose the best ERP navigation action for the user request. Return JSON only.",
        "language": lang,
        "user_request": query,
        "actions": actions,
        "schema": {"href": "one href from actions or empty string", "confidence": "0 to 1"},
    }
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "input": json.dumps(prompt, ensure_ascii=False),
        "text": {"format": {"type": "json_object"}},
    }
    try:
        req = UrlRequest(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
        raw_text = data.get("output_text") or ""
        if not raw_text:
            for item in data.get("output", []):
                for content in item.get("content", []):
                    raw_text += content.get("text", "")
        result = json.loads(raw_text or "{}")
        href = result.get("href", "")
        confidence = float(result.get("confidence", 0) or 0)
        action = assistant_action_by_href(href)
        if action and confidence >= 0.68:
            return action
    except Exception:
        return None
    return None


def assistant_best_action(query: str, lang: str, current_path: str = ""):
    return assistant_confident_action(query, current_path) or openai_assistant_action(query, lang)


def openai_free_reply(message: str, lang: str, current_path: str):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or not message:
        return None
    current_action = assistant_action_for_current_page(current_path)
    current_screen = assistant_action_title(current_action, lang) if current_action else current_path
    prompt = {
        "role": "You are a friendly ERP assistant inside Premium One ERP.",
        "language": "Arabic" if lang == "ar" else "English",
        "current_screen": current_screen,
        "user_message": message,
        "instruction": "Reply naturally like a chat assistant. If the user asks how to use the current screen, explain concise practical steps. Do not invent buttons that are not implied by the screen name.",
    }
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "input": json.dumps(prompt, ensure_ascii=False),
    }
    try:
        req = UrlRequest(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        raw_text = data.get("output_text") or ""
        if not raw_text:
            for item in data.get("output", []):
                for content in item.get("content", []):
                    raw_text += content.get("text", "")
        return raw_text.strip() or None
    except Exception:
        return None


def assistant_matches(query: str, current_path: str = ""):
    return [action for _, action in assistant_rank(query, current_path)]


def assistant_is_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text or "")


def assistant_is_greeting(text: str) -> bool:
    value = (text or "").strip().lower()
    greetings = [
        "\u0645\u0633\u0627\u0621", "\u0635\u0628\u0627\u062d", "\u0627\u0644\u0633\u0644\u0627\u0645",
        "\u0627\u0647\u0644\u0627", "\u0623\u0647\u0644\u0627", "\u0647\u0627\u064a", "\u0627\u0632\u064a\u0643",
        "hello", "hi", "hey", "good morning", "good evening",
    ]
    return bool(value) and any(word in value for word in greetings) and len(value) <= 40


def assistant_greeting_reply(lang: str):
    if lang == "ar":
        return "\u0623\u0647\u0644\u0627 \u0628\u064a\u0643. \u0642\u0648\u0644\u064a \u0639\u0627\u0648\u0632 \u062a\u0639\u0645\u0644 \u0625\u064a\u0647\u060c \u0648\u0644\u0648 \u0645\u062d\u062a\u0627\u062c \u0623\u0646\u0641\u0630 \u062d\u0627\u062c\u0629 \u0647\u0623\u0641\u062a\u062d\u0647\u0627\u0644\u0643 \u0639\u0644\u0649 \u0637\u0648\u0644."
    return "Good evening. Tell me what you want to do, and if it needs a screen I will open the right one for you."


def assistant_action_steps(action, lang: str):
    if lang == "ar" and action["href"] in AR_ASSISTANT_TEXT:
        return AR_ASSISTANT_TEXT[action["href"]]["steps"]
    if lang == "ar":
        return [fix_mojibake(step) for step in action.get("ar_steps", [])]
    return action["steps"]


def assistant_action_title(action, lang: str):
    if lang == "ar" and action["href"] in AR_ASSISTANT_TEXT:
        return AR_ASSISTANT_TEXT[action["href"]]["title"]
    if lang == "ar":
        return fix_mojibake(action.get("ar_title", "")) or action["title"]
    return action["title"]


def assistant_card(action, lang: str, query: str = ""):
    title = assistant_action_title(action, lang)
    steps = assistant_action_steps(action, lang)
    step_html = "".join(f"<li>{escape(step)}</li>" for step in steps)
    go_href = f"/ui/assistant/go?q={quote_plus(query)}" if query else action["href"]
    return f"""
    <div class="assistant-result">
        <div>
            <h3>{escape(title)}</h3>
            <ol>{step_html}</ol>
            <div class="assistant-actions">
                <a class="btn green" href="{go_href}">{t(lang, "Go", "ظ†ظپط°")}</a>
            </div>
        </div>
        <a class="btn blue" href="{action['href']}">{t(lang, "Open", "ط§ظپطھط­")}</a>
    </div>
    """


@app.get("/ui/assistant/go")
def assistant_go(request: Request):
    lang = get_lang(request)
    query = request.query_params.get("q", "")
    action = assistant_best_action(query, lang, request.query_params.get("current_path", ""))
    if action:
        return RedirectResponse(action["href"], status_code=302)
    return RedirectResponse(f"/ui/assistant?q={quote_plus(query)}", status_code=302)


@app.post("/ui/assistant/chat")
async def assistant_chat(request: Request):
    payload = await request.json()
    message = str(payload.get("message") or "").strip()
    current_path = str(payload.get("current_path") or "")
    lang = str(payload.get("lang") or get_lang(request))
    if assistant_is_greeting(message):
        return JSONResponse({
            "reply": assistant_greeting_reply(lang),
            "redirect": False,
            "action": None,
        })
    action = assistant_best_action(message, lang, current_path)
    if action:
        title = assistant_action_title(action, lang)
        reply = f"\u062a\u0645\u0627\u0645\u060c \u0627\u0641\u062a\u062d {title} \u0648\u0627\u0645\u0634\u064a \u0628\u0627\u0644\u062a\u0631\u062a\u064a\u0628 \u062f\u0647:" if lang == "ar" else f"Open {title} and follow these steps:"
        return JSONResponse({
            "reply": reply,
            "redirect": False,
            "action": {"href": action["href"], "label": title, "steps": assistant_action_steps(action, lang)},
        })
    ranked_matches = assistant_rank(message, current_path)
    matches = [item[1] for item in ranked_matches if item[0] > 0][:3]
    if matches:
        title = assistant_action_title(matches[0], lang)
        reply = "\u0645\u062d\u062a\u0627\u062c \u0623\u062d\u062f\u062f \u0642\u0635\u062f\u0643 \u0623\u0643\u062a\u0631. \u0623\u0642\u0631\u0628 \u0627\u062e\u062a\u064a\u0627\u0631 \u0639\u0646\u062f\u064a:" if lang == "ar" else "I need one more detail. Closest match:"
        return JSONResponse({
            "reply": f"{reply} {title}",
            "redirect": False,
            "action": {"href": matches[0]["href"], "label": title, "steps": assistant_action_steps(matches[0], lang)},
        })
    free_reply = openai_free_reply(message, lang, current_path)
    return JSONResponse({
        "reply": free_reply or ("\u0645\u0645\u0643\u0646 \u062a\u0648\u0636\u062d\u0647\u0627 \u0623\u0643\u062a\u0631\u061f" if lang == "ar" else "Can you clarify that a bit more?"),
        "redirect": False,
        "action": None,
    })


@app.get("/ui/assistant", response_class=HTMLResponse)
def assistant_page(request: Request):
    lang = get_lang(request)
    query = request.query_params.get("q", "")
    if request.query_params.get("direct") == "1":
        action = assistant_best_action(query, lang, request.query_params.get("current_path", ""))
        if action:
            return RedirectResponse(action["href"], status_code=302)
    matches = assistant_matches(query, request.query_params.get("current_path", ""))[:5]
    quick = [
        ("Create invoice", "ط§ط¹ظ…ظ„ ظپط§طھظˆط±ط©"),
        ("Pay expense", "ط§طµط±ظپ ظ…طµط±ظˆظپ"),
        ("Run payroll", "ط§ط¹ظ…ظ„ ظ…ط±طھط¨ط§طھ"),
        ("Employee advance", "ط³ظ„ظپط© ظ…ظˆط¸ظپ"),
        ("Journal entry", "ظ‚ظٹط¯ ظٹظˆظ…ظٹط©"),
        ("Reports", "طھظ‚ط§ط±ظٹط±"),
    ]
    quick_html = "".join(
        f'<a class="assistant-chip" href="/ui/assistant/go?q={quote_plus(ar if lang == "ar" else en)}">{escape(ar if lang == "ar" else en)}</a>'
        for en, ar in quick
    )
    results_html = "".join(assistant_card(action, lang, query) for action in matches)
    content = f"""
    <style>
        .assistant-shell {{
            display: grid;
            gap: 16px;
        }}
        .assistant-search {{
            display: grid;
            grid-template-columns: 1fr auto auto;
            gap: 12px;
            align-items: end;
        }}
        .assistant-chips {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .assistant-chip {{
            display: inline-flex;
            align-items: center;
            padding: 9px 12px;
            border-radius: 10px;
            background: #eef2f8;
            color: #183153;
            font-weight: 800;
            border: 1px solid #dfe6f1;
        }}
        .assistant-result {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 14px;
            padding: 16px;
            border: 1px solid #dfe6f1;
            border-radius: 12px;
            background: #fff;
            margin-top: 12px;
        }}
        .assistant-result h3 {{
            margin: 0 0 8px 0;
            color: #13315c;
        }}
        .assistant-result ol {{
            margin: 0;
            padding-inline-start: 22px;
            color: #244267;
            line-height: 1.7;
        }}
        .assistant-actions {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            margin-top: 10px;
        }}
        @media (max-width: 720px) {{
            .assistant-search,
            .assistant-result {{
                grid-template-columns: 1fr;
                flex-direction: column;
            }}
        }}
    </style>
    <div class="card assistant-shell">
        <form class="assistant-search" method="get" action="/ui/assistant">
            <div>
                <label>{t(lang, "Ask Assistant", "ط§ط³ط£ظ„ ط§ظ„ظ…ط³ط§ط¹ط¯")}</label>
                <input name="q" value="{escape(query)}" placeholder="{t(lang, "What do you want to do?", "ط¹ط§ظˆط² طھط¹ظ…ظ„ ط§ظٹظ‡طں")}">
            </div>
            <button class="btn gray" type="submit">{t(lang, "Search", "ط¨ط­ط«")}</button>
            <button class="btn green" type="submit" name="direct" value="1">{t(lang, "Go", "ظ†ظپط°")}</button>
        </form>
        <div class="assistant-chips">{quick_html}</div>
    </div>
    <div class="card">
        {results_html}
    </div>
    """
    return HTMLResponse(render_page(t(lang, "Assistant", "ط§ظ„ظ…ط³ط§ط¹ط¯"), content, lang, current_path="/ui/assistant"))


@app.get("/")
def root(request: Request):
    return RedirectResponse(default_home_path_for_user(request), status_code=302)


@app.get("/ui")
def ui_root(request: Request):
    return RedirectResponse(default_home_path_for_user(request), status_code=302)


@app.get("/ui/settings", response_class=HTMLResponse)
def settings_root(request: Request):
    lang = get_lang(request)
    settings_cards = [
        (
            t(lang, "Company Profile", "ط¨ظٹط§ظ†ط§طھ ط§ظ„ط´ط±ظƒط©"),
            "/ui/accounting/setup",
            "/static/icons/system-setup.svg",
            t(lang, "Company data, logo, base currency, and feature activation.", "ط¨ظٹط§ظ†ط§طھ ط§ظ„ط´ط±ظƒط© ظˆط§ظ„ط´ط¹ط§ط± ظˆط§ظ„ط¹ظ…ظ„ط© ط§ظ„ط£ط³ط§ط³ظٹط© ظˆطھظپط¹ظٹظ„ ط§ظ„ط®طµط§ط¦طµ."),
        ),
        (
            t(lang, "Accounting Configuration", "ط§ظ„ط¥ط¹ط¯ط§ط¯ط§طھ ط§ظ„ظ…ط­ط§ط³ط¨ظٹط©"),
            "/ui/accounting/config",
            "/static/icons/configuration.svg",
            t(lang, "Default accounts, prefixes, and accounting defaults.", "ط§ظ„ط­ط³ط§ط¨ط§طھ ط§ظ„ط§ظپطھط±ط§ط¶ظٹط© ظˆط§ظ„ط¨ط§ط¯ط¦ط§طھ ظˆط§ظ„ط¥ط¹ط¯ط§ط¯ط§طھ ط§ظ„ظ…ط­ط§ط³ط¨ظٹط© ط§ظ„ط£ط³ط§ط³ظٹط©."),
        ),
        (
            t(lang, "Users & Permissions", "ط§ظ„ظ…ط³طھط®ط¯ظ…ظˆظ† ظˆط§ظ„طµظ„ط§ط­ظٹط§طھ"),
            "/ui/system/users",
            "/static/icons/employees.svg",
            t(lang, "Create users, assign roles, and control access levels.", "ط¥ظ†ط´ط§ط، ط§ظ„ظ…ط³طھط®ط¯ظ…ظٹظ† ظˆطھط­ط¯ظٹط¯ ط§ظ„ط£ط¯ظˆط§ط± ظˆط§ظ„طھط­ظƒظ… ظپظٹ ظ…ط³طھظˆظٹط§طھ ط§ظ„طµظ„ط§ط­ظٹط©."),
        ),
    ]

    content = f"""
    <div class="card">
        <h2>{t(lang, "Settings", "ط§ظ„ط¥ط¹ط¯ط§ط¯ط§طھ")}</h2>
        <p style="color:#6d809c; margin-top:8px;">
            {t(lang, "General system settings in one place without mixing them with daily transactions.", "ظƒظ„ ط§ظ„ط¥ط¹ط¯ط§ط¯ط§طھ ط§ظ„ط¹ط§ظ…ط© ظپظٹ ظ…ظƒط§ظ† ظˆط§ط­ط¯ ط¨ط¯ظˆظ† ط®ظ„ط·ظ‡ط§ ظ…ط¹ ط§ظ„ط­ط±ظƒط§طھ ط§ظ„ظٹظˆظ…ظٹط©.")}
        </p>
    </div>
    {card_section(t(lang, "System Settings", "ط¥ط¹ط¯ط§ط¯ط§طھ ط§ظ„ظ†ط¸ط§ظ…"), settings_cards)}
    """
    return HTMLResponse(render_page(t(lang, "Settings", "ط§ظ„ط¥ط¹ط¯ط§ط¯ط§طھ"), content, lang, current_path="/ui/settings"))


@app.get("/ui/accounting", response_class=HTMLResponse)
def accounting_root(request: Request):
    lang = get_lang(request)
    main_cards = [
        (t(lang, "System Setup", "طھظ‡ظٹط¦ط© ط§ظ„ظ†ط¸ط§ظ…"), "/ui/accounting/setup", "/static/icons/system-setup.svg", t(lang, "Company and system initialization", "طھظ‡ظٹط¦ط© ط¨ظٹط§ظ†ط§طھ ط§ظ„ط´ط±ظƒط© ظˆط§ظ„ظ†ط¸ط§ظ…")),
        (t(lang, "Configuration", "ط§ظ„ط¥ط¹ط¯ط§ط¯ط§طھ"), "/ui/accounting/config", "/static/icons/configuration.svg", t(lang, "Default accounts and prefixes", "ط§ظ„ط­ط³ط§ط¨ط§طھ ط§ظ„ط§ظپطھط±ط§ط¶ظٹط© ظˆط§ظ„ط¨ط¯ط§ظٹط§طھ")),
        (t(lang, "Chart of Accounts", "ط¯ظ„ظٹظ„ ط§ظ„ط­ط³ط§ط¨ط§طھ"), "/ui/accounting/accounts", "/static/icons/chart-accounts.svg", t(lang, "Manage account structure", "ط¥ط¯ط§ط±ط© ظ‡ظٹظƒظ„ ط§ظ„ط­ط³ط§ط¨ط§طھ")),
        (t(lang, "Cost Centers", "ظ…ط±ط§ظƒط² ط§ظ„طھظƒظ„ظپط©"), "/ui/accounting/cost-centers", "/static/icons/cost-centers.svg", t(lang, "Department and activity allocation", "طھظˆط²ظٹط¹ ط§ظ„ط£ظ‚ط³ط§ظ… ظˆط§ظ„ط£ظ†ط´ط·ط©")),
        (t(lang, "Customers", "ط§ظ„ط¹ظ…ظ„ط§ط،"), "/ui/accounting/customers-hub", "/static/icons/customers.svg", t(lang, "Customer master and transactions", "ط¨ظٹط§ظ†ط§طھ ط§ظ„ط¹ظ…ظ„ط§ط، ظˆط­ط±ظƒط§طھظ‡ظ…")),
        (t(lang, "Vendors", "ط§ظ„ظ…ظˆط±ط¯ظˆظ†"), "/ui/accounting/vendors-hub", "/static/icons/vendors.svg", t(lang, "Vendor master and transactions", "ط¨ظٹط§ظ†ط§طھ ط§ظ„ظ…ظˆط±ط¯ظٹظ† ظˆط­ط±ظƒط§طھظ‡ظ…")),
        (t(lang, "Journal", "ط§ظ„ظٹظˆظ…ظٹط©"), "/ui/accounting/journal", "/static/icons/journal.svg", t(lang, "Journal entries and review", "ظ‚ظٹظˆط¯ ط§ظ„ظٹظˆظ…ظٹط© ظˆط§ظ„ظ…ط±ط§ط¬ط¹ط©")),
        (t(lang, "Expenses", "ط§ظ„ظ…طµط±ظˆظپط§طھ"), "/ui/accounting/expenses", "/static/icons/expenses.svg", t(lang, "Operational expenses and posting", "ط§ظ„ظ…طµط±ظˆظپط§طھ ط§ظ„طھط´ط؛ظٹظ„ظٹط© ظˆطھط±ط­ظٹظ„ظ‡ط§")),
        (t(lang, "Cash Receipts", "ط³ظ†ط¯ط§طھ ط§ظ„ظ‚ط¨ط¶"), "/ui/accounting/cash-receipts", "/static/icons/customer-payments.svg", t(lang, "Cash receipt vouchers with posting and print.", "ط³ظ†ط¯ط§طھ ظ‚ط¨ط¶ ظ†ظ‚ط¯ظٹ ظ…ط¹ ط§ظ„طھط±ط­ظٹظ„ ظˆط§ظ„ط·ط¨ط§ط¹ط©.")),
        (t(lang, "Cash Payments", "ط³ظ†ط¯ط§طھ ط§ظ„طµط±ظپ"), "/ui/accounting/cash-payments", "/static/icons/vendor-payments.svg", t(lang, "Cash payment vouchers with posting and print.", "ط³ظ†ط¯ط§طھ طµط±ظپ ظ†ظ‚ط¯ظٹ ظ…ط¹ ط§ظ„طھط±ط­ظٹظ„ ظˆط§ظ„ط·ط¨ط§ط¹ط©.")),
        (t(lang, "Employee Advances", "ط³ظ„ظپ ط§ظ„ظ…ظˆط¸ظپظٹظ†"), "/ui/accounting/employee-advances", "/static/icons/reports.svg", t(lang, "Disbursement, balances, and payroll deduction tracking", "طµط±ظپ ط§ظ„ط³ظ„ظپطŒ ط§ظ„ط£ط±طµط¯ط©طŒ ظˆظ…طھط§ط¨ط¹ط© ط®طµظ… ط§ظ„ظ…ط±طھط¨ط§طھ")),
        (t(lang, "Petty Cash", "ط§ظ„ط¹ظ‡ط¯ط© ط§ظ„ظ†ظ‚ط¯ظٹط©"), "/ui/accounting/petty-cash", "/static/icons/petty-cash.svg", t(lang, "Custody and returns", "ط§ظ„ط¹ظ‡ط¯ط© ظˆط§ظ„ط±ط¯ظˆط¯")),
        (t(lang, "Fixed Assets", "ط§ظ„ط£طµظˆظ„ ط§ظ„ط«ط§ط¨طھط©"), "/ui/accounting/fixed-assets", "/static/icons/fixed-assets.svg", t(lang, "Assets, depreciation, and disposal", "ط§ظ„ط£طµظˆظ„ ظˆط§ظ„ط¥ظ‡ظ„ط§ظƒ ظˆط§ظ„ط§ط³طھط¨ط¹ط§ط¯")),
        (t(lang, "Reports", "ط§ظ„طھظ‚ط§ط±ظٹط±"), "/ui/accounting/reports", "/static/icons/reports.svg", t(lang, "Financial and operational reports", "ط§ظ„طھظ‚ط§ط±ظٹط± ط§ظ„ظ…ط§ظ„ظٹط© ظˆط§ظ„طھط´ط؛ظٹظ„ظٹط©")),
    ]
    content = card_section(t(lang, "Accounting", "ط§ظ„ط­ط³ط§ط¨ط§طھ"), main_cards)
    return HTMLResponse(render_page(t(lang, "Accounting", "ط§ظ„ط­ط³ط§ط¨ط§طھ"), content, lang, current_path="/ui/accounting"))


@app.get("/ui/accounting/customers-hub", response_class=HTMLResponse)
def customers_hub():
    cards = [
        ("Customers", "/ui/accounting/customers", "/static/icons/customers.svg", "Customer master data"),
        ("Customer Invoices", "/ui/accounting/customer-invoices", "/static/icons/customer-invoices.svg", "Sales invoices"),
        ("Customer Statement", "/ui/accounting/customer-statement", "/static/icons/customer-statement.svg", "Account statement and balances"),
    ]
    content = card_section("Customers", cards)
    return HTMLResponse(render_page("Customers", content, current_path="/ui/accounting/customers-hub"))


@app.get("/ui/accounting/vendors-hub", response_class=HTMLResponse)
def vendors_hub():
    cards = [
        ("Vendors", "/ui/accounting/vendors", "/static/icons/vendors.svg", "Vendor master data"),
        ("Vendor Bills", "/ui/accounting/vendor-bills", "/static/icons/vendor-bills.svg", "Purchase bills"),
        ("Vendor Statement", "/ui/accounting/vendor-statement", "/static/icons/vendor-statement.svg", "Account statement and balances"),
    ]
    content = card_section("Vendors", cards)
    return HTMLResponse(render_page("Vendors", content, current_path="/ui/accounting/vendors-hub"))


@app.get("/ui/hr", response_class=HTMLResponse)
def hr_root(request: Request):
    lang = get_lang(request)
    hr_cards = [
        (t(lang, "Employees", "ط§ظ„ظ…ظˆط¸ظپظˆظ†"), "/ui/hr/employees", "/static/icons/employees.svg", t(lang, "Employee master data and import template", "ط¨ظٹط§ظ†ط§طھ ط§ظ„ظ…ظˆط¸ظپظٹظ† ظˆظ‚ط§ظ„ط¨ ط§ظ„ط§ط³طھظٹط±ط§ط¯")),
        (t(lang, "Employee Categories", "ظپط¦ط§طھ ط§ظ„ظ…ظˆط¸ظپظٹظ†"), "/ui/hr/categories", "/static/icons/employees.svg", t(lang, "Attendance groups, roles, shifts, and grace rules", "ظ…ط¬ظ…ظˆط¹ط§طھ ط§ظ„ط­ط¶ظˆط± ظˆط§ظ„ط£ط¯ظˆط§ط± ظˆط§ظ„ظˆط±ط¯ظٹط§طھ ظˆظ‚ظˆط§ط¹ط¯ ط§ظ„ط³ظ…ط§ط­")),
        (t(lang, "Attendance", "ط§ظ„ط­ط¶ظˆط±"), "/ui/hr/attendance", "/static/icons/reports.svg", t(lang, "Biometric attendance import and daily logs", "ط§ط³طھظٹط±ط§ط¯ ط§ظ„ط¨طµظ…ط© ظˆط³ط¬ظ„ط§طھ ط§ظ„ط­ط¶ظˆط± ط§ظ„ظٹظˆظ…ظٹط©")),
        (t(lang, "Payroll", "ط§ظ„ظ…ط±طھط¨ط§طھ"), "/ui/hr/payroll", "/static/icons/reports.svg", t(lang, "Monthly payroll generation, review, and posting", "ط¥ط¹ط¯ط§ط¯ ظ…ط³ظٹط± ط§ظ„ظ…ط±طھط¨ط§طھ ط§ظ„ط´ظ‡ط±ظٹ ظˆظ…ط±ط§ط¬ط¹طھظ‡ ظˆطھط±ط­ظٹظ„ظ‡")),
        ("Employee Rewards", "/ui/hr/employee-rewards", "/static/icons/reports.svg", "Employee rewards with required attachments"),
        ("Employee Penalties", "/ui/hr/employee-penalties", "/static/icons/reports.svg", "Employee penalties with required attachments"),
    ]
    content = card_section(t(lang, "HR", "ط§ظ„ظ…ظˆط§ط±ط¯ ط§ظ„ط¨ط´ط±ظٹط©"), hr_cards)
    return HTMLResponse(render_page(t(lang, "HR", "ط§ظ„ظ…ظˆط§ط±ط¯ ط§ظ„ط¨ط´ط±ظٹط©"), content, lang, current_path="/ui/hr"))


@app.get("/ui/inventory", response_class=HTMLResponse)
def inventory_root():
    inventory_cards = [
        ("Items", "/ui/inventory/items", "/static/icons/items.svg", "Item master and stock products"),
        ("Warehouses", "/ui/inventory/warehouses", "/static/icons/warehouses.svg", "Warehouse structure and status"),
        ("Goods Receipts", "/ui/inventory/goods-receipts", "/static/icons/goods-receipts.svg", "Receiving and stock intake"),
        ("Stock Balance", "/ui/inventory/stock-balance", "/static/icons/stock-balance.svg", "Current stock by item and warehouse"),
        ("Stock Ledger", "/ui/inventory/stock-ledger", "/static/icons/stock-ledger.svg", "Detailed inventory movement history"),
    ]
    content = card_section("Inventory", inventory_cards)
    return HTMLResponse(render_page("Inventory", content, current_path="/ui/inventory"))


@app.get("/ui/purchasing", response_class=HTMLResponse)
def purchasing_root():
    purchasing_cards = [
        ("Purchase Orders", "/ui/purchasing/purchase-orders", "/static/icons/purchase-orders.svg", "Purchase orders and follow-up"),
        ("PO Variances", "/ui/purchasing/po-variances", "/static/icons/po-variances.svg", "Review quantity and price differences"),
    ]
    content = card_section("Purchasing", purchasing_cards)
    return HTMLResponse(render_page("Purchasing", content, current_path="/ui/purchasing"))


from modules.accounting.accounts import router as accounts_router
from modules.accounting.config import router as config_router
from modules.accounting.customer_invoices import router as customer_invoices_router
from modules.accounting.journal import router as journal_router
from modules.accounting.partners import router as partners_router
from modules.accounting.setup import router as setup_router
from modules.accounting.vendor_bills import router as vendor_bills_router

try:
    from modules.accounting.cash_vouchers import router as cash_vouchers_router
except Exception:
    cash_vouchers_router = None

try:
    from modules.accounting.customer_payments import router as customer_payments_router
except Exception:
    customer_payments_router = None

try:
    from modules.accounting.customer_statement import router as customer_statement_router
except Exception:
    customer_statement_router = None

try:
    from modules.accounting.fixed_assets import router as fixed_assets_router
except Exception:
    fixed_assets_router = None

try:
    from modules.accounting.expenses import router as expenses_router
except Exception:
    expenses_router = None

try:
    from modules.accounting.fixed_asset_statement import router as fixed_asset_statement_router
except Exception:
    fixed_asset_statement_router = None

try:
    from modules.accounting.general_ledger import router as general_ledger_router
except Exception:
    general_ledger_router = None

try:
    from modules.accounting.trial_balance import router as trial_balance_router
except Exception:
    trial_balance_router = None

try:
    from modules.accounting.profit_loss import router as profit_loss_router
except Exception:
    profit_loss_router = None

try:
    from modules.accounting.balance_sheet import router as balance_sheet_router
except Exception:
    balance_sheet_router = None

try:
    from modules.accounting.partner_ledger import router as partner_ledger_router
except Exception:
    partner_ledger_router = None

try:
    from modules.accounting.aging import router as aging_router
except Exception:
    aging_router = None

try:
    from modules.accounting.monthly_dues import router as monthly_dues_router
except Exception:
    monthly_dues_router = None

try:
    from modules.accounting.cost_centers import router as cost_centers_router
except Exception:
    cost_centers_router = None

try:
    from modules.accounting.reports import router as reports_router
except Exception:
    reports_router = None

try:
    from modules.accounting.petty_cash import router as petty_cash_router
except Exception:
    petty_cash_router = None

try:
    from modules.accounting.petty_cash_list import router as petty_cash_list_router
except Exception:
    petty_cash_list_router = None

try:
    from modules.accounting.petty_cash_statement import router as petty_cash_statement_router
except Exception:
    petty_cash_statement_router = None

try:
    from modules.accounting.vendor_payments import router as vendor_payments_router
except Exception:
    vendor_payments_router = None

try:
    from modules.accounting.vendor_statement import router as vendor_statement_router
except Exception:
    vendor_statement_router = None

try:
    from modules.accounting.employee_advances import router as employee_advances_router
except Exception:
    employee_advances_router = None

try:
    from modules.hr.employees import router as employees_router
except Exception:
    employees_router = None

try:
    from modules.hr.categories import router as categories_router
except Exception:
    categories_router = None

try:
    from modules.hr.payroll import router as payroll_router
except Exception:
    payroll_router = None

try:
    from modules.hr.advances import router as advances_router
except Exception:
    advances_router = None

try:
    from modules.hr.attendance import router as attendance_router
except Exception:
    attendance_router = None

try:
    from modules.inventory.goods_receipts import router as goods_receipts_router
except Exception:
    goods_receipts_router = None

try:
    from modules.inventory.items import router as inventory_items_router
except Exception:
    inventory_items_router = None

try:
    from modules.inventory.warehouses import router as inventory_warehouses_router
except Exception:
    inventory_warehouses_router = None

try:
    from modules.inventory.stock import router as inventory_stock_router
except Exception:
    inventory_stock_router = None

try:
    from modules.sales.sales import router as sales_router
except Exception:
    sales_router = None

try:
    from modules.operations.operations import router as operations_router
except Exception:
    operations_router = None

try:
    from modules.purchasing.po_variances import router as po_variances_router
except Exception:
    po_variances_router = None

try:
    from modules.purchasing.purchase_orders import router as purchase_orders_router
except Exception:
    purchase_orders_router = None

try:
    from modules.system.users import router as system_users_router
except Exception:
    system_users_router = None


def include_optional_router(router):
    if router is not None:
        app.include_router(router)


app.include_router(accounts_router)
app.include_router(setup_router)
app.include_router(config_router)
app.include_router(partners_router)
app.include_router(customer_invoices_router)
app.include_router(vendor_bills_router)
app.include_router(journal_router)

include_optional_router(cash_vouchers_router)
include_optional_router(customer_payments_router)
include_optional_router(customer_statement_router)
include_optional_router(expenses_router)
include_optional_router(fixed_asset_statement_router)
include_optional_router(general_ledger_router)
include_optional_router(trial_balance_router)
include_optional_router(profit_loss_router)
include_optional_router(balance_sheet_router)
include_optional_router(partner_ledger_router)
include_optional_router(aging_router)
include_optional_router(monthly_dues_router)
include_optional_router(fixed_assets_router)
include_optional_router(cost_centers_router)
include_optional_router(reports_router)
include_optional_router(petty_cash_router)
include_optional_router(petty_cash_list_router)
include_optional_router(petty_cash_statement_router)
include_optional_router(vendor_payments_router)
include_optional_router(vendor_statement_router)
include_optional_router(employee_advances_router)
include_optional_router(employees_router)
include_optional_router(categories_router)
include_optional_router(attendance_router)
include_optional_router(advances_router)
include_optional_router(payroll_router)
include_optional_router(goods_receipts_router)
include_optional_router(inventory_items_router)
include_optional_router(inventory_warehouses_router)
include_optional_router(inventory_stock_router)
include_optional_router(sales_router)
include_optional_router(operations_router)
include_optional_router(po_variances_router)
include_optional_router(purchase_orders_router)
include_optional_router(system_users_router)
