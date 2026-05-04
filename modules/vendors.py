from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from db import get_conn
from auth import current_user
from layout import render_page

router = APIRouter()

# DB
def init_db():
    conn = get_conn()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS vendors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        account_id INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS vendor_bills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vendor_id INTEGER,
        amount REAL
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS vendor_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vendor_id INTEGER,
        amount REAL
    )
    """)

    conn.commit()
    conn.close()

init_db()


# PAGE
@router.get("/ui/vendors", response_class=HTMLResponse)
def vendors_page(request: Request):
    user = current_user(request)
    conn = get_conn()

    vendors = conn.execute("SELECT * FROM vendors").fetchall()

    rows = ""
    for v in vendors:
        rows += f"<tr><td>{v['name']}</td></tr>"

    content = f"""
    <h1>Vendors</h1>

    <form method="post" action="/ui/vendors/add">
        <input name="name" placeholder="Vendor Name">
        <button>Add</button>
    </form>

    <table class="erp-table">
        {rows}
    </table>
    """

    return HTMLResponse(render_page("Vendors","vendors",content,user["username"]))


# ADD
@router.post("/ui/vendors/add")
def add_vendor(name: str = Form(...)):
    conn = get_conn()

    # create payable account automatically
    conn.execute("INSERT INTO accounts (code,name,type) VALUES (?,?,?)",
                 ("2000", name, "liability"))

    acc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute("INSERT INTO vendors (name,account_id) VALUES (?,?)",
                 (name, acc_id))

    conn.commit()
    conn.close()

    return RedirectResponse("/ui/vendors", status_code=303)