from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page
from modules.inventory.core import next_warehouse_code, ensure_inventory_tables

router = APIRouter()


def safe(x):
    return "" if x is None else str(x).strip()


ensure_inventory_tables()


@router.get("/ui/inventory/warehouses", response_class=HTMLResponse)
def warehouses_list(request: Request):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT *
        FROM warehouses
        ORDER BY code, name
        """
    ).fetchall()
    conn.close()

    body = ""
    for r in rows:
        active = "Yes" if int(r["is_active"] or 0) == 1 else "No"
        body += f"""
        <tr>
            <td>{r['code'] or ''}</td>
            <td>{r['name'] or ''}</td>
            <td>{active}</td>
            <td><a class="btn gray" href="/ui/inventory/warehouses/{r['id']}/edit">Edit</a></td>
        </tr>
        """

    if not body:
        body = "<tr><td colspan='4' style='text-align:center;'>No warehouses found.</td></tr>"

    html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>Warehouses</h2>
            <a class="btn green" href="/ui/inventory/warehouses/new">+ New Warehouse</a>
        </div>
    </div>

    <div class="card">
        <table>
            <tr>
                <th>Code</th>
                <th>Name</th>
                <th>Active</th>
                <th>Actions</th>
            </tr>
            {body}
        </table>
    </div>
    """
    return HTMLResponse(render_page("Warehouses", html, current_path=request.url.path))


def warehouse_form_html(action, data=None):
    data = data or {}
    selected_yes = "selected" if str(data.get("is_active", "1")) == "1" else ""
    selected_no = "selected" if str(data.get("is_active", "1")) != "1" else ""

    return f"""
    <div class="card">
        <h2>{'Edit Warehouse' if '/edit' in action else 'New Warehouse'}</h2>
        <form method="post" action="{action}">
            <div class="row">
                <div class="col">
                    <label>Code</label>
                    <input name="code" value="{data.get('code') or next_warehouse_code()}" readonly required>
                </div>
                <div class="col">
                    <label>Name</label>
                    <input name="name" value="{data.get('name') or ''}" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Active</label>
                    <select name="is_active">
                        <option value="1" {selected_yes}>Yes</option>
                        <option value="0" {selected_no}>No</option>
                    </select>
                </div>
                <div class="col"></div>
            </div>

            <div style="margin-top:20px;">
                <button class="btn green" type="submit">Save</button>
                <a class="btn gray" href="/ui/inventory/warehouses">Back</a>
            </div>
        </form>
    </div>
    """


@router.get("/ui/inventory/warehouses/new", response_class=HTMLResponse)
def warehouse_new(request: Request):
    return HTMLResponse(
        render_page("New Warehouse", warehouse_form_html("/ui/inventory/warehouses/new"), current_path=request.url.path)
    )


@router.post("/ui/inventory/warehouses/new")
def warehouse_create(
    code: str = Form(""),
    name: str = Form(...),
    is_active: int = Form(1),
):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO warehouses (code, name, is_active)
        VALUES (?, ?, ?)
        """,
        (
            safe(code) or next_warehouse_code(),
            safe(name),
            int(is_active or 0),
        ),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/ui/inventory/warehouses", status_code=302)


@router.get("/ui/inventory/warehouses/{warehouse_id}/edit", response_class=HTMLResponse)
def warehouse_edit(request: Request, warehouse_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)).fetchone()
    conn.close()
    if not row:
        return HTMLResponse("Warehouse not found", status_code=404)
    return HTMLResponse(
        render_page(
            "Edit Warehouse",
            warehouse_form_html(f"/ui/inventory/warehouses/{warehouse_id}/edit", dict(row)),
            current_path=request.url.path,
        )
    )


@router.post("/ui/inventory/warehouses/{warehouse_id}/edit")
def warehouse_update(
    warehouse_id: int,
    code: str = Form(""),
    name: str = Form(...),
    is_active: int = Form(1),
):
    conn = get_conn()
    conn.execute(
        """
        UPDATE warehouses
        SET code = ?, name = ?, is_active = ?
        WHERE id = ?
        """,
        (
            safe(code),
            safe(name),
            int(is_active or 0),
            warehouse_id,
        ),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/ui/inventory/warehouses", status_code=302)
