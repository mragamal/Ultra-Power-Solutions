from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db import get_conn
from layout import render_page
from modules.inventory.core import (
    qty,
    item_display,
    warehouse_display,
    stock_balance_rows,
    ensure_inventory_tables,
    sync_goods_receipts_to_stock,
)

router = APIRouter()


ensure_inventory_tables()


@router.get("/ui/inventory/stock-balance", response_class=HTMLResponse)
def stock_balance(request: Request):
    rows = stock_balance_rows()

    body = ""
    total_qty = 0.0
    for r in rows:
        bal = float(r["balance_qty"] or 0)
        total_qty += bal
        body += f"""
        <tr>
            <td>{r['item_code'] or ''}</td>
            <td>{r['item_name'] or ''}</td>
            <td>{r['warehouse_code'] or ''}</td>
            <td>{r['warehouse_name'] or ''}</td>
            <td style="text-align:right;">{qty(bal)}</td>
        </tr>
        """

    if not body:
        body = "<tr><td colspan='5' style='text-align:center;'>No stock movement found.</td></tr>"

    html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>Stock Balance</h2>
            <div class="summary-pill">Total Qty: {qty(total_qty)}</div>
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
        current = float(running.get(key, 0.0))
        current += float(r["qty_in"] or 0) - float(r["qty_out"] or 0)
        running[key] = current

        body += f"""
        <tr>
            <td>{r['trans_date'] or ''}</td>
            <td>{r['trans_type'] or ''}</td>
            <td>{r['trans_no'] or ''}</td>
            <td>{item_display(conn, r['item_id'])}</td>
            <td>{warehouse_display(conn, r['warehouse_id'])}</td>
            <td>{r['description'] or ''}</td>
            <td style="text-align:right;">{qty(r['qty_in'])}</td>
            <td style="text-align:right;">{qty(r['qty_out'])}</td>
            <td style="text-align:right;">{qty(current)}</td>
        </tr>
        """

    if not body:
        body = "<tr><td colspan='9' style='text-align:center;'>No stock ledger entries found.</td></tr>"

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
                <th style="text-align:right;">Running Balance</th>
            </tr>
            {body}
        </table>
    </div>
    """
    return HTMLResponse(render_page("Stock Ledger", html, current_path=request.url.path))
