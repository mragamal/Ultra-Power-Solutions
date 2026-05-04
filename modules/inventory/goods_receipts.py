from html import escape

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page
from modules.inventory.core import ensure_inventory_tables, record_stock_movement
from modules.purchasing.workflow import ensure_workflow_tables, create_po_alert

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


def vendor_table_name(conn):
    if table_exists(conn, "vendors"):
        return "vendors"
    if table_exists(conn, "partners"):
        return "partners"
    return None


def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS goods_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grn_no TEXT UNIQUE,
            grn_date TEXT,
            po_id INTEGER,
            vendor_id INTEGER,
            warehouse_id INTEGER,
            shortage_mode TEXT DEFAULT 'complete_later',
            status TEXT DEFAULT 'draft',
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS goods_receipt_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grn_id INTEGER NOT NULL,
            po_line_id INTEGER,
            item_id INTEGER,
            description TEXT,
            ordered_qty REAL DEFAULT 0,
            previously_received_qty REAL DEFAULT 0,
            open_qty_before REAL DEFAULT 0,
            received_qty REAL DEFAULT 0,
            accepted_qty REAL DEFAULT 0,
            rejected_qty REAL DEFAULT 0,
            variance_qty REAL DEFAULT 0,
            variance_type TEXT,
            status TEXT DEFAULT 'matched'
        )
    """)

    ensure_column(conn, "goods_receipts", "grn_no", "ALTER TABLE goods_receipts ADD COLUMN grn_no TEXT")
    ensure_column(conn, "goods_receipts", "grn_date", "ALTER TABLE goods_receipts ADD COLUMN grn_date TEXT")
    ensure_column(conn, "goods_receipts", "po_id", "ALTER TABLE goods_receipts ADD COLUMN po_id INTEGER")
    ensure_column(conn, "goods_receipts", "vendor_id", "ALTER TABLE goods_receipts ADD COLUMN vendor_id INTEGER")
    ensure_column(conn, "goods_receipts", "warehouse_id", "ALTER TABLE goods_receipts ADD COLUMN warehouse_id INTEGER")
    ensure_column(conn, "goods_receipts", "shortage_mode", "ALTER TABLE goods_receipts ADD COLUMN shortage_mode TEXT DEFAULT 'complete_later'")
    ensure_column(conn, "goods_receipts", "status", "ALTER TABLE goods_receipts ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "goods_receipts", "notes", "ALTER TABLE goods_receipts ADD COLUMN notes TEXT")
    ensure_column(conn, "goods_receipts", "created_at", "ALTER TABLE goods_receipts ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "goods_receipt_lines", "grn_id", "ALTER TABLE goods_receipt_lines ADD COLUMN grn_id INTEGER")
    ensure_column(conn, "goods_receipt_lines", "po_line_id", "ALTER TABLE goods_receipt_lines ADD COLUMN po_line_id INTEGER")
    ensure_column(conn, "goods_receipt_lines", "item_id", "ALTER TABLE goods_receipt_lines ADD COLUMN item_id INTEGER")
    ensure_column(conn, "goods_receipt_lines", "description", "ALTER TABLE goods_receipt_lines ADD COLUMN description TEXT")
    ensure_column(conn, "goods_receipt_lines", "ordered_qty", "ALTER TABLE goods_receipt_lines ADD COLUMN ordered_qty REAL DEFAULT 0")
    ensure_column(conn, "goods_receipt_lines", "previously_received_qty", "ALTER TABLE goods_receipt_lines ADD COLUMN previously_received_qty REAL DEFAULT 0")
    ensure_column(conn, "goods_receipt_lines", "open_qty_before", "ALTER TABLE goods_receipt_lines ADD COLUMN open_qty_before REAL DEFAULT 0")
    ensure_column(conn, "goods_receipt_lines", "received_qty", "ALTER TABLE goods_receipt_lines ADD COLUMN received_qty REAL DEFAULT 0")
    ensure_column(conn, "goods_receipt_lines", "accepted_qty", "ALTER TABLE goods_receipt_lines ADD COLUMN accepted_qty REAL DEFAULT 0")
    ensure_column(conn, "goods_receipt_lines", "rejected_qty", "ALTER TABLE goods_receipt_lines ADD COLUMN rejected_qty REAL DEFAULT 0")
    ensure_column(conn, "goods_receipt_lines", "variance_qty", "ALTER TABLE goods_receipt_lines ADD COLUMN variance_qty REAL DEFAULT 0")
    ensure_column(conn, "goods_receipt_lines", "variance_type", "ALTER TABLE goods_receipt_lines ADD COLUMN variance_type TEXT")
    ensure_column(conn, "goods_receipt_lines", "status", "ALTER TABLE goods_receipt_lines ADD COLUMN status TEXT DEFAULT 'matched'")

    conn.commit()
    conn.close()


ensure_tables()
ensure_inventory_tables()
ensure_workflow_tables()


def next_grn_no():
    conn = get_conn()
    row = conn.execute("""
        SELECT grn_no
        FROM goods_receipts
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["grn_no"]:
        return "GRN-0001"

    last = str(row["grn_no"])
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"GRN-{num + 1:04d}"


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


def get_open_purchase_orders(conn):
    if not table_exists(conn, "purchase_orders"):
        return []

    rows = conn.execute("""
        SELECT *
        FROM purchase_orders
        WHERE LOWER(COALESCE(status,'')) IN ('open', 'partial_received')
        ORDER BY id DESC
    """).fetchall()

    result = []
    for r in rows:
        label = f"{r['po_no']} | {vendor_display(conn, r['vendor_id'])}"
        result.append({
            "id": r["id"],
            "label": label
        })
    return result


def get_po_lines(conn, po_id):
    return conn.execute("""
        SELECT *
        FROM purchase_order_lines
        WHERE po_id = ?
          AND COALESCE(open_qty,0) > 0
        ORDER BY line_no, id
    """, (po_id,)).fetchall()


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
            let startsWithMatch = null;
            for (const opt of options) {
                const optVal = (opt.value || "").trim();
                if (optVal === val) {
                    hidden.value = opt.getAttribute(attrName) || "";
                    break;
                }
                if (!startsWithMatch && val && optVal.toLowerCase().startsWith(val.toLowerCase())) {
                    startsWithMatch = opt.getAttribute(attrName) || "";
                }
            }
            if (!hidden.value && startsWithMatch) {
                hidden.value = startsWithMatch;
            }
        }

        input.addEventListener("input", syncHidden);
        input.addEventListener("change", syncHidden);
        input.addEventListener("blur", syncHidden);

        const form = input.closest("form");
        if (form) {
            form.addEventListener("submit", syncHidden);
        }
    }

    window.addEventListener("DOMContentLoaded", function() {
        bindDatalistInput("po_label", "po_id", "po_list", "data-id");
    });
    </script>
    """


def datalist_options_by_id(items):
    return "".join([
        f"<option value=\"{item['label']}\" data-id=\"{item['id']}\"></option>"
        for item in items
    ])


def modal_html(title, message, kind="error", actions_html=""):
    badge_kind = kind if kind in ("error", "warning", "info") else "info"
    badge_label = {
        "error": "Input Error",
        "warning": "Decision Needed",
        "info": "Notice",
    }.get(badge_kind, "Notice")
    close_button = ""
    if not actions_html:
        actions_html = '<button type="button" class="btn gray" onclick="closePageModal()">Back To Form</button>'
    else:
        close_button = '<button type="button" class="modal-close" onclick="closePageModal()">×</button>'

    return f"""
    <div class="modal-shell" data-page-modal>
        <div class="modal-card-wrap">
            {close_button}
            <div class="modal-card">
                <div class="modal-head">
                    <div class="modal-kicker {badge_kind}">{badge_label}</div>
                    <div class="modal-title">{escape(title)}</div>
                </div>
                <div class="modal-body">
                    <div>{escape(message)}</div>
                    <div class="modal-actions">
                        {actions_html}
                    </div>
                </div>
            </div>
        </div>
    </div>
    """


def render_grn_form_page(request: Request, po_id: str = "", shortage_mode: str = "", form_values=None, popup_html=""):
    form_values = form_values or {}
    conn = get_conn()
    po_items = get_open_purchase_orders(conn)
    po_map = {str(p["id"]): p["label"] for p in po_items}
    if shortage_mode not in ("complete_later", "close_remaining"):
        shortage_mode = ""

    selected_po = None
    po_lines = []
    po_label_value = po_map.get(str(po_id), "")

    if po_id:
        try:
            po_id_int = int(po_id)
            selected_po = conn.execute("""
                SELECT *
                FROM purchase_orders
                WHERE id = ?
                LIMIT 1
            """, (po_id_int,)).fetchone()
            if selected_po:
                po_lines = get_po_lines(conn, po_id_int)
                po_label_value = f"{selected_po['po_no']} | {vendor_display(conn, selected_po['vendor_id'])}"
        except Exception:
            selected_po = None

    no_po_msg = ""
    if len(po_items) == 0:
        no_po_msg = (
            "<div class='msg warn' style='margin-top:10px;'>"
            "No approved/open PO available for receiving yet."
            "</div>"
        )

    def field(name, default=""):
        return escape(str(form_values.get(name) or default))

    lines_html = ""
    for l in po_lines:
        receive_val = field(f"receive_qty_{l['id']}", "0")
        rejected_val = field(f"rejected_qty_{l['id']}", "0")
        lines_html += f"""
        <tr>
            <td>{l['line_no']}</td>
            <td>{item_display(conn, l['item_id'])}</td>
            <td>{escape(str(l['description'] or ''))}</td>
            <td>{l['ordered_qty'] or 0}</td>
            <td>{l['received_qty'] or 0}</td>
            <td>{l['open_qty'] or 0}</td>
            <td>
                <input type="hidden" name="po_line_id_{l['id']}" value="{l['id']}">
                <input type="number" step="0.01" name="receive_qty_{l['id']}" value="{receive_val}">
            </td>
            <td><input type="number" step="0.01" name="rejected_qty_{l['id']}" value="{rejected_val}"></td>
        </tr>
        """

    html = f"""
    <div class="card">
        <h2>New Goods Receipt</h2>

        <form method="get" action="/ui/inventory/goods-receipts/new">
            <div class="row">
                <div class="col">
                    <label>Select PO</label>
                    <input id="po_label" list="po_list" autocomplete="off" placeholder="Search PO..." value="{escape(po_label_value)}">
                    <input type="hidden" id="po_id" name="po_id" value="{escape(str(po_id))}">
                    <input type="hidden" name="shortage_mode" value="{escape(shortage_mode)}">
                    <datalist id="po_list">
                        {datalist_options_by_id(po_items)}
                    </datalist>
                    {no_po_msg}
                </div>
                <div class="col" style="display:flex;align-items:end;">
                    <button class="btn blue" type="submit">Load PO</button>
                </div>
            </div>
        </form>
    </div>

    <div class="card">
        <form id="grnForm" method="post" action="/ui/inventory/goods-receipts/new">
            <div class="row">
                <div class="col">
                    <label>GRN No</label>
                    <input name="grn_no" value="{field('grn_no', next_grn_no())}" required>
                </div>
                <div class="col">
                    <label>GRN Date</label>
                    <input type="date" name="grn_date" value="{field('grn_date')}" required>
                </div>
            </div>

            <input type="hidden" name="po_id" value="{escape(str(po_id))}">
            <input type="hidden" name="shortage_mode" value="{escape(shortage_mode)}">

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Notes</label>
                    <input name="notes" value="{field('notes')}">
                </div>
                <div class="col"></div>
            </div>

            <div class="card" style="margin-top:14px;">
                <h3>Short Receipt Handling</h3>
                <p style="margin:0 0 8px 0;color:#6b7280;">
                    If receipt is short, you must choose whether supplier will complete later or close remaining qty.
                </p>
                <label style="display:block;margin-bottom:6px;">
                    <input type="radio" name="shortage_mode_choice" value="complete_later" {"checked" if shortage_mode == "complete_later" else ""}>
                    Supplier will complete remaining quantity later.
                </label>
                <label style="display:block;">
                    <input type="radio" name="shortage_mode_choice" value="close_remaining" {"checked" if shortage_mode == "close_remaining" else ""}>
                    Supplier will NOT complete. Close remaining quantity and finalize PO on received quantity.
                </label>
            </div>

            <div class="card" style="margin-top:18px;">
                <h3>Receipt Lines</h3>
                <table>
                    <tr>
                        <th>#</th>
                        <th>Item</th>
                        <th>Description</th>
                        <th>Ordered</th>
                        <th>Received</th>
                        <th>Open</th>
                        <th>Receive Now</th>
                        <th>Rejected</th>
                    </tr>
                    {lines_html}
                </table>
            </div>

            <div style="margin-top:18px;">
                <button class="btn green" type="submit">Save Receipt</button>
                <a class="btn gray" href="/ui/inventory/goods-receipts">Back</a>
            </div>
        </form>
    </div>

    {popup_html}
    {datalist_script()}
    <script>
    function submitGrnDecision(mode) {{
        const form = document.getElementById("grnForm");
        const radio = form ? form.querySelector(`input[name="shortage_mode_choice"][value="${{mode}}"]`) : null;
        if (radio) radio.checked = true;
        if (form) form.submit();
    }}
    </script>
    """

    conn.close()
    return HTMLResponse(render_page("New Goods Receipt", html, "en", current_path=request.url.path))


@router.get("/ui/inventory/goods-receipts", response_class=HTMLResponse)
def grn_list(request: Request):
    conn = get_conn()
    rows = conn.execute("""
        SELECT *
        FROM goods_receipts
        ORDER BY id DESC
    """).fetchall()

    body = ""
    for r in rows:
        body += f"""
        <tr>
            <td><a class="btn gray" href="/ui/inventory/goods-receipts/{r['id']}">{r['grn_no'] or ''}</a></td>
            <td>{r['grn_date'] or ''}</td>
            <td>{po_display(conn, r['po_id'])}</td>
            <td>{vendor_display(conn, r['vendor_id'])}</td>
            <td>{warehouse_display(conn, r['warehouse_id'])}</td>
            <td>{r['status'] or ''}</td>
        </tr>
        """

    conn.close()

    html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>Goods Receipts</h2>
            <a class="btn green" href="/ui/inventory/goods-receipts/new">New Receipt</a>
        </div>
    </div>

    <div class="card">
        <table>
            <tr>
                <th>GRN No</th>
                <th>Date</th>
                <th>PO</th>
                <th>Vendor</th>
                <th>Warehouse</th>
                <th>Status</th>
            </tr>
            {body}
        </table>
    </div>
    """

    return HTMLResponse(render_page("Goods Receipts", html, "en", current_path=request.url.path))


@router.get("/ui/inventory/goods-receipts/new", response_class=HTMLResponse)
def new_grn_form(request: Request, po_id: str = "", shortage_mode: str = ""):
    return render_grn_form_page(request, po_id=po_id, shortage_mode=shortage_mode)


@router.post("/ui/inventory/goods-receipts/new")
async def create_grn(request: Request):
    form = await request.form()
    form_values = dict(form)

    grn_no = (form.get("grn_no") or "").strip()
    grn_date = (form.get("grn_date") or "").strip()
    po_id_raw = (form.get("po_id") or "").strip()
    notes = (form.get("notes") or "").strip()
    shortage_mode = (form.get("shortage_mode_choice") or form.get("shortage_mode") or "").strip().lower()
    if shortage_mode not in ("complete_later", "close_remaining"):
        shortage_mode = ""

    if not grn_no:
        popup = modal_html("GRN number is missing", "Please enter the goods receipt number before saving.", "error")
        return render_grn_form_page(request, po_id=po_id_raw, shortage_mode=shortage_mode, form_values=form_values, popup_html=popup)
    if not grn_date:
        popup = modal_html("GRN date is missing", "Please choose the receipt date before saving.", "error")
        return render_grn_form_page(request, po_id=po_id_raw, shortage_mode=shortage_mode, form_values=form_values, popup_html=popup)
    if not po_id_raw:
        popup = modal_html("Purchase order is required", "Search and load a purchase order first, then continue the receipt.", "error")
        return render_grn_form_page(request, po_id="", shortage_mode=shortage_mode, form_values=form_values, popup_html=popup)

    try:
        po_id = int(po_id_raw)
    except Exception:
        popup = modal_html("Invalid purchase order", "The selected PO is not valid. Please search and load the PO again.", "error")
        return render_grn_form_page(request, po_id="", shortage_mode=shortage_mode, form_values=form_values, popup_html=popup)

    conn = get_conn()

    po = conn.execute("""
        SELECT *
        FROM purchase_orders
        WHERE id = ?
        LIMIT 1
    """, (po_id,)).fetchone()

    if not po:
        conn.close()
        popup = modal_html("Purchase order not found", "This PO is no longer available. Please reload the page and choose the PO again.", "error")
        return render_grn_form_page(request, po_id="", shortage_mode=shortage_mode, form_values=form_values, popup_html=popup)

    po_lines = conn.execute("""
        SELECT *
        FROM purchase_order_lines
        WHERE po_id = ?
        ORDER BY line_no, id
    """, (po_id,)).fetchall()

    parsed_lines = []
    created_lines = 0
    has_short_receipt = False
    for l in po_lines:
        try:
            receive_qty = float((form.get(f"receive_qty_{l['id']}") or "0").strip() or 0)
        except Exception:
            receive_qty = 0.0

        try:
            rejected_qty = float((form.get(f"rejected_qty_{l['id']}") or "0").strip() or 0)
        except Exception:
            rejected_qty = 0.0

        if receive_qty <= 0 and rejected_qty <= 0:
            continue

        open_before = float(l["open_qty"] or 0)
        if receive_qty < open_before:
            has_short_receipt = True

        parsed_lines.append((l, receive_qty, rejected_qty))
        created_lines += 1

    if created_lines == 0:
        conn.close()
        popup = modal_html("No receipt quantity entered", "Enter at least one received quantity before saving the goods receipt.", "error")
        return render_grn_form_page(request, po_id=po_id_raw, shortage_mode=shortage_mode, form_values=form_values, popup_html=popup)

    if has_short_receipt and shortage_mode not in ("complete_later", "close_remaining"):
        conn.close()
        actions = """
        <button type="button" class="btn blue" onclick="submitGrnDecision('complete_later')">Supplier Will Complete Later</button>
        <button type="button" class="btn orange" onclick="submitGrnDecision('close_remaining')">Close Remaining Qty</button>
        """
        popup = modal_html(
            "Short receipt detected",
            "The received quantity is less than the PO open quantity. Please decide whether the supplier will complete the remaining quantity later or you want to close the remaining balance now.",
            "warning",
            actions,
        )
        return render_grn_form_page(request, po_id=po_id_raw, shortage_mode="", form_values=form_values, popup_html=popup)

    shortage_mode_final = shortage_mode if has_short_receipt else "not_applicable"

    cur = conn.execute("""
        INSERT INTO goods_receipts (
            grn_no, grn_date, po_id, vendor_id, warehouse_id, shortage_mode, status, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, 'posted', ?)
    """, (
        grn_no,
        grn_date,
        po_id,
        po["vendor_id"],
        po["warehouse_id"],
        shortage_mode_final,
        notes,
    ))
    grn_id = cur.lastrowid

    short_count = 0
    over_count = 0
    accepted_total = 0.0

    for l, receive_qty, rejected_qty in parsed_lines:

        ordered_qty = float(l["ordered_qty"] or 0)
        prev_received = float(l["received_qty"] or 0)
        open_before = float(l["open_qty"] or 0)
        accepted_qty = max(receive_qty - rejected_qty, 0)

        variance_qty = 0.0
        variance_type = "matched"
        line_status = "matched"

        if receive_qty < open_before:
            variance_qty = open_before - receive_qty
            variance_type = "short"
            line_status = "partial"
            short_count += 1
        elif receive_qty == open_before:
            variance_qty = 0.0
            variance_type = "matched"
            line_status = "matched"
        elif receive_qty > open_before:
            variance_qty = receive_qty - open_before
            variance_type = "over"
            line_status = "over_received"
            over_count += 1

        conn.execute("""
            INSERT INTO goods_receipt_lines (
                grn_id, po_line_id, item_id, description,
                ordered_qty, previously_received_qty, open_qty_before,
                received_qty, accepted_qty, rejected_qty,
                variance_qty, variance_type, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            grn_id,
            l["id"],
            l["item_id"],
            l["description"],
            ordered_qty,
            prev_received,
            open_before,
            receive_qty,
            accepted_qty,
            rejected_qty,
            variance_qty,
            variance_type,
            line_status,
        ))
        grn_line_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        new_received = prev_received + receive_qty
        new_open = max(ordered_qty - new_received, 0)
        line_closed_by_decision = False
        if shortage_mode_final == "close_remaining" and new_open > 0:
            line_closed_by_decision = True
            new_open = 0.0

        if new_received <= 0:
            po_line_status = "closed_short" if line_closed_by_decision else "open"
        elif new_open > 0:
            po_line_status = "partial_received"
        else:
            po_line_status = "closed_short" if line_closed_by_decision else "received"

        conn.execute("""
            UPDATE purchase_order_lines
            SET received_qty = ?,
                open_qty = ?,
                status = ?
            WHERE id = ?
        """, (
            new_received,
            new_open,
            po_line_status,
            l["id"],
        ))

        if accepted_qty > 0:
            accepted_total += accepted_qty
            record_stock_movement(
                conn,
                trans_date=grn_date,
                trans_type="goods_receipt",
                trans_no=grn_no,
                reference_type="goods_receipt",
                reference_id=grn_id,
                reference_line_id=grn_line_id,
                warehouse_id=po["warehouse_id"],
                item_id=l["item_id"],
                description=l["description"] or f"Goods Receipt {grn_no}",
                qty_in=accepted_qty,
                qty_out=0,
                unit_cost=0,
            )

        created_lines += 1

    if shortage_mode_final == "close_remaining":
        untouched_open_lines = conn.execute(
            """
            SELECT id, received_qty, open_qty
            FROM purchase_order_lines
            WHERE po_id = ?
              AND COALESCE(open_qty, 0) > 0
            """,
            (po_id,),
        ).fetchall()
        for ul in untouched_open_lines:
            received_qty = float(ul["received_qty"] or 0)
            status = "closed_short" if received_qty > 0 else "closed_not_received"
            conn.execute(
                """
                UPDATE purchase_order_lines
                SET open_qty = 0,
                    status = ?
                WHERE id = ?
                """,
                (status, ul["id"]),
            )

    recalc_po_status(conn, po_id)
    updated_po = conn.execute(
        """
        SELECT status, po_no
        FROM purchase_orders
        WHERE id = ?
        LIMIT 1
        """,
        (po_id,),
    ).fetchone()
    po_status = (updated_po["status"] or "").lower() if updated_po else ""
    po_no = updated_po["po_no"] if updated_po else f"PO#{po_id}"

    create_po_alert(
        conn,
        po_id=po_id,
        grn_id=grn_id,
        alert_type="receipt",
        severity="success",
        title=f"Warehouse Receipt Posted ({grn_no})",
        message=(
            f"{po_no} received in warehouse. PO status: {po_status or 'updated'}."
            if shortage_mode_final != "close_remaining"
            else f"{po_no} received and remaining qty was closed by warehouse decision. PO status: {po_status or 'updated'}."
        ),
    )
    if short_count > 0 or over_count > 0:
        create_po_alert(
            conn,
            po_id=po_id,
            grn_id=grn_id,
            alert_type="variance",
            severity="warning",
            title="Receipt Variance Detected",
            message=f"{po_no}: short lines={short_count}, over lines={over_count}. Review PO Variances.",
        )
    if accepted_total > 0:
        create_po_alert(
            conn,
            po_id=po_id,
            grn_id=grn_id,
            alert_type="billing_ready",
            severity="info",
            title="Ready For Vendor Bill",
            message=f"{po_no}: accepted qty {accepted_total:,.2f} is ready for vendor billing.",
        )

    conn.commit()
    conn.close()

    return RedirectResponse(f"/ui/inventory/goods-receipts/{grn_id}", status_code=302)


@router.get("/ui/inventory/goods-receipts/{grn_id}", response_class=HTMLResponse)
def open_grn(request: Request, grn_id: int):
    conn = get_conn()

    grn = conn.execute("""
        SELECT *
        FROM goods_receipts
        WHERE id = ?
        LIMIT 1
    """, (grn_id,)).fetchone()

    if not grn:
        conn.close()
        return HTMLResponse("Goods Receipt not found", status_code=404)

    lines = conn.execute("""
        SELECT *
        FROM goods_receipt_lines
        WHERE grn_id = ?
        ORDER BY id
    """, (grn_id,)).fetchall()

    lines_html = ""
    for l in lines:
        lines_html += f"""
        <tr>
            <td>{item_display(conn, l['item_id'])}</td>
            <td>{l['description'] or ''}</td>
            <td>{l['ordered_qty'] or 0}</td>
            <td>{l['previously_received_qty'] or 0}</td>
            <td>{l['open_qty_before'] or 0}</td>
            <td>{l['received_qty'] or 0}</td>
            <td>{l['accepted_qty'] or 0}</td>
            <td>{l['rejected_qty'] or 0}</td>
            <td>{l['variance_qty'] or 0}</td>
            <td>{l['variance_type'] or ''}</td>
            <td>{l['status'] or ''}</td>
        </tr>
        """

    shortage_mode = (grn["shortage_mode"] or "").lower() if "shortage_mode" in grn.keys() else ""
    if shortage_mode == "complete_later":
        shortage_mode_text = "Supplier will complete remaining quantity later."
    elif shortage_mode == "close_remaining":
        shortage_mode_text = "Supplier will NOT complete. Remaining quantity closed."
    else:
        shortage_mode_text = "Not applicable (no short receipt)."

    html = f"""
    <div class="card">
        <h2>Goods Receipt {grn['grn_no'] or ''}</h2>

        <div class="row">
            <div class="col"><p><b>Date:</b> {grn['grn_date'] or ''}</p></div>
            <div class="col"><p><b>Status:</b> {grn['status'] or ''}</p></div>
        </div>

        <div class="row">
            <div class="col"><p><b>PO:</b> {po_display(conn, grn['po_id'])}</p></div>
            <div class="col"><p><b>Vendor:</b> {vendor_display(conn, grn['vendor_id'])}</p></div>
        </div>

        <div class="row">
            <div class="col"><p><b>Warehouse:</b> {warehouse_display(conn, grn['warehouse_id'])}</p></div>
            <div class="col"><p><b>Short Receipt Handling:</b> {shortage_mode_text}</p></div>
        </div>

        <p><b>Notes:</b> {grn['notes'] or ''}</p>

        <div style="margin-top:15px;">
            <a class="btn gray" href="/ui/inventory/goods-receipts">Back</a>
            <a class="btn blue" href="/ui/purchasing/purchase-orders/{grn['po_id']}">Open PO</a>
        </div>
    </div>

    <div class="card">
        <h3>Receipt Lines</h3>
        <table>
            <tr>
                <th>Item</th>
                <th>Description</th>
                <th>Ordered</th>
                <th>Prev Received</th>
                <th>Open Before</th>
                <th>Received</th>
                <th>Accepted</th>
                <th>Rejected</th>
                <th>Variance Qty</th>
                <th>Variance Type</th>
                <th>Status</th>
            </tr>
            {lines_html}
        </table>
    </div>
    """

    conn.close()
    return HTMLResponse(render_page("Goods Receipt", html, "en", current_path=request.url.path))
