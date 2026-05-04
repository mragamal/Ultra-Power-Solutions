import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page
from modules.inventory.core import ensure_inventory_tables

router = APIRouter()


def safe(value):
    return "" if value is None else str(value).strip()


def money(value):
    try:
        return f"{float(value or 0):,.2f}"
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
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return bool(row)


def ensure_tables():
    ensure_inventory_tables()
    conn = get_conn()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_quotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quotation_no TEXT UNIQUE,
            quotation_date TEXT,
            valid_until TEXT,
            customer_id INTEGER,
            status TEXT DEFAULT 'draft',
            notes TEXT,
            total_amount REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_quotation_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quotation_id INTEGER NOT NULL,
            line_no INTEGER DEFAULT 1,
            item_id INTEGER,
            description TEXT,
            qty REAL DEFAULT 0,
            unit_price REAL DEFAULT 0,
            line_total REAL DEFAULT 0
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sales_order_no TEXT UNIQUE,
            sales_order_date TEXT,
            quotation_id INTEGER,
            customer_id INTEGER,
            status TEXT DEFAULT 'draft',
            notes TEXT,
            total_amount REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_order_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sales_order_id INTEGER NOT NULL,
            line_no INTEGER DEFAULT 1,
            item_id INTEGER,
            description TEXT,
            qty REAL DEFAULT 0,
            unit_price REAL DEFAULT 0,
            line_total REAL DEFAULT 0
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery_no TEXT UNIQUE,
            delivery_date TEXT,
            sales_order_id INTEGER,
            customer_id INTEGER,
            status TEXT DEFAULT 'draft',
            notes TEXT,
            total_amount REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_note_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery_note_id INTEGER NOT NULL,
            line_no INTEGER DEFAULT 1,
            item_id INTEGER,
            description TEXT,
            qty REAL DEFAULT 0,
            unit_price REAL DEFAULT 0,
            line_total REAL DEFAULT 0
        )
        """
    )

    ensure_column(conn, "sales_quotations", "quotation_no", "ALTER TABLE sales_quotations ADD COLUMN quotation_no TEXT")
    ensure_column(conn, "sales_quotations", "quotation_date", "ALTER TABLE sales_quotations ADD COLUMN quotation_date TEXT")
    ensure_column(conn, "sales_quotations", "valid_until", "ALTER TABLE sales_quotations ADD COLUMN valid_until TEXT")
    ensure_column(conn, "sales_quotations", "customer_id", "ALTER TABLE sales_quotations ADD COLUMN customer_id INTEGER")
    ensure_column(conn, "sales_quotations", "status", "ALTER TABLE sales_quotations ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "sales_quotations", "notes", "ALTER TABLE sales_quotations ADD COLUMN notes TEXT")
    ensure_column(conn, "sales_quotations", "total_amount", "ALTER TABLE sales_quotations ADD COLUMN total_amount REAL DEFAULT 0")
    ensure_column(conn, "sales_quotations", "created_at", "ALTER TABLE sales_quotations ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "sales_quotation_lines", "quotation_id", "ALTER TABLE sales_quotation_lines ADD COLUMN quotation_id INTEGER")
    ensure_column(conn, "sales_quotation_lines", "line_no", "ALTER TABLE sales_quotation_lines ADD COLUMN line_no INTEGER DEFAULT 1")
    ensure_column(conn, "sales_quotation_lines", "item_id", "ALTER TABLE sales_quotation_lines ADD COLUMN item_id INTEGER")
    ensure_column(conn, "sales_quotation_lines", "description", "ALTER TABLE sales_quotation_lines ADD COLUMN description TEXT")
    ensure_column(conn, "sales_quotation_lines", "qty", "ALTER TABLE sales_quotation_lines ADD COLUMN qty REAL DEFAULT 0")
    ensure_column(conn, "sales_quotation_lines", "unit_price", "ALTER TABLE sales_quotation_lines ADD COLUMN unit_price REAL DEFAULT 0")
    ensure_column(conn, "sales_quotation_lines", "line_total", "ALTER TABLE sales_quotation_lines ADD COLUMN line_total REAL DEFAULT 0")

    ensure_column(conn, "sales_orders", "sales_order_no", "ALTER TABLE sales_orders ADD COLUMN sales_order_no TEXT")
    ensure_column(conn, "sales_orders", "sales_order_date", "ALTER TABLE sales_orders ADD COLUMN sales_order_date TEXT")
    ensure_column(conn, "sales_orders", "quotation_id", "ALTER TABLE sales_orders ADD COLUMN quotation_id INTEGER")
    ensure_column(conn, "sales_orders", "customer_id", "ALTER TABLE sales_orders ADD COLUMN customer_id INTEGER")
    ensure_column(conn, "sales_orders", "status", "ALTER TABLE sales_orders ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "sales_orders", "notes", "ALTER TABLE sales_orders ADD COLUMN notes TEXT")
    ensure_column(conn, "sales_orders", "total_amount", "ALTER TABLE sales_orders ADD COLUMN total_amount REAL DEFAULT 0")
    ensure_column(conn, "sales_orders", "created_at", "ALTER TABLE sales_orders ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "sales_order_lines", "sales_order_id", "ALTER TABLE sales_order_lines ADD COLUMN sales_order_id INTEGER")
    ensure_column(conn, "sales_order_lines", "line_no", "ALTER TABLE sales_order_lines ADD COLUMN line_no INTEGER DEFAULT 1")
    ensure_column(conn, "sales_order_lines", "item_id", "ALTER TABLE sales_order_lines ADD COLUMN item_id INTEGER")
    ensure_column(conn, "sales_order_lines", "description", "ALTER TABLE sales_order_lines ADD COLUMN description TEXT")
    ensure_column(conn, "sales_order_lines", "qty", "ALTER TABLE sales_order_lines ADD COLUMN qty REAL DEFAULT 0")
    ensure_column(conn, "sales_order_lines", "unit_price", "ALTER TABLE sales_order_lines ADD COLUMN unit_price REAL DEFAULT 0")
    ensure_column(conn, "sales_order_lines", "line_total", "ALTER TABLE sales_order_lines ADD COLUMN line_total REAL DEFAULT 0")

    ensure_column(conn, "delivery_notes", "delivery_no", "ALTER TABLE delivery_notes ADD COLUMN delivery_no TEXT")
    ensure_column(conn, "delivery_notes", "delivery_date", "ALTER TABLE delivery_notes ADD COLUMN delivery_date TEXT")
    ensure_column(conn, "delivery_notes", "sales_order_id", "ALTER TABLE delivery_notes ADD COLUMN sales_order_id INTEGER")
    ensure_column(conn, "delivery_notes", "customer_id", "ALTER TABLE delivery_notes ADD COLUMN customer_id INTEGER")
    ensure_column(conn, "delivery_notes", "status", "ALTER TABLE delivery_notes ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "delivery_notes", "notes", "ALTER TABLE delivery_notes ADD COLUMN notes TEXT")
    ensure_column(conn, "delivery_notes", "total_amount", "ALTER TABLE delivery_notes ADD COLUMN total_amount REAL DEFAULT 0")
    ensure_column(conn, "delivery_notes", "created_at", "ALTER TABLE delivery_notes ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "delivery_note_lines", "delivery_note_id", "ALTER TABLE delivery_note_lines ADD COLUMN delivery_note_id INTEGER")
    ensure_column(conn, "delivery_note_lines", "line_no", "ALTER TABLE delivery_note_lines ADD COLUMN line_no INTEGER DEFAULT 1")
    ensure_column(conn, "delivery_note_lines", "item_id", "ALTER TABLE delivery_note_lines ADD COLUMN item_id INTEGER")
    ensure_column(conn, "delivery_note_lines", "description", "ALTER TABLE delivery_note_lines ADD COLUMN description TEXT")
    ensure_column(conn, "delivery_note_lines", "qty", "ALTER TABLE delivery_note_lines ADD COLUMN qty REAL DEFAULT 0")
    ensure_column(conn, "delivery_note_lines", "unit_price", "ALTER TABLE delivery_note_lines ADD COLUMN unit_price REAL DEFAULT 0")
    ensure_column(conn, "delivery_note_lines", "line_total", "ALTER TABLE delivery_note_lines ADD COLUMN line_total REAL DEFAULT 0")

    conn.commit()
    conn.close()


def next_doc_no(table_name, column_name, prefix):
    conn = get_conn()
    row = conn.execute(
        f"""
        SELECT {column_name}
        FROM {table_name}
        WHERE COALESCE({column_name}, '') <> ''
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()

    last = safe(row[column_name]) if row else ""
    if not last:
        return f"{prefix}-0001"
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"{prefix}-{num + 1:04d}"


def customer_records():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, code, name
        FROM partners
        WHERE LOWER(COALESCE(partner_type, '')) = 'customer'
          AND COALESCE(is_active, 1) = 1
        ORDER BY name, code
        """
    ).fetchall()
    conn.close()
    return [
        {
            "id": str(r["id"]),
            "label": f"{safe(r['code'])} - {safe(r['name'])}" if safe(r["code"]) else safe(r["name"]),
        }
        for r in rows
        if safe(r["name"])
    ]


def item_records():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, code, name
        FROM items
        WHERE COALESCE(is_active, 1) = 1
        ORDER BY name, code
        """
    ).fetchall()
    conn.close()
    return [
        {
            "id": str(r["id"]),
            "label": f"{safe(r['code'])} - {safe(r['name'])}" if safe(r["code"]) else safe(r["name"]),
        }
        for r in rows
        if safe(r["name"])
    ]


def datalist_options_by_id(records):
    return "".join(
        f'<option value="{safe(r["label"])}" data-id="{safe(r["id"])}"></option>'
        for r in records
    )


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
    label = safe(typed_label).lower()
    if not label:
        return ""
    if label in item_lookup:
        return str(item_lookup[label])
    for item_label, item_id in item_lookup.items():
        if item_label.startswith(label):
            return str(item_id)
    return ""


def extract_lines(form):
    raw_items = item_records()
    item_lookup = {}
    for rec in raw_items:
        key = safe(rec.get("label")).lower()
        if key and key not in item_lookup:
            item_lookup[key] = int(rec["id"])

    lines = []
    for idx in parse_line_indices(form):
        item_id = safe(form.get(f"item_id_{idx}"))
        item_label = safe(form.get(f"item_label_{idx}"))
        if not item_id and item_label:
            item_id = resolve_item_id(item_lookup, item_label)

        description = safe(form.get(f"description_{idx}"))
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


def normalize_lines(lines, item_map):
    normalized = []
    for idx, line in enumerate(lines or []):
        item_id = str(line.get("item_id") or "")
        normalized.append(
            {
                "idx": idx,
                "item_id": item_id,
                "item_label": item_map.get(item_id, ""),
                "description": safe(line.get("description")),
                "qty": f"{float(line.get('qty') or 0):.2f}",
                "price": f"{float(line.get('unit_price') or 0):.2f}",
            }
        )
    return normalized or [{"idx": 0, "item_id": "", "item_label": "", "description": "", "qty": "1.00", "price": "0.00"}]


def status_chip(status):
    val = safe(status).lower()
    if val in ("confirmed", "approved", "delivered", "converted"):
        return '<span class="status-chip green">%s</span>' % safe(status or "draft")
    if val in ("draft", ""):
        return '<span class="status-chip blue">Draft</span>'
    return '<span class="status-chip orange">%s</span>' % safe(status)


def sales_form_html(title, action_url, doc_no_label, doc_no_name, doc_no_value, date_label, date_name, date_value, values=None, lines=None, info_note="", extra_field_html=""):
    values = values or {}
    customers = customer_records()
    items = item_records()
    customer_map = {str(c["id"]): c["label"] for c in customers}
    item_map = {str(i["id"]): i["label"] for i in items}
    customer_id = str(values.get("customer_id") or "")
    customer_label = customer_map.get(customer_id, "")
    normalized_lines = normalize_lines(lines, item_map)

    return f"""
    <div class="card">
        <h2>{title}</h2>
        <p class="section-note">{info_note}</p>

        <form method="post" action="{action_url}">
            <div class="row">
                <div class="col">
                    <label>{doc_no_label}</label>
                    <input name="{doc_no_name}" value="{safe(doc_no_value)}" required>
                </div>
                <div class="col">
                    <label>{date_label}</label>
                    <input type="date" name="{date_name}" value="{safe(date_value)}" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Customer</label>
                    <input id="customer_label" list="customer_list" autocomplete="off" placeholder="Search customer..." value="{customer_label}">
                    <input type="hidden" id="customer_id" name="customer_id" value="{customer_id}">
                    <datalist id="customer_list">
                        {datalist_options_by_id(customers)}
                    </datalist>
                </div>
                <div class="col">
                    {extra_field_html}
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Notes</label>
                    <input name="notes" value="{safe(values.get('notes'))}">
                </div>
                <div class="col"></div>
            </div>

            <div class="card" style="margin-top:18px;">
                <h3>Lines</h3>
                <table>
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
                    <tbody id="doc_lines_body"></tbody>
                </table>
                <div style="margin-top:12px;">
                    <button type="button" class="btn blue" onclick="addLine()">Add Line</button>
                </div>
            </div>

            <div style="margin-top:18px;">
                <button class="btn green" type="submit">Save</button>
                <a class="btn gray" href="/ui/sales">Back</a>
            </div>
        </form>
    </div>

    <datalist id="item_list">
        {datalist_options_by_id(items)}
    </datalist>

    <script>
    let docLineIndex = 0;
    const initialLines = {json.dumps(normalized_lines)};

    function bindDatalistInputEl(input, hidden, listId, attrName) {{
        const list = document.getElementById(listId);
        if (!input || !hidden || !list) return;

        function syncHidden() {{
            const val = (input.value || "").trim();
            hidden.value = "";
            let startsWithMatch = "";
            list.querySelectorAll("option").forEach((opt) => {{
                const optVal = (opt.value || "").trim();
                if (optVal === val) {{
                    hidden.value = opt.getAttribute(attrName) || "";
                }}
                if (!startsWithMatch && val && optVal.toLowerCase().startsWith(val.toLowerCase())) {{
                    startsWithMatch = opt.getAttribute(attrName) || "";
                }}
            }});
            if (!hidden.value && startsWithMatch) {{
                hidden.value = startsWithMatch;
            }}
        }}

        input.addEventListener("input", syncHidden);
        input.addEventListener("change", syncHidden);
        input.addEventListener("blur", syncHidden);
    }}

    function recalcRow(row) {{
        const qty = parseFloat(row.querySelector(".line-qty")?.value || "0") || 0;
        const price = parseFloat(row.querySelector(".line-price")?.value || "0") || 0;
        row.querySelector(".line-total").value = (qty * price).toFixed(2);
    }}

    function bindLine(row) {{
        bindDatalistInputEl(row.querySelector(".item-label"), row.querySelector(".item-id"), "item_list", "data-id");
        row.querySelectorAll(".line-qty, .line-price").forEach((el) => {{
            el.addEventListener("input", () => recalcRow(row));
            el.addEventListener("change", () => recalcRow(row));
        }});
    }}

    function addLine(lineData = null) {{
        const row = document.createElement("tr");
        const i = docLineIndex;
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
            <td><input type="number" step="0.01" class="line-qty" name="qty_${{i}}" value="${{qty}}"></td>
            <td><input type="number" step="0.01" class="line-price" name="price_${{i}}" value="${{price}}"></td>
            <td><input class="line-total" value="0.00" readonly></td>
            <td><button type="button" class="btn red" onclick="this.closest('tr').remove()">X</button></td>
        `;
        document.getElementById("doc_lines_body").appendChild(row);
        bindLine(row);
        recalcRow(row);
        docLineIndex++;
    }}

    window.addEventListener("DOMContentLoaded", function() {{
        bindDatalistInputEl(document.getElementById("customer_label"), document.getElementById("customer_id"), "customer_list", "data-id");
        if (initialLines.length > 0) {{
            initialLines.forEach((line) => addLine(line));
        }} else {{
            addLine();
        }}
    }});
    </script>
    """


def sales_root_html():
    conn = get_conn()
    quote_count = conn.execute("SELECT COUNT(*) AS c FROM sales_quotations").fetchone()["c"]
    order_count = conn.execute("SELECT COUNT(*) AS c FROM sales_orders").fetchone()["c"]
    delivery_count = conn.execute("SELECT COUNT(*) AS c FROM delivery_notes").fetchone()["c"]
    invoice_count = conn.execute("SELECT COUNT(*) AS c FROM customer_invoices").fetchone()["c"] if table_exists(conn, "customer_invoices") else 0
    conn.close()

    return f"""
    <div class="report-hero">
        <div>
            <div class="report-hero-kicker">Sales Workspace</div>
            <h2>Sales Module</h2>
            <p>Run the commercial cycle from quotation to sales order and delivery, with direct access to customer invoices, collections, and statements from the same place.</p>
        </div>
        <div class="kpi-grid">
            <div class="kpi-card"><div class="kpi-label">Quotations</div><div class="kpi-value">{int(quote_count or 0)}</div></div>
            <div class="kpi-card"><div class="kpi-label">Sales Orders</div><div class="kpi-value">{int(order_count or 0)}</div></div>
            <div class="kpi-card"><div class="kpi-label">Deliveries</div><div class="kpi-value">{int(delivery_count or 0)}</div></div>
            <div class="kpi-card"><div class="kpi-label">Invoices</div><div class="kpi-value">{int(invoice_count or 0)}</div></div>
        </div>
    </div>

    <div class="card-grid">
        <a href="/ui/sales/quotations" class="module-card">
            <div class="module-card-icon"><img src="/static/icons/customer-statement.svg" alt="Quotations"></div>
            <div class="module-card-title">Quotations</div>
            <div class="module-card-sub">Price offers, negotiation, and pre-sale documents.</div>
        </a>
        <a href="/ui/sales/orders" class="module-card">
            <div class="module-card-icon"><img src="/static/icons/purchase-orders.svg" alt="Sales Orders"></div>
            <div class="module-card-title">Sales Orders</div>
            <div class="module-card-sub">Confirmed customer demand ready for delivery or invoicing.</div>
        </a>
        <a href="/ui/sales/deliveries" class="module-card">
            <div class="module-card-icon"><img src="/static/icons/goods-receipts.svg" alt="Delivery Notes"></div>
            <div class="module-card-title">Delivery Notes</div>
            <div class="module-card-sub">Track goods handover before billing and operations follow-up.</div>
        </a>
        <a href="/ui/accounting/customer-invoices" class="module-card">
            <div class="module-card-icon"><img src="/static/icons/customer-invoices.svg" alt="Customer Invoices"></div>
            <div class="module-card-title">Customer Invoices</div>
            <div class="module-card-sub">Commercial billing linked with accounting and collections.</div>
        </a>
        <a href="/ui/accounting/customer-payments" class="module-card">
            <div class="module-card-icon"><img src="/static/icons/customer-payments.svg" alt="Customer Receipts"></div>
            <div class="module-card-title">Customer Receipts</div>
            <div class="module-card-sub">Track collections and allocate them to customer invoices.</div>
        </a>
        <a href="/ui/accounting/customer-statement" class="module-card">
            <div class="module-card-icon"><img src="/static/icons/customer-statement.svg" alt="Customer Statement"></div>
            <div class="module-card-title">Customer Statement</div>
            <div class="module-card-sub">Open balances and movement history per customer.</div>
        </a>
    </div>
    """


def list_page_html(title, subtitle, new_href, rows_html, cols):
    return f"""
    <div class="card">
        <div class="list-header">
            <div class="list-title">
                <h2>{title}</h2>
                <p>{subtitle}</p>
            </div>
            <div class="action-strip">
                <a class="btn gray" href="/ui/sales">Back to Sales</a>
                <a class="btn green" href="{new_href}">New</a>
            </div>
        </div>
    </div>

    <div class="card">
        <div class="table-wrap">
            <table>
                {cols}
                {rows_html}
            </table>
        </div>
    </div>
    """


def detail_page_html(title, back_href, doc, lines, doc_no_label, doc_no_key, date_label, date_key, extra_meta_html="", next_action_html=""):
    line_rows = ""
    total_amount = 0.0
    for line in lines:
        total_amount += float(line["line_total"] or 0)
        line_rows += f"""
        <tr>
            <td>{safe(line['item_code'])}</td>
            <td>{safe(line['item_name'])}</td>
            <td>{safe(line['description'])}</td>
            <td class="number-cell">{float(line['qty'] or 0):,.2f}</td>
            <td class="number-cell">{money(line['unit_price'])}</td>
            <td class="number-cell">{money(line['line_total'])}</td>
        </tr>
        """
    if not line_rows:
        line_rows = "<tr><td colspan='6' style='text-align:center;'>No lines found.</td></tr>"

    return f"""
    <div class="card">
        <div class="list-header">
            <div class="list-title">
                <h2>{title}</h2>
                <p>{status_chip(doc['status'])}</p>
            </div>
            <div class="action-strip">
                <a class="btn gray" href="{back_href}">Back</a>
                {next_action_html}
            </div>
        </div>
    </div>

    <div class="card">
        <div class="row">
            <div class="col">
                <p><b>{doc_no_label}:</b> {safe(doc[doc_no_key])}</p>
                <p><b>{date_label}:</b> {safe(doc[date_key])}</p>
                <p><b>Customer:</b> {safe(doc['customer_name'])}</p>
            </div>
            <div class="col">
                {extra_meta_html}
                <p><b>Total:</b> {money(total_amount)}</p>
                <p><b>Notes:</b> {safe(doc['notes'])}</p>
            </div>
        </div>
    </div>

    <div class="card">
        <div class="table-wrap">
            <table>
                <tr>
                    <th>Item Code</th>
                    <th>Item Name</th>
                    <th>Description</th>
                    <th>Qty</th>
                    <th>Unit Price</th>
                    <th>Total</th>
                </tr>
                {line_rows}
            </table>
        </div>
    </div>
    """


ensure_tables()


@router.get("/ui/sales", response_class=HTMLResponse)
def sales_root(request: Request):
    return HTMLResponse(render_page("Sales", sales_root_html(), current_path=request.url.path))


@router.get("/ui/sales/customer-invoices")
def sales_invoices_redirect():
    return RedirectResponse("/ui/accounting/customer-invoices", status_code=302)


@router.get("/ui/sales/customer-payments")
def sales_payments_redirect():
    return RedirectResponse("/ui/accounting/customer-payments", status_code=302)


@router.get("/ui/sales/customer-statement")
def sales_statement_redirect():
    return RedirectResponse("/ui/accounting/customer-statement", status_code=302)


@router.get("/ui/sales/quotations", response_class=HTMLResponse)
def quotations_list(request: Request):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT q.*, p.name AS customer_name
        FROM sales_quotations q
        LEFT JOIN partners p ON p.id = q.customer_id
        ORDER BY q.id DESC
        """
    ).fetchall()
    conn.close()

    body = """
    <tr>
        <th>Quotation No</th>
        <th>Date</th>
        <th>Valid Until</th>
        <th>Customer</th>
        <th>Status</th>
        <th>Total</th>
        <th>Action</th>
    </tr>
    """
    for row in rows:
        body += f"""
        <tr>
            <td><a class="btn gray" href="/ui/sales/quotations/{row['id']}">{safe(row['quotation_no'])}</a></td>
            <td>{safe(row['quotation_date'])}</td>
            <td>{safe(row['valid_until'])}</td>
            <td>{safe(row['customer_name'])}</td>
            <td>{status_chip(row['status'])}</td>
            <td class="number-cell">{money(row['total_amount'])}</td>
            <td><a class="btn blue" href="/ui/sales/quotations/{row['id']}">Open</a></td>
        </tr>
        """
    if not rows:
        body += "<tr><td colspan='7' style='text-align:center;'>No quotations found.</td></tr>"

    html = list_page_html("Quotations", "Create and manage customer quotations before sales confirmation.", "/ui/sales/quotations/new", body, "")
    return HTMLResponse(render_page("Quotations", html, current_path=request.url.path))


@router.get("/ui/sales/quotations/new", response_class=HTMLResponse)
def quotations_new(request: Request):
    html = sales_form_html(
        "New Quotation",
        "/ui/sales/quotations/new",
        "Quotation No",
        "quotation_no",
        next_doc_no("sales_quotations", "quotation_no", "QT"),
        "Quotation Date",
        "quotation_date",
        "",
        values={},
        lines=[],
        info_note="Start the sales cycle from a quotation, then convert it to a sales order when approved by the customer.",
        extra_field_html='<label>Valid Until</label><input type="date" name="valid_until" value="">',
    )
    return HTMLResponse(render_page("New Quotation", html, current_path=request.url.path))


@router.post("/ui/sales/quotations/new")
async def quotations_create(request: Request):
    form = await request.form()
    lines = extract_lines(form)
    total = sum(float(line["line_total"] or 0) for line in lines)

    conn = get_conn()
    cur = conn.execute(
        """
        INSERT INTO sales_quotations (
            quotation_no, quotation_date, valid_until, customer_id, status, notes, total_amount
        )
        VALUES (?, ?, ?, ?, 'draft', ?, ?)
        """,
        (
            safe(form.get("quotation_no")) or next_doc_no("sales_quotations", "quotation_no", "QT"),
            safe(form.get("quotation_date")),
            safe(form.get("valid_until")),
            int(safe(form.get("customer_id")) or 0) or None,
            safe(form.get("notes")),
            total,
        ),
    )
    quotation_id = cur.lastrowid
    for idx, line in enumerate(lines, start=1):
        conn.execute(
            """
            INSERT INTO sales_quotation_lines (
                quotation_id, line_no, item_id, description, qty, unit_price, line_total
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (quotation_id, idx, line["item_id"], line["description"], line["qty"], line["unit_price"], line["line_total"]),
        )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/ui/sales/quotations/{quotation_id}", status_code=302)


@router.get("/ui/sales/quotations/{quotation_id}", response_class=HTMLResponse)
def quotation_open(request: Request, quotation_id: int):
    conn = get_conn()
    doc = conn.execute(
        """
        SELECT q.*, p.name AS customer_name
        FROM sales_quotations q
        LEFT JOIN partners p ON p.id = q.customer_id
        WHERE q.id = ?
        LIMIT 1
        """,
        (quotation_id,),
    ).fetchone()
    if not doc:
        conn.close()
        return HTMLResponse("Quotation not found", status_code=404)
    lines = conn.execute(
        """
        SELECT l.*, i.code AS item_code, i.name AS item_name
        FROM sales_quotation_lines l
        LEFT JOIN items i ON i.id = l.item_id
        WHERE l.quotation_id = ?
        ORDER BY l.line_no, l.id
        """,
        (quotation_id,),
    ).fetchall()
    conn.close()

    html = detail_page_html(
        "Quotation",
        "/ui/sales/quotations",
        doc,
        lines,
        "Quotation No",
        "quotation_no",
        "Quotation Date",
        "quotation_date",
        extra_meta_html=f"<p><b>Valid Until:</b> {safe(doc['valid_until'])}</p>",
        next_action_html=f'<a class="btn green" href="/ui/sales/orders/new?quotation_id={quotation_id}">Create Sales Order</a>',
    )
    return HTMLResponse(render_page("Quotation", html, current_path=request.url.path))


def quotation_prefill(quotation_id):
    if not quotation_id:
        return None
    conn = get_conn()
    doc = conn.execute("SELECT * FROM sales_quotations WHERE id = ? LIMIT 1", (quotation_id,)).fetchone()
    lines = conn.execute(
        """
        SELECT item_id, description, qty, unit_price
        FROM sales_quotation_lines
        WHERE quotation_id = ?
        ORDER BY line_no, id
        """,
        (quotation_id,),
    ).fetchall()
    conn.close()
    if not doc:
        return None
    return {"doc": doc, "lines": [dict(line) for line in lines]}


@router.get("/ui/sales/orders", response_class=HTMLResponse)
def orders_list(request: Request):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT o.*, p.name AS customer_name, q.quotation_no
        FROM sales_orders o
        LEFT JOIN partners p ON p.id = o.customer_id
        LEFT JOIN sales_quotations q ON q.id = o.quotation_id
        ORDER BY o.id DESC
        """
    ).fetchall()
    conn.close()

    body = """
    <tr>
        <th>SO No</th>
        <th>Date</th>
        <th>Customer</th>
        <th>Quotation</th>
        <th>Status</th>
        <th>Total</th>
        <th>Action</th>
    </tr>
    """
    for row in rows:
        body += f"""
        <tr>
            <td><a class="btn gray" href="/ui/sales/orders/{row['id']}">{safe(row['sales_order_no'])}</a></td>
            <td>{safe(row['sales_order_date'])}</td>
            <td>{safe(row['customer_name'])}</td>
            <td>{safe(row['quotation_no'])}</td>
            <td>{status_chip(row['status'])}</td>
            <td class="number-cell">{money(row['total_amount'])}</td>
            <td><a class="btn blue" href="/ui/sales/orders/{row['id']}">Open</a></td>
        </tr>
        """
    if not rows:
        body += "<tr><td colspan='7' style='text-align:center;'>No sales orders found.</td></tr>"

    html = list_page_html("Sales Orders", "Confirmed commercial orders ready for delivery and invoicing.", "/ui/sales/orders/new", body, "")
    return HTMLResponse(render_page("Sales Orders", html, current_path=request.url.path))


@router.get("/ui/sales/orders/new", response_class=HTMLResponse)
def orders_new(request: Request, quotation_id: int = 0):
    values = {}
    lines = []
    note = "Create the sales order directly, or prefill it from an approved quotation."
    extra_field = '<label>Source Quotation</label><input name="quotation_id_display" value="" readonly>'

    prefill = quotation_prefill(quotation_id)
    if prefill:
        values = {
            "customer_id": prefill["doc"]["customer_id"],
            "notes": prefill["doc"]["notes"],
        }
        lines = prefill["lines"]
        note = "This sales order was prefilled from the selected quotation. You can review and save it now."
        extra_field = f'<label>Source Quotation</label><input name="quotation_id_display" value="{safe(prefill["doc"]["quotation_no"])}" readonly><input type="hidden" name="quotation_id" value="{quotation_id}">'
    else:
        extra_field = '<label>Source Quotation</label><input name="quotation_id_display" value="" readonly><input type="hidden" name="quotation_id" value="">'

    html = sales_form_html(
        "New Sales Order",
        "/ui/sales/orders/new",
        "Sales Order No",
        "sales_order_no",
        next_doc_no("sales_orders", "sales_order_no", "SO"),
        "Sales Order Date",
        "sales_order_date",
        "",
        values=values,
        lines=lines,
        info_note=note,
        extra_field_html=extra_field,
    )
    return HTMLResponse(render_page("New Sales Order", html, current_path=request.url.path))


@router.post("/ui/sales/orders/new")
async def orders_create(request: Request):
    form = await request.form()
    lines = extract_lines(form)
    total = sum(float(line["line_total"] or 0) for line in lines)

    conn = get_conn()
    cur = conn.execute(
        """
        INSERT INTO sales_orders (
            sales_order_no, sales_order_date, quotation_id, customer_id, status, notes, total_amount
        )
        VALUES (?, ?, ?, ?, 'draft', ?, ?)
        """,
        (
            safe(form.get("sales_order_no")) or next_doc_no("sales_orders", "sales_order_no", "SO"),
            safe(form.get("sales_order_date")),
            int(safe(form.get("quotation_id")) or 0) or None,
            int(safe(form.get("customer_id")) or 0) or None,
            safe(form.get("notes")),
            total,
        ),
    )
    sales_order_id = cur.lastrowid
    for idx, line in enumerate(lines, start=1):
        conn.execute(
            """
            INSERT INTO sales_order_lines (
                sales_order_id, line_no, item_id, description, qty, unit_price, line_total
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sales_order_id, idx, line["item_id"], line["description"], line["qty"], line["unit_price"], line["line_total"]),
        )
    if int(safe(form.get("quotation_id")) or 0) > 0:
        conn.execute("UPDATE sales_quotations SET status = 'converted' WHERE id = ?", (int(form.get("quotation_id")),))
    conn.commit()
    conn.close()
    return RedirectResponse(f"/ui/sales/orders/{sales_order_id}", status_code=302)


@router.get("/ui/sales/orders/{sales_order_id}", response_class=HTMLResponse)
def order_open(request: Request, sales_order_id: int):
    conn = get_conn()
    doc = conn.execute(
        """
        SELECT o.*, p.name AS customer_name, q.quotation_no
        FROM sales_orders o
        LEFT JOIN partners p ON p.id = o.customer_id
        LEFT JOIN sales_quotations q ON q.id = o.quotation_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (sales_order_id,),
    ).fetchone()
    if not doc:
        conn.close()
        return HTMLResponse("Sales order not found", status_code=404)
    lines = conn.execute(
        """
        SELECT l.*, i.code AS item_code, i.name AS item_name
        FROM sales_order_lines l
        LEFT JOIN items i ON i.id = l.item_id
        WHERE l.sales_order_id = ?
        ORDER BY l.line_no, l.id
        """,
        (sales_order_id,),
    ).fetchall()
    conn.close()

    quotation_meta = f"<p><b>Quotation:</b> {safe(doc['quotation_no'])}</p>" if safe(doc["quotation_no"]) else ""
    html = detail_page_html(
        "Sales Order",
        "/ui/sales/orders",
        doc,
        lines,
        "SO No",
        "sales_order_no",
        "SO Date",
        "sales_order_date",
        extra_meta_html=quotation_meta,
        next_action_html=f'<a class="btn green" href="/ui/sales/deliveries/new?sales_order_id={sales_order_id}">Create Delivery</a>',
    )
    return HTMLResponse(render_page("Sales Order", html, current_path=request.url.path))


def order_prefill(sales_order_id):
    if not sales_order_id:
        return None
    conn = get_conn()
    doc = conn.execute("SELECT * FROM sales_orders WHERE id = ? LIMIT 1", (sales_order_id,)).fetchone()
    lines = conn.execute(
        """
        SELECT item_id, description, qty, unit_price
        FROM sales_order_lines
        WHERE sales_order_id = ?
        ORDER BY line_no, id
        """,
        (sales_order_id,),
    ).fetchall()
    conn.close()
    if not doc:
        return None
    return {"doc": doc, "lines": [dict(line) for line in lines]}


@router.get("/ui/sales/deliveries", response_class=HTMLResponse)
def deliveries_list(request: Request):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT d.*, p.name AS customer_name, o.sales_order_no
        FROM delivery_notes d
        LEFT JOIN partners p ON p.id = d.customer_id
        LEFT JOIN sales_orders o ON o.id = d.sales_order_id
        ORDER BY d.id DESC
        """
    ).fetchall()
    conn.close()

    body = """
    <tr>
        <th>Delivery No</th>
        <th>Date</th>
        <th>Customer</th>
        <th>Sales Order</th>
        <th>Status</th>
        <th>Total</th>
        <th>Action</th>
    </tr>
    """
    for row in rows:
        body += f"""
        <tr>
            <td><a class="btn gray" href="/ui/sales/deliveries/{row['id']}">{safe(row['delivery_no'])}</a></td>
            <td>{safe(row['delivery_date'])}</td>
            <td>{safe(row['customer_name'])}</td>
            <td>{safe(row['sales_order_no'])}</td>
            <td>{status_chip(row['status'])}</td>
            <td class="number-cell">{money(row['total_amount'])}</td>
            <td><a class="btn blue" href="/ui/sales/deliveries/{row['id']}">Open</a></td>
        </tr>
        """
    if not rows:
        body += "<tr><td colspan='7' style='text-align:center;'>No delivery notes found.</td></tr>"

    html = list_page_html("Delivery Notes", "Deliver goods against the sales order and prepare for invoicing or operations handoff.", "/ui/sales/deliveries/new", body, "")
    return HTMLResponse(render_page("Delivery Notes", html, current_path=request.url.path))


@router.get("/ui/sales/deliveries/new", response_class=HTMLResponse)
def deliveries_new(request: Request, sales_order_id: int = 0):
    values = {}
    lines = []
    note = "Create the delivery note directly, or prefill it from an existing sales order."
    extra_field = '<label>Source Sales Order</label><input name="sales_order_id_display" value="" readonly><input type="hidden" name="sales_order_id" value="">'

    prefill = order_prefill(sales_order_id)
    if prefill:
        values = {
            "customer_id": prefill["doc"]["customer_id"],
            "notes": prefill["doc"]["notes"],
        }
        lines = prefill["lines"]
        note = "This delivery note was prefilled from the selected sales order. Review and save the delivered quantities."
        extra_field = f'<label>Source Sales Order</label><input name="sales_order_id_display" value="{safe(prefill["doc"]["sales_order_no"])}" readonly><input type="hidden" name="sales_order_id" value="{sales_order_id}">'

    html = sales_form_html(
        "New Delivery Note",
        "/ui/sales/deliveries/new",
        "Delivery No",
        "delivery_no",
        next_doc_no("delivery_notes", "delivery_no", "DN"),
        "Delivery Date",
        "delivery_date",
        "",
        values=values,
        lines=lines,
        info_note=note,
        extra_field_html=extra_field,
    )
    return HTMLResponse(render_page("New Delivery Note", html, current_path=request.url.path))


@router.post("/ui/sales/deliveries/new")
async def deliveries_create(request: Request):
    form = await request.form()
    lines = extract_lines(form)
    total = sum(float(line["line_total"] or 0) for line in lines)

    conn = get_conn()
    cur = conn.execute(
        """
        INSERT INTO delivery_notes (
            delivery_no, delivery_date, sales_order_id, customer_id, status, notes, total_amount
        )
        VALUES (?, ?, ?, ?, 'draft', ?, ?)
        """,
        (
            safe(form.get("delivery_no")) or next_doc_no("delivery_notes", "delivery_no", "DN"),
            safe(form.get("delivery_date")),
            int(safe(form.get("sales_order_id")) or 0) or None,
            int(safe(form.get("customer_id")) or 0) or None,
            safe(form.get("notes")),
            total,
        ),
    )
    delivery_id = cur.lastrowid
    for idx, line in enumerate(lines, start=1):
        conn.execute(
            """
            INSERT INTO delivery_note_lines (
                delivery_note_id, line_no, item_id, description, qty, unit_price, line_total
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (delivery_id, idx, line["item_id"], line["description"], line["qty"], line["unit_price"], line["line_total"]),
        )
    if int(safe(form.get("sales_order_id")) or 0) > 0:
        conn.execute("UPDATE sales_orders SET status = 'confirmed' WHERE id = ?", (int(form.get("sales_order_id")),))
    conn.commit()
    conn.close()
    return RedirectResponse(f"/ui/sales/deliveries/{delivery_id}", status_code=302)


@router.get("/ui/sales/deliveries/{delivery_id}", response_class=HTMLResponse)
def delivery_open(request: Request, delivery_id: int):
    conn = get_conn()
    doc = conn.execute(
        """
        SELECT d.*, p.name AS customer_name, o.sales_order_no
        FROM delivery_notes d
        LEFT JOIN partners p ON p.id = d.customer_id
        LEFT JOIN sales_orders o ON o.id = d.sales_order_id
        WHERE d.id = ?
        LIMIT 1
        """,
        (delivery_id,),
    ).fetchone()
    if not doc:
        conn.close()
        return HTMLResponse("Delivery note not found", status_code=404)
    lines = conn.execute(
        """
        SELECT l.*, i.code AS item_code, i.name AS item_name
        FROM delivery_note_lines l
        LEFT JOIN items i ON i.id = l.item_id
        WHERE l.delivery_note_id = ?
        ORDER BY l.line_no, l.id
        """,
        (delivery_id,),
    ).fetchall()
    conn.close()

    order_meta = f"<p><b>Sales Order:</b> {safe(doc['sales_order_no'])}</p>" if safe(doc["sales_order_no"]) else ""
    next_action = '<a class="btn blue" href="/ui/accounting/customer-invoices">Open Customer Invoices</a>'
    html = detail_page_html(
        "Delivery Note",
        "/ui/sales/deliveries",
        doc,
        lines,
        "Delivery No",
        "delivery_no",
        "Delivery Date",
        "delivery_date",
        extra_meta_html=order_meta,
        next_action_html=next_action,
    )
    return HTMLResponse(render_page("Delivery Note", html, current_path=request.url.path))
