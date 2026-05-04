from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from auth import current_user
from layout import render_page

router = APIRouter()


def init_products_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            code TEXT,
            price REAL DEFAULT 0,
            cost REAL DEFAULT 0,
            notes TEXT
        )
    """)

    conn.commit()
    conn.close()


init_products_db()


@router.get("/ui/products", response_class=HTMLResponse)
def products_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    conn = get_conn()
    products = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    conn.close()

    rows = ""
    for p in products:
        rows += f"""
        <tr>
            <td>{p["id"]}</td>
            <td>{p["name"] or ""}</td>
            <td>{p["code"] or ""}</td>
            <td>{float(p["price"] or 0):.2f}</td>
            <td>{float(p["cost"] or 0):.2f}</td>
        </tr>
        """

    content = f"""
        <h1 class="page-title">Products</h1>

        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">Add Product</div>
            </div>
            <div class="panel-body">
                <form method="post" action="/ui/products/add" class="form-grid">
                    <input name="name" placeholder="Name" required>
                    <input name="code" placeholder="Code">
                    <input name="price" type="number" step="0.01" placeholder="Sale Price">
                    <input name="cost" type="number" step="0.01" placeholder="Cost">
                    <textarea name="notes" placeholder="Notes"></textarea>
                    <button class="btn btn-primary">Save</button>
                </form>
            </div>
        </div>

        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">Products List</div>
            </div>
            <div class="panel-body table-wrap">
                <table class="erp-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Name</th>
                            <th>Code</th>
                            <th>Price</th>
                            <th>Cost</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
        </div>
    """

    return HTMLResponse(render_page("Products", "products", content, user["username"]))


@router.post("/ui/products/add")
def add_product(
    name: str = Form(...),
    code: str = Form(""),
    price: float = Form(0),
    cost: float = Form(0),
    notes: str = Form("")
):
    conn = get_conn()

    conn.execute("""
        INSERT INTO products (name, code, price, cost, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (name, code, price, cost, notes))

    conn.commit()
    conn.close()

    return RedirectResponse("/ui/products", status_code=303)