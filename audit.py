from db import get_conn
from datetime import datetime
from html import escape


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

    conn.commit()
    conn.close()


def log_action(entity_type: str, entity_id: int, action: str, done_by: str = "admin", notes: str = "", conn=None, done_at: str | None = None):
    own_conn = conn is None
    if conn is None:
        conn = get_conn()

    conn.execute("""
        INSERT INTO audit_log (
            entity_type, entity_id, action, notes, done_by, done_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        entity_type,
        entity_id,
        action,
        notes,
        done_by,
        done_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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


def safe_log_action(entity_type: str, entity_id: int, action: str, done_by: str = "System", notes: str = "", conn=None, done_at: str | None = None):
    try:
        log_action(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            done_by=done_by,
            notes=notes,
            conn=conn,
            done_at=done_at,
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
            note_html = f'<div class="audit-note">{notes}</div>' if notes else ""
            chunks.append(f"""
            <div class="audit-item">
                <div class="audit-item-head">
                    <div class="audit-item-title">{action}</div>
                    <div class="audit-item-meta">{done_at}</div>
                </div>
                {note_html}
                <div class="audit-item-user">By: {done_by}</div>
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
