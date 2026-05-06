import os
import hashlib
import hmac
from functools import wraps

from fastapi.responses import RedirectResponse
from db import get_conn


SECRET_SALT = b"premium_one_erp_salt_v1"
MODULE_CATALOG = [
    ("dashboard", "Dashboard"),
    ("accounting", "Accounting"),
    ("reports", "Reports"),
    ("fixed_assets", "Fixed Assets"),
    ("inventory", "Inventory"),
    ("purchasing", "Purchasing"),
    ("sales", "Sales"),
    ("operations", "Operations"),
    ("hr", "HR"),
    ("users", "Users"),
    ("system", "System"),
]


def hash_password(password: str) -> str:
    password = (password or "").encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", password, SECRET_SALT, 120000).hex()


def verify_password(password: str, password_hash: str) -> bool:
    expected = hash_password(password)
    return hmac.compare_digest(expected, password_hash or "")


def ensure_auth_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            name TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            full_name TEXT,
            password_hash TEXT NOT NULL,
            role_code TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS role_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role_code TEXT NOT NULL,
            module_code TEXT NOT NULL,
            can_view INTEGER DEFAULT 0,
            can_create INTEGER DEFAULT 0,
            can_edit INTEGER DEFAULT 0,
            can_delete INTEGER DEFAULT 0,
            can_approve INTEGER DEFAULT 0,
            can_post INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            module_code TEXT NOT NULL,
            can_view INTEGER DEFAULT 0,
            can_create INTEGER DEFAULT 0,
            can_edit INTEGER DEFAULT 0,
            can_delete INTEGER DEFAULT 0,
            can_approve INTEGER DEFAULT 0,
            can_post INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        DELETE FROM role_permissions
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM role_permissions
            GROUP BY role_code, module_code
        )
    """)
    conn.execute("""
        DELETE FROM user_permissions
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM user_permissions
            GROUP BY user_id, module_code
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_role_permissions_unique ON role_permissions (role_code, module_code)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_permissions_unique ON user_permissions (user_id, module_code)")

    # seed roles
    base_roles = [
        ("admin", "Administrator"),
        ("purchasing", "Purchasing"),
        ("operations", "Operations"),
        ("warehouse", "Warehouse"),
        ("accounting", "Accounting"),
        ("hr", "HR"),
    ]
    for code, name in base_roles:
        conn.execute("""
            INSERT OR IGNORE INTO roles (code, name)
            VALUES (?, ?)
        """, (code, name))

    # admin permissions
    for m, _ in MODULE_CATALOG:
        conn.execute("""
            INSERT OR IGNORE INTO role_permissions (
                role_code, module_code, can_view, can_create, can_edit,
                can_delete, can_approve, can_post
            )
            VALUES ('admin', ?, 1, 1, 1, 1, 1, 1)
        """, (m,))

    # basic module permissions
    defaults = [
        ("purchasing", "purchasing", 1, 1, 1, 0, 1, 0),
        ("purchasing", "reports", 1, 0, 0, 0, 0, 0),

        ("warehouse", "inventory", 1, 1, 1, 0, 0, 1),
        ("warehouse", "reports", 1, 0, 0, 0, 0, 0),

        ("operations", "operations", 1, 1, 1, 0, 1, 0),
        ("operations", "inventory", 1, 0, 0, 0, 0, 0),
        ("operations", "hr", 1, 0, 0, 0, 0, 0),
        ("operations", "reports", 1, 0, 0, 0, 0, 0),

        ("accounting", "accounting", 1, 1, 1, 0, 1, 1),
        ("accounting", "fixed_assets", 1, 1, 1, 0, 1, 1),
        ("accounting", "purchasing", 1, 0, 0, 0, 0, 0),
        ("accounting", "reports", 1, 0, 0, 0, 0, 0),

        ("hr", "hr", 1, 1, 1, 0, 0, 0),
        ("hr", "reports", 1, 0, 0, 0, 0, 0),
    ]
    for row in defaults:
        conn.execute("""
            INSERT OR IGNORE INTO role_permissions (
                role_code, module_code, can_view, can_create, can_edit,
                can_delete, can_approve, can_post
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, row)

    # default admin user
    admin_exists = conn.execute("""
        SELECT id FROM users WHERE username = 'admin' LIMIT 1
    """).fetchone()

    if not admin_exists:
        conn.execute("""
            INSERT INTO users (username, full_name, password_hash, role_code, is_active)
            VALUES (?, ?, ?, ?, 1)
        """, (
            "admin",
            "System Administrator",
            hash_password("admin123"),
            "admin",
        ))
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin' LIMIT 1").fetchone()["id"]
        for module_code, _ in MODULE_CATALOG:
            conn.execute("""
                INSERT OR IGNORE INTO user_permissions (
                    user_id, module_code, can_view, can_create, can_edit,
                    can_delete, can_approve, can_post
                )
                VALUES (?, ?, 1, 1, 1, 1, 1, 1)
            """, (admin_id, module_code))

    user_rows = conn.execute("SELECT id, role_code FROM users").fetchall()
    for user_row in user_rows:
        role_rows = conn.execute("""
            SELECT *
            FROM role_permissions
            WHERE role_code = ?
        """, (user_row["role_code"],)).fetchall()
        for role_row in role_rows:
            conn.execute("""
                INSERT OR IGNORE INTO user_permissions (
                    user_id, module_code, can_view, can_create, can_edit,
                    can_delete, can_approve, can_post
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_row["id"],
                role_row["module_code"],
                int(role_row["can_view"] or 0),
                int(role_row["can_create"] or 0),
                int(role_row["can_edit"] or 0),
                int(role_row["can_delete"] or 0),
                int(role_row["can_approve"] or 0),
                int(role_row["can_post"] or 0),
            ))

    conn.commit()
    conn.close()


def get_user_by_username(username: str):
    conn = get_conn()
    row = conn.execute("""
        SELECT *
        FROM users
        WHERE username = ?
        LIMIT 1
    """, (username,)).fetchone()
    conn.close()
    return row


def permission_row_to_dict(row):
    return {
        "view": bool(row["can_view"]),
        "create": bool(row["can_create"]),
        "edit": bool(row["can_edit"]),
        "delete": bool(row["can_delete"]),
        "approve": bool(row["can_approve"]),
        "post": bool(row["can_post"]),
    }


def get_module_catalog():
    return MODULE_CATALOG[:]


def get_role_permissions(role_code: str):
    conn = get_conn()
    rows = conn.execute("""
        SELECT *
        FROM role_permissions
        WHERE role_code = ?
    """, (role_code,)).fetchall()
    conn.close()

    perms = {}
    for r in rows:
        perms[r["module_code"]] = permission_row_to_dict(r)
    return perms


def get_user_permissions(user_id: int):
    conn = get_conn()
    rows = conn.execute("""
        SELECT *
        FROM user_permissions
        WHERE user_id = ?
    """, (user_id,)).fetchall()
    conn.close()

    perms = {}
    for r in rows:
        perms[r["module_code"]] = permission_row_to_dict(r)
    return perms


def get_effective_permissions(user_id: int, role_code: str):
    perms = get_role_permissions(role_code)
    user_perms = get_user_permissions(user_id)
    if user_perms:
        perms.update(user_perms)
    return perms


def copy_role_permissions_to_user(user_id: int, role_code: str):
    conn = get_conn()
    conn.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
    rows = conn.execute("""
        SELECT *
        FROM role_permissions
        WHERE role_code = ?
    """, (role_code,)).fetchall()
    for row in rows:
        conn.execute("""
            INSERT INTO user_permissions (
                user_id, module_code, can_view, can_create, can_edit,
                can_delete, can_approve, can_post
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            row["module_code"],
            int(row["can_view"] or 0),
            int(row["can_create"] or 0),
            int(row["can_edit"] or 0),
            int(row["can_delete"] or 0),
            int(row["can_approve"] or 0),
            int(row["can_post"] or 0),
        ))
    conn.commit()
    conn.close()


def login_user(request, user_row):
    session = request.session
    session["user_id"] = user_row["id"]
    session["username"] = user_row["username"]
    session["full_name"] = user_row["full_name"] or user_row["username"]
    session["role_code"] = user_row["role_code"]


def logout_user(request):
    try:
        request.session.clear()
    except Exception:
        request.scope["session"] = {}


def current_user(request):
    try:
        session = request.session
    except Exception:
        session = request.scope.get("session") or {}

    if not session.get("user_id"):
        return None

    return {
        "user_id": session.get("user_id"),
        "username": session.get("username"),
        "full_name": session.get("full_name"),
        "role_code": session.get("role_code"),
    }


def is_logged_in(request):
    return current_user(request) is not None


def can(request, module_code: str, action: str = "view") -> bool:
    user = current_user(request)
    if not user:
        return False

    user_id = user.get("user_id")
    role_code = user.get("role_code") or ""

    perms = get_effective_permissions(user_id, role_code)
    module_perm = perms.get(module_code, {})
    return bool(module_perm.get(action, False))


def default_home_path_for_user(request):
    if not is_logged_in(request):
        return "/login"

    if can(request, "accounting", "view"):
        return "/ui/accounting"
    if can(request, "hr", "view"):
        return "/ui/hr"
    if can(request, "inventory", "view"):
        return "/ui/inventory"
    if can(request, "purchasing", "view"):
        return "/ui/purchasing"
    if can(request, "sales", "view"):
        return "/ui/sales"
    if can(request, "operations", "view"):
        return "/ui/operations"
    if can(request, "users", "view"):
        return "/ui/system/users"
    return "/login"


def require_login(module_code=None, action="view"):
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            request = kwargs.get("request")
            if request is None:
                for a in args:
                    if hasattr(a, "session"):
                        request = a
                        break

            if request is None or not is_logged_in(request):
                return RedirectResponse("/login", status_code=302)

            if module_code and not can(request, module_code, action):
                return RedirectResponse("/login", status_code=302)

            return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            request = kwargs.get("request")
            if request is None:
                for a in args:
                    if hasattr(a, "session"):
                        request = a
                        break

            if request is None or not is_logged_in(request):
                return RedirectResponse("/login", status_code=302)

            if module_code and not can(request, module_code, action):
                return RedirectResponse("/login", status_code=302)

            return func(*args, **kwargs)

        return async_wrapper if callable(getattr(func, "__await__", None)) else sync_wrapper
    return decorator


ensure_auth_tables()
