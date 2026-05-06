from html import escape

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page
from audit import safe_log_action, safe_log_request_action
from auth import (
    copy_role_permissions_to_user,
    default_home_path_for_user,
    get_module_catalog,
    get_user_permissions,
    hash_password,
    verify_password,
    get_user_by_username,
    login_user,
    logout_user,
    current_user,
    require_login,
)

router = APIRouter()


def get_lang(request: Request | None = None) -> str:
    try:
        if request and (request.query_params.get("lang") or "").lower() == "ar":
            return "ar"
    except Exception:
        pass
    return "en"


def roles_html_options(selected=""):
    conn = get_conn()
    rows = conn.execute("""
        SELECT code, name
        FROM roles
        ORDER BY name
    """).fetchall()
    conn.close()

    html = '<option value="">Select Role</option>'
    for r in rows:
        sel = "selected" if (selected or "") == r["code"] else ""
        html += f'<option value="{r["code"]}" {sel}>{r["name"]}</option>'
    return html


def roles_radio_options(selected=""):
    conn = get_conn()
    rows = conn.execute("""
        SELECT code, name
        FROM roles
        ORDER BY name
    """).fetchall()
    conn.close()

    if not rows:
        return '<div class="msg error">No roles found. Create a role first.</div>'

    selected = (selected or "").strip() or (rows[0]["code"] or "")
    html = '<div class="role-choice-grid">'
    for r in rows:
        code = r["code"] or ""
        name = r["name"] or code
        checked = "checked" if selected == code else ""
        html += f"""
        <label class="role-choice">
            <input type="radio" name="role_code" value="{escape(code)}" {checked} required>
            <span>{escape(name)}</span>
        </label>
        """
    html += "</div>"
    return html


def role_exists(conn, role_code: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM roles WHERE code = ? LIMIT 1", ((role_code or "").strip(),)).fetchone())


def checked_attr(value) -> str:
    return "checked" if bool(value) else ""


def _company_prefix(request: Request | None = None) -> str:
    try:
        prefix = request.scope.get("company_prefix") or request.scope.get("root_path") or ""
    except Exception:
        prefix = ""
    prefix = str(prefix or "").strip()
    if prefix and not prefix.startswith("/"):
        prefix = "/" + prefix
    return prefix.rstrip("/")


def render_login_page(error_message: str = "", username: str = "", prefix: str = "") -> str:
    error_html = f'<div class="login-alert">{error_message}</div>' if error_message else ""
    login_action = f"{prefix}/login" if prefix else "/login"
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Premium One ERP Login</title>
        <style>
            * {{
                box-sizing: border-box;
                margin: 0;
                padding: 0;
                font-family: Arial, sans-serif;
            }}
            :root {{
                --bg-1: #05255b;
                --bg-2: #083889;
                --bg-3: #03214f;
                --cyan: #1dd3f2;
                --cyan-soft: rgba(29, 211, 242, 0.18);
                --card-text: #17345f;
                --muted: #7187a4;
                --line: #dce5f1;
            }}
            html, body {{
                min-height: 100%;
            }}
            body {{
                min-height: 100vh;
                color: #fff;
                background:
                    radial-gradient(circle at 18% 16%, rgba(66, 121, 221, 0.16), transparent 20%),
                    radial-gradient(circle at 84% 56%, rgba(28, 213, 242, 0.16), transparent 17%),
                    linear-gradient(135deg, #072d76 0%, #062f81 44%, #042661 100%);
                overflow: hidden;
            }}
            .login-shell {{
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: space-between;
                padding: 26px 24px 26px;
                position: relative;
                overflow: hidden;
            }}
            .login-shell::after {{
                content: "";
                position: absolute;
                right: -86px;
                top: 210px;
                width: min(760px, 60vw);
                height: 470px;
                background:
                    radial-gradient(circle at 72% 44%, rgba(49, 223, 245, 0.2), transparent 17%),
                    radial-gradient(circle, rgba(177,248,255,0.9) 0 1.2px, transparent 1.9px) 62% 48% / 12px 12px no-repeat,
                    radial-gradient(circle, rgba(177,248,255,0.55) 0 1px, transparent 1.7px) 66% 53% / 14px 14px no-repeat,
                    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='860' height='460' viewBox='0 0 860 460'%3E%3Cg fill='none'%3E%3Cpath d='M0 320 C170 260 248 230 350 236 C502 244 618 332 860 266' stroke='%232ce4ff' stroke-opacity='0.98' stroke-width='3.5'/%3E%3Cpath d='M14 348 C182 292 262 262 362 266 C512 272 628 358 860 296' stroke='%2377f2ff' stroke-opacity='0.42' stroke-width='1.8'/%3E%3Cpath d='M0 380 C160 328 252 304 356 308 C508 314 624 390 860 334' stroke='%23b6f8ff' stroke-opacity='0.18' stroke-width='1.4'/%3E%3Cpath d='M0 406 C164 356 252 334 360 340 C518 348 634 420 860 370' stroke='%23d8feff' stroke-opacity='0.12' stroke-width='1'/%3E%3C/g%3E%3C/svg%3E") center/contain no-repeat;
                opacity: 1;
                pointer-events: none;
            }}
            .brand-watermark {{
                position: absolute;
                left: -18px;
                top: 78px;
                font-size: 405px;
                font-weight: 900;
                line-height: 0.8;
                color: rgba(255,255,255,0.07);
                pointer-events: none;
                user-select: none;
            }}
            .login-main {{
                flex: 1;
                width: 100%;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                position: relative;
                z-index: 2;
            }}
            .brand-wrap {{
                text-align: center;
                margin-bottom: 18px;
                position: relative;
                z-index: 2;
            }}
            .brand-wrap img {{
                width: min(410px, 84vw);
                display: block;
                margin: 0 auto;
                filter: drop-shadow(0 14px 24px rgba(0,0,0,0.2));
            }}
            .login-card {{
                width: 100%;
                max-width: 500px;
                background: rgba(255,255,255,0.99);
                color: var(--card-text);
                border-radius: 22px;
                padding: 34px 42px 28px;
                box-shadow: 0 24px 52px rgba(2, 17, 44, 0.24);
                border: 1px solid rgba(255,255,255,0.45);
                position: relative;
                z-index: 2;
            }}
            .login-card h1 {{
                font-size: 31px;
                text-align: center;
                margin-bottom: 10px;
                font-weight: 800;
                letter-spacing: -0.4px;
            }}
            .login-sub {{
                text-align: center;
                color: var(--muted);
                font-size: 14px;
                margin-bottom: 28px;
            }}
            .login-alert {{
                margin-bottom: 18px;
                padding: 12px 14px;
                border-radius: 14px;
                background: #fff1f1;
                color: #c62828;
                font-weight: 700;
                text-align: center;
            }}
            .field {{
                margin-bottom: 22px;
            }}
            .field label {{
                display: block;
                margin-bottom: 10px;
                font-size: 14px;
                font-weight: 800;
                color: var(--card-text);
            }}
            .input-wrap {{
                display: flex;
                align-items: center;
                gap: 10px;
                border: 1px solid var(--line);
                border-radius: 14px;
                padding: 0 16px;
                background: #fff;
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.75);
                min-height: 60px;
            }}
            .input-wrap input {{
                width: 100%;
                border: 0;
                outline: none;
                font-size: 15px;
                color: var(--card-text);
                padding: 17px 0;
                background: transparent;
            }}
            .input-wrap input::placeholder {{
                color: #8a98ae;
            }}
            .input-icon, .toggle-eye {{
                color: #6f83a2;
                min-width: 22px;
                width: 22px;
                height: 22px;
                text-align: center;
                display: inline-flex;
                align-items: center;
                justify-content: center;
            }}
            .input-icon svg, .toggle-eye svg {{
                width: 20px;
                height: 20px;
                stroke: currentColor;
                fill: none;
                stroke-width: 1.8;
                stroke-linecap: round;
                stroke-linejoin: round;
            }}
            .toggle-eye {{
                border: 0;
                background: transparent;
                cursor: pointer;
                padding: 0;
            }}
            .helper-row {{
                display: flex;
                justify-content: flex-end;
                margin-top: -4px;
                margin-bottom: 18px;
            }}
            .helper-row a {{
                color: #1bb9d9;
                font-size: 13px;
                font-weight: 700;
                text-decoration: none;
            }}
            .login-btn {{
                width: 100%;
                border: 0;
                border-radius: 14px;
                padding: 18px 18px;
                color: #fff;
                font-size: 18px;
                font-weight: 800;
                cursor: pointer;
                background: linear-gradient(90deg, #0d3c96 0%, #0d59d1 52%, #19c8e8 100%);
                box-shadow: 0 16px 28px rgba(13, 71, 173, 0.22);
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 14px;
            }}
            .divider {{
                display: flex;
                align-items: center;
                gap: 12px;
                margin: 24px 0 22px;
                color: #8a9bb4;
                font-weight: 700;
            }}
            .divider::before, .divider::after {{
                content: "";
                flex: 1;
                height: 1px;
                background: #e2e8f2;
            }}
            .ghost-btn {{
                width: 100%;
                border-radius: 14px;
                border: 1px solid var(--line);
                background: #fff;
                color: var(--card-text);
                padding: 16px 18px;
                font-size: 15px;
                font-weight: 800;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 10px;
                cursor: pointer;
            }}
            .login-note {{
                margin-top: 18px;
                text-align: center;
                font-size: 13px;
                color: #7a8da9;
                line-height: 1.5;
            }}
            .feature-row {{
                width: min(1080px, 100%);
                margin-top: 26px;
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 14px;
                align-items: stretch;
                position: relative;
                z-index: 2;
            }}
            .feature {{
                text-align: center;
                padding: 2px 12px 6px;
                position: relative;
            }}
            .feature:not(:last-child)::after {{
                content: "";
                position: absolute;
                top: 12px;
                right: -8px;
                width: 1px;
                height: 116px;
                background: rgba(255,255,255,0.22);
            }}
            .feature-icon {{
                width: 98px;
                height: 98px;
                margin: 0 auto 12px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #36dbf7;
                filter: drop-shadow(0 8px 18px rgba(28, 208, 243, 0.2));
            }}
            .feature-icon svg {{
                width: 86px;
                height: 86px;
                stroke: currentColor;
                fill: none;
                stroke-width: 1.8;
                stroke-linecap: round;
                stroke-linejoin: round;
            }}
            .feature-title {{
                font-size: 18px;
                font-weight: 800;
                margin-bottom: 7px;
            }}
            .feature-sub {{
                color: rgba(255,255,255,0.8);
                line-height: 1.5;
                font-size: 14px;
            }}
            @media (max-width: 900px) {{
                .feature-row {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}
                .feature:nth-child(2)::after {{
                    display: none;
                }}
                .login-shell::after {{
                    right: -180px;
                    top: 260px;
                    width: 560px;
                }}
            }}
            @media (max-width: 560px) {{
                .login-shell {{
                    padding: 18px 14px 18px;
                    overflow-y: auto;
                }}
                .login-card {{
                    padding: 24px 18px;
                    border-radius: 22px;
                }}
                .brand-wrap img {{
                    width: min(300px, 84vw);
                }}
                .feature-row {{
                    grid-template-columns: 1fr;
                    gap: 4px;
                }}
                .feature::after {{
                    display: none;
                }}
                .brand-watermark {{
                    font-size: 250px;
                    top: 80px;
                }}
                .login-shell::after {{
                    opacity: 0.55;
                    right: -220px;
                    top: 290px;
                    width: 440px;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="login-shell">
            <div class="brand-watermark">P</div>
            <div class="login-main">
                <div class="brand-wrap">
                    <img src="/static/logo6.png" alt="Premium One ERP">
                </div>

                <div class="login-card">
                    <h1>Welcome Back</h1>
                    <div class="login-sub">Sign in to your Premium One ERP account</div>
                    {error_html}

                    <form method="post" action="{login_action}">
                        <div class="field">
                            <label>Username</label>
                            <div class="input-wrap">
                                <span class="input-icon">
                                    <svg viewBox="0 0 24 24" aria-hidden="true">
                                        <path d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Z"/>
                                        <path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>
                                    </svg>
                                </span>
                                <input name="username" value="{username}" placeholder="Enter your username" required>
                            </div>
                        </div>

                        <div class="field">
                            <label>Password</label>
                            <div class="input-wrap">
                                <span class="input-icon">
                                    <svg viewBox="0 0 24 24" aria-hidden="true">
                                        <rect x="5" y="11" width="14" height="10" rx="2"/>
                                        <path d="M8 11V8a4 4 0 1 1 8 0v3"/>
                                    </svg>
                                </span>
                                <input id="password" type="password" name="password" placeholder="Enter your password" required>
                                <button class="toggle-eye" type="button" onclick="togglePassword()" aria-label="Toggle password">
                                    <svg viewBox="0 0 24 24" aria-hidden="true">
                                        <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z"/>
                                        <circle cx="12" cy="12" r="2.8"/>
                                        <path d="M4 4 20 20"/>
                                    </svg>
                                </button>
                            </div>
                        </div>

                        <div class="helper-row">
                            <a href="#">Forgot Password?</a>
                        </div>

                        <button class="login-btn" type="submit">
                            <span>Sign In</span>
                            <svg viewBox="0 0 24 24" aria-hidden="true" style="width:20px;height:20px;stroke:#fff;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;">
                                <path d="M5 12h14"/>
                                <path d="m13 5 7 7-7 7"/>
                            </svg>
                        </button>

                        <div class="divider">or</div>

                        <button class="ghost-btn" type="button">
                            <svg viewBox="0 0 24 24" aria-hidden="true" style="width:20px;height:20px;stroke:#17345f;fill:none;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round;">
                                <path d="M12 3 5 6v5c0 4.7 3.2 8.8 7 10 3.8-1.2 7-5.3 7-10V6l-7-3Z"/>
                                <path d="m9.5 12 1.8 1.8 3.6-4"/>
                            </svg>
                            <span>Sign in with SSO</span>
                        </button>

                        <div class="login-note">
                            &copy; 2025 Premium One ERP. All rights reserved.
                        </div>
                    </form>
                </div>
            </div>

            <div class="feature-row">
                <div class="feature">
                    <div class="feature-icon">
                        <svg viewBox="0 0 64 64" aria-hidden="true">
                            <path d="M32 8 14 16v14c0 12 8.2 22.5 18 26 9.8-3.5 18-14 18-26V16L32 8Z"/>
                            <path d="m24 32 5 5 11-12"/>
                        </svg>
                    </div>
                    <div class="feature-title">Secure & Reliable</div>
                    <div class="feature-sub">Your data is safe with us</div>
                </div>
                <div class="feature">
                    <div class="feature-icon">
                        <svg viewBox="0 0 64 64" aria-hidden="true">
                            <circle cx="32" cy="32" r="20"/>
                            <path d="M32 32 44 24"/>
                            <path d="M32 12v4M12 32h4M48 32h4M32 48v4"/>
                        </svg>
                    </div>
                    <div class="feature-title">Fast & Efficient</div>
                    <div class="feature-sub">Save time and boost productivity</div>
                </div>
                <div class="feature">
                    <div class="feature-icon">
                        <svg viewBox="0 0 64 64" aria-hidden="true">
                            <path d="M12 50h40"/>
                            <path d="M18 50V34"/>
                            <path d="M30 50V24"/>
                            <path d="M42 50V16"/>
                            <path d="m18 30 12-10 12-6 8-6"/>
                        </svg>
                    </div>
                    <div class="feature-title">Smart Reporting</div>
                    <div class="feature-sub">Real-time insights for better decisions</div>
                </div>
                <div class="feature">
                    <div class="feature-icon">
                        <svg viewBox="0 0 64 64" aria-hidden="true">
                            <path d="M20 46h24a12 12 0 0 0 3-23.6A16 16 0 0 0 16.8 26 10 10 0 0 0 20 46Z"/>
                            <path d="m24 38 6 6 10-12"/>
                        </svg>
                    </div>
                    <div class="feature-title">Cloud Based</div>
                    <div class="feature-sub">Access your data anytime, anywhere</div>
                </div>
            </div>

        </div>

        <script>
            function togglePassword() {{
                var input = document.getElementById("password");
                input.type = input.type === "password" ? "text" : "password";
            }}
        </script>
    </body>
    </html>
    """


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    user = current_user(request)
    if user:
        return RedirectResponse(default_home_path_for_user(request), status_code=302)

    return HTMLResponse(render_login_page(prefix=_company_prefix(request)))


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    user = get_user_by_username((username or "").strip())

    if not user:
        safe_log_action(
            "system_activity",
            0,
            "Login failed",
            (username or "").strip() or "Unknown user",
            "Invalid username.",
            path=request.scope.get("original_path") or request.url.path,
            method=request.method,
        )
        return HTMLResponse(render_login_page("Invalid username or password.", (username or "").strip(), _company_prefix(request)), status_code=400)

    if not bool(user["is_active"]):
        safe_log_action(
            "system_activity",
            int(user["id"] or 0),
            "Login failed",
            user["full_name"] or user["username"] or "Unknown user",
            "Inactive user.",
            user_id=user["id"],
            username=user["username"],
            path=request.scope.get("original_path") or request.url.path,
            method=request.method,
        )
        return HTMLResponse(render_login_page("This user is inactive.", (username or "").strip(), _company_prefix(request)), status_code=400)

    if not verify_password(password, user["password_hash"]):
        safe_log_action(
            "system_activity",
            int(user["id"] or 0),
            "Login failed",
            user["full_name"] or user["username"] or "Unknown user",
            "Invalid password.",
            user_id=user["id"],
            username=user["username"],
            path=request.scope.get("original_path") or request.url.path,
            method=request.method,
        )
        return HTMLResponse(render_login_page("Invalid username or password.", (username or "").strip(), _company_prefix(request)), status_code=400)

    login_user(request, user)
    safe_log_request_action(
        request,
        "system_activity",
        int(user["id"] or 0),
        "Login",
        "User logged in.",
        module="system",
    )
    return RedirectResponse(default_home_path_for_user(request), status_code=302)


@router.get("/logout")
def logout(request: Request):
    user = current_user(request)
    if user:
        safe_log_request_action(
            request,
            "system_activity",
            int(user.get("user_id") or 0),
            "Logout",
            "User logged out.",
            module="system",
        )
    logout_user(request)
    return RedirectResponse("/login", status_code=302)


@router.get("/ui/system/users", response_class=HTMLResponse)
@require_login("users", "view")
def users_list(request: Request):
    lang = get_lang(request)
    conn = get_conn()
    rows = conn.execute("""
        SELECT u.*, r.name AS role_name
        FROM users u
        LEFT JOIN roles r ON r.code = u.role_code
        ORDER BY u.id DESC
    """).fetchall()
    conn.close()

    body = ""
    for r in rows:
        is_active = int(r["is_active"] or 0) == 1
        status_label = "Active" if is_active else "Inactive"
        toggle_label = "Deactivate" if is_active else "Activate"
        toggle_class = "red" if is_active else "green"
        body += f"""
        <tr>
            <td>{r['username'] or ''}</td>
            <td>{r['full_name'] or ''}</td>
            <td>{r['role_name'] or r['role_code'] or ''}</td>
            <td>{status_label}</td>
            <td>
                <div class="row-actions">
                    <a class="btn gray btn-sm" href="/ui/system/users/{r['id']}">Open</a>
                    <a class="btn blue btn-sm" href="/ui/system/users/{r['id']}/edit">Edit</a>
                    <a class="btn purple btn-sm" href="/ui/system/users/{r['id']}/permissions">Permissions</a>
                    <form method="post" action="/ui/system/users/{r['id']}/toggle-active">
                        <button class="btn {toggle_class} btn-sm" type="submit">{toggle_label}</button>
                    </form>
                </div>
            </td>
        </tr>
        """

    html = f"""
    <style>
        .row-actions {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: nowrap;
            white-space: nowrap;
        }}
        .row-actions form {{
            display: inline-flex;
            margin: 0;
        }}
        .btn.btn-sm {{
            min-height: 36px;
            padding: 8px 12px;
            border-radius: 10px;
            font-size: 14px;
            line-height: 1;
        }}
        @media (max-width: 1100px) {{
            .row-actions {{
                flex-wrap: wrap;
            }}
        }}
    </style>
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>Users</h2>
            <a class="btn green" href="/ui/system/users/new">New User</a>
        </div>
    </div>

    <div class="card">
        <table>
            <tr>
                <th>Username</th>
                <th>Full Name</th>
                <th>Role</th>
                <th>Status</th>
                <th>Action</th>
            </tr>
            {body}
        </table>
    </div>
    """
    return HTMLResponse(render_page("Users", html, lang, current_path=request.url.path))


@router.get("/ui/system/users/new", response_class=HTMLResponse)
@require_login("users", "create")
def new_user_form(request: Request):
    lang = get_lang(request)
    html = f"""
    <style>
        .role-choice-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 10px;
        }}
        .role-choice {{
            display: flex;
            align-items: center;
            gap: 10px;
            min-height: 48px;
            padding: 12px 14px;
            border: 1px solid #d7e2f1;
            border-radius: 10px;
            background: #fff;
            font-weight: 800;
            cursor: pointer;
        }}
        .role-choice input {{
            width: auto;
            min-width: 18px;
        }}
    </style>
    <div class="card">
        <h2>New User</h2>

        <form method="post" action="/ui/system/users/new">
            <div class="row">
                <div class="col">
                    <label>Username</label>
                    <input name="username" required>
                </div>
                <div class="col">
                    <label>Full Name</label>
                    <input name="full_name" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Password</label>
                    <input type="password" name="password" required>
                </div>
                <div class="col">
                    <label>Role</label>
                    {roles_radio_options()}
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Status</label>
                    <select name="is_active">
                        <option value="1">Active</option>
                        <option value="0">Inactive</option>
                    </select>
                </div>
                <div class="col"></div>
            </div>

            <div style="margin-top:18px;">
                <button class="btn green" type="submit">Save</button>
                <a class="btn gray" href="/ui/system/users">Back</a>
            </div>
        </form>
    </div>
    """
    return HTMLResponse(render_page("New User", html, lang, current_path=request.url.path))
@router.post("/ui/system/users/new")
@require_login("users", "create")
def create_user(
    request: Request,
    username: str = Form(...),
    full_name: str = Form(...),
    password: str = Form(...),
    role_code: str = Form(...),
    is_active: int = Form(1),
):
    lang = get_lang(request)
    role_code = (role_code or "").strip()
    conn = get_conn()

    if not role_exists(conn, role_code):
        conn.close()
        return HTMLResponse(render_page(
            "New User",
            '<div class="card"><h2>New User</h2><p style="color:red;">Please select a valid role.</p><a class="btn gray" href="/ui/system/users/new">Back</a></div>',
            lang,
            current_path=request.url.path
        ), status_code=400)

    exists = conn.execute("""
        SELECT id FROM users WHERE username = ? LIMIT 1
    """, ((username or "").strip(),)).fetchone()

    if exists:
        conn.close()
        return HTMLResponse(render_page(
            "New User",
            '<div class="card"><h2>New User</h2><p style="color:red;">Username already exists.</p><a class="btn gray" href="/ui/system/users/new">Back</a></div>',
            lang,
            current_path=request.url.path
        ), status_code=400)

    conn.execute("""
        INSERT INTO users (username, full_name, password_hash, role_code, is_active)
        VALUES (?, ?, ?, ?, ?)
    """, (
        (username or "").strip(),
        (full_name or "").strip(),
        hash_password(password),
        role_code,
        int(is_active or 0),
    ))
    user_id = conn.execute("SELECT last_insert_rowid() AS user_id").fetchone()["user_id"]
    conn.commit()
    conn.close()

    copy_role_permissions_to_user(user_id, role_code)
    return RedirectResponse(f"/ui/system/users/{user_id}/permissions", status_code=302)


@router.get("/ui/system/users/{user_id}", response_class=HTMLResponse)
@require_login("users", "view")
def open_user(request: Request, user_id: int):
    lang = get_lang(request)
    conn = get_conn()
    user = conn.execute("""
        SELECT u.*, r.name AS role_name
        FROM users u
        LEFT JOIN roles r ON r.code = u.role_code
        WHERE u.id = ?
        LIMIT 1
    """, (user_id,)).fetchone()
    permission_count = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM user_permissions
        WHERE user_id = ?
          AND COALESCE(can_view, 0) = 1
    """, (user_id,)).fetchone()["cnt"]
    conn.close()

    if not user:
        return HTMLResponse("User not found", status_code=404)

    html = f"""
    <div class="card">
        <h2>User {user['username'] or ''}</h2>

        <div class="row">
            <div class="col"><p><b>Username:</b> {user['username'] or ''}</p></div>
            <div class="col"><p><b>Full Name:</b> {user['full_name'] or ''}</p></div>
        </div>

        <div class="row">
            <div class="col"><p><b>Role:</b> {user['role_name'] or user['role_code'] or ''}</p></div>
            <div class="col"><p><b>Status:</b> {"Active" if int(user['is_active'] or 0) == 1 else "Inactive"}</p></div>
        </div>

        <div class="row">
            <div class="col"><p><b>Accessible Modules:</b> {permission_count}</p></div>
            <div class="col"><p><b>Permissions Source:</b> User-level control</p></div>
        </div>

        <div style="margin-top:18px;">
            <a class="btn blue" href="/ui/system/users/{user_id}/edit">Edit User</a>
            <a class="btn blue" href="/ui/system/users/{user_id}/permissions">Manage Permissions</a>
            <a class="btn gray" href="/ui/system/users">Back</a>
        </div>
    </div>
    """
    return HTMLResponse(render_page("User", html, lang, current_path=request.url.path))


@router.get("/ui/system/users/{user_id}/edit", response_class=HTMLResponse)
@require_login("users", "edit")
def edit_user_form(request: Request, user_id: int):
    lang = get_lang(request)
    conn = get_conn()
    user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
        LIMIT 1
    """, (user_id,)).fetchone()
    conn.close()

    if not user:
        return HTMLResponse("User not found", status_code=404)

    active_selected = "selected" if int(user["is_active"] or 0) == 1 else ""
    inactive_selected = "selected" if int(user["is_active"] or 0) == 0 else ""

    html = f"""
    <style>
        .role-choice-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 10px;
        }}
        .role-choice {{
            display: flex;
            align-items: center;
            gap: 10px;
            min-height: 48px;
            padding: 12px 14px;
            border: 1px solid #d7e2f1;
            border-radius: 10px;
            background: #fff;
            font-weight: 800;
            cursor: pointer;
        }}
        .role-choice input {{
            width: auto;
            min-width: 18px;
        }}
    </style>
    <div class="card">
        <h2>Edit User</h2>

        <form method="post" action="/ui/system/users/{user_id}/edit">
            <div class="row">
                <div class="col">
                    <label>Username</label>
                    <input name="username" value="{user['username'] or ''}" required>
                </div>
                <div class="col">
                    <label>Full Name</label>
                    <input name="full_name" value="{user['full_name'] or ''}" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Role</label>
                    {roles_radio_options(user['role_code'] or '')}
                </div>
                <div class="col">
                    <label>Status</label>
                    <select name="is_active">
                        <option value="1" {active_selected}>Active</option>
                        <option value="0" {inactive_selected}>Inactive</option>
                    </select>
                </div>
            </div>

            <div class="form-actions">
                <button class="btn green" type="submit">Save</button>
                <a class="btn gray" href="/ui/system/users/{user_id}">Back</a>
            </div>
        </form>
    </div>

    <div class="card">
        <h2>Reset Password</h2>
        <form method="post" action="/ui/system/users/{user_id}/password">
            <div class="row">
                <div class="col">
                    <label>New Password</label>
                    <input type="password" name="new_password" required>
                </div>
                <div class="col">
                    <label>Confirm Password</label>
                    <input type="password" name="confirm_password" required>
                </div>
            </div>
            <div class="form-actions">
                <button class="btn blue" type="submit">Update Password</button>
            </div>
        </form>
    </div>
    """
    return HTMLResponse(render_page("Edit User", html, lang, current_path=request.url.path))


@router.post("/ui/system/users/{user_id}/edit")
@require_login("users", "edit")
def update_user(
    request: Request,
    user_id: int,
    username: str = Form(...),
    full_name: str = Form(...),
    role_code: str = Form(...),
    is_active: int = Form(1),
):
    current = current_user(request) or {}
    role_code = (role_code or "").strip()
    if int(current.get("user_id") or 0) == int(user_id):
        is_active = 1

    conn = get_conn()
    existing = conn.execute("SELECT id FROM users WHERE id = ? LIMIT 1", (user_id,)).fetchone()
    duplicate = conn.execute("""
        SELECT id
        FROM users
        WHERE username = ?
          AND id <> ?
        LIMIT 1
    """, ((username or "").strip(), user_id)).fetchone()

    if not existing:
        conn.close()
        return HTMLResponse("User not found", status_code=404)

    if not role_exists(conn, role_code):
        conn.close()
        return HTMLResponse(render_page(
            "Edit User",
            f'<div class="card"><h2>Edit User</h2><p style="color:red;">Please select a valid role.</p><a class="btn gray" href="/ui/system/users/{user_id}/edit">Back</a></div>',
            get_lang(request),
            current_path=request.url.path
        ), status_code=400)

    if duplicate:
        conn.close()
        return HTMLResponse(render_page(
            "Edit User",
            f'<div class="card"><h2>Edit User</h2><p style="color:red;">Username already exists.</p><a class="btn gray" href="/ui/system/users/{user_id}/edit">Back</a></div>',
            get_lang(request),
            current_path=request.url.path
        ), status_code=400)

    conn.execute("""
        UPDATE users
        SET username = ?, full_name = ?, role_code = ?, is_active = ?
        WHERE id = ?
    """, (
        (username or "").strip(),
        (full_name or "").strip(),
        role_code,
        int(is_active or 0),
        user_id,
    ))
    conn.commit()
    conn.close()

    return RedirectResponse(f"/ui/system/users/{user_id}", status_code=302)


@router.post("/ui/system/users/{user_id}/password")
@require_login("users", "edit")
def reset_user_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    lang = get_lang(request)
    if (new_password or "") != (confirm_password or ""):
        return HTMLResponse(render_page(
            "Reset Password",
            f'<div class="card"><h2>Reset Password</h2><p style="color:red;">Password confirmation does not match.</p><a class="btn gray" href="/ui/system/users/{user_id}/edit">Back</a></div>',
            lang,
            current_path=request.url.path
        ), status_code=400)

    conn = get_conn()
    user = conn.execute("SELECT id FROM users WHERE id = ? LIMIT 1", (user_id,)).fetchone()
    if not user:
        conn.close()
        return HTMLResponse("User not found", status_code=404)

    conn.execute("""
        UPDATE users
        SET password_hash = ?
        WHERE id = ?
    """, (hash_password(new_password), user_id))
    conn.commit()
    conn.close()

    return RedirectResponse(f"/ui/system/users/{user_id}/edit", status_code=302)


@router.post("/ui/system/users/{user_id}/toggle-active")
@require_login("users", "edit")
def toggle_user_active(request: Request, user_id: int):
    current = current_user(request) or {}
    if int(current.get("user_id") or 0) == int(user_id):
        return RedirectResponse("/ui/system/users", status_code=302)

    conn = get_conn()
    user = conn.execute("SELECT is_active FROM users WHERE id = ? LIMIT 1", (user_id,)).fetchone()
    if not user:
        conn.close()
        return HTMLResponse("User not found", status_code=404)

    new_state = 0 if int(user["is_active"] or 0) == 1 else 1
    conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_state, user_id))
    conn.commit()
    conn.close()
    return RedirectResponse("/ui/system/users", status_code=302)


@router.get("/ui/system/users/{user_id}/permissions", response_class=HTMLResponse)
@require_login("users", "edit")
def user_permissions_form(request: Request, user_id: int):
    lang = get_lang(request)
    conn = get_conn()
    user = conn.execute("""
        SELECT u.*, r.name AS role_name
        FROM users u
        LEFT JOIN roles r ON r.code = u.role_code
        WHERE u.id = ?
        LIMIT 1
    """, (user_id,)).fetchone()
    conn.close()

    if not user:
        return HTMLResponse("User not found", status_code=404)

    permissions = get_user_permissions(user_id)
    if not permissions:
        copy_role_permissions_to_user(user_id, user["role_code"])
        permissions = get_user_permissions(user_id)

    rows_html = ""
    for module_code, module_name in get_module_catalog():
        perm = permissions.get(module_code, {})
        rows_html += f"""
        <tr>
            <td><b>{module_name}</b><br><span class="muted">{module_code}</span></td>
            <td style="text-align:center;"><input type="checkbox" name="{module_code}_view" value="1" {checked_attr(perm.get("view"))}></td>
            <td style="text-align:center;"><input type="checkbox" name="{module_code}_create" value="1" {checked_attr(perm.get("create"))}></td>
            <td style="text-align:center;"><input type="checkbox" name="{module_code}_edit" value="1" {checked_attr(perm.get("edit"))}></td>
            <td style="text-align:center;"><input type="checkbox" name="{module_code}_delete" value="1" {checked_attr(perm.get("delete"))}></td>
            <td style="text-align:center;"><input type="checkbox" name="{module_code}_approve" value="1" {checked_attr(perm.get("approve"))}></td>
            <td style="text-align:center;"><input type="checkbox" name="{module_code}_post" value="1" {checked_attr(perm.get("post"))}></td>
        </tr>
        """

    html = f"""
    <div class="card">
        <div class="toolbar">
            <div>
                <h2>User Permissions</h2>
                <div class="section-note">Control exactly what <b>{user['username'] or ''}</b> can access and do inside the system. Example: enable Create/Edit for Accounting and keep Post disabled so the user can prepare drafts without posting them.</div>
            </div>
            <div class="table-summary">
                <span class="summary-pill">User: {user['full_name'] or user['username'] or ''}</span>
                <span class="summary-pill">Role: {user['role_name'] or user['role_code'] or ''}</span>
            </div>
        </div>
    </div>

    <div class="card">
        <form method="post" action="/ui/system/users/{user_id}/permissions">
            <div class="table-wrap">
                <table>
                    <tr>
                        <th>Module</th>
                        <th style="text-align:center;">View</th>
                        <th style="text-align:center;">Create</th>
                        <th style="text-align:center;">Edit</th>
                        <th style="text-align:center;">Delete</th>
                        <th style="text-align:center;">Approve</th>
                        <th style="text-align:center;">Post</th>
                    </tr>
                    {rows_html}
                </table>
            </div>

            <div class="form-actions">
                <button class="btn blue" type="submit">Save Permissions</button>
                <button class="btn gray" type="submit" formaction="/ui/system/users/{user_id}/permissions/reset">Reset From Role</button>
                <a class="btn gray" href="/ui/system/users/{user_id}">Back</a>
            </div>
        </form>
    </div>
    """
    return HTMLResponse(render_page("User Permissions", html, lang, current_path=request.url.path))


@router.post("/ui/system/users/{user_id}/permissions")
@require_login("users", "edit")
async def save_user_permissions(request: Request, user_id: int):
    form = await request.form()
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE id = ? LIMIT 1", (user_id,)).fetchone()
    if not user:
        conn.close()
        return HTMLResponse("User not found", status_code=404)

    conn.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
    for module_code, _ in get_module_catalog():
        can_view = 1 if form.get(f"{module_code}_view") else 0
        can_create = 1 if form.get(f"{module_code}_create") else 0
        can_edit = 1 if form.get(f"{module_code}_edit") else 0
        can_delete = 1 if form.get(f"{module_code}_delete") else 0
        can_approve = 1 if form.get(f"{module_code}_approve") else 0
        can_post = 1 if form.get(f"{module_code}_post") else 0

        if any([can_create, can_edit, can_delete, can_approve, can_post]):
            can_view = 1

        conn.execute("""
            INSERT INTO user_permissions (
                user_id, module_code, can_view, can_create, can_edit,
                can_delete, can_approve, can_post
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            module_code,
            can_view,
            can_create,
            can_edit,
            can_delete,
            can_approve,
            can_post,
        ))

    conn.commit()
    conn.close()
    return RedirectResponse(f"/ui/system/users/{user_id}/permissions", status_code=302)


@router.post("/ui/system/users/{user_id}/permissions/reset")
@require_login("users", "edit")
def reset_user_permissions(request: Request, user_id: int):
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE id = ? LIMIT 1", (user_id,)).fetchone()
    conn.close()
    if not user:
        return HTMLResponse("User not found", status_code=404)

    copy_role_permissions_to_user(user_id, user["role_code"])
    return RedirectResponse(f"/ui/system/users/{user_id}/permissions", status_code=302)
