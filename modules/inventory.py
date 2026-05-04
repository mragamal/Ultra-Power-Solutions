from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from auth import current_user
from layout import render_page
from db import get_conn

router = APIRouter()


# =========================================================
# LANG
# =========================================================
def get_lang(request: Request, fallback: str = "en") -> str:
    lang = request.query_params.get("lang", fallback).strip().lower()
    return "ar" if lang == "ar" else "en"


def tx(lang: str, ar: str, en: str) -> str:
    return ar if lang == "ar" else en


# =========================================================
# INIT DB
# =========================================================
def init_inventory_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            code TEXT,
            qty REAL DEFAULT 0,
            cost REAL DEFAULT 0,
            price REAL DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


init_inventory_db()


# =========================================================
# INVENTORY PAGE
# =========================================================
@router.get("/ui/inventory", response_class=HTMLResponse)
def inventory_page(request: Request):
    lang = get_lang(request)
    user = current_user(request)

    if not user:
        return RedirectResponse("/login", status_code=302)

    conn = get_conn()

    products = conn.execute("""
        SELECT * FROM products ORDER BY id DESC
    """).fetchall()

    total_products = len(products)
    total_qty = sum([p["qty"] or 0 for p in products])
    total_value = sum([(p["qty"] or 0) * (p["cost"] or 0) for p in products])

    conn.close()

    rows = ""
    for p in products:
        rows += f"""
        <tr>
            <td>{p["id"]}</td>
            <td>{p["name"]}</td>
            <td>{p["code"] or "-"}</td>
            <td>{p["qty"]}</td>
            <td>{p["cost"]:.2f}</td>
            <td>{p["price"]:.2f}</td>
            <td>{(p["qty"] * p["cost"]):.2f}</td>
        </tr>
        """

    content = f"""
    <h1 class="page-title">{tx(lang, "المخزون", "Inventory")}</h1>
    <div class="page-subtitle">{tx(lang, "إدارة المنتجات والمخزون", "Manage products and stock")}</div>

    <div class="dashboard-grid">
        <div class="card">
            <div class="card-title">{tx(lang, "عدد المنتجات", "Products")}</div>
            <div class="card-value">{total_products}</div>
        </div>

        <div class="card">
            <div class="card-title">{tx(lang, "إجمالي الكمية", "Total Qty")}</div>
            <div class="card-value">{total_qty}</div>
        </div>

        <div class="card">
            <div class="card-title">{tx(lang, "قيمة المخزون", "Stock Value")}</div>
            <div class="card-value">{total_value:.2f}</div>
        </div>
    </div>

    <div style="height:16px;"></div>

    <div class="card">
        <h2 style="margin-bottom:14px;">{tx(lang, "إضافة منتج", "Add Product")}</h2>

        <form method="post" action="/ui/inventory/add">
            <input type="hidden" name="lang" value="{lang}">

            <input name="name" placeholder="{tx(lang, 'اسم المنتج', 'Product Name')}" required>
            <input name="code" placeholder="{tx(lang, 'الكود', 'Code')}">
            <input name="qty" type="number" step="0.01" placeholder="{tx(lang, 'الكمية', 'Quantity')}">
            <input name="cost" type="number" step="0.01" placeholder="{tx(lang, 'التكلفة', 'Cost')}">
            <input name="price" type="number" step="0.01" placeholder="{tx(lang, 'سعر البيع', 'Selling Price')}">

            <button class="btn-primary">{tx(lang, "حفظ", "Save")}</button>
        </form>
    </div>

    <div style="height:16px;"></div>

    <div class="card">
        <h2 style="margin-bottom:14px;">{tx(lang, "قائمة المنتجات", "Products List")}</h2>

        <table style="width:100%; border-collapse:collapse;">
            <thead>
                <tr>
                    <th style="padding:12px;">ID</th>
                    <th style="padding:12px;">{tx(lang, "الاسم", "Name")}</th>
                    <th style="padding:12px;">{tx(lang, "الكود", "Code")}</th>
                    <th style="padding:12px;">{tx(lang, "الكمية", "Qty")}</th>
                    <th style="padding:12px;">{tx(lang, "التكلفة", "Cost")}</th>
                    <th style="padding:12px;">{tx(lang, "السعر", "Price")}</th>
                    <th style="padding:12px;">{tx(lang, "القيمة", "Value")}</th>
                </tr>
            </thead>
            <tbody>
                {rows if rows else f'<tr><td colspan="7" style="padding:12px;">{tx(lang, "لا يوجد منتجات", "No products")}</td></tr>'}
            </tbody>
        </table>
    </div>
    """

    return HTMLResponse(
        render_page(
            tx(lang, "المخزون", "Inventory"),
            "inventory",
            content,
            user["username"],
            lang,
        )
    )


# =========================================================
# ADD PRODUCT
# =========================================================
@router.post("/ui/inventory/add")
def add_product(
    name: str = Form(...),
    code: str = Form(""),
    qty: float = Form(0),
    cost: float = Form(0),
    price: float = Form(0),
    lang: str = Form("en"),
):
    conn = get_conn()

    conn.execute("""
        INSERT INTO products (name, code, qty, cost, price)
        VALUES (?, ?, ?, ?, ?)
    """, (name, code, qty, cost, price))

    conn.commit()
    conn.close()

    return RedirectResponse(f"/ui/inventory?lang={lang}", status_code=303)