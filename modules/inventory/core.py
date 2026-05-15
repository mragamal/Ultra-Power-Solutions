from db import get_conn


def safe(x):
    return "" if x is None else str(x).strip()


def money(x):
    try:
        return f"{float(x or 0):,.2f}"
    except Exception:
        return "0.00"


def qty(x):
    try:
        return f"{float(x or 0):,.2f}"
    except Exception:
        return "0.00"


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


def ensure_inventory_tables():
    conn = get_conn()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            name TEXT NOT NULL,
            category TEXT,
            uom TEXT DEFAULT 'Unit',
            item_type TEXT DEFAULT 'stock_item',
            standard_cost REAL DEFAULT 0,
            sale_price REAL DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS warehouses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            name TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trans_date TEXT,
            trans_type TEXT,
            trans_no TEXT,
            reference_type TEXT,
            reference_id INTEGER,
            reference_line_id INTEGER,
            warehouse_id INTEGER,
            item_id INTEGER,
            description TEXT,
            qty_in REAL DEFAULT 0,
            qty_out REAL DEFAULT 0,
            unit_cost REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_uoms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            name TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_material_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_no TEXT UNIQUE,
            issue_date TEXT,
            work_order_id INTEGER,
            warehouse_id INTEGER,
            notes TEXT,
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_material_issue_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER,
            item_id INTEGER,
            qty REAL DEFAULT 0,
            unit_cost REAL DEFAULT 0,
            description TEXT
        )
        """
    )

    ensure_column(conn, "items", "code", "ALTER TABLE items ADD COLUMN code TEXT")
    ensure_column(conn, "items", "name", "ALTER TABLE items ADD COLUMN name TEXT")
    ensure_column(conn, "items", "category", "ALTER TABLE items ADD COLUMN category TEXT")
    ensure_column(conn, "items", "uom", "ALTER TABLE items ADD COLUMN uom TEXT DEFAULT 'Unit'")
    ensure_column(conn, "items", "item_type", "ALTER TABLE items ADD COLUMN item_type TEXT DEFAULT 'stock_item'")
    ensure_column(conn, "items", "standard_cost", "ALTER TABLE items ADD COLUMN standard_cost REAL DEFAULT 0")
    ensure_column(conn, "items", "sale_price", "ALTER TABLE items ADD COLUMN sale_price REAL DEFAULT 0")
    ensure_column(conn, "items", "is_active", "ALTER TABLE items ADD COLUMN is_active INTEGER DEFAULT 1")
    ensure_column(conn, "items", "created_at", "ALTER TABLE items ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "warehouses", "code", "ALTER TABLE warehouses ADD COLUMN code TEXT")
    ensure_column(conn, "warehouses", "name", "ALTER TABLE warehouses ADD COLUMN name TEXT")
    ensure_column(conn, "warehouses", "is_active", "ALTER TABLE warehouses ADD COLUMN is_active INTEGER DEFAULT 1")
    ensure_column(conn, "warehouses", "created_at", "ALTER TABLE warehouses ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "stock_ledger", "trans_date", "ALTER TABLE stock_ledger ADD COLUMN trans_date TEXT")
    ensure_column(conn, "stock_ledger", "trans_type", "ALTER TABLE stock_ledger ADD COLUMN trans_type TEXT")
    ensure_column(conn, "stock_ledger", "trans_no", "ALTER TABLE stock_ledger ADD COLUMN trans_no TEXT")
    ensure_column(conn, "stock_ledger", "reference_type", "ALTER TABLE stock_ledger ADD COLUMN reference_type TEXT")
    ensure_column(conn, "stock_ledger", "reference_id", "ALTER TABLE stock_ledger ADD COLUMN reference_id INTEGER")
    ensure_column(conn, "stock_ledger", "reference_line_id", "ALTER TABLE stock_ledger ADD COLUMN reference_line_id INTEGER")
    ensure_column(conn, "stock_ledger", "warehouse_id", "ALTER TABLE stock_ledger ADD COLUMN warehouse_id INTEGER")
    ensure_column(conn, "stock_ledger", "item_id", "ALTER TABLE stock_ledger ADD COLUMN item_id INTEGER")
    ensure_column(conn, "stock_ledger", "description", "ALTER TABLE stock_ledger ADD COLUMN description TEXT")
    ensure_column(conn, "stock_ledger", "qty_in", "ALTER TABLE stock_ledger ADD COLUMN qty_in REAL DEFAULT 0")
    ensure_column(conn, "stock_ledger", "qty_out", "ALTER TABLE stock_ledger ADD COLUMN qty_out REAL DEFAULT 0")
    ensure_column(conn, "stock_ledger", "unit_cost", "ALTER TABLE stock_ledger ADD COLUMN unit_cost REAL DEFAULT 0")
    ensure_column(conn, "stock_ledger", "created_at", "ALTER TABLE stock_ledger ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "inventory_uoms", "code", "ALTER TABLE inventory_uoms ADD COLUMN code TEXT")
    ensure_column(conn, "inventory_uoms", "name", "ALTER TABLE inventory_uoms ADD COLUMN name TEXT")
    ensure_column(conn, "inventory_uoms", "is_active", "ALTER TABLE inventory_uoms ADD COLUMN is_active INTEGER DEFAULT 1")

    ensure_column(conn, "inventory_material_issues", "issue_no", "ALTER TABLE inventory_material_issues ADD COLUMN issue_no TEXT")
    ensure_column(conn, "inventory_material_issues", "issue_date", "ALTER TABLE inventory_material_issues ADD COLUMN issue_date TEXT")
    ensure_column(conn, "inventory_material_issues", "work_order_id", "ALTER TABLE inventory_material_issues ADD COLUMN work_order_id INTEGER")
    ensure_column(conn, "inventory_material_issues", "warehouse_id", "ALTER TABLE inventory_material_issues ADD COLUMN warehouse_id INTEGER")
    ensure_column(conn, "inventory_material_issues", "notes", "ALTER TABLE inventory_material_issues ADD COLUMN notes TEXT")
    ensure_column(conn, "inventory_material_issues", "created_by", "ALTER TABLE inventory_material_issues ADD COLUMN created_by TEXT")
    ensure_column(conn, "inventory_material_issues", "created_at", "ALTER TABLE inventory_material_issues ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "inventory_material_issue_lines", "issue_id", "ALTER TABLE inventory_material_issue_lines ADD COLUMN issue_id INTEGER")
    ensure_column(conn, "inventory_material_issue_lines", "item_id", "ALTER TABLE inventory_material_issue_lines ADD COLUMN item_id INTEGER")
    ensure_column(conn, "inventory_material_issue_lines", "qty", "ALTER TABLE inventory_material_issue_lines ADD COLUMN qty REAL DEFAULT 0")
    ensure_column(conn, "inventory_material_issue_lines", "unit_cost", "ALTER TABLE inventory_material_issue_lines ADD COLUMN unit_cost REAL DEFAULT 0")
    ensure_column(conn, "inventory_material_issue_lines", "description", "ALTER TABLE inventory_material_issue_lines ADD COLUMN description TEXT")

    default_uoms = [
        ("UNIT", "Unit"),
        ("PCS", "Piece"),
        ("BOX", "Box"),
        ("PACK", "Pack"),
        ("CTN", "Carton"),
        ("KG", "Kilogram"),
        ("G", "Gram"),
        ("TON", "Ton"),
        ("L", "Liter"),
        ("ML", "Milliliter"),
        ("M", "Meter"),
        ("CM", "Centimeter"),
        ("MM", "Millimeter"),
        ("ROLL", "Roll"),
        ("SET", "Set"),
    ]
    for code, name in default_uoms:
        exists = conn.execute(
            """
            SELECT id
            FROM inventory_uoms
            WHERE UPPER(COALESCE(code, '')) = ?
               OR UPPER(COALESCE(name, '')) = ?
            LIMIT 1
            """,
            (code.upper(), name.upper()),
        ).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO inventory_uoms (code, name, is_active)
                VALUES (?, ?, 1)
                """,
                (code, name),
            )

    warehouse_count = conn.execute("SELECT COUNT(*) AS c FROM warehouses").fetchone()
    if int((warehouse_count["c"] if warehouse_count else 0) or 0) == 0:
        conn.execute(
            """
            INSERT INTO warehouses (code, name, is_active)
            VALUES ('WH-0001', 'Main Warehouse', 1)
            """
        )

    conn.commit()
    conn.close()


def get_uom_names(active_only=True):
    conn = get_conn()
    where_sql = "WHERE COALESCE(is_active, 1) = 1" if active_only else ""
    rows = conn.execute(
        f"""
        SELECT name
        FROM inventory_uoms
        {where_sql}
        ORDER BY name
        """
    ).fetchall()
    conn.close()
    return [safe(r["name"]) for r in rows if safe(r["name"])]


def ensure_uom_exists(uom_name):
    name = safe(uom_name)
    if not name:
        return
    conn = get_conn()
    exists = conn.execute(
        """
        SELECT id
        FROM inventory_uoms
        WHERE UPPER(COALESCE(name, '')) = UPPER(?)
        LIMIT 1
        """,
        (name,),
    ).fetchone()
    if not exists:
        code = name.upper().replace(" ", "_")[:20] or "UOM"
        code_exists = conn.execute(
            """
            SELECT id
            FROM inventory_uoms
            WHERE UPPER(COALESCE(code, '')) = UPPER(?)
            LIMIT 1
            """,
            (code,),
        ).fetchone()
        if code_exists:
            code = f"{code}_{int(code_exists['id']) + 1}"
        conn.execute(
            """
            INSERT INTO inventory_uoms (code, name, is_active)
            VALUES (?, ?, 1)
            """,
            (code, name),
        )
        conn.commit()
    conn.close()


def next_item_code():
    conn = get_conn()
    row = conn.execute(
        """
        SELECT code
        FROM items
        WHERE COALESCE(code, '') <> ''
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()

    last = safe(row["code"]) if row else ""
    if not last:
        return "ITM-0001"
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"ITM-{num + 1:04d}"


def next_warehouse_code():
    conn = get_conn()
    row = conn.execute(
        """
        SELECT code
        FROM warehouses
        WHERE COALESCE(code, '') <> ''
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()

    last = safe(row["code"]) if row else ""
    if not last:
        return "WH-0001"
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"WH-{num + 1:04d}"


def next_material_issue_no(conn=None):
    own_conn = conn is None
    if conn is None:
        conn = get_conn()

    row = conn.execute(
        """
        SELECT issue_no
        FROM inventory_material_issues
        WHERE COALESCE(issue_no, '') <> ''
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    if own_conn:
        conn.close()

    last = safe(row["issue_no"]) if row else ""
    if not last:
        return "MI-0001"
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"MI-{num + 1:04d}"


def item_display(conn, item_id):
    if not item_id or not table_exists(conn, "items"):
        return ""
    row = conn.execute(
        """
        SELECT code, name
        FROM items
        WHERE id = ?
        LIMIT 1
        """,
        (item_id,),
    ).fetchone()
    if not row:
        return ""
    code = safe(row["code"])
    name = safe(row["name"])
    return f"{code} - {name}" if code else name


def warehouse_display(conn, warehouse_id):
    if not warehouse_id or not table_exists(conn, "warehouses"):
        return ""
    row = conn.execute(
        """
        SELECT code, name
        FROM warehouses
        WHERE id = ?
        LIMIT 1
        """,
        (warehouse_id,),
    ).fetchone()
    if not row:
        return ""
    code = safe(row["code"])
    name = safe(row["name"])
    return f"{code} - {name}" if code else name


def record_stock_movement(
    conn,
    *,
    trans_date,
    trans_type,
    trans_no,
    reference_type,
    reference_id,
    reference_line_id=None,
    warehouse_id,
    item_id,
    description="",
    qty_in=0,
    qty_out=0,
    unit_cost=0,
):
    conn.execute(
        """
        INSERT INTO stock_ledger (
            trans_date, trans_type, trans_no, reference_type, reference_id, reference_line_id,
            warehouse_id, item_id, description, qty_in, qty_out, unit_cost
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            safe(trans_date),
            safe(trans_type),
            safe(trans_no),
            safe(reference_type),
            reference_id,
            reference_line_id,
            warehouse_id,
            item_id,
            safe(description),
            float(qty_in or 0),
            float(qty_out or 0),
            float(unit_cost or 0),
        ),
    )


def item_standard_cost(conn, item_id):
    if not item_id:
        return 0.0
    row = conn.execute(
        """
        SELECT standard_cost
        FROM items
        WHERE id = ?
        LIMIT 1
        """,
        (item_id,),
    ).fetchone()
    try:
        return float(row["standard_cost"] or 0) if row else 0.0
    except Exception:
        return 0.0


def sync_goods_receipts_to_stock():
    conn = get_conn()
    if not table_exists(conn, "goods_receipts") or not table_exists(conn, "goods_receipt_lines"):
        conn.close()
        return

    rows = conn.execute(
        """
        SELECT
            gr.id AS grn_id,
            gr.grn_no,
            gr.grn_date,
            gr.warehouse_id,
            grl.id AS grn_line_id,
            grl.item_id,
            grl.description,
            grl.accepted_qty
        FROM goods_receipts gr
        JOIN goods_receipt_lines grl ON grl.grn_id = gr.id
        WHERE COALESCE(grl.accepted_qty, 0) > 0
        ORDER BY gr.id, grl.id
        """
    ).fetchall()

    for row in rows:
        exists = conn.execute(
            """
            SELECT id
            FROM stock_ledger
            WHERE reference_type = 'goods_receipt'
              AND reference_id = ?
              AND reference_line_id = ?
            LIMIT 1
            """,
            (row["grn_id"], row["grn_line_id"]),
        ).fetchone()
        if exists:
            continue

        record_stock_movement(
            conn,
            trans_date=row["grn_date"],
            trans_type="goods_receipt",
            trans_no=row["grn_no"],
            reference_type="goods_receipt",
            reference_id=row["grn_id"],
            reference_line_id=row["grn_line_id"],
            warehouse_id=row["warehouse_id"],
            item_id=row["item_id"],
            description=row["description"] or f"Goods Receipt {row['grn_no']}",
            qty_in=row["accepted_qty"],
            qty_out=0,
            unit_cost=0,
        )

    conn.commit()
    conn.close()


def stock_balance_rows(item_id=None, warehouse_id=None):
    conn = get_conn()
    sql = """
        SELECT
            sl.item_id,
            i.code AS item_code,
            i.name AS item_name,
            sl.warehouse_id,
            w.code AS warehouse_code,
            w.name AS warehouse_name,
            SUM(COALESCE(sl.qty_in, 0) - COALESCE(sl.qty_out, 0)) AS balance_qty,
            SUM(
                (COALESCE(sl.qty_in, 0) - COALESCE(sl.qty_out, 0))
                * COALESCE(NULLIF(sl.unit_cost, 0), i.standard_cost, 0)
            ) AS stock_value
        FROM stock_ledger sl
        LEFT JOIN items i ON i.id = sl.item_id
        LEFT JOIN warehouses w ON w.id = sl.warehouse_id
        WHERE 1 = 1
    """
    params = []

    if item_id:
        sql += " AND sl.item_id = ?"
        params.append(item_id)
    if warehouse_id:
        sql += " AND sl.warehouse_id = ?"
        params.append(warehouse_id)

    sql += """
        GROUP BY sl.item_id, i.code, i.name, sl.warehouse_id, w.code, w.name
        HAVING ABS(SUM(COALESCE(sl.qty_in, 0) - COALESCE(sl.qty_out, 0))) > 0.000001
        ORDER BY i.code, i.name, w.code, w.name
    """

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


ensure_inventory_tables()
sync_goods_receipts_to_stock()
