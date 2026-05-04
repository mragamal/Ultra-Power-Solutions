from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from auth import current_user, get_user_modules
from layout import render_page

router = APIRouter()


ALL_MODULES = [
    ("dashboard", "Dashboard"),
    ("clients", "Clients"),
    ("inventory", "Inventory"),
    ("accounting", "Accounting"),
    ("users", "Users"),
]


def require_admin(request: Request):
    user = current_user(request)
    if not user:
        return None
    if user["role"] != "admin":
        return None
    return user


@router.get("/ui/users", response_class=HTMLResponse)
def users_page(request: Request):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    conn = get_conn()
    users = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    conn.close()

    rows = ""
    for u in users:
        modules = get_user_modules(u["username"])
        modules_text = ", ".join(modules) if modules else "-"

        rows += f"""
        <tr>
            <td>{u["id"]}</td>
            <td>{u["username"]}</td>
            <td>{u["role"]}</td>
            <td>{modules_text}</td>
            <td>
                <a class="btn btn-light" href="/ui/users/edit/{u["id"]}">Edit</a>
            </td>
        </tr>
        """

    content = f"""
        <h1 class="page-title">Users</h1>

        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">Add User</div>
            </div>
            <div class="panel-body">
                <form method="post" action="/ui/users/add" class="form-grid">
                    <div class="form-group">
                        <label>Username</label>
                        <input name="username" required>
                    </div>

                    <div class="form-group">
                        <label>Password</label>
                        <input name="password" required>
                    </div>

                    <div class="form-group">
                        <label>Role</label>
                        <select name="role">
                            <option value="user">user</option>
                            <option value="admin">admin</option>
                        </select>
                    </div>

                    <div class="form-group">
                        <label>Modules</label>
                        <div>
                            <label><input type="checkbox" name="modules" value="dashboard"> Dashboard</label><br>
                            <label><input type="checkbox" name="modules" value="clients"> Clients</label><br>
                            <label><input type="checkbox" name="modules" value="inventory"> Inventory</label><br>
                            <label><input type="checkbox" name="modules" value="accounting"> Accounting</label><br>
                            <label><input type="checkbox" name="modules" value="users"> Users</label>
                        </div>
                    </div>

                    <button class="btn btn-primary" type="submit">Save User</button>
                </form>
            </div>
        </div>

        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">Users List</div>
            </div>
            <div class="panel-body table-wrap">
                <table class="erp-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Username</th>
                            <th>Role</th>
                            <th>Modules</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
        </div>
    """

    return HTMLResponse(render_page("Users", "users", content, user["username"]))


@router.post("/ui/users/add")
def add_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    modules: list[str] = Form([])
):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    conn = get_conn()
    conn.execute(
        "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
        (username, password, role)
    )

    for module_name in modules:
        conn.execute(
            "INSERT INTO user_permissions (username, module_name) VALUES (?, ?)",
            (username, module_name)
        )

    conn.commit()
    conn.close()

    return RedirectResponse("/ui/users", status_code=303)


@router.get("/ui/users/edit/{user_id}", response_class=HTMLResponse)
def edit_user_page(user_id: int, request: Request):
    admin = require_admin(request)
    if not admin:
        return RedirectResponse("/login", status_code=302)

    conn = get_conn()
    user_row = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    conn.close()

    if not user_row:
        return RedirectResponse("/ui/users", status_code=303)

    user_modules = get_user_modules(user_row["username"])

    checkboxes = ""
    for module_key, module_label in ALL_MODULES:
        checked = "checked" if module_key in user_modules else ""
        checkboxes += f"""
            <label>
                <input type="checkbox" name="modules" value="{module_key}" {checked}>
                {module_label}
            </label><br>
        """

    selected_user = "selected" if user_row["role"] == "user" else ""
    selected_admin = "selected" if user_row["role"] == "admin" else ""

    content = f"""
        <h1 class="page-title">Edit User</h1>

        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">Edit User #{user_row["id"]}</div>
            </div>
            <div class="panel-body">
                <form method="post" action="/ui/users/edit/{user_row["id"]}" class="form-grid">
                    <div class="form-group">
                        <label>Username</label>
                        <input name="username" value="{user_row["username"]}" required>
                    </div>

                    <div class="form-group">
                        <label>Password</label>
                        <input name="password" value="{user_row["password"]}" required>
                    </div>

                    <div class="form-group">
                        <label>Role</label>
                        <select name="role">
                            <option value="user" {selected_user}>user</option>
                            <option value="admin" {selected_admin}>admin</option>
                        </select>
                    </div>

                    <div class="form-group">
                        <label>Modules</label>
                        <div>
                            {checkboxes}
                        </div>
                    </div>

                    <button class="btn btn-primary" type="submit">Save Changes</button>
                </form>
            </div>
        </div>
    """

    return HTMLResponse(render_page("Users", "users", content, admin["username"]))


@router.post("/ui/users/edit/{user_id}")
def edit_user_save(
    user_id: int,
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    modules: list[str] = Form([])
):
    admin = require_admin(request)
    if not admin:
        return RedirectResponse("/login", status_code=302)

    conn = get_conn()

    old_user = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not old_user:
        conn.close()
        return RedirectResponse("/ui/users", status_code=303)

    conn.execute("""
        UPDATE users
        SET username = ?, password = ?, role = ?
        WHERE id = ?
    """, (username, password, role, user_id))

    conn.execute(
        "DELETE FROM user_permissions WHERE username = ?",
        (old_user["username"],)
    )

    for module_name in modules:
        conn.execute(
            "INSERT INTO user_permissions (username, module_name) VALUES (?, ?)",
            (username, module_name)
        )

    conn.commit()
    conn.close()

    return RedirectResponse("/ui/users", status_code=303)