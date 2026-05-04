from db import get_conn


def safe(x):
    return "" if x is None else str(x).strip()


def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


def table_exists(conn, table_name):
    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name=?
        """,
        (table_name,),
    ).fetchone()
    return bool(row)


def get_columns(conn, table_name):
    if not table_exists(conn, table_name):
        return []
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [r["name"] for r in rows]


def ensure_workflow_tables():
    conn = get_conn()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_workflow_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            po_id INTEGER,
            grn_id INTEGER,
            alert_type TEXT,
            severity TEXT DEFAULT 'info',
            title TEXT,
            message TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    ensure_column(conn, "purchase_workflow_alerts", "po_id", "ALTER TABLE purchase_workflow_alerts ADD COLUMN po_id INTEGER")
    ensure_column(conn, "purchase_workflow_alerts", "grn_id", "ALTER TABLE purchase_workflow_alerts ADD COLUMN grn_id INTEGER")
    ensure_column(conn, "purchase_workflow_alerts", "alert_type", "ALTER TABLE purchase_workflow_alerts ADD COLUMN alert_type TEXT")
    ensure_column(conn, "purchase_workflow_alerts", "severity", "ALTER TABLE purchase_workflow_alerts ADD COLUMN severity TEXT DEFAULT 'info'")
    ensure_column(conn, "purchase_workflow_alerts", "title", "ALTER TABLE purchase_workflow_alerts ADD COLUMN title TEXT")
    ensure_column(conn, "purchase_workflow_alerts", "message", "ALTER TABLE purchase_workflow_alerts ADD COLUMN message TEXT")
    ensure_column(conn, "purchase_workflow_alerts", "is_read", "ALTER TABLE purchase_workflow_alerts ADD COLUMN is_read INTEGER DEFAULT 0")
    ensure_column(conn, "purchase_workflow_alerts", "created_at", "ALTER TABLE purchase_workflow_alerts ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    conn.commit()
    conn.close()


def create_po_alert(conn, po_id, alert_type, title, message, severity="info", grn_id=None):
    conn.execute(
        """
        INSERT INTO purchase_workflow_alerts (
            po_id, grn_id, alert_type, severity, title, message, is_read
        )
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (
            po_id,
            grn_id,
            safe(alert_type),
            safe(severity) or "info",
            safe(title),
            safe(message),
        ),
    )


def latest_po_alerts(conn, limit=20, only_unread=False):
    where_sql = "WHERE COALESCE(is_read, 0) = 0" if only_unread else ""
    return conn.execute(
        f"""
        SELECT *
        FROM purchase_workflow_alerts
        {where_sql}
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit or 20),),
    ).fetchall()


def po_pending_variance_count(conn, po_id):
    if not table_exists(conn, "po_receipt_variances"):
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM po_receipt_variances
        WHERE po_id = ?
          AND LOWER(COALESCE(decision_status, 'pending')) = 'pending'
        """,
        (po_id,),
    ).fetchone()
    return int(row["c"] or 0) if row else 0


def po_line_billed_qty(conn, po_line_id):
    if not table_exists(conn, "vendor_bill_lines") or not table_exists(conn, "vendor_bills"):
        return 0.0
    cols = get_columns(conn, "vendor_bill_lines")
    if "po_line_id" not in cols:
        return 0.0

    row = conn.execute(
        """
        SELECT COALESCE(SUM(vbl.qty), 0) AS billed_qty
        FROM vendor_bill_lines vbl
        JOIN vendor_bills vb ON vb.id = vbl.bill_id
        WHERE vbl.po_line_id = ?
          AND LOWER(COALESCE(vb.status, 'draft')) IN ('draft', 'posted')
        """,
        (po_line_id,),
    ).fetchone()
    return float(row["billed_qty"] or 0.0) if row else 0.0


def po_billable_summary(conn, po_id):
    if not table_exists(conn, "purchase_order_lines"):
        return {"line_count": 0, "qty": 0.0}

    rows = conn.execute(
        """
        SELECT id, received_qty
        FROM purchase_order_lines
        WHERE po_id = ?
        ORDER BY line_no, id
        """,
        (po_id,),
    ).fetchall()

    line_count = 0
    qty_total = 0.0

    for row in rows:
        received = float(row["received_qty"] or 0.0)
        billed = po_line_billed_qty(conn, row["id"])
        pending = max(received - billed, 0.0)
        if pending > 0.000001:
            line_count += 1
            qty_total += pending

    return {"line_count": line_count, "qty": qty_total}


def po_billable_lines(conn, po_id):
    if not table_exists(conn, "purchase_order_lines"):
        return []
    cols = get_columns(conn, "purchase_order_lines")
    has_price = "unit_price" in cols
    has_desc = "description" in cols
    has_line_no = "line_no" in cols

    select_cols = ["id", "item_id", "received_qty"]
    if has_price:
        select_cols.append("unit_price")
    if has_desc:
        select_cols.append("description")
    if has_line_no:
        select_cols.append("line_no")

    rows = conn.execute(
        f"""
        SELECT {", ".join(select_cols)}
        FROM purchase_order_lines
        WHERE po_id = ?
        ORDER BY {("line_no, " if has_line_no else "")}id
        """,
        (po_id,),
    ).fetchall()

    result = []
    for row in rows:
        received = float(row["received_qty"] or 0.0)
        billed = po_line_billed_qty(conn, row["id"])
        pending = max(received - billed, 0.0)
        if pending <= 0.000001:
            continue
        result.append(
            {
                "po_line_id": int(row["id"]),
                "item_id": row["item_id"],
                "description": safe(row["description"]) if has_desc else "",
                "unit_price": float(row["unit_price"] or 0.0) if has_price else 0.0,
                "pending_qty": pending,
                "received_qty": received,
                "billed_qty": billed,
                "line_no": int(row["line_no"]) if has_line_no and row["line_no"] is not None else 0,
            }
        )
    return result


ensure_workflow_tables()
