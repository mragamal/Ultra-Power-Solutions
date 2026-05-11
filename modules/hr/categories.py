from html import escape

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page

router = APIRouter()


def safe(value):
    return "" if value is None else str(value).strip()


def to_float(value, default=0.0):
    try:
        return float(safe(value).replace(",", "") or default)
    except Exception:
        return float(default)


def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


def ensure_categories_table():
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS employee_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            name TEXT NOT NULL,
            department_name TEXT,
            attendance_role TEXT,
            shift_start TEXT,
            shift_end TEXT,
            expected_daily_hours REAL DEFAULT 8,
            late_grace_minutes REAL DEFAULT 15,
            early_leave_grace_minutes REAL DEFAULT 15,
            overtime_after_hours REAL DEFAULT 8,
            attendance_exempt INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    ensure_column(conn, "employee_categories", "code", "ALTER TABLE employee_categories ADD COLUMN code TEXT")
    ensure_column(conn, "employee_categories", "name", "ALTER TABLE employee_categories ADD COLUMN name TEXT")
    ensure_column(conn, "employee_categories", "department_name", "ALTER TABLE employee_categories ADD COLUMN department_name TEXT")
    ensure_column(conn, "employee_categories", "attendance_role", "ALTER TABLE employee_categories ADD COLUMN attendance_role TEXT")
    ensure_column(conn, "employee_categories", "shift_start", "ALTER TABLE employee_categories ADD COLUMN shift_start TEXT")
    ensure_column(conn, "employee_categories", "shift_end", "ALTER TABLE employee_categories ADD COLUMN shift_end TEXT")
    ensure_column(conn, "employee_categories", "expected_daily_hours", "ALTER TABLE employee_categories ADD COLUMN expected_daily_hours REAL DEFAULT 8")
    ensure_column(conn, "employee_categories", "late_grace_minutes", "ALTER TABLE employee_categories ADD COLUMN late_grace_minutes REAL DEFAULT 15")
    ensure_column(conn, "employee_categories", "early_leave_grace_minutes", "ALTER TABLE employee_categories ADD COLUMN early_leave_grace_minutes REAL DEFAULT 15")
    ensure_column(conn, "employee_categories", "overtime_after_hours", "ALTER TABLE employee_categories ADD COLUMN overtime_after_hours REAL DEFAULT 8")
    ensure_column(conn, "employee_categories", "attendance_exempt", "ALTER TABLE employee_categories ADD COLUMN attendance_exempt INTEGER DEFAULT 0")
    ensure_column(conn, "employee_categories", "is_active", "ALTER TABLE employee_categories ADD COLUMN is_active INTEGER DEFAULT 1")
    ensure_column(conn, "employee_categories", "created_at", "ALTER TABLE employee_categories ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    seed_rows = [
        ("CAT-OFF", "Office Staff", "Administration", "office", "08:00", "16:00", 8, 15, 15, 8, 0, 1),
        ("CAT-FLD", "Field Team", "Operations", "field", "09:00", "17:00", 8, 30, 15, 8, 0, 1),
        ("CAT-MGT", "Managerial Level", "Management", "managerial", "", "", 8, 0, 0, 8, 1, 1),
    ]
    for row in seed_rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO employee_categories (
                code, name, department_name, attendance_role, shift_start, shift_end,
                expected_daily_hours, late_grace_minutes, early_leave_grace_minutes, overtime_after_hours, attendance_exempt, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
    conn.execute(
        """
        UPDATE employee_categories
        SET attendance_exempt = 1,
            name = CASE WHEN UPPER(COALESCE(code, '')) = 'CAT-MGT' THEN 'Managerial Level' ELSE name END,
            attendance_role = CASE WHEN UPPER(COALESCE(code, '')) = 'CAT-MGT' THEN 'managerial' ELSE attendance_role END,
            shift_start = CASE WHEN UPPER(COALESCE(code, '')) = 'CAT-MGT' THEN '' ELSE shift_start END,
            shift_end = CASE WHEN UPPER(COALESCE(code, '')) = 'CAT-MGT' THEN '' ELSE shift_end END,
            late_grace_minutes = CASE WHEN UPPER(COALESCE(code, '')) = 'CAT-MGT' THEN 0 ELSE late_grace_minutes END,
            early_leave_grace_minutes = CASE WHEN UPPER(COALESCE(code, '')) = 'CAT-MGT' THEN 0 ELSE early_leave_grace_minutes END
        WHERE UPPER(COALESCE(code, '')) IN ('CAT-MGT', 'MGT', 'MANAGERIAL')
           OR LOWER(COALESCE(attendance_role, '')) IN ('manager', 'managerial', 'managerial level', 'managerial_level')
        """
    )

    conn.commit()
    conn.close()


def category_rows(active_only=False):
    ensure_categories_table()
    conn = get_conn()
    sql = "SELECT * FROM employee_categories"
    if active_only:
        sql += " WHERE COALESCE(is_active,1)=1"
    sql += " ORDER BY code, name"
    rows = conn.execute(sql).fetchall()
    conn.close()
    return rows


def category_options_html(selected_value=""):
    rows = category_rows(active_only=True)
    out = '<option value="">-- Select Category --</option>'
    for row in rows:
        selected = "selected" if str(row["id"]) == str(selected_value or "") else ""
        label = f"{safe(row['code'])} - {safe(row['name'])}"
        out += f'<option value="{row["id"]}" {selected}>{escape(label)}</option>'
    return out


def category_form_html(action, data=None):
    data = data or {}
    active_yes = "selected" if str(data.get("is_active", "1")) == "1" else ""
    active_no = "selected" if str(data.get("is_active", "1")) != "1" else ""
    exempt_yes = "selected" if str(data.get("attendance_exempt", "0")) == "1" else ""
    exempt_no = "selected" if str(data.get("attendance_exempt", "0")) != "1" else ""
    return f"""
    <div class="card">
        <h2>{'Edit Category' if '/edit' in action else 'New Category'}</h2>
        <form method="post" action="{action}">
            <div class="row">
                <div class="col">
                    <label>Code</label>
                    <input name="code" value="{escape(safe(data.get('code')))}" required>
                </div>
                <div class="col">
                    <label>Name</label>
                    <input name="name" value="{escape(safe(data.get('name')))}" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Department</label>
                    <input name="department_name" value="{escape(safe(data.get('department_name')))}">
                </div>
                <div class="col">
                    <label>Attendance Role</label>
                    <input name="attendance_role" value="{escape(safe(data.get('attendance_role')))}" placeholder="office / manager / field / shift-a">
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Attendance Schedule</label>
                    <select name="attendance_exempt">
                        <option value="0" {exempt_no}>Required</option>
                        <option value="1" {exempt_yes}>No Attendance Schedule</option>
                    </select>
                </div>
                <div class="col"></div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Shift Start</label>
                    <input type="time" name="shift_start" value="{escape(safe(data.get('shift_start')))}">
                </div>
                <div class="col">
                    <label>Shift End</label>
                    <input type="time" name="shift_end" value="{escape(safe(data.get('shift_end')))}">
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Expected Daily Hours</label>
                    <input type="number" step="0.01" name="expected_daily_hours" value="{safe(data.get('expected_daily_hours') or '8')}">
                </div>
                <div class="col">
                    <label>Overtime After Hours</label>
                    <input type="number" step="0.01" name="overtime_after_hours" value="{safe(data.get('overtime_after_hours') or '8')}">
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Late Grace Minutes</label>
                    <input type="number" step="0.01" name="late_grace_minutes" value="{safe(data.get('late_grace_minutes') or '15')}">
                </div>
                <div class="col">
                    <label>Early Leave Grace Minutes</label>
                    <input type="number" step="0.01" name="early_leave_grace_minutes" value="{safe(data.get('early_leave_grace_minutes') or '15')}">
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Status</label>
                    <select name="is_active">
                        <option value="1" {active_yes}>Active</option>
                        <option value="0" {active_no}>Inactive</option>
                    </select>
                </div>
                <div class="col"></div>
            </div>

            <div style="margin-top:20px;">
                <button class="btn green" type="submit">Save</button>
                <a class="btn gray" href="/ui/hr/categories">Back</a>
            </div>
        </form>
    </div>
    """


ensure_categories_table()


@router.get("/ui/hr/categories", response_class=HTMLResponse)
def categories_list(request: Request):
    rows = category_rows(active_only=False)
    body = ""
    for row in rows:
        status_html = '<span class="status-chip green">Active</span>' if int(row["is_active"] or 0) == 1 else '<span class="status-chip gray">Inactive</span>'
        body += f"""
        <tr>
            <td>{escape(safe(row['code']))}</td>
            <td>{escape(safe(row['name']))}</td>
            <td>{escape(safe(row['department_name']))}</td>
            <td>{escape(safe(row['attendance_role']))}</td>
            <td>{'No Schedule' if int(row['attendance_exempt'] or 0) == 1 else 'Required'}</td>
            <td>{escape(safe(row['shift_start']))}</td>
            <td>{escape(safe(row['shift_end']))}</td>
            <td>{float(row['expected_daily_hours'] or 0):,.2f}</td>
            <td>{float(row['late_grace_minutes'] or 0):,.0f}</td>
            <td>{float(row['early_leave_grace_minutes'] or 0):,.0f}</td>
            <td>{status_html}</td>
            <td><a class="btn blue" href="/ui/hr/categories/{row['id']}/edit">Edit</a></td>
        </tr>
        """
    if not body:
        body = "<tr><td colspan='12' style='text-align:center;'>No categories found.</td></tr>"

    html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>Employee Categories</h2>
            <a class="btn green" href="/ui/hr/categories/new">+ New Category</a>
        </div>
        <p class="section-note" style="margin-top:10px;">Each category can carry its own attendance role, shift timing, late grace, early leave grace, and overtime rule. Employees linked to the category inherit these attendance rules.</p>
    </div>

    <div class="card">
        <div class="table-wrap">
            <table>
                <tr>
                    <th>Code</th>
                    <th>Name</th>
                    <th>Department</th>
                    <th>Attendance Role</th>
                    <th>Schedule</th>
                    <th>Shift Start</th>
                    <th>Shift End</th>
                    <th>Daily Hours</th>
                    <th>Late Grace</th>
                    <th>Early Leave Grace</th>
                    <th>Status</th>
                    <th>Action</th>
                </tr>
                {body}
            </table>
        </div>
    </div>
    """
    return HTMLResponse(render_page("Employee Categories", html, "en", current_path=request.url.path))


@router.get("/ui/hr/categories/new", response_class=HTMLResponse)
def category_new(request: Request):
    return HTMLResponse(render_page("New Category", category_form_html("/ui/hr/categories/new"), "en", current_path=request.url.path))


@router.post("/ui/hr/categories/new")
def category_create(
    code: str = Form(...),
    name: str = Form(...),
    department_name: str = Form(""),
    attendance_role: str = Form(""),
    shift_start: str = Form(""),
    shift_end: str = Form(""),
    expected_daily_hours: str = Form("8"),
    late_grace_minutes: str = Form("15"),
    early_leave_grace_minutes: str = Form("15"),
    overtime_after_hours: str = Form("8"),
    attendance_exempt: int = Form(0),
    is_active: int = Form(1),
):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO employee_categories (
            code, name, department_name, attendance_role, shift_start, shift_end,
            expected_daily_hours, late_grace_minutes, early_leave_grace_minutes, overtime_after_hours, attendance_exempt, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            safe(code),
            safe(name),
            safe(department_name),
            safe(attendance_role),
            safe(shift_start),
            safe(shift_end),
            to_float(expected_daily_hours, 8),
            to_float(late_grace_minutes, 15),
            to_float(early_leave_grace_minutes, 15),
            to_float(overtime_after_hours, 8),
            int(attendance_exempt or 0),
            int(is_active or 0),
        ),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/ui/hr/categories", status_code=302)


@router.get("/ui/hr/categories/{category_id}/edit", response_class=HTMLResponse)
def category_edit(request: Request, category_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM employee_categories WHERE id = ? LIMIT 1", (category_id,)).fetchone()
    conn.close()
    if not row:
        return HTMLResponse("Category not found", status_code=404)
    return HTMLResponse(render_page("Edit Category", category_form_html(f"/ui/hr/categories/{category_id}/edit", dict(row)), "en", current_path=request.url.path))


@router.post("/ui/hr/categories/{category_id}/edit")
def category_update(
    category_id: int,
    code: str = Form(...),
    name: str = Form(...),
    department_name: str = Form(""),
    attendance_role: str = Form(""),
    shift_start: str = Form(""),
    shift_end: str = Form(""),
    expected_daily_hours: str = Form("8"),
    late_grace_minutes: str = Form("15"),
    early_leave_grace_minutes: str = Form("15"),
    overtime_after_hours: str = Form("8"),
    attendance_exempt: int = Form(0),
    is_active: int = Form(1),
):
    conn = get_conn()
    conn.execute(
        """
        UPDATE employee_categories
        SET code = ?, name = ?, department_name = ?, attendance_role = ?, shift_start = ?, shift_end = ?,
            expected_daily_hours = ?, late_grace_minutes = ?, early_leave_grace_minutes = ?, overtime_after_hours = ?, attendance_exempt = ?, is_active = ?
        WHERE id = ?
        """,
        (
            safe(code),
            safe(name),
            safe(department_name),
            safe(attendance_role),
            safe(shift_start),
            safe(shift_end),
            to_float(expected_daily_hours, 8),
            to_float(late_grace_minutes, 15),
            to_float(early_leave_grace_minutes, 15),
            to_float(overtime_after_hours, 8),
            int(attendance_exempt or 0),
            int(is_active or 0),
            category_id,
        ),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/ui/hr/categories", status_code=302)
