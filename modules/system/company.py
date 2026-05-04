from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page

router = APIRouter()


@router.get("/ui/system/company/new", response_class=HTMLResponse)
def new_company_form(request: Request):
    content = """
    <h2>Create Company</h2>

    <form method="post">
        <input name="name" placeholder="Company Name" required><br><br>
        <input name="currency" placeholder="Currency"><br><br>
        <input name="fiscal_year" placeholder="Fiscal Year"><br><br>

        <button type="submit">Create</button>
    </form>
    """

    return HTMLResponse(render_page(content))


@router.post("/ui/system/company/new")
def create_company(
    request: Request,
    name: str = Form(...),
    currency: str = Form("EGP"),
    fiscal_year: str = Form("2025")
):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO companies (name, currency, fiscal_year)
        VALUES (?, ?, ?)
    """, (name, currency, fiscal_year))

    conn.commit()

    company_id = cur.lastrowid
    conn.close()

    return RedirectResponse(
        url=f"/ui/system/setup/{company_id}",
        status_code=302
    )