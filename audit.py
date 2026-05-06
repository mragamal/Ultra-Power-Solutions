from db import get_conn
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo


AUDIT_TIMEZONE = "Africa/Cairo"


def audit_now() -> str:
    try:
        return datetime.now(ZoneInfo(AUDIT_TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _table_columns(conn, table_name: str) -> set[str]:
    try:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    except Exception:
        return set()


def _ensure_column(conn, table_name: str, column_name: str, column_sql: str):
    if column_name not in _table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def ensure_audit_table():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            notes TEXT,
            done_by TEXT,
            done_at TEXT NOT NULL
        )
    """)
    _ensure_column(conn, "audit_log", "user_id", "user_id INTEGER")
    _ensure_column(conn, "audit_log", "username", "username TEXT")
    _ensure_column(conn, "audit_log", "module", "module TEXT")
    _ensure_column(conn, "audit_log", "path", "path TEXT")
    _ensure_column(conn, "audit_log", "method", "method TEXT")
    _ensure_column(conn, "audit_log", "ip_address", "ip_address TEXT")
    _ensure_column(conn, "audit_log", "status_code", "status_code INTEGER")
    conn.execute("""
        UPDATE audit_log
        SET done_by = COALESCE((
                SELECT b.done_by
                FROM audit_log b
                WHERE b.entity_type = audit_log.entity_type
                  AND b.entity_id = audit_log.entity_id
                  AND COALESCE(TRIM(b.done_by), '') <> ''
                  AND lower(TRIM(b.done_by)) <> 'system'
                ORDER BY b.id DESC
                LIMIT 1
            ), done_by),
            username = COALESCE(username, (
                SELECT b.username
                FROM audit_log b
                WHERE b.entity_type = audit_log.entity_type
                  AND b.entity_id = audit_log.entity_id
                  AND COALESCE(TRIM(b.done_by), '') <> ''
                  AND lower(TRIM(b.done_by)) <> 'system'
                  AND COALESCE(TRIM(b.username), '') <> ''
                ORDER BY b.id DESC
                LIMIT 1
            ))
        WHERE lower(TRIM(COALESCE(done_by, ''))) = 'system'
          AND EXISTS (
                SELECT 1
                FROM audit_log b
                WHERE b.entity_type = audit_log.entity_type
                  AND b.entity_id = audit_log.entity_id
                  AND COALESCE(TRIM(b.done_by), '') <> ''
                  AND lower(TRIM(b.done_by)) <> 'system'
          )
    """)

    conn.commit()
    conn.close()


def log_action(
    entity_type: str,
    entity_id: int,
    action: str,
    done_by: str = "admin",
    notes: str = "",
    conn=None,
    done_at: str | None = None,
    user_id: int | None = None,
    username: str = "",
    module: str = "",
    path: str = "",
    method: str = "",
    ip_address: str = "",
    status_code: int | None = None,
):
    own_conn = conn is None
    if conn is None:
        conn = get_conn()

    conn.execute("""
        INSERT INTO audit_log (
            entity_type, entity_id, action, notes, done_by, done_at,
            user_id, username, module, path, method, ip_address, status_code
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entity_type,
        entity_id,
        action,
        notes,
        done_by,
        done_at or audit_now(),
        user_id,
        username,
        module,
        path,
        method,
        ip_address,
        status_code,
    ))

    if own_conn:
        conn.commit()
        conn.close()


def get_audit_logs(entity_type: str, entity_id: int):
    conn = get_conn()

    rows = conn.execute("""
        SELECT *
        FROM audit_log
        WHERE entity_type = ? AND entity_id = ?
        ORDER BY id DESC
    """, (entity_type, entity_id)).fetchall()

    conn.close()
    return rows


def actor_name_from_request(request, fallback: str = "System") -> str:
    try:
        from auth import current_user
        user = current_user(request)
    except Exception:
        user = None

    if not user:
        return fallback

    return (
        (user.get("full_name") or "").strip()
        or (user.get("username") or "").strip()
        or fallback
    )


def actor_info_from_request(request, fallback: str = "System") -> dict:
    try:
        from auth import current_user
        user = current_user(request)
    except Exception:
        user = None

    if not user:
        return {
            "done_by": fallback,
            "user_id": None,
            "username": "",
        }

    username = (user.get("username") or "").strip()
    done_by = (user.get("full_name") or "").strip() or username or fallback
    return {
        "done_by": done_by,
        "user_id": user.get("user_id"),
        "username": username,
    }


def request_ip_address(request) -> str:
    try:
        forwarded = request.headers.get("x-forwarded-for") or ""
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else ""
    except Exception:
        return ""


def log_request_action(
    request,
    entity_type: str,
    entity_id: int,
    action: str,
    notes: str = "",
    conn=None,
    done_at: str | None = None,
    module: str = "",
    status_code: int | None = None,
):
    actor = actor_info_from_request(request)
    log_action(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        done_by=actor["done_by"],
        notes=notes,
        conn=conn,
        done_at=done_at,
        user_id=actor["user_id"],
        username=actor["username"],
        module=module,
        path=request.scope.get("original_path") or request.url.path,
        method=request.method,
        ip_address=request_ip_address(request),
        status_code=status_code,
    )


def safe_log_request_action(
    request,
    entity_type: str,
    entity_id: int,
    action: str,
    notes: str = "",
    conn=None,
    done_at: str | None = None,
    module: str = "",
    status_code: int | None = None,
):
    try:
        log_request_action(
            request=request,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            notes=notes,
            conn=conn,
            done_at=done_at,
            module=module,
            status_code=status_code,
        )
    except Exception:
        pass


def safe_log_action(
    entity_type: str,
    entity_id: int,
    action: str,
    done_by: str = "System",
    notes: str = "",
    conn=None,
    done_at: str | None = None,
    user_id: int | None = None,
    username: str = "",
    module: str = "",
    path: str = "",
    method: str = "",
    ip_address: str = "",
    status_code: int | None = None,
):
    try:
        log_action(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            done_by=done_by,
            notes=notes,
            conn=conn,
            done_at=done_at,
            user_id=user_id,
            username=username,
            module=module,
            path=path,
            method=method,
            ip_address=ip_address,
            status_code=status_code,
        )
    except Exception:
        pass


def render_audit_log_card(entity_type: str, entity_id: int, title: str = "Activity Log") -> str:
    rows = get_audit_logs(entity_type, entity_id)
    if not rows:
        items = """
        <div class="empty-state" style="padding:18px 0;">
            No activity recorded yet.
        </div>
        """
    else:
        chunks = []
        for row in rows:
            action = escape(str(row["action"] or "Action"))
            notes = escape(str(row["notes"] or ""))
            done_by = escape(str(row["done_by"] or "System"))
            done_at = escape(str(row["done_at"] or ""))
            username = escape(str(row["username"] or ""))
            method = escape(str(row["method"] or ""))
            path = escape(str(row["path"] or ""))
            note_html = f'<div class="audit-note">{notes}</div>' if notes else ""
            user_html = f"By: {done_by}"
            if username and username != done_by:
                user_html += f" ({username})"
            request_html = ""
            if method or path:
                request_html = f'<div class="audit-item-user">{method} {path}</div>'
            chunks.append(f"""
            <div class="audit-item">
                <div class="audit-item-head">
                    <div class="audit-item-title">{action}</div>
                    <div class="audit-item-meta">{done_at}</div>
                </div>
                {note_html}
                <div class="audit-item-user">{user_html}</div>
                {request_html}
            </div>
            """)
        items = "".join(chunks)

    return f"""
    <div class="card" style="margin-top:20px;">
        <div class="toolbar" style="margin-bottom:12px;">
            <h3 class="sub-title" style="margin:0;">{escape(title)}</h3>
        </div>
        <div class="audit-list">
            {items}
        </div>
    </div>
    """


ensure_audit_table()
