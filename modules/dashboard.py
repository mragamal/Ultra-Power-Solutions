from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from auth import current_user
from layout import render_page
from db import get_conn

router = APIRouter()


@router.get("/ui/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    user = current_user(request)

    if not user:
        return RedirectResponse(url="/login", status_code=302)

    conn = get_conn()

    try:
        accounts = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        vendors = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
        users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()

    content = f"""
    <div style="display:grid;grid-template-columns:repeat(3,minmax(220px,1fr));gap:20px;">
        <div style="background:white;padding:20px;border-radius:14px;box-shadow:0 4px 14px rgba(0,0,0,0.06);">
            <div style="color:#666;margin-bottom:8px;">Accounts</div>
            <div style="font-size:30px;font-weight:bold;color:#1d4ed8;">{accounts}</div>
        </div>

        <div style="background:white;padding:20px;border-radius:14px;box-shadow:0 4px 14px rgba(0,0,0,0.06);">
            <div style="color:#666;margin-bottom:8px;">Vendors</div>
            <div style="font-size:30px;font-weight:bold;color:#1d4ed8;">{vendors}</div>
        </div>

        <div style="background:white;padding:20px;border-radius:14px;box-shadow:0 4px 14px rgba(0,0,0,0.06);">
            <div style="color:#666;margin-bottom:8px;">Users</div>
            <div style="font-size:30px;font-weight:bold;color:#1d4ed8;">{users_count}</div>
        </div>
    </div>

    <div style="margin-top:24px;background:white;padding:24px;border-radius:14px;box-shadow:0 4px 14px rgba(0,0,0,0.06);">
        <h2 style="margin-top:0;">Welcome to Premium One ERP</h2>
        <p style="margin-bottom:0;color:#555;">
            You are logged in successfully as <b>{user["username"]}</b>.
        </p>
    </div>
    """

    return HTMLResponse(render_page(content, active="dashboard", user=user))