from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page
from i18n import get_lang

router = APIRouter()


def tr(lang: str, en: str, ar: str) -> str:
    return ar if lang == "ar" else en


def ensure_table():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cost_centers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            name TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    """)

    cols = conn.execute("PRAGMA table_info(cost_centers)").fetchall()
    names = [c["name"] for c in cols]

    if "code" not in names:
        conn.execute("ALTER TABLE cost_centers ADD COLUMN code TEXT")
    if "name" not in names:
        conn.execute("ALTER TABLE cost_centers ADD COLUMN name TEXT")
    if "is_active" not in names:
        conn.execute("ALTER TABLE cost_centers ADD COLUMN is_active INTEGER DEFAULT 1")

    row = conn.execute("""
        SELECT COUNT(*) AS c
        FROM cost_centers
    """).fetchone()
    if int((row["c"] if row and "c" in row.keys() else 0) or 0) == 0:
        conn.execute("""
            INSERT INTO cost_centers (code, name, is_active)
            VALUES ('CC-0001', 'General', 1)
        """)

    conn.commit()
    conn.close()


ensure_table()


def safe(x):
    return "" if x is None else str(x).strip()


def next_cost_center_code():
    conn = get_conn()
    row = conn.execute("""
        SELECT code
        FROM cost_centers
        WHERE COALESCE(code, '') <> ''
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    last = safe(row["code"]) if row else ""
    if not last:
        return "CC-0001"

    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"CC-{num + 1:04d}"


@router.get("/ui/accounting/cost-centers", response_class=HTMLResponse)
def cost_centers_list(request: Request):
    lang = get_lang(request)
    conn = get_conn()

    rows = conn.execute("""
        SELECT *
        FROM cost_centers
        ORDER BY code, name
    """).fetchall()

    body = ""
    for r in rows:
        active = tr(lang, "Yes", "نعم") if int(r["is_active"] or 0) == 1 else tr(lang, "No", "لا")
        body += f"""
        <tr>
            <td>{r['code'] or ''}</td>
            <td>{r['name'] or ''}</td>
            <td>{active}</td>
            <td>
                <a class="btn gray" href="/ui/accounting/cost-centers/{r['id']}/edit?lang={lang}">{tr(lang, "Edit", "تعديل")}</a>
            </td>
        </tr>
        """

    conn.close()

    html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>{tr(lang, "Cost Centers", "مراكز التكلفة")}</h2>
            <a class="btn green" href="/ui/accounting/cost-centers/new?lang={lang}">{tr(lang, "+ New Cost Center", "+ مركز تكلفة جديد")}</a>
        </div>

        <table>
            <tr>
                <th>{tr(lang, "Code", "الكود")}</th>
                <th>{tr(lang, "Name", "الاسم")}</th>
                <th>{tr(lang, "Active", "نشط")}</th>
                <th>{tr(lang, "Actions", "الإجراءات")}</th>
            </tr>
            {body}
        </table>
    </div>
    """

    return HTMLResponse(render_page(tr(lang, "Cost Centers", "مراكز التكلفة"), html, lang, current_path=str(request.url.path)))


@router.get("/ui/accounting/cost-centers/new", response_class=HTMLResponse)
def cost_center_new(request: Request):
    lang = get_lang(request)

    html = f"""
    <div class="card">
        <h2>{tr(lang, "New Cost Center", "مركز تكلفة جديد")}</h2>

        <form method="post">
            <div class="row">
                <div class="col">
                    <label>{tr(lang, "Code", "الكود")}</label>
                    <input type="text" name="code" value="{next_cost_center_code()}" readonly required>
                </div>
                <div class="col">
                    <label>{tr(lang, "Name", "الاسم")}</label>
                    <input type="text" name="name" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{tr(lang, "Active", "نشط")}</label>
                    <select name="is_active">
                        <option value="1">{tr(lang, "Yes", "نعم")}</option>
                        <option value="0">{tr(lang, "No", "لا")}</option>
                    </select>
                </div>
                <div class="col"></div>
            </div>

            <div style="margin-top:20px;">
                <button class="btn green" type="submit">{tr(lang, "Save", "حفظ")}</button>
                <a class="btn gray" href="/ui/accounting/cost-centers?lang={lang}">{tr(lang, "Back", "رجوع")}</a>
            </div>
        </form>
    </div>
    """

    return HTMLResponse(render_page(tr(lang, "New Cost Center", "مركز تكلفة جديد"), html, lang, current_path=str(request.url.path)))


@router.post("/ui/accounting/cost-centers/new")
async def cost_center_create(request: Request):
    form = await request.form()

    conn = get_conn()
    conn.execute("""
        INSERT INTO cost_centers (code, name, is_active)
        VALUES (?, ?, ?)
    """, (
        safe(form.get("code")) or next_cost_center_code(),
        (form.get("name") or "").strip(),
        int(form.get("is_active") or 1),
    ))
    conn.commit()
    conn.close()

    return RedirectResponse(f"/ui/accounting/cost-centers?lang={get_lang(request)}", status_code=302)


@router.get("/ui/accounting/cost-centers/{cc_id}/edit", response_class=HTMLResponse)
def cost_center_edit(request: Request, cc_id: int):
    lang = get_lang(request)
    conn = get_conn()

    row = conn.execute("""
        SELECT *
        FROM cost_centers
        WHERE id = ?
    """, (cc_id,)).fetchone()

    conn.close()

    if not row:
        return HTMLResponse(tr(lang, "Cost center not found", "مركز التكلفة غير موجود"), status_code=404)

    html = f"""
    <div class="card">
        <h2>{tr(lang, "Edit Cost Center", "تعديل مركز التكلفة")}</h2>

        <form method="post">
            <div class="row">
                <div class="col">
                    <label>{tr(lang, "Code", "الكود")}</label>
                    <input type="text" name="code" value="{row['code'] or ''}" readonly required>
                </div>
                <div class="col">
                    <label>{tr(lang, "Name", "الاسم")}</label>
                    <input type="text" name="name" value="{row['name'] or ''}" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>{tr(lang, "Active", "نشط")}</label>
                    <select name="is_active">
                        <option value="1" {"selected" if int(row['is_active'] or 0) == 1 else ""}>{tr(lang, "Yes", "نعم")}</option>
                        <option value="0" {"selected" if int(row['is_active'] or 0) == 0 else ""}>{tr(lang, "No", "لا")}</option>
                    </select>
                </div>
                <div class="col"></div>
            </div>

            <div style="margin-top:20px;">
                <button class="btn green" type="submit">{tr(lang, "Save", "حفظ")}</button>
                <a class="btn gray" href="/ui/accounting/cost-centers?lang={lang}">{tr(lang, "Back", "رجوع")}</a>
            </div>
        </form>
    </div>
    """

    return HTMLResponse(render_page(tr(lang, "Edit Cost Center", "تعديل مركز التكلفة"), html, lang, current_path=str(request.url.path)))


@router.post("/ui/accounting/cost-centers/{cc_id}/edit")
async def cost_center_update(request: Request, cc_id: int):
    form = await request.form()

    conn = get_conn()
    current_row = conn.execute("SELECT code FROM cost_centers WHERE id = ?", (cc_id,)).fetchone()
    current_code = safe(current_row["code"]) if current_row else ""
    conn.execute("""
        UPDATE cost_centers
        SET code = ?, name = ?, is_active = ?
        WHERE id = ?
    """, (
        safe(form.get("code")) or current_code or next_cost_center_code(),
        (form.get("name") or "").strip(),
        int(form.get("is_active") or 1),
        cc_id,
    ))
    conn.commit()
    conn.close()

    return RedirectResponse(f"/ui/accounting/cost-centers?lang={get_lang(request)}", status_code=302)
