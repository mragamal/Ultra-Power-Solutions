from db import get_conn


def ensure_activity_table():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            notes TEXT,
            done_by TEXT DEFAULT 'admin',
            done_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def log_activity(entity_type: str, entity_id: int, action: str, notes: str = "", done_by: str = "admin"):
    ensure_activity_table()
    conn = get_conn()
    conn.execute("""
        INSERT INTO activity_logs (entity_type, entity_id, action, notes, done_by)
        VALUES (?, ?, ?, ?, ?)
    """, (entity_type, entity_id, action, notes, done_by))
    conn.commit()
    conn.close()


def render_activity_timeline(entity_type: str, entity_id: int) -> str:
    ensure_activity_table()
    conn = get_conn()
    rows = conn.execute("""
        SELECT action, notes, done_by, done_at
        FROM activity_logs
        WHERE entity_type = ? AND entity_id = ?
        ORDER BY id DESC
    """, (entity_type, entity_id)).fetchall()
    conn.close()

    items = ""
    for r in rows:
        items += f"""
        <div style="padding:10px 0;border-bottom:1px solid #e5e7eb;">
            <div style="font-weight:700;">{r['action']}</div>
            <div style="font-size:13px;color:#4b5563;">{r['notes'] or ''}</div>
            <div style="font-size:12px;color:#6b7280;">{r['done_at']} — {r['done_by']}</div>
        </div>
        """

    if not items:
        items = "<div style='color:#6b7280;'>No activity yet.</div>"

    return f"""
    <div class="card">
        <h3 style="margin-top:0;">Activity</h3>
        {items}
    </div>
    """