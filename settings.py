from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page

router = APIRouter()


@router.get("/ui/system/setup/{company_id}", response_class=HTMLResponse)
def setup_page(request: Request, company_id: int):
    conn = get_conn()
    modules = conn.execute("SELECT * FROM modules").fetchall()
    conn.close()

    modules_html = ""
    for m in modules:
        modules_html += f"""
        <label>
            <input type="checkbox" name="modules" value="{m['name']}">
            {m['name']}
        </label><br>
        """

    content = f"""
    <h2>Setup Company</h2>

    <form method="post">
        <h3>Select Modules</h3>
        {modules_html}

        <br>
        <button type="submit">Continue</button>
    </form>
    """

    return HTMLResponse(render_page(content))


@router.post("/ui/system/setup/{company_id}")
def setup_modules(
    request: Request,
    company_id: int,
    modules: list[str] = Form(...)
):
    conn = get_conn()
    cur = conn.cursor()

    for m in modules:
        cur.execute("""
            INSERT INTO company_modules (company_id, module_name, is_active)
            VALUES (?, ?, 1)
        """, (company_id, m))

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/ui/system/setup/{company_id}/accounts",
        status_code=302
    )


# 🔥 Step: Generate Accounts
@router.get("/ui/system/setup/{company_id}/accounts", response_class=HTMLResponse)
def setup_accounts_page(request: Request, company_id: int):
    content = """
    <h2>Generate Chart of Accounts</h2>

    <form method="post">
        <button type="submit">Generate Default Accounts</button>
    </form>
    """

    return HTMLResponse(render_page(content))


@router.post("/ui/system/setup/{company_id}/accounts")
def generate_accounts(request: Request, company_id: int):
    conn = get_conn()
    cur = conn.cursor()

    accounts = [
        ("1000", "Assets", "asset", None, 1),
        ("1100", "Cash", "asset", 1, 0),
        ("1200", "Bank", "asset", 1, 0),

        ("2000", "Liabilities", "liability", None, 1),
        ("2100", "Vendors", "liability", 4, 0),

        ("4000", "Revenue", "revenue", None, 1),
        ("4100", "Sales", "revenue", 6, 0),
    ]

    for code, name, typ, parent, is_group in accounts:
        cur.execute("""
            INSERT INTO accounts (company_id, code, name, type, parent_id, is_group)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (company_id, code, name, typ, parent, is_group))

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/ui/system/setup/{company_id}/config",
        status_code=302
    )


# 🔥 Config Step
@router.get("/ui/system/setup/{company_id}/config", response_class=HTMLResponse)
def config_page(request: Request, company_id: int):
    conn = get_conn()
    accounts = conn.execute("""
        SELECT id, name FROM accounts WHERE company_id=?
    """, (company_id,)).fetchall()
    conn.close()

    options = ""
    for a in accounts:
        options += f'<option value="{a["id"]}">{a["name"]}</option>'

    content = f"""
    <h2>Accounting Configuration</h2>

    <form method="post">
        Revenue:
        <select name="revenue">{options}</select><br><br>

        Customer:
        <select name="customer">{options}</select><br><br>

        Vendor:
        <select name="vendor">{options}</select><br><br>

        <button type="submit">Finish Setup</button>
    </form>
    """

    return HTMLResponse(render_page(content))


@router.post("/ui/system/setup/{company_id}/config")
def save_config(
    request: Request,
    company_id: int,
    revenue: int = Form(...),
    customer: int = Form(...),
    vendor: int = Form(...)
):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO accounting_settings
        (company_id, revenue_account_id, customer_account_id, vendor_account_id)
        VALUES (?, ?, ?, ?)
    """, (company_id, revenue, customer, vendor))

    cur.execute("""
        UPDATE companies SET setup_completed = 1 WHERE id=?
    """, (company_id,))

    conn.commit()
    conn.close()

    return RedirectResponse("/ui/accounting", status_code=302)