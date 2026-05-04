from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from db import get_conn
from settings import COOKIE_NAME

router = APIRouter()


def login_page(error: str = ""):
    error_html = f"<p style='color:#dc2626;margin-bottom:15px;text-align:center;'>{error}</p>" if error else ""

    return f"""
    <html>
    <head>
        <title>Login</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>

    <body style="
        margin:0;
        font-family:Arial;
        background:linear-gradient(135deg,#eaf0ff,#f6f9ff);
        min-height:100vh;
        display:flex;
        align-items:center;
        justify-content:center;
    ">

        <div style="
            width:420px;
            background:white;
            padding:35px;
            border-radius:18px;
            box-shadow:0 10px 30px rgba(0,0,0,0.08);
        ">

            <!-- 🔥 LOGO -->
            <div style="text-align:center;margin-bottom:20px;">
                <img src="/static/logo.png" 
                     style="width:400px;height:400px;object-fit:contain;
                     filter:drop-shadow(0 4px 8px rgba(0,0,0,0.15));">
            </div>

            <!-- TITLE -->
            <div style="font-size:28px;font-weight:bold;color:#1d4ed8;margin-bottom:8px;text-align:center;">
                Premium One ERP
            </div>

            <div style="color:#666;margin-bottom:25px;text-align:center;">
                Sign in to continue
            </div>

            {error_html}

            <form method="post" action="/login">

                <div style="margin-bottom:15px;">
                    <label style="display:block;margin-bottom:6px;">Username</label>
                    <input
                        type="text"
                        name="username"
                        required
                        style="
                            width:100%;
                            padding:12px;
                            border:1px solid #d9e1f2;
                            border-radius:10px;
                            box-sizing:border-box;
                        "
                    >
                </div>

                <div style="margin-bottom:20px;">
                    <label style="display:block;margin-bottom:6px;">Password</label>
                    <input
                        type="password"
                        name="password"
                        required
                        style="
                            width:100%;
                            padding:12px;
                            border:1px solid #d9e1f2;
                            border-radius:10px;
                            box-sizing:border-box;
                        "
                    >
                </div>

                <button type="submit" style="
                    width:100%;
                    padding:12px;
                    background:#1d4ed8;
                    color:white;
                    border:none;
                    border-radius:10px;
                    font-size:16px;
                    cursor:pointer;
                ">
                    Login
                </button>
            </form>

            <div style="margin-top:18px;color:#777;font-size:14px;text-align:center;">
                Default admin: <b>admin</b> / <b>1234</b>
            </div>

        </div>

    </body>
    </html>
    """


@router.get("/login", response_class=HTMLResponse)
def login_get():
    return HTMLResponse(login_page())


@router.post("/login")
def login_post(username: str = Form(...), password: str = Form(...)):
    conn = get_conn()
    cur = conn.cursor()

    user = cur.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username.strip(), password.strip())
    ).fetchone()

    conn.close()

    if not user:
        return HTMLResponse(login_page("Invalid username or password"), status_code=401)

    response = RedirectResponse(url="/ui/dashboard", status_code=302)
    response.set_cookie(key=COOKIE_NAME, value=username.strip(), httponly=True)
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response