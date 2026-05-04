from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from layout import render_page

router = APIRouter()

@router.get("/ui/accounting/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):

    content = """
    <h2>Accounting Dashboard</h2>

    <div style="display:flex;gap:20px;flex-wrap:wrap;margin-top:20px">

        <a href="/ui/accounting/accounts" class="card">Accounts</a>
        <a href="/ui/accounting/journal" class="card">Journals</a>
        <a href="/ui/accounting/reports" class="card">Reports</a>
        <a href="/ui/accounting/expenses" class="card">Expenses</a>
        <a href="/ui/accounting/fixed-assets" class="card">Fixed Assets</a>
        ("Vendor Bills", "/ui/accounting/vendor-bills"),

    </div>

    <style>
    .card{
        display:block;
        width:180px;
        padding:20px;
        background:#f4f6f9;
        border-radius:12px;
        text-decoration:none;
        color:#333;
        font-weight:bold;
        text-align:center;
        transition:0.2s;
    }

    .card:hover{
        background:#1565c0;
        color:white;
    }
    </style>
    """

    return render_page(request, content, "Accounting Dashboard")