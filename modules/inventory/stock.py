from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from audit import actor_name_from_request, safe_log_request_action
from db import get_conn
from layout import render_page
from modules.inventory.core import (
    ensure_inventory_tables,
    item_display,
    item_standard_cost,
    money,
    next_material_issue_no,
    qty,
    record_stock_movement,
    stock_balance_rows,
    sync_goods_receipts_to_stock,
    table_exists,
    warehouse_display,
)

router = APIRouter()


ensure_inventory_tables()


def safe(value):
    return "" if value is None else str(value).strip()


def to_float(value, default=0.0):
    try:
        return float(str(value or "").replace(",", "").strip() or default)
    except Exception:
        return default


def today_text():
    return date.today().isoformat()


def item_options_html(conn, selected_id=0):
    rows = conn.execute(
        """
        SELECT id, code, name, standard_cost
        FROM items
        WHERE COALESCE(is_active, 1) = 1
        ORDER BY code, name
        """
    ).fetchall()
    html = "<option value=''>-- Select Item --</option>"
    for row in rows:
        selected = "selected" if int(row["id"]) == int(selected_id or 0) else ""
        label = f"{safe(row['code'])} - {safe(row['name'])}".strip(" -")
        cost = to_float(row["standard_cost"])
        html += f"<option value='{row['id']}' data-cost='{cost}' {selected}>{label}</option>"
    return html


def warehouse_options_html(conn, selected_id=0):
    rows = conn.execute(
        """
        SELECT id, code, name
        FROM warehouses
        WHERE COALESCE(is_active, 1) = 1
        ORDER BY code, name
        """
    ).fetchall()
    html = "<option value=''>-- Select Warehouse --</option>"
    for row in rows:
        selected = "selected" if int(row["id"]) == int(selected_id or 0) else ""
        label = f"{safe(row['code'])} - {safe(row['name'])}".strip(" -")
        html += f"<option value='{row['id']}' {selected}>{label}</option>"
    return html


def work_order_options_html(conn, selected_id=0):
    if not table_exists(conn, "ops_work_orders"):
        return "<option value=''>-- No Work Orders --</option>"
    rows = conn.execute(
        """
        SELECT id, work_order_no, site_code, site_name, status
        FROM ops_work_orders
        ORDER BY id DESC
        LIMIT 300
        """
    ).fetchall()
    html = "<option value=''>-- Select Work Order --</option>"
    for row in rows:
        selected = "selected" if int(row["id"]) == int(selected_id or 0) else ""
        parts = [safe(row["work_order_no"]), safe(row["site_code"]), safe(row["site_name"]), safe(row["status"])]
        label = " | ".join([p for p in parts if p])
        html += f"<option value='{row['id']}' {selected}>{label}</option>"
    return html


def inventory_balance(conn, item_id, warehouse_id):
    row = conn.execute(
        """
        SELECT SUM(COALESCE(qty_in, 0) - COALESCE(qty_out, 0)) AS balance_qty
        FROM stock_ledger
        WHERE item_id = ? AND warehouse_id = ?
        """,
        (item_id, warehouse_id),
    ).fetchone()
    return to_float(row["balance_qty"] if row else 0)


@router.get("/ui/inventory/stock-balance", response_class=HTMLResponse)
def stock_balance(request: Request):
    sync_goods_receipts_to_stock()
    rows = stock_balance_rows()

    body = ""
    total_qty = 0.0
    total_value = 0.0
    for r in rows:
        bal = to_float(r["balance_qty"])
        value = to_float(r["stock_value"])
        total_qty += bal
        total_value += value
        body += f"""
        <tr>
            <td>{safe(r['item_code'])}</td>
            <td>{safe(r['item_name'])}</td>
            <td>{safe(r['warehouse_code'])}</td>
            <td>{safe(r['warehouse_name'])}</td>
            <td style="text-align:right;">{qty(bal)}</td>
            <td style="text-align:right;">{money(value)}</td>
        </tr>
        """

    if not body:
        body = "<tr><td colspan='6' style='text-align:center;'>No stock movement found.</td></tr>"

    html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>Stock Balance</h2>
            <div style="display:flex;gap:10px;flex-wrap:wrap;">
                <div class="summary-pill">Total Qty: {qty(total_qty)}</div>
                <div class="summary-pill">Stock Value: {money(total_value)}</div>
            </div>
        </div>
    </div>

    <div class="card">
        <table>
            <tr>
                <th>Item Code</th>
                <th>Item Name</th>
                <th>Warehouse Code</th>
                <th>Warehouse Name</th>
                <th style="text-align:right;">Balance Qty</th>
                <th style="text-align:right;">Stock Value</th>
            </tr>
            {body}
        </table>
    </div>
    """
    return HTMLResponse(render_page("Stock Balance", html, current_path=request.url.path))


@router.get("/ui/inventory/stock-ledger", response_class=HTMLResponse)
def stock_ledger(request: Request):
    sync_goods_receipts_to_stock()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT *
        FROM stock_ledger
        ORDER BY COALESCE(trans_date, ''), id
        """
    ).fetchall()

    running = {}
    body = ""
    for r in rows:
        key = f"{r['item_id']}-{r['warehouse_id']}"
        current = to_float(running.get(key, 0.0))
        qty_in = to_float(r["qty_in"])
        qty_out = to_float(r["qty_out"])
        unit_cost = to_float(r["unit_cost"])
        current += qty_in - qty_out
        running[key] = current
        movement_value = (qty_in - qty_out) * unit_cost

        body += f"""
        <tr>
            <td>{safe(r['trans_date'])}</td>
            <td>{safe(r['trans_type'])}</td>
            <td>{safe(r['trans_no'])}</td>
            <td>{item_display(conn, r['item_id'])}</td>
            <td>{warehouse_display(conn, r['warehouse_id'])}</td>
            <td>{safe(r['description'])}</td>
            <td style="text-align:right;">{qty(qty_in)}</td>
            <td style="text-align:right;">{qty(qty_out)}</td>
            <td style="text-align:right;">{money(unit_cost)}</td>
            <td style="text-align:right;">{money(movement_value)}</td>
            <td style="text-align:right;">{qty(current)}</td>
        </tr>
        """

    if not body:
        body = "<tr><td colspan='11' style='text-align:center;'>No stock ledger entries found.</td></tr>"

    conn.close()

    html = f"""
    <div class="card">
        <h2>Stock Ledger</h2>
    </div>

    <div class="card">
        <table>
            <tr>
                <th>Date</th>
                <th>Type</th>
                <th>No</th>
                <th>Item</th>
                <th>Warehouse</th>
                <th>Description</th>
                <th style="text-align:right;">Qty In</th>
                <th style="text-align:right;">Qty Out</th>
                <th style="text-align:right;">Unit Cost</th>
                <th style="text-align:right;">Value</th>
                <th style="text-align:right;">Running Balance</th>
            </tr>
            {body}
        </table>
    </div>
    """
    return HTMLResponse(render_page("Stock Ledger", html, current_path=request.url.path))


@router.get("/ui/inventory/material-issues", response_class=HTMLResponse)
def material_issues(request: Request, msg: str = ""):
    ensure_inventory_tables()
    sync_goods_receipts_to_stock()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            mi.*,
            w.code AS warehouse_code,
            w.name AS warehouse_name,
            wo.work_order_no,
            COALESCE(SUM(COALESCE(mil.qty, 0) * COALESCE(mil.unit_cost, 0)), 0) AS total_cost
        FROM inventory_material_issues mi
        LEFT JOIN inventory_material_issue_lines mil ON mil.issue_id = mi.id
        LEFT JOIN warehouses w ON w.id = mi.warehouse_id
        LEFT JOIN ops_work_orders wo ON wo.id = mi.work_order_id
        GROUP BY mi.id
        ORDER BY mi.id DESC
        """
    ).fetchall()
    conn.close()

    notice = f"<div class='alert ok'>{safe(msg)}</div>" if safe(msg) else ""
    body = ""
    for row in rows:
        warehouse_label = f"{safe(row['warehouse_code'])} - {safe(row['warehouse_name'])}".strip(" -")
        body += f"""
        <tr>
            <td>{safe(row['issue_no'])}</td>
            <td>{safe(row['issue_date'])}</td>
            <td>{safe(row['work_order_no'])}</td>
            <td>{warehouse_label}</td>
            <td style="text-align:right;">{money(row['total_cost'])}</td>
            <td>{safe(row['created_by'])}</td>
            <td><a class="btn blue" href="/ui/inventory/material-issues/{row['id']}">Open</a></td>
        </tr>
        """
    if not body:
        body = "<tr><td colspan='7' style='text-align:center;'>No material issues found.</td></tr>"

    html = f"""
    {notice}
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>Material Issues</h2>
            <a class="btn green" href="/ui/inventory/material-issues/new">+ New Material Issue</a>
        </div>
    </div>
    <div class="card">
        <table>
            <tr>
                <th>No</th>
                <th>Date</th>
                <th>Work Order</th>
                <th>Warehouse</th>
                <th style="text-align:right;">Total Cost</th>
                <th>Created By</th>
                <th>Action</th>
            </tr>
            {body}
        </table>
    </div>
    """
    return HTMLResponse(render_page("Material Issues", html, current_path=request.url.path))


@router.get("/ui/inventory/material-issues/new", response_class=HTMLResponse)
def material_issue_new(request: Request, work_order_id: int = 0, msg: str = ""):
    ensure_inventory_tables()
    conn = get_conn()
    issue_no = next_material_issue_no(conn)
    item_options = item_options_html(conn)
    warehouse_options = warehouse_options_html(conn)
    work_order_options = work_order_options_html(conn, work_order_id)
    conn.close()

    notice = f"<div class='alert error'>{safe(msg)}</div>" if safe(msg) else ""
    line_rows = ""
    for _ in range(8):
        line_rows += f"""
        <tr>
            <td><select name="item_id" class="material-item">{item_options}</select></td>
            <td><input type="number" step="0.01" name="line_qty" value=""></td>
            <td><input type="number" step="0.01" name="unit_cost" value="" class="material-cost"></td>
            <td><input name="description" value=""></td>
        </tr>
        """

    html = f"""
    {notice}
    <div class="card">
        <h2>New Material Issue</h2>
        <form method="post" action="/ui/inventory/material-issues/new">
            <div class="form-grid">
                <div class="form-group"><label>No</label><input name="issue_no_display" value="{issue_no}" readonly></div>
                <div class="form-group"><label>Date</label><input type="date" name="issue_date" value="{today_text()}" required></div>
                <div class="form-group"><label>Work Order</label><select name="work_order_id">{work_order_options}</select></div>
                <div class="form-group"><label>Warehouse</label><select name="warehouse_id" required>{warehouse_options}</select></div>
                <div class="form-group" style="grid-column:span 2;"><label>Notes</label><input name="notes" value=""></div>
            </div>
            <h3>Lines</h3>
            <table>
                <tr>
                    <th>Item</th>
                    <th>Qty</th>
                    <th>Unit Cost</th>
                    <th>Description</th>
                </tr>
                {line_rows}
            </table>
            <div class="form-actions">
                <button class="btn green" type="submit">Save</button>
                <a class="btn gray" href="/ui/inventory/material-issues">Back</a>
            </div>
        </form>
    </div>
    <script>
    document.querySelectorAll('.material-item').forEach(function(select) {{
        select.addEventListener('change', function() {{
            const cost = this.selectedOptions[0] ? this.selectedOptions[0].dataset.cost : '';
            const input = this.closest('tr').querySelector('.material-cost');
            if (input && !input.value && cost) input.value = cost;
        }});
    }});
    </script>
    """
    return HTMLResponse(render_page("New Material Issue", html, current_path=request.url.path))


@router.post("/ui/inventory/material-issues/new")
def material_issue_create(
    request: Request,
    issue_date: str = Form(""),
    work_order_id: int = Form(0),
    warehouse_id: int = Form(...),
    notes: str = Form(""),
    item_id: list[int] = Form([]),
    line_qty: list[str] = Form([]),
    unit_cost: list[str] = Form([]),
    description: list[str] = Form([]),
):
    ensure_inventory_tables()
    conn = get_conn()
    parsed_lines = []
    requested_by_item = {}

    max_len = max(len(item_id), len(line_qty), len(unit_cost), len(description))
    for idx in range(max_len):
        current_item_id = int(item_id[idx]) if idx < len(item_id) and item_id[idx] else 0
        current_qty = to_float(line_qty[idx] if idx < len(line_qty) else 0)
        if not current_item_id or current_qty <= 0:
            continue
        current_cost = to_float(unit_cost[idx] if idx < len(unit_cost) else 0)
        if current_cost <= 0:
            current_cost = item_standard_cost(conn, current_item_id)
        current_desc = safe(description[idx] if idx < len(description) else "")
        parsed_lines.append((current_item_id, current_qty, current_cost, current_desc))
        requested_by_item[current_item_id] = requested_by_item.get(current_item_id, 0.0) + current_qty

    if not parsed_lines:
        conn.close()
        return RedirectResponse(
            f"/ui/inventory/material-issues/new?work_order_id={work_order_id}&msg={quote('Add at least one material line')}",
            status_code=303,
        )

    for current_item_id, requested_qty in requested_by_item.items():
        available = inventory_balance(conn, current_item_id, warehouse_id)
        if requested_qty > available + 0.000001:
            item_name = item_display(conn, current_item_id)
            conn.close()
            return RedirectResponse(
                f"/ui/inventory/material-issues/new?work_order_id={work_order_id}&msg={quote(f'Not enough stock for {item_name}. Available {qty(available)}')}",
                status_code=303,
            )

    actor = actor_name_from_request(request)
    issue_no = next_material_issue_no(conn)
    issue_date_value = safe(issue_date) or today_text()
    cur = conn.execute(
        """
        INSERT INTO inventory_material_issues (
            issue_no, issue_date, work_order_id, warehouse_id, notes, created_by
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (issue_no, issue_date_value, work_order_id or None, warehouse_id, safe(notes), actor),
    )
    issue_id = cur.lastrowid

    total_cost = 0.0
    for current_item_id, current_qty, current_cost, current_desc in parsed_lines:
        total_cost += current_qty * current_cost
        cur = conn.execute(
            """
            INSERT INTO inventory_material_issue_lines (issue_id, item_id, qty, unit_cost, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            (issue_id, current_item_id, current_qty, current_cost, current_desc),
        )
        line_id = cur.lastrowid
        record_stock_movement(
            conn,
            trans_date=issue_date_value,
            trans_type="material_issue",
            trans_no=issue_no,
            reference_type="material_issue",
            reference_id=issue_id,
            reference_line_id=line_id,
            warehouse_id=warehouse_id,
            item_id=current_item_id,
            description=current_desc or safe(notes) or f"Material issue {issue_no}",
            qty_in=0,
            qty_out=current_qty,
            unit_cost=current_cost,
        )

    safe_log_request_action(
        request,
        "inventory_material_issue",
        issue_id,
        "Created",
        notes=f"{issue_no} total cost {money(total_cost)}",
        conn=conn,
        module="inventory",
    )
    if work_order_id:
        safe_log_request_action(
            request,
            "ops_work_order",
            work_order_id,
            "Material Issued",
            notes=f"{issue_no} from {warehouse_display(conn, warehouse_id)} total cost {money(total_cost)}",
            conn=conn,
            module="operations",
        )

    conn.commit()
    conn.close()
    return RedirectResponse(
        f"/ui/inventory/material-issues?msg={quote(f'Material issue {issue_no} saved')}",
        status_code=303,
    )


@router.get("/ui/inventory/material-issues/{issue_id}", response_class=HTMLResponse)
def material_issue_detail(request: Request, issue_id: int):
    ensure_inventory_tables()
    conn = get_conn()
    row = conn.execute(
        """
        SELECT mi.*, wo.work_order_no
        FROM inventory_material_issues mi
        LEFT JOIN ops_work_orders wo ON wo.id = mi.work_order_id
        WHERE mi.id = ?
        LIMIT 1
        """,
        (issue_id,),
    ).fetchone()
    if not row:
        conn.close()
        return HTMLResponse(render_page("Material Issue", "<div class='card'>Material issue not found.</div>", current_path=request.url.path))

    lines = conn.execute(
        """
        SELECT *
        FROM inventory_material_issue_lines
        WHERE issue_id = ?
        ORDER BY id
        """,
        (issue_id,),
    ).fetchall()
    warehouse_label = warehouse_display(conn, row["warehouse_id"])
    line_body = ""
    total_cost = 0.0
    for line in lines:
        line_total = to_float(line["qty"]) * to_float(line["unit_cost"])
        total_cost += line_total
        line_body += f"""
        <tr>
            <td>{item_display(conn, line['item_id'])}</td>
            <td style="text-align:right;">{qty(line['qty'])}</td>
            <td style="text-align:right;">{money(line['unit_cost'])}</td>
            <td style="text-align:right;">{money(line_total)}</td>
            <td>{safe(line['description'])}</td>
        </tr>
        """
    conn.close()
    if not line_body:
        line_body = "<tr><td colspan='5' style='text-align:center;'>No lines found.</td></tr>"

    html = f"""
    <div class="card">
        <h2>Material Issue {safe(row['issue_no'])}</h2>
        <div><b>Date:</b> {safe(row['issue_date'])}</div>
        <div><b>Work Order:</b> {safe(row['work_order_no'])}</div>
        <div><b>Warehouse:</b> {warehouse_label}</div>
        <div><b>Total Cost:</b> {money(total_cost)}</div>
        <div><b>Created By:</b> {safe(row['created_by'])}</div>
        <div><b>Notes:</b> {safe(row['notes'])}</div>
        <div class="form-actions">
            <a class="btn gray" href="/ui/inventory/material-issues">Back</a>
        </div>
    </div>
    <div class="card">
        <h3>Lines</h3>
        <table>
            <tr>
                <th>Item</th>
                <th style="text-align:right;">Qty</th>
                <th style="text-align:right;">Unit Cost</th>
                <th style="text-align:right;">Total</th>
                <th>Description</th>
            </tr>
            {line_body}
        </table>
    </div>
    """
    return HTMLResponse(render_page(f"Material Issue {safe(row['issue_no'])}", html, current_path=request.url.path))
