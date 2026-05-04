from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page

router = APIRouter()


def money(x):
    try:
        return f"{float(x or 0):,.2f}"
    except Exception:
        return "0.00"


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


def build_name_expr(cols):
    direct = [c for c in ["name", "vendor_name", "item_name", "warehouse_name", "full_name"] if c in cols]
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
    for c in ["code", "vendor_code", "item_code", "warehouse_code", "sku"]:
        if c in cols:
            return c
    return "''"


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


def po_display(conn, po_id):
    if not po_id or not table_exists(conn, "purchase_orders"):
        return ""
    row = conn.execute("""
        SELECT po_no
        FROM purchase_orders
        WHERE id = ?
        LIMIT 1
    """, (po_id,)).fetchone()
    return row["po_no"] if row else ""


def grn_display(conn, grn_id):
    if not grn_id or not table_exists(conn, "goods_receipts"):
        return ""
    row = conn.execute("""
        SELECT grn_no
        FROM goods_receipts
        WHERE id = ?
        LIMIT 1
    """, (grn_id,)).fetchone()
    return row["grn_no"] if row else ""


def recalc_po_status(conn, po_id):
    lines = conn.execute("""
        SELECT ordered_qty, received_qty, open_qty
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


def ensure_variance_table():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS po_receipt_variances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grn_id INTEGER NOT NULL,
            grn_line_id INTEGER NOT NULL,
            po_id INTEGER NOT NULL,
            po_line_id INTEGER NOT NULL,
            item_id INTEGER,
            variance_type TEXT,
            ordered_qty REAL DEFAULT 0,
            previous_received_qty REAL DEFAULT 0,
            open_qty_before REAL DEFAULT 0,
            received_qty REAL DEFAULT 0,
            variance_qty REAL DEFAULT 0,
            decision_status TEXT DEFAULT 'pending',
            decision_type TEXT,
            decision_note TEXT,
            approved_by TEXT,
            approved_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


ensure_variance_table()


def sync_variances_from_grn(conn, grn_id):
    grn = conn.execute("""
        SELECT *
        FROM goods_receipts
        WHERE id = ?
        LIMIT 1
    """, (grn_id,)).fetchone()

    if not grn:
        return

    lines = conn.execute("""
        SELECT *
        FROM goods_receipt_lines
        WHERE grn_id = ?
          AND LOWER(COALESCE(variance_type,'')) IN ('over', 'short')
    """, (grn_id,)).fetchall()

    for l in lines:
        existing = conn.execute("""
            SELECT id
            FROM po_receipt_variances
            WHERE grn_line_id = ?
            LIMIT 1
        """, (l["id"],)).fetchone()

        if existing:
            continue

        conn.execute("""
            INSERT INTO po_receipt_variances (
                grn_id, grn_line_id, po_id, po_line_id, item_id,
                variance_type, ordered_qty, previous_received_qty,
                open_qty_before, received_qty, variance_qty,
                decision_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (
            grn_id,
            l["id"],
            grn["po_id"],
            l["po_line_id"],
            l["item_id"],
            (l["variance_type"] or "").strip(),
            float(l["ordered_qty"] or 0),
            float(l["previously_received_qty"] or 0),
            float(l["open_qty_before"] or 0),
            float(l["received_qty"] or 0),
            float(l["variance_qty"] or 0),
        ))


@router.get("/ui/purchasing/po-variances", response_class=HTMLResponse)
def variance_list(request: Request, po_id: str = ""):
    conn = get_conn()

    if table_exists(conn, "goods_receipts"):
        grns = conn.execute("SELECT id FROM goods_receipts").fetchall()
        for g in grns:
            sync_variances_from_grn(conn, g["id"])
        conn.commit()

    sql = """
        SELECT *
        FROM po_receipt_variances
        WHERE 1 = 1
    """
    params = []
    if str(po_id or "").strip().isdigit():
        sql += " AND po_id = ?"
        params.append(int(po_id))

    sql += """
        ORDER BY
            CASE WHEN LOWER(COALESCE(decision_status,'')) = 'pending' THEN 0 ELSE 1 END,
            id DESC
    """

    rows = conn.execute(sql, params).fetchall()

    body = ""
    for r in rows:
        body += f"""
        <tr>
            <td><a class="btn gray" href="/ui/purchasing/po-variances/{r['id']}">{r['id']}</a></td>
            <td>{grn_display(conn, r['grn_id'])}</td>
            <td>{po_display(conn, r['po_id'])}</td>
            <td>{item_display(conn, r['item_id'])}</td>
            <td>{r['variance_type'] or ''}</td>
            <td>{r['open_qty_before'] or 0}</td>
            <td>{r['received_qty'] or 0}</td>
            <td>{r['variance_qty'] or 0}</td>
            <td>{r['decision_status'] or ''}</td>
            <td>{r['decision_type'] or ''}</td>
        </tr>
        """

    conn.close()

    html = f"""
    <div class="card">
        <h2>PO Receipt Variances</h2>
        {"<p><b>Filtered PO:</b> %s</p><a class='btn gray' href='/ui/purchasing/po-variances'>Clear Filter</a>" % po_display(conn, int(po_id)) if str(po_id or '').strip().isdigit() else ""}
    </div>

    <div class="card">
        <table>
            <tr>
                <th>ID</th>
                <th>GRN</th>
                <th>PO</th>
                <th>Item</th>
                <th>Type</th>
                <th>Open Qty Before</th>
                <th>Received Qty</th>
                <th>Variance Qty</th>
                <th>Status</th>
                <th>Decision</th>
            </tr>
            {body}
        </table>
    </div>
    """

    return HTMLResponse(render_page("PO Variances", html, "en", current_path=request.url.path))


@router.get("/ui/purchasing/po-variances/{variance_id}", response_class=HTMLResponse)
def open_variance(request: Request, variance_id: int):
    conn = get_conn()

    var = conn.execute("""
        SELECT *
        FROM po_receipt_variances
        WHERE id = ?
        LIMIT 1
    """, (variance_id,)).fetchone()

    if not var:
        conn.close()
        return HTMLResponse("Variance not found", status_code=404)

    po_line = conn.execute("""
        SELECT *
        FROM purchase_order_lines
        WHERE id = ?
        LIMIT 1
    """, (var["po_line_id"],)).fetchone()

    html = f"""
    <div class="card">
        <h2>PO Variance #{var['id']}</h2>

        <div class="row">
            <div class="col"><p><b>GRN:</b> {grn_display(conn, var['grn_id'])}</p></div>
            <div class="col"><p><b>PO:</b> {po_display(conn, var['po_id'])}</p></div>
        </div>

        <div class="row">
            <div class="col"><p><b>Item:</b> {item_display(conn, var['item_id'])}</p></div>
            <div class="col"><p><b>Type:</b> {var['variance_type'] or ''}</p></div>
        </div>

        <div class="row">
            <div class="col"><p><b>Ordered Qty:</b> {var['ordered_qty'] or 0}</p></div>
            <div class="col"><p><b>Previously Received:</b> {var['previous_received_qty'] or 0}</p></div>
        </div>

        <div class="row">
            <div class="col"><p><b>Open Qty Before:</b> {var['open_qty_before'] or 0}</p></div>
            <div class="col"><p><b>Received Now:</b> {var['received_qty'] or 0}</p></div>
        </div>

        <div class="row">
            <div class="col"><p><b>Variance Qty:</b> {var['variance_qty'] or 0}</p></div>
            <div class="col"><p><b>Status:</b> {var['decision_status'] or ''}</p></div>
        </div>

        <div class="row">
            <div class="col"><p><b>Decision:</b> {var['decision_type'] or ''}</p></div>
            <div class="col"><p><b>Decision Note:</b> {var['decision_note'] or ''}</p></div>
        </div>

        <div class="row">
            <div class="col"><p><b>PO Line Current Ordered:</b> {po_line['ordered_qty'] if po_line else 0}</p></div>
            <div class="col"><p><b>PO Line Current Open:</b> {po_line['open_qty'] if po_line else 0}</p></div>
        </div>

        <div style="margin-top:15px;">
            <a class="btn gray" href="/ui/purchasing/po-variances">Back</a>
            <a class="btn blue" href="/ui/inventory/goods-receipts/{var['grn_id']}">Open GRN</a>
            <a class="btn blue" href="/ui/purchasing/purchase-orders/{var['po_id']}">Open PO</a>
        </div>
    </div>
    """

    if (var["decision_status"] or "").lower() == "pending":
        html += f"""
        <div class="card">
            <h3>Decision</h3>

            <form method="post" action="/ui/purchasing/po-variances/{variance_id}/decision">
                <div class="row">
                    <div class="col">
                        <label>Decision</label>
                        <select name="decision_type" required>
                            <option value="">Select Decision</option>
                            <option value="approve_over">Approve Over Receipt</option>
                            <option value="short_close">Short Close Remaining Qty</option>
                            <option value="keep_open">Keep PO Open</option>
                        </select>
                    </div>
                    <div class="col">
                        <label>Decision Note</label>
                        <input name="decision_note">
                    </div>
                </div>

                <div style="margin-top:18px;">
                    <button class="btn green" type="submit">Apply</button>
                </div>
            </form>
        </div>
        """

    conn.close()
    return HTMLResponse(render_page("PO Variance", html, "en", current_path=request.url.path))


@router.post("/ui/purchasing/po-variances/{variance_id}/decision")
def apply_variance_decision(
    variance_id: int,
    decision_type: str = Form(...),
    decision_note: str = Form("")
):
    conn = get_conn()

    var = conn.execute("""
        SELECT *
        FROM po_receipt_variances
        WHERE id = ?
        LIMIT 1
    """, (variance_id,)).fetchone()

    if not var:
        conn.close()
        return HTMLResponse("Variance not found", status_code=404)

    if (var["decision_status"] or "").lower() != "pending":
        conn.close()
        return RedirectResponse(f"/ui/purchasing/po-variances/{variance_id}", status_code=302)

    po_line = conn.execute("""
        SELECT *
        FROM purchase_order_lines
        WHERE id = ?
        LIMIT 1
    """, (var["po_line_id"],)).fetchone()

    if not po_line:
        conn.close()
        return HTMLResponse("PO line not found", status_code=404)

    current_ordered = float(po_line["ordered_qty"] or 0)
    current_received = float(po_line["received_qty"] or 0)
    current_open = float(po_line["open_qty"] or 0)

    variance_type = (var["variance_type"] or "").lower()
    decision_type = (decision_type or "").strip().lower()

    if decision_type == "approve_over" and variance_type == "over":
        new_ordered = current_received
        new_open = max(new_ordered - current_received, 0)

        line_status = "received" if new_open <= 0 else "partial_received"

        conn.execute("""
            UPDATE purchase_order_lines
            SET ordered_qty = ?,
                open_qty = ?,
                line_total = COALESCE(unit_price,0) * ?,
                status = ?
            WHERE id = ?
        """, (
            new_ordered,
            new_open,
            new_ordered,
            line_status,
            po_line["id"],
        ))

        decision_status = "approved"

    elif decision_type == "short_close" and variance_type == "short":
        new_open = 0
        new_ordered = current_received

        conn.execute("""
            UPDATE purchase_order_lines
            SET ordered_qty = ?,
                open_qty = 0,
                line_total = COALESCE(unit_price,0) * ?,
                status = 'received'
            WHERE id = ?
        """, (
            new_ordered,
            new_ordered,
            po_line["id"],
        ))

        decision_status = "approved"

    elif decision_type == "keep_open":
        # لا نغيّر أي كمية، فقط نثبت قرار المشتريات إن الـ PO يفضل مفتوح
        decision_status = "approved"

    else:
        conn.close()
        return HTMLResponse("Invalid decision for this variance type.", status_code=400)

    conn.execute("""
        UPDATE po_receipt_variances
        SET decision_status = ?,
            decision_type = ?,
            decision_note = ?,
            approved_by = 'admin',
            approved_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        decision_status,
        decision_type,
        (decision_note or "").strip(),
        variance_id,
    ))

    recalc_po_status(conn, var["po_id"])

    conn.commit()
    conn.close()

    return RedirectResponse(f"/ui/purchasing/po-variances/{variance_id}", status_code=302)
