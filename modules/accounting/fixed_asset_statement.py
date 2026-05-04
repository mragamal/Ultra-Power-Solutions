from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db import get_conn
from layout import render_page

router = APIRouter()


# =========================================================
# HELPERS
# =========================================================
def safe(x):
    return "" if x is None else str(x).strip()


def D(x):
    try:
        return Decimal(str(x if x is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def q2(x):
    return D(x).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)


def money(x):
    try:
        return f"{float(x or 0):,.2f}"
    except Exception:
        return "0.00"


def safe_int(x, default=0):
    try:
        if x is None or str(x).strip() == "":
            return default
        return int(float(x))
    except Exception:
        return default


# =========================================================
# LOOKUPS
# =========================================================
def get_asset(conn, asset_id: int):
    return conn.execute("""
        SELECT *
        FROM fixed_assets
        WHERE id = ?
        LIMIT 1
    """, (asset_id,)).fetchone()


def get_category(conn, category_id: int):
    return conn.execute("""
        SELECT *
        FROM asset_categories
        WHERE id = ?
        LIMIT 1
    """, (category_id,)).fetchone()


def category_label(conn, category_id: int):
    row = get_category(conn, category_id)
    if not row:
        return ""
    code = safe(row["code"])
    name = safe(row["name"])
    return f"{code} - {name}" if code else name


# =========================================================
# COMPUTED VALUES
# =========================================================
def get_posted_depreciation_total(conn, asset_id: int):
    row = conn.execute("""
        SELECT COALESCE(SUM(amount), 0) AS total_amount
        FROM asset_depreciation_moves
        WHERE asset_id = ?
          AND LOWER(COALESCE(status,'')) = 'posted'
    """, (asset_id,)).fetchone()

    return q2(row["total_amount"] if row else 0)


def get_asset_nbv(conn, asset_row):
    cost = q2(asset_row["cost"])
    accum_dep = get_posted_depreciation_total(conn, asset_row["id"])
    nbv = q2(cost - accum_dep)
    return nbv if nbv > Decimal("0.00") else Decimal("0.00")


def get_acquisition_status(conn, asset_row):
    if not asset_row or not asset_row["acquisition_journal_id"]:
        return "no_journal"

    row = conn.execute("""
        SELECT status
        FROM journal_entries
        WHERE id = ?
        LIMIT 1
    """, (asset_row["acquisition_journal_id"],)).fetchone()

    if not row:
        return "missing"
    return safe(row["status"]).lower() or "draft"


# =========================================================
# DATA BUILDERS
# =========================================================
def get_asset_statement_rows(conn, asset_id: int):
    asset = get_asset(conn, asset_id)
    if not asset:
        return None, []

    rows = []

    acquisition_status = get_acquisition_status(conn, asset)
    cost = q2(asset["cost"])

    if asset["acquisition_journal_id"]:
        rows.append({
            "trx_date": safe(asset["purchase_date"]) or safe(asset["in_service_date"]),
            "trx_type": "Acquisition",
            "reference": safe(asset["code"]),
            "description": f"Asset acquisition - {safe(asset['name'])}",
            "debit": cost,
            "credit": Decimal("0.00"),
            "status": acquisition_status,
            "journal_id": asset["acquisition_journal_id"],
            "sort_key": (safe(asset["purchase_date"]) or safe(asset["in_service_date"]), 1, safe(asset["code"]))
        })

    dep_moves = conn.execute("""
        SELECT *
        FROM asset_depreciation_moves
        WHERE asset_id = ?
        ORDER BY dep_date, id
    """, (asset_id,)).fetchall()

    for m in dep_moves:
        amt = q2(m["amount"])
        rows.append({
            "trx_date": safe(m["dep_date"]),
            "trx_type": "Depreciation",
            "reference": f"DEP-{safe(asset['code'])}",
            "description": f"Depreciation for {safe(asset['name'])}",
            "debit": Decimal("0.00"),
            "credit": amt,
            "status": safe(m["status"]).lower(),
            "journal_id": m["journal_id"],
            "reversed_journal_id": m["reversed_journal_id"],
            "sort_key": (safe(m["dep_date"]), 2, str(m["id"]))
        })

    rows.sort(key=lambda x: x["sort_key"])
    return asset, rows


def get_asset_summary_rows(conn, status_filter: str = ""):
    sql = """
        SELECT *
        FROM fixed_assets
        WHERE 1 = 1
    """
    params = []

    if safe(status_filter):
        sql += " AND LOWER(COALESCE(status,'')) = ?"
        params.append(safe(status_filter).lower())

    sql += " ORDER BY code, name, id"

    assets = conn.execute(sql, params).fetchall()

    result = []
    for a in assets:
        accum_dep = get_posted_depreciation_total(conn, a["id"])
        nbv = get_asset_nbv(conn, a)

        result.append({
            "id": a["id"],
            "code": safe(a["code"]),
            "name": safe(a["name"]),
            "category": category_label(conn, a["category_id"]),
            "status": safe(a["status"]),
            "cost": q2(a["cost"]),
            "accum_dep": accum_dep,
            "nbv": nbv,
            "in_service_date": safe(a["in_service_date"]),
        })

    return result


# =========================================================
# ROUTES
# =========================================================
@router.get("/ui/accounting/fixed-assets/statement", response_class=HTMLResponse)
def fixed_asset_statement(
    request: Request,
    asset_id: str = "",
    status: str = "",
    embed: int = 0,
):
    conn = get_conn()

    if safe(asset_id):
        try:
            asset_id_int = int(asset_id)
        except Exception:
            conn.close()
            return HTMLResponse("Invalid asset.", status_code=400)

        asset, rows = get_asset_statement_rows(conn, asset_id_int)
        if not asset:
            conn.close()
            return HTMLResponse("Asset not found.", status_code=404)

        body = ""
        running_balance = Decimal("0.00")
        total_debit = Decimal("0.00")
        total_credit = Decimal("0.00")

        for r in rows:
            total_debit += r["debit"]
            total_credit += r["credit"]
            running_balance += r["debit"] - r["credit"]

            open_link = ""
            if r.get("journal_id"):
                open_link = f'<a class="btn gray" href="/ui/accounting/journal/{r["journal_id"]}">Open</a>'

            reverse_info = ""
            if r["trx_type"] == "Depreciation" and r.get("reversed_journal_id"):
                reverse_info = f" | Reversed By: {r['reversed_journal_id']}"

            body += f"""
            <tr>
                <td>{safe(r['trx_date'])}</td>
                <td>{safe(r['trx_type'])}</td>
                <td>{safe(r['reference'])}</td>
                <td>{safe(r['description'])}{reverse_info}</td>
                <td>{money(r['debit'])}</td>
                <td>{money(r['credit'])}</td>
                <td>{money(running_balance)}</td>
                <td>{safe(r['status'])}</td>
                <td>{open_link}</td>
            </tr>
            """

        if not body:
            body = "<tr><td colspan='9' style='text-align:center;'>No asset movements found.</td></tr>"

        accum_dep = get_posted_depreciation_total(conn, asset["id"])
        nbv = get_asset_nbv(conn, asset)
        cat_label = category_label(conn, asset["category_id"])

        content = f"""
        <div class="card">
            <h2>Asset Statement</h2>
            <p><b>Asset:</b> {safe(asset['code'])} - {safe(asset['name'])}</p>
            <p><b>Category:</b> {cat_label}</p>
            <p><b>Status:</b> {safe(asset['status'])}</p>
            <p><b>Purchase Date:</b> {safe(asset['purchase_date'])}</p>
            <p><b>In Service Date:</b> {safe(asset['in_service_date'])}</p>
            <p><b>Cost:</b> {money(asset['cost'])}</p>
            <p><b>Accumulated Depreciation:</b> {money(accum_dep)}</p>
            <p><b>NBV:</b> {money(nbv)}</p>

            <div style="margin-top:18px;">
                <a class="btn gray" href="/ui/accounting/fixed-assets/statement">Back to Register</a>
                <a class="btn blue" href="/ui/accounting/fixed-assets/{asset['id']}">Open Asset</a>
            </div>
        </div>

        <div class="card">
            <table>
                <tr>
                    <th>Date</th>
                    <th>Type</th>
                    <th>Reference</th>
                    <th>Description</th>
                    <th>Debit</th>
                    <th>Credit</th>
                    <th>Net Book Value Flow</th>
                    <th>Status</th>
                    <th>Open</th>
                </tr>
                {body}
                <tr style="font-weight:800;background:#f9fafb;">
                    <td colspan="4">TOTAL</td>
                    <td>{money(total_debit)}</td>
                    <td>{money(total_credit)}</td>
                    <td>{money(total_debit - total_credit)}</td>
                    <td colspan="2"></td>
                </tr>
            </table>
        </div>
        """

        conn.close()
        if int(embed or 0) == 1:
            return HTMLResponse(content)
        return HTMLResponse(render_page("Asset Statement", content, current_path=request.url.path))

    summary_rows = get_asset_summary_rows(conn, status_filter=status)
    conn.close()

    total_cost = Decimal("0.00")
    total_accum = Decimal("0.00")
    total_nbv = Decimal("0.00")

    body = ""
    for r in summary_rows:
        total_cost += r["cost"]
        total_accum += r["accum_dep"]
        total_nbv += r["nbv"]

        body += f"""
        <tr>
            <td>
                <a href="/ui/accounting/fixed-assets/statement?asset_id={r['id']}">
                    {safe(r['code'])}
                </a>
            </td>
            <td>{safe(r['name'])}</td>
            <td>{safe(r['category'])}</td>
            <td>{safe(r['status'])}</td>
            <td>{safe(r['in_service_date'])}</td>
            <td>{money(r['cost'])}</td>
            <td>{money(r['accum_dep'])}</td>
            <td>{money(r['nbv'])}</td>
        </tr>
        """

    if not body:
        body = "<tr><td colspan='8' style='text-align:center;'>No assets found.</td></tr>"

    content = f"""
    <div class="card">
        <h2>Asset Register / Statement</h2>

        <form method="get">
            <div class="row">
                <div class="col">
                    <label>Status</label>
                    <select name="status">
                        <option value="" {"selected" if status == "" else ""}>All</option>
                        <option value="draft" {"selected" if status == "draft" else ""}>Draft</option>
                        <option value="running" {"selected" if status == "running" else ""}>Running</option>
                        <option value="disposed" {"selected" if status == "disposed" else ""}>Disposed</option>
                    </select>
                </div>
            </div>

            <div style="margin-top:14px;">
                <button class="btn green" type="submit">Show</button>
                <a class="btn gray" href="/ui/accounting/fixed-assets/statement">Clear</a>
                <a class="btn gray" href="/ui/accounting/export-center">Export</a>
            </div>
        </form>
    </div>

    <div class="card">
        <table>
            <tr>
                <th>Code</th>
                <th>Name</th>
                <th>Category</th>
                <th>Status</th>
                <th>In Service</th>
                <th>Cost</th>
                <th>Accumulated Depreciation</th>
                <th>NBV</th>
            </tr>
            {body}
            <tr style="font-weight:800;background:#f9fafb;">
                <td colspan="5">TOTAL</td>
                <td>{money(total_cost)}</td>
                <td>{money(total_accum)}</td>
                <td>{money(total_nbv)}</td>
            </tr>
        </table>
    </div>
    """

    if int(embed or 0) == 1:
        return HTMLResponse(content)

    return HTMLResponse(render_page("Asset Register", content, current_path=request.url.path))
