from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from db import get_conn
from utils.templates import render_page

router = APIRouter()

# =========================
# INIT TABLE (FULL CLEAN SAFE)
# =========================
def ensure_employees_table():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            department TEXT,
            job_title TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Upgrade لو الجدول قديم
    columns = [row[1] for row in conn.execute("PRAGMA table_info(employees)").fetchall()]

    if "code" not in columns:
        conn.execute("ALTER TABLE employees ADD COLUMN code TEXT")
    if "phone" not in columns:
        conn.execute("ALTER TABLE employees ADD COLUMN phone TEXT")
    if "email" not in columns:
        conn.execute("ALTER TABLE employees ADD COLUMN email TEXT")
    if "department" not in columns:
        conn.execute("ALTER TABLE employees ADD COLUMN department TEXT")
    if "job_title" not in columns:
        conn.execute("ALTER TABLE employees ADD COLUMN job_title TEXT")
    if "is_active" not in columns:
        conn.execute("ALTER TABLE employees ADD COLUMN is_active INTEGER DEFAULT 1")

    conn.commit()
    conn.close()


# =========================
# LIST
# =========================
@router.get("/ui/hr/employees", response_class=HTMLResponse)
def employees_list(request: Request):
    ensure_employees_table()

    conn = get_conn()
    rows = conn.execute("""
        SELECT id, code, name, phone, email, department, job_title, is_active
        FROM employees
        ORDER BY id DESC
    """).fetchall()
    conn.close()

    content = f"""
    <h2>Employees</h2>

    <a href="/ui/hr/employees/new">
        <button style="background:green;color:white;padding:6px 12px;border:none;border-radius:5px;">
            + New Employee
        </button>
    </a>

    <br><br>

    <table border="1" cellpadding="8" style="border-collapse:collapse;width:100%;">
        <tr>
            <th>ID</th>
            <th>Code</th>
            <th>Name</th>
            <th>Phone</th>
            <th>Email</th>
            <th>Department</th>
            <th>Job Title</th>
            <th>Status</th>
        </tr>
    """

    for r in rows:
        content += f"""
        <tr>
            <td>{r[0]}</td>
            <td>{r[1] or ''}</td>
            <td>{r[2]}</td>
            <td>{r[3] or ''}</td>
            <td>{r[4] or ''}</td>
            <td>{r[5] or ''}</td>
            <td>{r[6] or ''}</td>
            <td>{"Active" if r[7] else "Inactive"}</td>
        </tr>
        """

    content += "</table>"

    return render_page("Employees", content, request)


# =========================
# NEW FORM
# =========================
@router.get("/ui/hr/employees/new", response_class=HTMLResponse)
def new_employee(request: Request):

    content = """
    <h2>New Employee</h2>

    <form method="post">
        Code:<br>
        <input name="code"><br><br>

        Name:<br>
        <input name="name" required><br><br>

        Phone:<br>
        <input name="phone"><br><br>

        Email:<br>
        <input name="email"><br><br>

        Department:<br>
        <input name="department"><br><br>

        Job Title:<br>
        <input name="job_title"><br><br>

        <button type="submit">Save</button>
    </form>
    """

    return render_page("New Employee", content, request)


# =========================
# SAVE
# =========================
@router.post("/ui/hr/employees/new")
def create_employee(
    code: str = Form(""),
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    department: str = Form(""),
    job_title: str = Form("")
):
    ensure_employees_table()

    conn = get_conn()

    conn.execute("""
        INSERT INTO employees (code, name, phone, email, department, job_title)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (code, name, phone, email, department, job_title))

    conn.commit()
    conn.close()

    return RedirectResponse("/ui/hr/employees", status_code=302)