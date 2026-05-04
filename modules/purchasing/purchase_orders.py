import json

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page
from modules.purchasing.workflow import (
    create_po_alert,
    ensure_workflow_tables,
    latest_po_alerts,
    po_pending_variance_count,
    po_billable_summary,
)
try:
    from auth import can as auth_can, current_user
except Exception:
    auth_can = None
    current_user = None

router = APIRouter()


def money(x):
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
    row = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name=?
    """, (table_name,)).fetchone()
    return bool(row)


def get_table_columns(conn, table_name):
    if not table_exists(conn, table_name):
        return []
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [r["name"] for r in rows]


def first_existing(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    return None


def build_name_expr(cols):
    direct = [c for c in ["name", "vendor_name", "item_name", "full_name"] if c in cols]
    if direct:
        if len(direct) == 1:
            return direct[0]
        return "COALESCE(" + ", ".join(direct) + ")"

    if "first_name" in cols and "last_name" in cols:
        return "TRIM(COALESCE(first_name,'') || ' ' || COALESCE(last_name,''))"

    for c in cols:
        if "name" in c.lower():
            return c
    return None


def build_code_expr(cols):
    for c in ["code", "vendor_code", "item_code", "sku"]:
        if c in cols:
            return c
    return "''"


def vendor_table_name(conn):
    if table_exists(conn, "vendors"):
        return "vendors"
    if table_exists(conn, "partners"):
        return "partners"
    return None


def can_do(request: Request, action: str = "view") -> bool:
    if auth_can is None:
        return True
    try:
        return bool(auth_can(request, "purchasing", action))
    except Exception:
        return True


def actor_name(request: Request) -> str:
    if current_user is None:
        return "admin"
    try:
        user = current_user(request)
        if not user:
            return "admin"
        return user.get("username") or user.get("full_name") or "admin"
    except Exception:
        return "admin"


def approval_chip(status: str):
    s = (status or "").lower()
    if s == "approved":
        return '<span class="status-chip green">Approved</span>'
    if s == "pending":
        return '<span class="status-chip orange">Pending</span>'
    if s == "rejected":
        return '<span class="status-chip red">Rejected</span>'
    return '<span class="status-chip blue">Draft</span>'


def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            po_no TEXT UNIQUE,
            po_date TEXT,
            vendor_id INTEGER,
            warehouse_id INTEGER,
            status TEXT DEFAULT 'draft',
            approval_status TEXT DEFAULT 'approved',
            submitted_by TEXT,
            submitted_at TEXT,
            approved_by TEXT,
            approved_at TEXT,
            rejected_by TEXT,
            rejected_at TEXT,
            rejection_note TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS purchase_order_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            po_id INTEGER NOT NULL,
            line_no INTEGER DEFAULT 1,
            item_id INTEGER,
            description TEXT,
            ordered_qty REAL DEFAULT 0,
            received_qty REAL DEFAULT 0,
            open_qty REAL DEFAULT 0,
            unit_price REAL DEFAULT 0,
            line_total REAL DEFAULT 0,
            status TEXT DEFAULT 'open'
        )
    """)

    ensure_column(conn, "purchase_orders", "po_no", "ALTER TABLE purchase_orders ADD COLUMN po_no TEXT")
    ensure_column(conn, "purchase_orders", "po_date", "ALTER TABLE purchase_orders ADD COLUMN po_date TEXT")
    ensure_column(conn, "purchase_orders", "vendor_id", "ALTER TABLE purchase_orders ADD COLUMN vendor_id INTEGER")
    ensure_column(conn, "purchase_orders", "warehouse_id", "ALTER TABLE purchase_orders ADD COLUMN warehouse_id INTEGER")
    ensure_column(conn, "purchase_orders", "status", "ALTER TABLE purchase_orders ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "purchase_orders", "approval_status", "ALTER TABLE purchase_orders ADD COLUMN approval_status TEXT DEFAULT 'approved'")
    ensure_column(conn, "purchase_orders", "submitted_by", "ALTER TABLE purchase_orders ADD COLUMN submitted_by TEXT")
    ensure_column(conn, "purchase_orders", "submitted_at", "ALTER TABLE purchase_orders ADD COLUMN submitted_at TEXT")
    ensure_column(conn, "purchase_orders", "approved_by", "ALTER TABLE purchase_orders ADD COLUMN approved_by TEXT")
    ensure_column(conn, "purchase_orders", "approved_at", "ALTER TABLE purchase_orders ADD COLUMN approved_at TEXT")
    ensure_column(conn, "purchase_orders", "rejected_by", "ALTER TABLE purchase_orders ADD COLUMN rejected_by TEXT")
    ensure_column(conn, "purchase_orders", "rejected_at", "ALTER TABLE purchase_orders ADD COLUMN rejected_at TEXT")
    ensure_column(conn, "purchase_orders", "rejection_note", "ALTER TABLE purchase_orders ADD COLUMN rejection_note TEXT")
    ensure_column(conn, "purchase_orders", "notes", "ALTER TABLE purchase_orders ADD COLUMN notes TEXT")
    ensure_column(conn, "purchase_orders", "created_at", "ALTER TABLE purchase_orders ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "purchase_order_lines", "po_id", "ALTER TABLE purchase_order_lines ADD COLUMN po_id INTEGER")
    ensure_column(conn, "purchase_order_lines", "line_no", "ALTER TABLE purchase_order_lines ADD COLUMN line_no INTEGER DEFAULT 1")
    ensure_column(conn, "purchase_order_lines", "item_id", "ALTER TABLE purchase_order_lines ADD COLUMN item_id INTEGER")
    ensure_column(conn, "purchase_order_lines", "description", "ALTER TABLE purchase_order_lines ADD COLUMN description TEXT")
    ensure_column(conn, "purchase_order_lines", "ordered_qty", "ALTER TABLE purchase_order_lines ADD COLUMN ordered_qty REAL DEFAULT 0")
    ensure_column(conn, "purchase_order_lines", "received_qty", "ALTER TABLE purchase_order_lines ADD COLUMN received_qty REAL DEFAULT 0")
    ensure_column(conn, "purchase_order_lines", "open_qty", "ALTER TABLE purchase_order_lines ADD COLUMN open_qty REAL DEFAULT 0")
    ensure_column(conn, "purchase_order_lines", "unit_price", "ALTER TABLE purchase_order_lines ADD COLUMN unit_price REAL DEFAULT 0")
    ensure_column(conn, "purchase_order_lines", "line_total", "ALTER TABLE purchase_order_lines ADD COLUMN line_total REAL DEFAULT 0")
    ensure_column(conn, "purchase_order_lines", "status", "ALTER TABLE purchase_order_lines ADD COLUMN status TEXT DEFAULT 'open'")

    conn.commit()
    conn.close()


ensure_tables()
ensure_workflow_tables()


def next_po_no():
    conn = get_conn()
    row = conn.execute("""
        SELECT po_no
        FROM purchase_orders
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["po_no"]:
        return "PO-0001"

    last = str(row["po_no"])
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"PO-{num + 1:04d}"


def vendor_records():
    conn = get_conn()
    table_name = vendor_table_name(conn)
    if not table_name:
        conn.close()
        return []

    cols = get_table_columns(conn, table_name)
    name_expr = build_name_expr(cols)
    code_expr = build_code_expr(cols)

    if not name_expr:
        conn.close()
        return []

    where_sql = ""
    if table_name == "partners" and "partner_type" in cols:
        where_sql += " WHERE LOWER(COALESCE(partner_type,'')) = 'vendor'"
        if "is_active" in cols:
            where_sql += " AND COALESCE(is_active,1) = 1"

    rows = conn.execute(f"""
        SELECT id, {code_expr} AS code, {name_expr} AS name
        FROM {table_name}
        {where_sql}
        ORDER BY name
    """).fetchall()
    conn.close()

    result = []
    for r in rows:
        name = (r["name"] or "").strip()
        code = (r["code"] or "").strip()
        if not name:
            continue
        label = f"{code} - {name}" if code else name
        result.append({"id": str(r["id"]), "label": label})
    return result


def item_records():
    conn = get_conn()

    # يشتغل لاحقًا مع المخزن سواء كان الجدول items أو inventory_items
    table_name = None
    if table_exists(conn, "items"):
        table_name = "items"
    elif table_exists(conn, "inventory_items"):
        table_name = "inventory_items"

    if not table_name:
        conn.close()
        return []

    cols = get_table_columns(conn, table_name)
    name_expr = build_name_expr(cols)
    code_expr = build_code_expr(cols)

    if not name_expr:
        conn.close()
        return []

    rows = conn.execute(f"""
        SELECT id, {code_expr} AS code, {name_expr} AS name
        FROM {table_name}
        ORDER BY name
    """).fetchall()
    conn.close()

    result = []
    for r in rows:
        name = (r["name"] or "").strip()
        code = (r["code"] or "").strip()
        if not name:
            continue
        label = f"{code} - {name}" if code else name
        result.append({"id": str(r["id"]), "label": label})
    return result


def warehouse_records():
    conn = get_conn()
    if not table_exists(conn, "warehouses"):
        conn.close()
        return []

    cols = get_table_columns(conn, "warehouses")
    name_expr = build_name_expr(cols)
    code_expr = build_code_expr(cols)

    if not name_expr:
        conn.close()
        return []

    rows = conn.execute(f"""
        SELECT id, {code_expr} AS code, {name_expr} AS name
        FROM warehouses
        ORDER BY name
    """).fetchall()
    conn.close()

    result = []
    for r in rows:
        name = (r["name"] or "").strip()
        code = (r["code"] or "").strip()
        if not name:
            continue
        label = f"{code} - {name}" if code else name
        result.append({"id": str(r["id"]), "label": label})
    return result


def vendor_display(conn, vendor_id):
    table_name = vendor_table_name(conn)
    if not vendor_id or not table_name:
        return ""

    cols = get_table_columns(conn, table_name)
    name_expr = build_name_expr(cols)
    code_expr = build_code_expr(cols)

    if not name_expr:
        return ""

    where_sql = "WHERE id = ?"
    if table_name == "partners" and "partner_type" in cols:
        where_sql += " AND LOWER(COALESCE(partner_type,'')) = 'vendor'"

    row = conn.execute(f"""
        SELECT {code_expr} AS code, {name_expr} AS name
        FROM {table_name}
        {where_sql}
        LIMIT 1
    """, (vendor_id,)).fetchone()

    if not row:
        return ""

    code = (row["code"] or "").strip()
    name = (row["name"] or "").strip()
    if not name:
        return ""
    return f"{code} - {name}" if code else name


def warehouse_display(conn, warehouse_id):
    if not warehouse_id or not table_exists(conn, "warehouses"):
        return ""

    cols = get_table_columns(conn, "warehouses")
    name_expr = build_name_expr(cols)
    code_expr = build_code_expr(cols)

    if not name_expr:
        return ""

    row = conn.execute(f"""
        SELECT {code_expr} AS code, {name_expr} AS name
        FROM warehouses
        WHERE id = ?
        LIMIT 1
    """, (warehouse_id,)).fetchone()

    if not row:
        return ""

    code = (row["code"] or "").strip()
    name = (row["name"] or "").strip()
    if not name:
        return ""
    return f"{code} - {name}" if code else name


def item_display(conn, item_id):
    table_name = None
    if table_exists(conn, "items"):
        table_name = "items"
    elif table_exists(conn, "inventory_items"):
        table_name = "inventory_items"

    if not item_id or not table_name:
        return ""

    cols = get_table_columns(conn, table_name)
    name_expr = build_name_expr(cols)
    code_expr = build_code_expr(cols)

    if not name_expr:
        return ""

    row = conn.execute(f"""
        SELECT {code_expr} AS code, {name_expr} AS name
        FROM {table_name}
        WHERE id = ?
        LIMIT 1
    """, (item_id,)).fetchone()

    if not row:
        return ""

    code = (row["code"] or "").strip()
    name = (row["name"] or "").strip()
    if not name:
        return ""
    return f"{code} - {name}" if code else name


def recalc_po_status(conn, po_id):
    po = conn.execute("""
        SELECT approval_status
        FROM purchase_orders
        WHERE id = ?
        LIMIT 1
    """, (po_id,)).fetchone()
    approval_status = (po["approval_status"] or "").lower() if po else ""

    lines = conn.execute("""
        SELECT ordered_qty, received_qty, open_qty, status
        FROM purchase_order_lines
        WHERE po_id = ?
    """, (po_id,)).fetchall()

    if not lines:
        conn.execute("""
            UPDATE purchase_orders
            SET status = 'draft'
            WHERE id = ?
        """, (po_id,))
        return

    if approval_status != "approved":
        conn.execute("""
            UPDATE purchase_orders
            SET status = 'draft'
            WHERE id = ?
        """, (po_id,))
        return

    total_received = sum(float(l["received_qty"] or 0) for l in lines)
    total_open = sum(float(l["open_qty"] or 0) for l in lines)

    if total_received <= 0:
        status = "open"
    elif total_open > 0:
        status = "partial_received"
    else:
        status = "received"

    conn.execute("""
        UPDATE purchase_orders
        SET status = ?
        WHERE id = ?
    """, (status, po_id))


def datalist_options_by_id(items):
    return "".join([
        f"<option value=\"{item['label']}\" data-id=\"{item['id']}\"></option>"
        for item in items
    ])


def datalist_script():
    return """
    <script>
    function bindDatalistInput(inputId, hiddenId, listId, attrName) {
        const input = document.getElementById(inputId);
        const hidden = document.getElementById(hiddenId);
        const list = document.getElementById(listId);
        if (!input || !hidden || !list) return;

        function syncHidden() {
            const val = input.value.trim();
            hidden.value = "";
            const options = list.querySelectorAll("option");
            for (const opt of options) {
                if ((opt.value || "").trim() === val) {
                    hidden.value = opt.getAttribute(attrName) || "";
                    break;
                }
            }
        }

        input.addEventListener("input", syncHidden);
        input.addEventListener("change", syncHidden);
        input.addEventListener("blur", syncHidden);
    }

    window.addEventListener("DOMContentLoaded", function() {
        bindDatalistInput("vendor_label", "vendor_id", "vendor_list", "data-id");
        bindDatalistInput("warehouse_label", "warehouse_id", "warehouse_list", "data-id");
    });
    </script>
    """


def po_form_html(action_url, values=None, lines=None, title="New Purchase Order", info_msg=""):
    values = values or {}
    lines = lines or []
    vendors = vendor_records()
    warehouses = warehouse_records()
    items = item_records()

    vendor_map = {str(v["id"]): v["label"] for v in vendors}
    warehouse_map = {str(w["id"]): w["label"] for w in warehouses}
    item_map = {str(i["id"]): i["label"] for i in items}

    vendor_id = str(values.get("vendor_id") or "")
    warehouse_id = str(values.get("warehouse_id") or "")
    vendor_label = vendor_map.get(vendor_id, "")
    warehouse_label = warehouse_map.get(warehouse_id, "")

    normalized_lines = []
    if lines:
        for idx, line in enumerate(lines, start=0):
            iid = str(line.get("item_id") or "")
            qty_val = float(line.get("qty") or 0)
            price_val = float(line.get("unit_price") or 0)
            normalized_lines.append(
                {
                    "idx": idx,
                    "item_id": iid,
                    "item_label": item_map.get(iid, ""),
                    "description": line.get("description") or "",
                    "qty": f"{qty_val:.2f}",
                    "price": f"{price_val:.2f}",
                }
            )
    else:
        normalized_lines = [
            {"idx": 0, "item_id": "", "item_label": "", "description": "", "qty": "1.00", "price": "0.00"}
        ]

    msg_html = f"<div class='msg ok'>{info_msg}</div>" if info_msg else ""

    html = f"""
    <div class="card">
        <h2>{title}</h2>
        {msg_html}

        <form method="post" action="{action_url}">
            <div class="row">
                <div class="col">
                    <label>PO No</label>
                    <input name="po_no" value="{values.get('po_no') or next_po_no()}" required>
                </div>
                <div class="col">
                    <label>PO Date</label>
                    <input type="date" name="po_date" value="{values.get('po_date') or ''}" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Vendor</label>
                    <input id="vendor_label" list="vendor_list" autocomplete="off" placeholder="Search vendor..." value="{vendor_label}">
                    <input type="hidden" id="vendor_id" name="vendor_id" value="{vendor_id}">
                    <datalist id="vendor_list">
                        {datalist_options_by_id(vendors)}
                    </datalist>
                </div>
                <div class="col">
                    <label>Warehouse</label>
                    <input id="warehouse_label" list="warehouse_list" autocomplete="off" placeholder="Search warehouse..." value="{warehouse_label}">
                    <input type="hidden" id="warehouse_id" name="warehouse_id" value="{warehouse_id}">
                    <datalist id="warehouse_list">
                        {datalist_options_by_id(warehouses)}
                    </datalist>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Notes</label>
                    <input name="notes" value="{values.get('notes') or ''}">
                </div>
                <div class="col"></div>
            </div>

            <div class="card" style="margin-top:18px;">
                <h3>PO Lines</h3>

                <table id="po_lines_table">
                    <thead>
                        <tr>
                            <th>Item</th>
                            <th>Description</th>
                            <th>Qty</th>
                            <th>Unit Price</th>
                            <th>Total</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody id="po_lines_body"></tbody>
                </table>

                <div style="margin-top:12px;">
                    <button type="button" class="btn blue" onclick="addLine()">Add Line</button>
                </div>
            </div>

            <div style="margin-top:18px;">
                <button class="btn green" type="submit">Save</button>
                <a class="btn gray" href="/ui/purchasing/purchase-orders">Back</a>
            </div>
        </form>
    </div>

    <datalist id="item_list">
        {datalist_options_by_id(items)}
    </datalist>

    <script>
    let poLineIndex = 0;
    const initialLines = {json.dumps(normalized_lines)};

    function bindDatalistInputEl(input, hidden, listId, attrName) {{
        const list = document.getElementById(listId);
        if (!input || !hidden || !list) return;

        function syncHidden() {{
            const val = (input.value || "").trim();
            hidden.value = "";
            const options = list.querySelectorAll("option");
            let startsWithMatch = null;
            for (const opt of options) {{
                const optVal = (opt.value || "").trim();
                if (optVal === val) {{
                    hidden.value = opt.getAttribute(attrName) || "";
                    break;
                }}
                if (!startsWithMatch && val && optVal.toLowerCase().startsWith(val.toLowerCase())) {{
                    startsWithMatch = opt.getAttribute(attrName) || "";
                }}
            }}
            if (!hidden.value && startsWithMatch) {{
                hidden.value = startsWithMatch;
            }}
        }}

        input.addEventListener("input", syncHidden);
        input.addEventListener("change", syncHidden);
        input.addEventListener("blur", syncHidden);
    }}

    function bindStaticDatalists() {{
        bindDatalistInputEl(
            document.getElementById("vendor_label"),
            document.getElementById("vendor_id"),
            "vendor_list",
            "data-id"
        );
        bindDatalistInputEl(
            document.getElementById("warehouse_label"),
            document.getElementById("warehouse_id"),
            "warehouse_list",
            "data-id"
        );
    }}

    function recalcRow(row) {{
        const qty = parseFloat(row.querySelector(".line-qty")?.value || "0") || 0;
        const price = parseFloat(row.querySelector(".line-price")?.value || "0") || 0;
        row.querySelector(".line-total").value = (qty * price).toFixed(2);
    }}

    function bindLine(row) {{
        const itemInput = row.querySelector(".item-label");
        const itemHidden = row.querySelector(".item-id");
        bindDatalistInputEl(itemInput, itemHidden, "item_list", "data-id");

        row.querySelectorAll(".line-qty, .line-price").forEach((el) => {{
            el.addEventListener("input", () => recalcRow(row));
            el.addEventListener("change", () => recalcRow(row));
            el.addEventListener("blur", () => recalcRow(row));
        }});
    }}

    function addLine(lineData = null) {{
        const tbody = document.getElementById("po_lines_body");
        const row = document.createElement("tr");
        const i = poLineIndex;

        const itemId = lineData && lineData.item_id ? lineData.item_id : "";
        const itemLabel = lineData && lineData.item_label ? lineData.item_label : "";
        const desc = lineData && lineData.description ? lineData.description : "";
        const qty = lineData && lineData.qty ? lineData.qty : "1.00";
        const price = lineData && lineData.price ? lineData.price : "0.00";

        row.innerHTML = `
            <td>
                <input class="item-label" name="item_label_${{i}}" list="item_list" autocomplete="off" placeholder="Search item..." value="${{itemLabel}}">
                <input type="hidden" class="item-id" name="item_id_${{i}}" value="${{itemId}}">
            </td>
            <td><input name="description_${{i}}" value="${{desc}}"></td>
            <td><input type="number" step="0.01" name="qty_${{i}}" class="line-qty" value="${{qty}}"></td>
            <td><input type="number" step="0.01" name="price_${{i}}" class="line-price" value="${{price}}"></td>
            <td><input class="line-total" value="0.00" readonly></td>
            <td><button type="button" class="btn red" onclick="this.closest('tr').remove()">X</button></td>
        `;

        tbody.appendChild(row);
        bindLine(row);
        recalcRow(row);
        poLineIndex++;
    }}

    window.addEventListener("DOMContentLoaded", function() {{
        bindStaticDatalists();
        if (initialLines.length > 0) {{
            initialLines.forEach(l => addLine(l));
        }} else {{
            addLine();
        }}
    }});
    </script>
    """

    return html


def float_or_zero(value):
    try:
        return float((value or "").strip() or 0)
    except Exception:
        try:
            return float(value or 0)
        except Exception:
            return 0.0


def parse_line_indices(form):
    indices = []
    for key in form.keys():
        if not str(key).startswith("item_id_"):
            continue
        idx = str(key).split("_")[-1]
        if idx.isdigit():
            indices.append(int(idx))
    return sorted(set(indices))


def resolve_item_id(item_lookup, typed_label):
    label = (typed_label or "").strip().lower()
    if not label:
        return ""
    if label in item_lookup:
        return str(item_lookup[label])

    matches = []
    for item_label, item_id in item_lookup.items():
        if item_label.startswith(label):
            matches.append(item_id)

    uniq = sorted(set(matches))
    if len(uniq) == 1:
        return str(uniq[0])
    return ""


def extract_po_lines(form):
    raw_items = item_records()
    item_lookup = {}
    for rec in raw_items:
        key = (rec.get("label") or "").strip().lower()
        if key and key not in item_lookup:
            item_lookup[key] = int(rec["id"])

    lines = []
    for idx in parse_line_indices(form):
        item_id = (form.get(f"item_id_{idx}") or "").strip()
        item_label = (form.get(f"item_label_{idx}") or "").strip()
        if not item_id and item_label:
            item_id = resolve_item_id(item_lookup, item_label)

        description = (form.get(f"description_{idx}") or "").strip()
        qty = float_or_zero(form.get(f"qty_{idx}"))
        price = float_or_zero(form.get(f"price_{idx}"))

        if not item_id or qty <= 0:
            continue

        lines.append(
            {
                "item_id": int(item_id),
                "description": description,
                "qty": qty,
                "unit_price": price,
                "line_total": qty * price,
            }
        )

    return lines


def po_received_total(conn, po_id):
    row = conn.execute(
        """
        SELECT COALESCE(SUM(received_qty),0) AS total_received
        FROM purchase_order_lines
        WHERE po_id = ?
        """,
        (po_id,),
    ).fetchone()
    return float(row["total_received"] or 0.0) if row else 0.0


def insert_po_lines(conn, po_id, lines):
    for line_no, line in enumerate(lines, start=1):
        conn.execute(
            """
            INSERT INTO purchase_order_lines (
                po_id, line_no, item_id, description,
                ordered_qty, received_qty, open_qty,
                unit_price, line_total, status
            )
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, 'open')
            """,
            (
                po_id,
                line_no,
                int(line["item_id"]),
                line["description"],
                float(line["qty"]),
                float(line["qty"]),
                float(line["unit_price"]),
                float(line["line_total"]),
            ),
        )


@router.get("/ui/purchasing/purchase-orders", response_class=HTMLResponse)
def po_list(request: Request):
    conn = get_conn()
    rows = conn.execute("""
        SELECT *
        FROM purchase_orders
        ORDER BY id DESC
    """).fetchall()
    alerts = latest_po_alerts(conn, limit=12, only_unread=True)

    alerts_html = ""
    for a in alerts:
        sev = (a["severity"] or "info").lower()
        color = "#0f766e" if sev == "success" else ("#b45309" if sev == "warning" else "#1d4ed8")
        link_part = f"<a class='btn gray' href='/ui/purchasing/purchase-orders/{a['po_id']}'>Open PO</a>" if a["po_id"] else ""
        variance_part = (
            f"<a class='btn gray' href='/ui/purchasing/po-variances?po_id={a['po_id']}'>Review Variances</a>"
            if (a["alert_type"] or "").lower() == "variance" and a["po_id"]
            else ""
        )
        alerts_html += f"""
        <div style="border:1px solid #dbeafe; border-left:4px solid {color}; padding:10px 12px; border-radius:10px; margin-bottom:8px;">
            <div style="display:flex; justify-content:space-between; gap:10px; align-items:flex-start; flex-wrap:wrap;">
                <div>
                    <div style="font-weight:700;">{a['title'] or ''}</div>
                    <div style="color:#374151;">{a['message'] or ''}</div>
                    <div style="font-size:12px;color:#6b7280;">{a['created_at'] or ''}</div>
                </div>
                <div style="display:flex; gap:6px; flex-wrap:wrap;">
                    {link_part}
                    {variance_part}
                    <form method="post" action="/ui/purchasing/workflow-alerts/{a['id']}/read" style="display:inline;">
                        <button class="btn gray" type="submit">Mark Read</button>
                    </form>
                </div>
            </div>
        </div>
        """

    if not alerts_html:
        alerts_html = "<div style='color:#6b7280;'>No unread workflow alerts.</div>"

    body = ""
    for r in rows:
        total_row = conn.execute("""
            SELECT COALESCE(SUM(line_total),0) AS total_amount
            FROM purchase_order_lines
            WHERE po_id = ?
        """, (r["id"],)).fetchone()
        total_received_row = conn.execute("""
            SELECT COALESCE(SUM(received_qty),0) AS total_received
            FROM purchase_order_lines
            WHERE po_id = ?
        """, (r["id"],)).fetchone()
        total_received = float(total_received_row["total_received"] or 0)

        pending_variances = po_pending_variance_count(conn, r["id"])
        billable = po_billable_summary(conn, r["id"])
        approval_status = (r["approval_status"] or "draft").lower()

        alerts_cell = ""
        if pending_variances > 0:
            alerts_cell += f"<span class='status-chip orange'>Variance Pending: {pending_variances}</span> "
        if billable["line_count"] > 0:
            alerts_cell += f"<span class='status-chip blue'>Ready for Bill ({billable['line_count']} lines)</span>"
        if alerts_cell.strip() == "":
            alerts_cell = "<span style='color:#6b7280;'>-</span>"

        actions = f"<a class='btn gray' href='/ui/purchasing/purchase-orders/{r['id']}'>Open</a>"
        if total_received <= 0.000001:
            actions += f" <a class='btn blue' href='/ui/purchasing/purchase-orders/{r['id']}/edit'>Edit</a>"
        if approval_status in ("draft", "rejected"):
            actions += (
                f" <form method='post' action='/ui/purchasing/purchase-orders/{r['id']}/submit-approval' style='display:inline;'>"
                f"<button class='btn green' type='submit'>Submit</button></form>"
            )
        if approval_status == "pending" and can_do(request, "approve"):
            actions += (
                f" <form method='post' action='/ui/purchasing/purchase-orders/{r['id']}/approve' style='display:inline;'>"
                f"<button class='btn green' type='submit'>Approve</button></form>"
            )
            actions += (
                f" <form method='post' action='/ui/purchasing/purchase-orders/{r['id']}/reject' style='display:inline;'>"
                f"<button class='btn red' type='submit'>Reject</button></form>"
            )

        if pending_variances > 0:
            actions += f" <a class='btn gray' href='/ui/purchasing/po-variances?po_id={r['id']}'>Variances</a>"
        if approval_status == "approved" and billable["line_count"] > 0:
            actions += f" <a class='btn green' href='/ui/accounting/vendor-bills/new?po_id={r['id']}'>Create Bill</a>"

        body += f"""
        <tr>
            <td><a class="btn gray" href="/ui/purchasing/purchase-orders/{r['id']}">{r['po_no'] or ''}</a></td>
            <td>{r['po_date'] or ''}</td>
            <td>{vendor_display(conn, r['vendor_id'])}</td>
            <td>{warehouse_display(conn, r['warehouse_id'])}</td>
            <td>{r['status'] or ''}</td>
            <td>{approval_chip(r['approval_status'] or 'draft')}</td>
            <td>{money(total_row['total_amount'])}</td>
            <td>{alerts_cell}</td>
            <td>{actions}</td>
        </tr>
        """

    conn.close()

    html = f"""
    <div class="card">
        <h2>Purchasing Workflow Alerts</h2>
        {alerts_html}
    </div>

    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>Purchase Orders</h2>
            <a class="btn green" href="/ui/purchasing/purchase-orders/new">New PO</a>
        </div>
    </div>

    <div class="card">
        <table>
            <tr>
                <th>PO No</th>
                <th>Date</th>
                <th>Vendor</th>
                <th>Warehouse</th>
                <th>Status</th>
                <th>Approval</th>
                <th>Total</th>
                <th>Workflow</th>
                <th>Actions</th>
            </tr>
            {body}
        </table>
    </div>
    """

    return HTMLResponse(render_page("Purchase Orders", html, "en", current_path=request.url.path))


@router.get("/ui/purchasing/purchase-orders/new", response_class=HTMLResponse)
def new_po_form(request: Request):
    if not can_do(request, "create"):
        return HTMLResponse("Not allowed.", status_code=403)

    html = po_form_html(
        "/ui/purchasing/purchase-orders/new",
        values={},
        lines=[],
        title="New Purchase Order",
    )
    return HTMLResponse(render_page("New PO", html, "en", current_path=request.url.path))


@router.post("/ui/purchasing/purchase-orders/new")
async def create_po(request: Request):
    if not can_do(request, "create"):
        return HTMLResponse("Not allowed.", status_code=403)

    form = await request.form()

    po_no = (form.get("po_no") or "").strip()
    po_date = (form.get("po_date") or "").strip()
    vendor_id = (form.get("vendor_id") or "").strip()
    warehouse_id = (form.get("warehouse_id") or "").strip()
    notes = (form.get("notes") or "").strip()

    if not po_no:
        return HTMLResponse("PO No is required.", status_code=400)
    if not po_date:
        return HTMLResponse("PO Date is required.", status_code=400)
    if not vendor_id:
        return HTMLResponse("Vendor is required.", status_code=400)

    lines = extract_po_lines(form)
    if len(lines) == 0:
        return HTMLResponse("Please add at least one valid line.", status_code=400)

    conn = get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO purchase_orders (
                po_no, po_date, vendor_id, warehouse_id, status, approval_status, notes
            )
            VALUES (?, ?, ?, ?, 'draft', 'draft', ?)
            """,
            (
                po_no,
                po_date,
                int(vendor_id),
                int(warehouse_id) if warehouse_id else None,
                notes,
            ),
        )
        po_id = cur.lastrowid
        insert_po_lines(conn, po_id, lines)
        recalc_po_status(conn, po_id)
        create_po_alert(
            conn,
            po_id=po_id,
            alert_type="approval",
            title="PO Draft Created",
            message=f"{po_no} created by {actor_name(request)}. Submit it for approval.",
            severity="info",
        )
        conn.commit()
    except Exception as ex:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Save error: {str(ex)}", status_code=500)
    conn.close()

    return RedirectResponse(f"/ui/purchasing/purchase-orders/{po_id}", status_code=302)


@router.get("/ui/purchasing/purchase-orders/{po_id}/edit", response_class=HTMLResponse)
def edit_po_form(request: Request, po_id: int):
    if not can_do(request, "edit"):
        return HTMLResponse("Not allowed.", status_code=403)

    conn = get_conn()
    po = conn.execute(
        """
        SELECT *
        FROM purchase_orders
        WHERE id = ?
        LIMIT 1
        """,
        (po_id,),
    ).fetchone()

    if not po:
        conn.close()
        return HTMLResponse("PO not found", status_code=404)

    received_total = po_received_total(conn, po_id)
    if received_total > 0.000001:
        conn.close()
        html = """
        <div class="card">
            <h2>Edit Purchase Order</h2>
            <p>Editing is blocked because this PO already has warehouse receipts.</p>
            <div style="margin-top:12px;">
                <a class="btn gray" href="/ui/purchasing/purchase-orders">Back</a>
            </div>
        </div>
        """
        return HTMLResponse(render_page("Edit PO", html, "en", current_path=request.url.path))

    rows = conn.execute(
        """
        SELECT *
        FROM purchase_order_lines
        WHERE po_id = ?
        ORDER BY line_no, id
        """,
        (po_id,),
    ).fetchall()
    conn.close()

    form_values = {
        "po_no": po["po_no"] or "",
        "po_date": po["po_date"] or "",
        "vendor_id": po["vendor_id"] or "",
        "warehouse_id": po["warehouse_id"] or "",
        "notes": po["notes"] or "",
    }

    form_lines = []
    for r in rows:
        form_lines.append(
            {
                "item_id": r["item_id"] or "",
                "description": r["description"] or "",
                "qty": float(r["ordered_qty"] or 0),
                "unit_price": float(r["unit_price"] or 0),
            }
        )

    info = ""
    if (po["approval_status"] or "").lower() == "approved":
        info = "After save, approval status will reset to Draft and needs re-approval."

    html = po_form_html(
        f"/ui/purchasing/purchase-orders/{po_id}/edit",
        values=form_values,
        lines=form_lines,
        title=f"Edit Purchase Order {po['po_no'] or ''}",
        info_msg=info,
    )
    return HTMLResponse(render_page("Edit PO", html, "en", current_path=request.url.path))


@router.post("/ui/purchasing/purchase-orders/{po_id}/edit")
async def edit_po_submit(request: Request, po_id: int):
    if not can_do(request, "edit"):
        return HTMLResponse("Not allowed.", status_code=403)

    form = await request.form()
    po_no = (form.get("po_no") or "").strip()
    po_date = (form.get("po_date") or "").strip()
    vendor_id = (form.get("vendor_id") or "").strip()
    warehouse_id = (form.get("warehouse_id") or "").strip()
    notes = (form.get("notes") or "").strip()

    if not po_no:
        return HTMLResponse("PO No is required.", status_code=400)
    if not po_date:
        return HTMLResponse("PO Date is required.", status_code=400)
    if not vendor_id:
        return HTMLResponse("Vendor is required.", status_code=400)

    lines = extract_po_lines(form)
    if len(lines) == 0:
        return HTMLResponse("Please add at least one valid line.", status_code=400)

    conn = get_conn()
    po = conn.execute(
        """
        SELECT *
        FROM purchase_orders
        WHERE id = ?
        LIMIT 1
        """,
        (po_id,),
    ).fetchone()
    if not po:
        conn.close()
        return HTMLResponse("PO not found", status_code=404)

    received_total = po_received_total(conn, po_id)
    if received_total > 0.000001:
        conn.close()
        return HTMLResponse("Cannot edit this PO after receiving goods.", status_code=400)

    try:
        conn.execute(
            """
            UPDATE purchase_orders
            SET po_no = ?,
                po_date = ?,
                vendor_id = ?,
                warehouse_id = ?,
                notes = ?,
                status = 'draft',
                approval_status = 'draft',
                submitted_by = NULL,
                submitted_at = NULL,
                approved_by = NULL,
                approved_at = NULL,
                rejected_by = NULL,
                rejected_at = NULL,
                rejection_note = NULL
            WHERE id = ?
            """,
            (
                po_no,
                po_date,
                int(vendor_id),
                int(warehouse_id) if warehouse_id else None,
                notes,
                po_id,
            ),
        )
        conn.execute("DELETE FROM purchase_order_lines WHERE po_id = ?", (po_id,))
        insert_po_lines(conn, po_id, lines)
        recalc_po_status(conn, po_id)
        create_po_alert(
            conn,
            po_id=po_id,
            alert_type="approval",
            title="PO Updated",
            message=f"{po_no} updated by {actor_name(request)}. Re-submit for approval.",
            severity="warning",
        )
        conn.commit()
    except Exception as ex:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Save error: {str(ex)}", status_code=500)
    conn.close()
    return RedirectResponse(f"/ui/purchasing/purchase-orders/{po_id}", status_code=302)


@router.post("/ui/purchasing/purchase-orders/{po_id}/submit-approval")
def submit_po_approval(request: Request, po_id: int):
    if not (can_do(request, "create") or can_do(request, "edit")):
        return HTMLResponse("Not allowed.", status_code=403)

    conn = get_conn()
    po = conn.execute(
        """
        SELECT *
        FROM purchase_orders
        WHERE id = ?
        LIMIT 1
        """,
        (po_id,),
    ).fetchone()
    if not po:
        conn.close()
        return HTMLResponse("PO not found", status_code=404)

    lines_count = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM purchase_order_lines
        WHERE po_id = ?
        """,
        (po_id,),
    ).fetchone()
    if int(lines_count["c"] or 0) == 0:
        conn.close()
        return HTMLResponse("Cannot submit PO without lines.", status_code=400)

    try:
        conn.execute(
            """
            UPDATE purchase_orders
            SET approval_status = 'pending',
                submitted_by = ?,
                submitted_at = CURRENT_TIMESTAMP,
                approved_by = NULL,
                approved_at = NULL,
                rejected_by = NULL,
                rejected_at = NULL,
                rejection_note = NULL
            WHERE id = ?
            """,
            (actor_name(request), po_id),
        )
        recalc_po_status(conn, po_id)
        create_po_alert(
            conn,
            po_id=po_id,
            alert_type="approval",
            title="PO Pending Approval",
            message=f"{po['po_no'] or f'PO#{po_id}'} submitted by {actor_name(request)}.",
            severity="warning",
        )
        conn.commit()
    except Exception as ex:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Submit error: {str(ex)}", status_code=500)
    conn.close()
    return RedirectResponse(f"/ui/purchasing/purchase-orders/{po_id}", status_code=302)


@router.post("/ui/purchasing/purchase-orders/{po_id}/approve")
def approve_po(request: Request, po_id: int):
    if not can_do(request, "approve"):
        return HTMLResponse("Not allowed.", status_code=403)

    conn = get_conn()
    po = conn.execute(
        """
        SELECT *
        FROM purchase_orders
        WHERE id = ?
        LIMIT 1
        """,
        (po_id,),
    ).fetchone()
    if not po:
        conn.close()
        return HTMLResponse("PO not found", status_code=404)

    try:
        conn.execute(
            """
            UPDATE purchase_orders
            SET approval_status = 'approved',
                approved_by = ?,
                approved_at = CURRENT_TIMESTAMP,
                rejected_by = NULL,
                rejected_at = NULL,
                rejection_note = NULL
            WHERE id = ?
            """,
            (actor_name(request), po_id),
        )
        recalc_po_status(conn, po_id)
        create_po_alert(
            conn,
            po_id=po_id,
            alert_type="approval",
            title="PO Approved",
            message=f"{po['po_no'] or f'PO#{po_id}'} approved by {actor_name(request)}.",
            severity="success",
        )
        conn.commit()
    except Exception as ex:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Approve error: {str(ex)}", status_code=500)
    conn.close()
    return RedirectResponse(f"/ui/purchasing/purchase-orders/{po_id}", status_code=302)


@router.post("/ui/purchasing/purchase-orders/{po_id}/reject")
async def reject_po(request: Request, po_id: int, rejection_note: str = Form("")):
    if not can_do(request, "approve"):
        return HTMLResponse("Not allowed.", status_code=403)

    note = (rejection_note or "").strip()
    conn = get_conn()
    po = conn.execute(
        """
        SELECT *
        FROM purchase_orders
        WHERE id = ?
        LIMIT 1
        """,
        (po_id,),
    ).fetchone()
    if not po:
        conn.close()
        return HTMLResponse("PO not found", status_code=404)

    try:
        conn.execute(
            """
            UPDATE purchase_orders
            SET approval_status = 'rejected',
                status = 'draft',
                rejected_by = ?,
                rejected_at = CURRENT_TIMESTAMP,
                rejection_note = ?,
                approved_by = NULL,
                approved_at = NULL
            WHERE id = ?
            """,
            (actor_name(request), note, po_id),
        )
        recalc_po_status(conn, po_id)
        msg_note = f" Note: {note}" if note else ""
        create_po_alert(
            conn,
            po_id=po_id,
            alert_type="approval",
            title="PO Rejected",
            message=f"{po['po_no'] or f'PO#{po_id}'} rejected by {actor_name(request)}.{msg_note}",
            severity="warning",
        )
        conn.commit()
    except Exception as ex:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reject error: {str(ex)}", status_code=500)
    conn.close()
    return RedirectResponse(f"/ui/purchasing/purchase-orders/{po_id}", status_code=302)


@router.get("/ui/purchasing/purchase-orders/{po_id}", response_class=HTMLResponse)
def open_po(request: Request, po_id: int):
    conn = get_conn()

    po = conn.execute(
        """
        SELECT *
        FROM purchase_orders
        WHERE id = ?
        LIMIT 1
        """,
        (po_id,),
    ).fetchone()

    if not po:
        conn.close()
        return HTMLResponse("PO not found", status_code=404)

    lines = conn.execute(
        """
        SELECT *
        FROM purchase_order_lines
        WHERE po_id = ?
        ORDER BY line_no, id
        """,
        (po_id,),
    ).fetchall()

    lines_html = ""
    total_amount = 0.0
    total_received = 0.0
    total_open = 0.0

    for l in lines:
        total_amount += float(l["line_total"] or 0)
        total_received += float(l["received_qty"] or 0)
        total_open += float(l["open_qty"] or 0)
        lines_html += f"""
        <tr>
            <td>{l['line_no'] or ''}</td>
            <td>{item_display(conn, l['item_id'])}</td>
            <td>{l['description'] or ''}</td>
            <td>{l['ordered_qty'] or 0}</td>
            <td>{l['received_qty'] or 0}</td>
            <td>{l['open_qty'] or 0}</td>
            <td>{money(l['unit_price'])}</td>
            <td>{money(l['line_total'])}</td>
            <td>{l['status'] or ''}</td>
        </tr>
        """

    pending_variances = po_pending_variance_count(conn, po_id)
    billable = po_billable_summary(conn, po_id)
    approval_status = (po["approval_status"] or "draft").lower()
    workflow_badges = ""
    if pending_variances > 0:
        workflow_badges += f"<span class='status-chip orange'>Pending Variances: {pending_variances}</span> "
    if billable["line_count"] > 0:
        workflow_badges += f"<span class='status-chip blue'>Ready for Vendor Bill ({billable['line_count']} lines)</span>"
    if not workflow_badges:
        workflow_badges = "<span style='color:#6b7280;'>No pending workflow actions.</span>"

    received_total = po_received_total(conn, po_id)
    can_edit_po = received_total <= 0.000001 and can_do(request, "edit")

    submit_btn = ""
    approve_btn = ""
    reject_btn = ""
    edit_btn = ""
    if can_edit_po:
        edit_btn = f"<a class='btn blue' href='/ui/purchasing/purchase-orders/{po_id}/edit'>Edit</a>"
    if approval_status in ("draft", "rejected") and (can_do(request, "create") or can_do(request, "edit")):
        submit_btn = (
            f"<form method='post' action='/ui/purchasing/purchase-orders/{po_id}/submit-approval' style='display:inline;'>"
            f"<button class='btn green' type='submit'>Submit For Approval</button></form>"
        )
    if approval_status == "pending" and can_do(request, "approve"):
        approve_btn = (
            f"<form method='post' action='/ui/purchasing/purchase-orders/{po_id}/approve' style='display:inline;'>"
            f"<button class='btn green' type='submit'>Approve</button></form>"
        )
        reject_btn = (
            f"<form method='post' action='/ui/purchasing/purchase-orders/{po_id}/reject' style='display:inline-flex;gap:6px;align-items:center;'>"
            f"<input name='rejection_note' placeholder='Reject note (optional)' style='min-width:230px;'>"
            f"<button class='btn red' type='submit'>Reject</button></form>"
        )

    variance_btn = f"<a class='btn gray' href='/ui/purchasing/po-variances?po_id={po_id}'>Review Variances</a>"
    bill_btn = ""
    if approval_status == "approved" and billable["line_count"] > 0:
        bill_btn = f"<a class='btn green' href='/ui/accounting/vendor-bills/new?po_id={po_id}'>Create Vendor Bill From Receipt</a>"

    approval_details = ""
    if po["submitted_by"]:
        approval_details += f"<div><b>Submitted By:</b> {po['submitted_by']} ({po['submitted_at'] or ''})</div>"
    if po["approved_by"]:
        approval_details += f"<div><b>Approved By:</b> {po['approved_by']} ({po['approved_at'] or ''})</div>"
    if po["rejected_by"]:
        approval_details += f"<div><b>Rejected By:</b> {po['rejected_by']} ({po['rejected_at'] or ''})</div>"
    if po["rejection_note"]:
        approval_details += f"<div><b>Reject Note:</b> {po['rejection_note']}</div>"
    if not approval_details:
        approval_details = "<div style='color:#6b7280;'>No approval actions yet.</div>"

    html = f"""
    <div class="card">
        <h2>Purchase Order {po['po_no'] or ''}</h2>

        <div class="row">
            <div class="col"><p><b>Date:</b> {po['po_date'] or ''}</p></div>
            <div class="col"><p><b>Status:</b> {po['status'] or ''}</p></div>
        </div>
        <div class="row">
            <div class="col"><p><b>Approval:</b> {approval_chip(po['approval_status'] or 'draft')}</p></div>
            <div class="col"></div>
        </div>

        <div class="row">
            <div class="col"><p><b>Vendor:</b> {vendor_display(conn, po['vendor_id'])}</p></div>
            <div class="col"><p><b>Warehouse:</b> {warehouse_display(conn, po['warehouse_id'])}</p></div>
        </div>

        <p><b>Notes:</b> {po['notes'] or ''}</p>
        <p><b>Total:</b> {money(total_amount)}</p>
        <p><b>Total Received Qty:</b> {total_received:,.2f} | <b>Total Open Qty:</b> {total_open:,.2f}</p>
        <div style="margin:10px 0;">{workflow_badges}</div>
        <div class="card" style="margin-top:12px;">
            <h3>Approval Cycle</h3>
            {approval_details}
        </div>

        <div style="margin-top:15px;">
            <a class="btn gray" href="/ui/purchasing/purchase-orders">Back</a>
            {edit_btn}
            {submit_btn}
            {approve_btn}
            {reject_btn}
            {variance_btn}
            {bill_btn}
        </div>
    </div>

    <div class="card">
        <h3>PO Lines</h3>
        <table>
            <tr>
                <th>#</th>
                <th>Item</th>
                <th>Description</th>
                <th>Ordered Qty</th>
                <th>Received Qty</th>
                <th>Open Qty</th>
                <th>Unit Price</th>
                <th>Total</th>
                <th>Status</th>
            </tr>
            {lines_html}
        </table>
    </div>
    """

    conn.close()
    return HTMLResponse(render_page("Purchase Order", html, "en", current_path=request.url.path))


@router.post("/ui/purchasing/workflow-alerts/{alert_id}/read")
def mark_workflow_alert_read(alert_id: int):
    conn = get_conn()
    conn.execute(
        """
        UPDATE purchase_workflow_alerts
        SET is_read = 1
        WHERE id = ?
        """,
        (alert_id,),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/ui/purchasing/purchase-orders", status_code=302)
