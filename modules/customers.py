from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from db import get_conn
from auth import current_user
from layout import render_page

router = APIRouter()

def init_db():
    conn = get_conn()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        account_id INTEGER
    )
    """)

    conn.commit()
    conn.close()

init_db()


@router.get("/ui/customers", response_class=HTMLResponse)
def page(request: Request):
    user = current_user(request)
    conn = get_conn()

    rows = conn.execute("SELECT * FROM customers").fetchall()

    html = ""
    for r in rows:
        html += f"<tr><td>{r['name']}</td></tr>"

    content = f"""
    <h1>Customers</h1>

    <form method="post" action="/ui/customers/add">
        <input name="name">
        <button>Add</button>
    </form>

    <table class="erp-table">{html}</table>
    """

    return HTMLResponse(render_page("Customers","customers",content,user["username"]))


@router.post("/ui/customers/add")
def add(name: str = Form(...)):
    conn = get_conn()

    conn.execute("INSERT INTO accounts (code,name,type) VALUES (?,?,?)",
                 ("1100", name, "asset"))

    acc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute("INSERT INTO customers (name,account_id) VALUES (?,?)",
                 (name, acc_id))

    conn.commit()
    conn.close()

    return RedirectResponse("/ui/customers", status_code=303)