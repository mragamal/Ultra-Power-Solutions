from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, date

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page
from modules.accounting.accounting_engine import (
    create_journal_entry,
    post_journal_entry,
    reverse_journal_entry,
)

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


def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


def parse_date(s):
    try:
        if not s:
            return None
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except Exception:
        return None


def month_end_dates_through(as_of_str):
    as_of = parse_date(as_of_str)
    if not as_of:
        return []

    out = []
    y = as_of.year
    m = as_of.month

    for mm in range(1, m + 1):
        if mm == 12:
            first_next = date(y + 1, 1, 1)
        else:
            first_next = date(y, mm + 1, 1)
        last_day = first_next.fromordinal(first_next.toordinal() - 1)
        out.append(last_day.strftime("%Y-%m-%d"))

    return out


# =========================================================
# DB INIT
# =========================================================
def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS asset_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            asset_account_code TEXT,
            accum_dep_account_code TEXT,
            dep_expense_account_code TEXT,
            life_months INTEGER DEFAULT 60,
            method TEXT DEFAULT 'straight_line',
            is_active INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fixed_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            category_id INTEGER,
            purchase_date TEXT,
            in_service_date TEXT,
            cost REAL DEFAULT 0,
            salvage_value REAL DEFAULT 0,
            status TEXT DEFAULT 'draft',
            acquisition_account_code TEXT,
            offset_account_code TEXT,
            acquisition_journal_id INTEGER,
            disposal_journal_id INTEGER,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS asset_depreciation_moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            dep_date TEXT,
            amount REAL DEFAULT 0,
            status TEXT DEFAULT 'draft',
            journal_id INTEGER,
            reversed_journal_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS asset_disposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            disposal_date TEXT,
            proceeds REAL DEFAULT 0,
            proceeds_account_code TEXT,
            gain_account_code TEXT,
            loss_account_code TEXT,
            note TEXT,
            status TEXT DEFAULT 'draft',
            journal_id INTEGER,
            reversed_journal_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    ensure_column(conn, "asset_categories", "code", "ALTER TABLE asset_categories ADD COLUMN code TEXT")
    ensure_column(conn, "asset_categories", "name", "ALTER TABLE asset_categories ADD COLUMN name TEXT")
    ensure_column(conn, "asset_categories", "asset_account_code", "ALTER TABLE asset_categories ADD COLUMN asset_account_code TEXT")
    ensure_column(conn, "asset_categories", "accum_dep_account_code", "ALTER TABLE asset_categories ADD COLUMN accum_dep_account_code TEXT")
    ensure_column(conn, "asset_categories", "dep_expense_account_code", "ALTER TABLE asset_categories ADD COLUMN dep_expense_account_code TEXT")
    ensure_column(conn, "asset_categories", "life_months", "ALTER TABLE asset_categories ADD COLUMN life_months INTEGER DEFAULT 60")
    ensure_column(conn, "asset_categories", "method", "ALTER TABLE asset_categories ADD COLUMN method TEXT DEFAULT 'straight_line'")
    ensure_column(conn, "asset_categories", "is_active", "ALTER TABLE asset_categories ADD COLUMN is_active INTEGER DEFAULT 1")

    ensure_column(conn, "fixed_assets", "code", "ALTER TABLE fixed_assets ADD COLUMN code TEXT")
    ensure_column(conn, "fixed_assets", "name", "ALTER TABLE fixed_assets ADD COLUMN name TEXT")
    ensure_column(conn, "fixed_assets", "category_id", "ALTER TABLE fixed_assets ADD COLUMN category_id INTEGER")
    ensure_column(conn, "fixed_assets", "purchase_date", "ALTER TABLE fixed_assets ADD COLUMN purchase_date TEXT")
    ensure_column(conn, "fixed_assets", "in_service_date", "ALTER TABLE fixed_assets ADD COLUMN in_service_date TEXT")
    ensure_column(conn, "fixed_assets", "cost", "ALTER TABLE fixed_assets ADD COLUMN cost REAL DEFAULT 0")
    ensure_column(conn, "fixed_assets", "salvage_value", "ALTER TABLE fixed_assets ADD COLUMN salvage_value REAL DEFAULT 0")
    ensure_column(conn, "fixed_assets", "status", "ALTER TABLE fixed_assets ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "fixed_assets", "acquisition_account_code", "ALTER TABLE fixed_assets ADD COLUMN acquisition_account_code TEXT")
    ensure_column(conn, "fixed_assets", "offset_account_code", "ALTER TABLE fixed_assets ADD COLUMN offset_account_code TEXT")
    ensure_column(conn, "fixed_assets", "acquisition_journal_id", "ALTER TABLE fixed_assets ADD COLUMN acquisition_journal_id INTEGER")
    ensure_column(conn, "fixed_assets", "disposal_journal_id", "ALTER TABLE fixed_assets ADD COLUMN disposal_journal_id INTEGER")
    ensure_column(conn, "fixed_assets", "source_vendor_bill_id", "ALTER TABLE fixed_assets ADD COLUMN source_vendor_bill_id INTEGER")
    ensure_column(conn, "fixed_assets", "source_vendor_bill_line_id", "ALTER TABLE fixed_assets ADD COLUMN source_vendor_bill_line_id INTEGER")
    ensure_column(conn, "fixed_assets", "notes", "ALTER TABLE fixed_assets ADD COLUMN notes TEXT")
    ensure_column(conn, "fixed_assets", "created_at", "ALTER TABLE fixed_assets ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "asset_depreciation_moves", "asset_id", "ALTER TABLE asset_depreciation_moves ADD COLUMN asset_id INTEGER")
    ensure_column(conn, "asset_depreciation_moves", "dep_date", "ALTER TABLE asset_depreciation_moves ADD COLUMN dep_date TEXT")
    ensure_column(conn, "asset_depreciation_moves", "amount", "ALTER TABLE asset_depreciation_moves ADD COLUMN amount REAL DEFAULT 0")
    ensure_column(conn, "asset_depreciation_moves", "status", "ALTER TABLE asset_depreciation_moves ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "asset_depreciation_moves", "journal_id", "ALTER TABLE asset_depreciation_moves ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "asset_depreciation_moves", "reversed_journal_id", "ALTER TABLE asset_depreciation_moves ADD COLUMN reversed_journal_id INTEGER")
    ensure_column(conn, "asset_depreciation_moves", "created_at", "ALTER TABLE asset_depreciation_moves ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "asset_disposals", "asset_id", "ALTER TABLE asset_disposals ADD COLUMN asset_id INTEGER")
    ensure_column(conn, "asset_disposals", "disposal_date", "ALTER TABLE asset_disposals ADD COLUMN disposal_date TEXT")
    ensure_column(conn, "asset_disposals", "proceeds", "ALTER TABLE asset_disposals ADD COLUMN proceeds REAL DEFAULT 0")
    ensure_column(conn, "asset_disposals", "proceeds_account_code", "ALTER TABLE asset_disposals ADD COLUMN proceeds_account_code TEXT")
    ensure_column(conn, "asset_disposals", "gain_account_code", "ALTER TABLE asset_disposals ADD COLUMN gain_account_code TEXT")
    ensure_column(conn, "asset_disposals", "loss_account_code", "ALTER TABLE asset_disposals ADD COLUMN loss_account_code TEXT")
    ensure_column(conn, "asset_disposals", "note", "ALTER TABLE asset_disposals ADD COLUMN note TEXT")
    ensure_column(conn, "asset_disposals", "status", "ALTER TABLE asset_disposals ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "asset_disposals", "journal_id", "ALTER TABLE asset_disposals ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "asset_disposals", "reversed_journal_id", "ALTER TABLE asset_disposals ADD COLUMN reversed_journal_id INTEGER")
    ensure_column(conn, "asset_disposals", "created_at", "ALTER TABLE asset_disposals ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    conn.commit()
    conn.close()


ensure_tables()


# =========================================================
# LOOKUPS
# =========================================================
def account_options(selected=""):
    conn = get_conn()
    rows = conn.execute("""
        SELECT code, name
        FROM accounts
        WHERE COALESCE(is_active,1) = 1
          AND COALESCE(is_group,0) = 0
          AND COALESCE(allow_posting,1) = 1
        ORDER BY code, name
    """).fetchall()
    conn.close()

    html = '<option value="">-- Select Account --</option>'
    for r in rows:
        code = safe(r["code"])
        name = safe(r["name"])
        sel = "selected" if code == safe(selected) else ""
        html += f'<option value="{code}" {sel}>{code} - {name}</option>'
    return html


def category_options(selected=None):
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, code, name
        FROM asset_categories
        WHERE COALESCE(is_active, 1) = 1
        ORDER BY code, name, id
    """).fetchall()
    conn.close()

    html = '<option value="">-- Select Category --</option>'
    for r in rows:
        sel = "selected" if str(r["id"]) == str(selected or "") else ""
        html += f'<option value="{r["id"]}" {sel}>{safe(r["code"])} - {safe(r["name"])}</option>'
    return html


def get_category(conn, category_id):
    return conn.execute("""
        SELECT *
        FROM asset_categories
        WHERE id = ?
        LIMIT 1
    """, (category_id,)).fetchone()


def get_asset(conn, asset_id):
    return conn.execute("""
        SELECT *
        FROM fixed_assets
        WHERE id = ?
        LIMIT 1
    """, (asset_id,)).fetchone()


def get_disposal(conn, disposal_id):
    return conn.execute("""
        SELECT *
        FROM asset_disposals
        WHERE id = ?
        LIMIT 1
    """, (disposal_id,)).fetchone()


def get_asset_category_display(conn, category_id):
    row = get_category(conn, category_id)
    if not row:
        return ""
    return f"{safe(row['code'])} - {safe(row['name'])}" if safe(row["code"]) else safe(row["name"])


# =========================================================
# COMPUTED VALUES
# =========================================================
def get_posted_depreciation_total(conn, asset_id):
    row = conn.execute("""
        SELECT COALESCE(SUM(amount), 0) AS total_amount
        FROM asset_depreciation_moves
        WHERE asset_id = ?
          AND LOWER(COALESCE(status,'')) = 'posted'
    """, (asset_id,)).fetchone()

    return q2(row["total_amount"] if row else 0)


def get_draft_depreciation_total(conn, asset_id):
    row = conn.execute("""
        SELECT COALESCE(SUM(amount), 0) AS total_amount
        FROM asset_depreciation_moves
        WHERE asset_id = ?
          AND LOWER(COALESCE(status,'')) = 'draft'
    """, (asset_id,)).fetchone()

    return q2(row["total_amount"] if row else 0)


def get_asset_nbv(conn, asset_row):
    cost = q2(asset_row["cost"])
    accum_dep = get_posted_depreciation_total(conn, asset_row["id"])
    nbv = q2(cost - accum_dep)
    return nbv if nbv > Decimal("0.00") else Decimal("0.00")


def monthly_depreciation_amount(asset_row, category_row):
    cost = q2(asset_row["cost"])
    salvage = q2(asset_row["salvage_value"])
    life_months = safe_int(category_row["life_months"], 0)

    if life_months <= 0:
        return Decimal("0.00")

    depreciable = q2(cost - salvage)
    if depreciable <= Decimal("0.00"):
        return Decimal("0.00")

    return q2(depreciable / Decimal(str(life_months)))


def max_total_depreciation(asset_row):
    cost = q2(asset_row["cost"])
    salvage = q2(asset_row["salvage_value"])
    max_dep = q2(cost - salvage)
    return max_dep if max_dep > Decimal("0.00") else Decimal("0.00")


# =========================================================
# NUMBERING
# =========================================================
def next_asset_code():
    conn = get_conn()
    row = conn.execute("""
        SELECT code
        FROM fixed_assets
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["code"]:
        return "FA-0001"

    last = safe(row["code"])
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"FA-{num + 1:04d}"


def next_category_code():
    conn = get_conn()
    row = conn.execute("""
        SELECT code
        FROM asset_categories
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row or not row["code"]:
        return "FAC-0001"

    last = safe(row["code"])
    try:
        num = int(last.split("-")[-1])
    except Exception:
        num = 0
    return f"FAC-{num + 1:04d}"


# =========================================================
# JOURNAL BUILDERS
# =========================================================
def create_asset_acquisition_journal(conn, asset_id):
    asset = get_asset(conn, asset_id)
    if not asset:
        raise Exception("Asset not found.")

    category = get_category(conn, asset["category_id"])
    if not category:
        raise Exception("Asset category not found.")

    asset_account = safe(asset["acquisition_account_code"]) or safe(category["asset_account_code"])
    offset_account = safe(asset["offset_account_code"])

    if not asset_account:
        raise Exception("Asset account is required.")
    if not offset_account:
        raise Exception("Offset account is required.")

    amount = q2(asset["cost"])
    if amount <= Decimal("0.00"):
        raise Exception("Asset cost must be greater than zero.")

    lines = [
        {
            "description": f"Asset Acquisition - {safe(asset['name'])}",
            "account_code": asset_account,
            "debit": amount,
            "credit": Decimal("0.00"),
            "partner_type": None,
            "partner_id": None,
        },
        {
            "description": f"Asset Acquisition Offset - {safe(asset['name'])}",
            "account_code": offset_account,
            "debit": Decimal("0.00"),
            "credit": amount,
            "partner_type": None,
            "partner_id": None,
        }
    ]

    journal_id = create_journal_entry(
        conn=conn,
        entry_date=safe(asset["purchase_date"]) or safe(asset["in_service_date"]),
        description=f"Asset Acquisition {safe(asset['code'])} - {safe(asset['name'])}",
        reference=safe(asset["code"]),
        source_type="fixed_asset_acquisition",
        source_id=asset_id,
        lines=lines,
    )

    conn.execute("""
        UPDATE fixed_assets
        SET acquisition_journal_id = ?
        WHERE id = ?
    """, (journal_id, asset_id))

    return journal_id


def create_depreciation_move_journal(conn, move_id):
    move = conn.execute("""
        SELECT *
        FROM asset_depreciation_moves
        WHERE id = ?
        LIMIT 1
    """, (move_id,)).fetchone()

    if not move:
        raise Exception("Depreciation move not found.")

    asset = get_asset(conn, move["asset_id"])
    if not asset:
        raise Exception("Asset not found.")

    category = get_category(conn, asset["category_id"])
    if not category:
        raise Exception("Asset category not found.")

    dep_expense_account = safe(category["dep_expense_account_code"])
    accum_dep_account = safe(category["accum_dep_account_code"])

    if not dep_expense_account:
        raise Exception("Depreciation expense account is required in category.")
    if not accum_dep_account:
        raise Exception("Accumulated depreciation account is required in category.")

    amount = q2(move["amount"])
    if amount <= Decimal("0.00"):
        raise Exception("Depreciation amount must be greater than zero.")

    lines = [
        {
            "description": f"Depreciation Expense - {safe(asset['name'])}",
            "account_code": dep_expense_account,
            "debit": amount,
            "credit": Decimal("0.00"),
            "partner_type": None,
            "partner_id": None,
        },
        {
            "description": f"Accumulated Depreciation - {safe(asset['name'])}",
            "account_code": accum_dep_account,
            "debit": Decimal("0.00"),
            "credit": amount,
            "partner_type": None,
            "partner_id": None,
        }
    ]

    journal_id = create_journal_entry(
        conn=conn,
        entry_date=safe(move["dep_date"]),
        description=f"Asset Depreciation {safe(asset['code'])} - {safe(asset['name'])}",
        reference=f"DEP-{safe(asset['code'])}-{safe(move['dep_date'])}",
        source_type="fixed_asset_depreciation",
        source_id=move_id,
        lines=lines,
    )

    conn.execute("""
        UPDATE asset_depreciation_moves
        SET journal_id = ?
        WHERE id = ?
    """, (journal_id, move_id))

    return journal_id


def create_asset_disposal_journal(conn, disposal_id):
    disposal = get_disposal(conn, disposal_id)
    if not disposal:
        raise Exception("Disposal not found.")

    asset = get_asset(conn, disposal["asset_id"])
    if not asset:
        raise Exception("Asset not found.")

    if safe(asset["status"]).lower() != "running":
        raise Exception("Only running assets can be disposed.")

    category = get_category(conn, asset["category_id"])
    if not category:
        raise Exception("Asset category not found.")

    asset_account = safe(asset["acquisition_account_code"]) or safe(category["asset_account_code"])
    accum_dep_account = safe(category["accum_dep_account_code"])

    if not asset_account:
        raise Exception("Asset account is required.")
    if not accum_dep_account:
        raise Exception("Accumulated depreciation account is required.")

    cost = q2(asset["cost"])
    accum_dep = get_posted_depreciation_total(conn, asset["id"])
    proceeds = q2(disposal["proceeds"])
    nbv = q2(cost - accum_dep)
    result = q2(proceeds - nbv)

    proceeds_account = safe(disposal["proceeds_account_code"])
    gain_account = safe(disposal["gain_account_code"])
    loss_account = safe(disposal["loss_account_code"])

    lines = []

    if accum_dep > Decimal("0.00"):
        lines.append({
            "description": f"Remove accumulated depreciation - {safe(asset['name'])}",
            "account_code": accum_dep_account,
            "debit": accum_dep,
            "credit": Decimal("0.00"),
            "partner_type": None,
            "partner_id": None,
        })

    if proceeds > Decimal("0.00"):
        if not proceeds_account:
            raise Exception("Proceeds account is required when proceeds > 0.")
        lines.append({
            "description": f"Disposal proceeds - {safe(asset['name'])}",
            "account_code": proceeds_account,
            "debit": proceeds,
            "credit": Decimal("0.00"),
            "partner_type": None,
            "partner_id": None,
        })

    if result > Decimal("0.00"):
        if not gain_account:
            raise Exception("Gain account is required because this disposal creates gain.")
        lines.append({
            "description": f"Gain on disposal - {safe(asset['name'])}",
            "account_code": gain_account,
            "debit": Decimal("0.00"),
            "credit": result,
            "partner_type": None,
            "partner_id": None,
        })
    elif result < Decimal("0.00"):
        loss_amount = q2(abs(result))
        if not loss_account:
            raise Exception("Loss account is required because this disposal creates loss.")
        lines.append({
            "description": f"Loss on disposal - {safe(asset['name'])}",
            "account_code": loss_account,
            "debit": loss_amount,
            "credit": Decimal("0.00"),
            "partner_type": None,
            "partner_id": None,
        })

    lines.append({
        "description": f"Remove asset cost - {safe(asset['name'])}",
        "account_code": asset_account,
        "debit": Decimal("0.00"),
        "credit": cost,
        "partner_type": None,
        "partner_id": None,
    })

    total_debit = q2(sum([l["debit"] for l in lines], Decimal("0.00")))
    total_credit = q2(sum([l["credit"] for l in lines], Decimal("0.00")))
    if total_debit != total_credit:
        raise Exception(f"Disposal entry is unbalanced. Debit={total_debit} Credit={total_credit}")

    journal_id = create_journal_entry(
        conn=conn,
        entry_date=safe(disposal["disposal_date"]),
        description=f"Asset Disposal {safe(asset['code'])} - {safe(asset['name'])}",
        reference=f"DISP-{safe(asset['code'])}",
        source_type="fixed_asset_disposal",
        source_id=disposal_id,
        lines=lines,
    )

    conn.execute("""
        UPDATE asset_disposals
        SET journal_id = ?
        WHERE id = ?
    """, (journal_id, disposal_id))

    return journal_id


# =========================================================
# UI PAGES
# =========================================================
@router.get("/ui/accounting/fixed-assets", response_class=HTMLResponse)
def fixed_assets_home(request: Request):
    conn = get_conn()

    categories = conn.execute("""
        SELECT *
        FROM asset_categories
        ORDER BY id DESC
        LIMIT 10
    """).fetchall()

    assets = conn.execute("""
        SELECT *
        FROM fixed_assets
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    conn.close()

    category_rows = ""
    for r in categories:
        category_rows += f"""
        <tr>
            <td>{safe(r['code'])}</td>
            <td>{safe(r['name'])}</td>
            <td>{safe(r['asset_account_code'])}</td>
            <td>{safe(r['accum_dep_account_code'])}</td>
            <td>{safe(r['dep_expense_account_code'])}</td>
            <td>{safe(r['life_months'])}</td>
            <td>{safe(r['method'])}</td>
        </tr>
        """

    asset_rows = ""
    conn2 = get_conn()
    for r in assets:
        accum_dep = get_posted_depreciation_total(conn2, r["id"])
        nbv = get_asset_nbv(conn2, r)
        asset_rows += f"""
        <tr>
            <td><a href="/ui/accounting/fixed-assets/{r['id']}">{safe(r['code'])}</a></td>
            <td>{safe(r['name'])}</td>
            <td>{safe(r['status'])}</td>
            <td>{money(r['cost'])}</td>
            <td>{money(accum_dep)}</td>
            <td>{money(nbv)}</td>
            <td>{safe(r['in_service_date'])}</td>
        </tr>
        """
    conn2.close()

    if not category_rows:
        category_rows = "<tr><td colspan='7' style='text-align:center;'>No categories found.</td></tr>"

    if not asset_rows:
        asset_rows = "<tr><td colspan='7' style='text-align:center;'>No assets found.</td></tr>"

    content = f"""
    <div class="table-header">
        <h3>Fixed Assets</h3>
        <div>
            <a class="btn blue" href="/ui/accounting/fixed-assets/new-category">New Category</a>
            <a class="btn green" href="/ui/accounting/fixed-assets/new-asset">New Asset</a>
            <a class="btn gray" href="/ui/accounting/fixed-assets/run-depreciation">Run Depreciation</a>
            <a class="btn gray" href="/ui/accounting/fixed-assets/statement">Asset Register</a>
        </div>
    </div>

    <h4>Latest Categories</h4>
    <table>
        <tr>
            <th>Code</th>
            <th>Name</th>
            <th>Asset Account</th>
            <th>Acc. Dep.</th>
            <th>Dep. Expense</th>
            <th>Life (Months)</th>
            <th>Method</th>
        </tr>
        {category_rows}
    </table>

    <br>

    <h4>Latest Assets</h4>
    <table>
        <tr>
            <th>Code</th>
            <th>Name</th>
            <th>Status</th>
            <th>Cost</th>
            <th>Acc. Dep.</th>
            <th>NBV</th>
            <th>In Service</th>
        </tr>
        {asset_rows}
    </table>
    """

    return HTMLResponse(render_page("Fixed Assets", content, current_path=request.url.path))


@router.get("/ui/accounting/fixed-assets/new-category", response_class=HTMLResponse)
def new_category(request: Request):
    content = f"""
    <h3>New Category</h3>

    <form method="post" action="/ui/accounting/fixed-assets/new-category">
        <div class="form-grid">
            <div class="form-group">
                <label>Code</label>
                <input name="code" value="{next_category_code()}" required>
            </div>

            <div class="form-group">
                <label>Name</label>
                <input name="name" required>
            </div>

            <div class="form-group">
                <label>Asset Account</label>
                <select name="asset_account_code">
                    {account_options()}
                </select>
            </div>

            <div class="form-group">
                <label>Accumulated Depreciation Account</label>
                <select name="accum_dep_account_code">
                    {account_options()}
                </select>
            </div>

            <div class="form-group">
                <label>Depreciation Expense Account</label>
                <select name="dep_expense_account_code">
                    {account_options()}
                </select>
            </div>

            <div class="form-group">
                <label>Life (Months)</label>
                <input name="life_months" value="60">
            </div>

            <div class="form-group">
                <label>Method</label>
                <select name="method">
                    <option value="straight_line">Straight Line</option>
                </select>
            </div>
        </div>

        <div class="form-actions">
            <button class="btn green" type="submit">Save</button>
            <a class="btn gray" href="/ui/accounting/fixed-assets">Back</a>
        </div>
    </form>
    """
    return HTMLResponse(render_page("New Category", content, current_path=request.url.path))


@router.post("/ui/accounting/fixed-assets/new-category")
def create_category(
    code: str = Form(""),
    name: str = Form(""),
    asset_account_code: str = Form(""),
    accum_dep_account_code: str = Form(""),
    dep_expense_account_code: str = Form(""),
    life_months: int = Form(60),
    method: str = Form("straight_line"),
):
    conn = get_conn()
    conn.execute("""
        INSERT INTO asset_categories (
            code, name, asset_account_code, accum_dep_account_code,
            dep_expense_account_code, life_months, method
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        safe(code),
        safe(name),
        safe(asset_account_code),
        safe(accum_dep_account_code),
        safe(dep_expense_account_code),
        safe_int(life_months, 60),
        safe(method) or "straight_line",
    ))
    conn.commit()
    conn.close()

    return RedirectResponse("/ui/accounting/fixed-assets", status_code=302)


@router.get("/ui/accounting/fixed-assets/new-asset", response_class=HTMLResponse)
def new_asset(request: Request):
    content = f"""
    <h3>New Asset</h3>

    <form method="post" action="/ui/accounting/fixed-assets/new-asset">
        <div class="form-grid">
            <div class="form-group">
                <label>Code</label>
                <input name="code" value="{next_asset_code()}" required>
            </div>

            <div class="form-group">
                <label>Name</label>
                <input name="name" required>
            </div>

            <div class="form-group">
                <label>Category</label>
                <select name="category_id">
                    {category_options()}
                </select>
            </div>

            <div class="form-group">
                <label>Purchase Date</label>
                <input type="date" name="purchase_date">
            </div>

            <div class="form-group">
                <label>In Service Date</label>
                <input type="date" name="in_service_date">
            </div>

            <div class="form-group">
                <label>Cost</label>
                <input name="cost" value="0">
            </div>

            <div class="form-group">
                <label>Salvage Value</label>
                <input name="salvage_value" value="0">
            </div>

            <div class="form-group">
                <label>Offset Account</label>
                <select name="offset_account_code">
                    {account_options()}
                </select>
            </div>

            <div class="form-group">
                <label>Notes</label>
                <input name="notes">
            </div>
        </div>

        <div class="form-actions">
            <button class="btn green" type="submit">Save Draft</button>
            <a class="btn gray" href="/ui/accounting/fixed-assets">Back</a>
        </div>
    </form>
    """
    return HTMLResponse(render_page("New Asset", content, current_path=request.url.path))


@router.post("/ui/accounting/fixed-assets/new-asset")
def create_asset(
    code: str = Form(""),
    name: str = Form(""),
    category_id: str = Form(""),
    purchase_date: str = Form(""),
    in_service_date: str = Form(""),
    cost: str = Form("0"),
    salvage_value: str = Form("0"),
    offset_account_code: str = Form(""),
    notes: str = Form(""),
):
    cost_val = q2(cost)
    salvage_val = q2(salvage_value)

    if cost_val <= Decimal("0.00"):
        return HTMLResponse("Cost must be greater than zero.", status_code=400)

    if salvage_val < Decimal("0.00"):
        return HTMLResponse("Salvage value cannot be negative.", status_code=400)

    if salvage_val > cost_val:
        return HTMLResponse("Salvage value cannot exceed cost.", status_code=400)

    conn = get_conn()
    category = get_category(conn, safe_int(category_id))
    if not category:
        conn.close()
        return HTMLResponse("Category is required.", status_code=400)

    conn.execute("""
        INSERT INTO fixed_assets (
            code, name, category_id, purchase_date, in_service_date,
            cost, salvage_value, status, acquisition_account_code,
            offset_account_code, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
    """, (
        safe(code),
        safe(name),
        safe_int(category_id),
        safe(purchase_date),
        safe(in_service_date),
        float(cost_val),
        float(salvage_val),
        safe(category["asset_account_code"]),
        safe(offset_account_code),
        safe(notes),
    ))
    conn.commit()

    asset_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    try:
        create_asset_acquisition_journal(conn, asset_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Asset saved but acquisition draft journal failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/fixed-assets/{asset_id}", status_code=302)


@router.get("/ui/accounting/fixed-assets/{asset_id:int}", response_class=HTMLResponse)
def open_asset(request: Request, asset_id: int):
    conn = get_conn()
    asset = get_asset(conn, asset_id)
    if not asset:
        conn.close()
        return HTMLResponse("Asset not found", status_code=404)

    category = get_category(conn, asset["category_id"])
    posted_dep = get_posted_depreciation_total(conn, asset_id)
    draft_dep = get_draft_depreciation_total(conn, asset_id)
    nbv = get_asset_nbv(conn, asset)

    dep_moves = conn.execute("""
        SELECT *
        FROM asset_depreciation_moves
        WHERE asset_id = ?
        ORDER BY dep_date, id
    """, (asset_id,)).fetchall()

    disposals = conn.execute("""
        SELECT *
        FROM asset_disposals
        WHERE asset_id = ?
        ORDER BY disposal_date, id
    """, (asset_id,)).fetchall()

    dep_rows = ""
    for m in dep_moves:
        dep_rows += f"""
        <tr>
            <td>{safe(m['dep_date'])}</td>
            <td>{money(m['amount'])}</td>
            <td>{safe(m['status'])}</td>
            <td>{safe(m['journal_id'])}</td>
            <td>{safe(m['reversed_journal_id'])}</td>
            <td>
                {"<form method='post' action='/ui/accounting/fixed-assets/depreciation/%s/post' style='display:inline;'><button class='btn green' type='submit'>Post</button></form>" % m['id'] if safe(m['status']).lower() == 'draft' else ""}
            </td>
        </tr>
        """

    if not dep_rows:
        dep_rows = "<tr><td colspan='6' style='text-align:center;'>No depreciation moves found.</td></tr>"

    disposal_rows = ""
    for d in disposals:
        disposal_rows += f"""
        <tr>
            <td><a href="/ui/accounting/fixed-assets/disposal/{d['id']}">{safe(d['disposal_date'])}</a></td>
            <td>{money(d['proceeds'])}</td>
            <td>{safe(d['status'])}</td>
            <td>{safe(d['journal_id'])}</td>
            <td>{safe(d['reversed_journal_id'])}</td>
        </tr>
        """

    if not disposal_rows:
        disposal_rows = "<tr><td colspan='5' style='text-align:center;'>No disposals found.</td></tr>"

    from_vendor_bill = safe_int(asset["source_vendor_bill_id"]) > 0
    can_post_acq = safe(asset["status"]).lower() == "draft" and asset["acquisition_journal_id"] and not from_vendor_bill
    can_run_dep = safe(asset["status"]).lower() == "running"
    can_dispose = safe(asset["status"]).lower() == "running"

    html = f"""
    <div class="card">
        <h2>Asset {safe(asset['code'])}</h2>

        <p><b>Name:</b> {safe(asset['name'])}</p>
        <p><b>Category:</b> {get_asset_category_display(conn, asset['category_id'])}</p>
        <p><b>Purchase Date:</b> {safe(asset['purchase_date'])}</p>
        <p><b>In Service Date:</b> {safe(asset['in_service_date'])}</p>
        <p><b>Cost:</b> {money(asset['cost'])}</p>
        <p><b>Salvage Value:</b> {money(asset['salvage_value'])}</p>
        <p><b>Accumulated Depreciation:</b> {money(posted_dep)}</p>
        <p><b>Draft Depreciation:</b> {money(draft_dep)}</p>
        <p><b>NBV:</b> {money(nbv)}</p>
        <p><b>Status:</b> {safe(asset['status'])}</p>
        <p><b>Asset Account:</b> {safe(asset['acquisition_account_code'])}</p>
        <p><b>Offset Account:</b> {safe(asset['offset_account_code'])}</p>
        <p><b>Acquisition Journal ID:</b> {safe(asset['acquisition_journal_id'])}</p>
        {"<p><b>Source Vendor Bill:</b> <a href='/ui/accounting/vendor-bills/%s/view'>Open Bill</a></p>" % safe(asset["source_vendor_bill_id"]) if from_vendor_bill else ""}
        <p><b>Disposal Journal ID:</b> {safe(asset['disposal_journal_id'])}</p>
        <p><b>Notes:</b> {safe(asset['notes'])}</p>

        <div style="margin-top:20px;">
            {"<form method='post' action='/ui/accounting/fixed-assets/%s/post-acquisition' style='display:inline;'><button class='btn green' type='submit'>Post Acquisition</button></form>" % asset_id if can_post_acq else ""}
            {"<a class='btn blue' href='/ui/accounting/fixed-assets/run-depreciation?asset_id=%s'>Run Depreciation</a>" % asset_id if can_run_dep else ""}
            {"<a class='btn red' href='/ui/accounting/fixed-assets/%s/dispose'>Dispose</a>" % asset_id if can_dispose else ""}
            <a class="btn gray" href="/ui/accounting/fixed-assets">Back</a>
        </div>
    </div>

    <div class="card">
        <h3>Depreciation Moves</h3>
        <table>
            <tr>
                <th>Date</th>
                <th>Amount</th>
                <th>Status</th>
                <th>Journal ID</th>
                <th>Reverse Journal ID</th>
                <th>Action</th>
            </tr>
            {dep_rows}
        </table>
    </div>

    <div class="card">
        <h3>Disposals</h3>
        <table>
            <tr>
                <th>Date</th>
                <th>Proceeds</th>
                <th>Status</th>
                <th>Journal ID</th>
                <th>Reverse Journal ID</th>
            </tr>
            {disposal_rows}
        </table>
    </div>
    """
    conn.close()
    return HTMLResponse(render_page("Asset", html, current_path=request.url.path))


@router.post("/ui/accounting/fixed-assets/{asset_id:int}/post-acquisition")
def post_asset_acquisition(asset_id: int):
    conn = get_conn()
    try:
        asset = get_asset(conn, asset_id)
        if not asset:
            raise Exception("Asset not found.")

        if safe(asset["status"]).lower() != "draft":
            raise Exception("Only draft assets can post acquisition.")

        if not asset["acquisition_journal_id"]:
            create_asset_acquisition_journal(conn, asset_id)
            asset = get_asset(conn, asset_id)

        post_journal_entry(conn, asset["acquisition_journal_id"])

        conn.execute("""
            UPDATE fixed_assets
            SET status = 'running'
            WHERE id = ?
        """, (asset_id,))

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Post acquisition failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/fixed-assets/{asset_id}", status_code=302)


@router.get("/ui/accounting/fixed-assets/run-depreciation", response_class=HTMLResponse)
def run_depreciation(request: Request, asset_id: str = "", as_of: str = ""):
    conn = get_conn()

    if not as_of:
        as_of = date.today().strftime("%Y-%m-%d")

    generated_count = 0
    error_message = ""

    if request.query_params.get("generate") == "1":
        try:
            assets_sql = """
                SELECT *
                FROM fixed_assets
                WHERE LOWER(COALESCE(status,'')) = 'running'
            """
            params = []

            if safe(asset_id):
                assets_sql += " AND id = ?"
                params.append(safe_int(asset_id))

            assets = conn.execute(assets_sql, params).fetchall()

            for asset in assets:
                category = get_category(conn, asset["category_id"])
                if not category:
                    continue

                monthly_amount = monthly_depreciation_amount(asset, category)
                if monthly_amount <= Decimal("0.00"):
                    continue

                max_dep = max_total_depreciation(asset)
                posted_total = get_posted_depreciation_total(conn, asset["id"])
                draft_total = get_draft_depreciation_total(conn, asset["id"])
                already_total = q2(posted_total + draft_total)

                if already_total >= max_dep:
                    continue

                service_date = safe(asset["in_service_date"])
                if not service_date:
                    continue

                dates = month_end_dates_through(as_of)

                for dep_date in dates:
                    if parse_date(dep_date) < parse_date(service_date):
                        continue

                    existing = conn.execute("""
                        SELECT id
                        FROM asset_depreciation_moves
                        WHERE asset_id = ?
                          AND dep_date = ?
                          AND LOWER(COALESCE(status,'')) IN ('draft','posted')
                        LIMIT 1
                    """, (asset["id"], dep_date)).fetchone()

                    if existing:
                        continue

                    remaining = q2(max_dep - already_total)
                    if remaining <= Decimal("0.00"):
                        break

                    amount = monthly_amount if monthly_amount <= remaining else remaining

                    conn.execute("""
                        INSERT INTO asset_depreciation_moves (
                            asset_id, dep_date, amount, status
                        )
                        VALUES (?, ?, ?, 'draft')
                    """, (
                        asset["id"],
                        dep_date,
                        float(amount),
                    ))

                    move_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
                    create_depreciation_move_journal(conn, move_id)

                    already_total = q2(already_total + amount)
                    generated_count += 1

            conn.commit()

        except Exception as e:
            conn.rollback()
            error_message = str(e)

    content = f"""
    <div class="card">
        <h3>Run Depreciation</h3>

        {"<div class='msg error'>%s</div>" % error_message if error_message else ""}
        {"<div class='msg success'>Generated draft depreciation moves: %s</div>" % generated_count if generated_count else ""}

        <form method="get" action="/ui/accounting/fixed-assets/run-depreciation">
            <input type="hidden" name="generate" value="1">

            <div class="form-grid">
                <div class="form-group">
                    <label>Asset ID (optional)</label>
                    <input name="asset_id" value="{safe(asset_id)}">
                </div>

                <div class="form-group">
                    <label>As Of Date</label>
                    <input type="date" name="as_of" value="{safe(as_of)}">
                </div>
            </div>

            <div class="form-actions">
                <button class="btn green" type="submit">Generate Draft Depreciation</button>
                <a class="btn gray" href="/ui/accounting/fixed-assets">Back</a>
            </div>
        </form>
    </div>
    """

    conn.close()
    return HTMLResponse(render_page("Run Depreciation", content, current_path=request.url.path))


@router.post("/ui/accounting/fixed-assets/depreciation/{move_id}/post")
def post_depreciation_move(move_id: int):
    conn = get_conn()
    try:
        move = conn.execute("""
            SELECT *
            FROM asset_depreciation_moves
            WHERE id = ?
            LIMIT 1
        """, (move_id,)).fetchone()

        if not move:
            raise Exception("Depreciation move not found.")

        if safe(move["status"]).lower() != "draft":
            raise Exception("Only draft depreciation moves can be posted.")

        if not move["journal_id"]:
            create_depreciation_move_journal(conn, move_id)
            move = conn.execute("""
                SELECT *
                FROM asset_depreciation_moves
                WHERE id = ?
                LIMIT 1
            """, (move_id,)).fetchone()

        post_journal_entry(conn, move["journal_id"])

        conn.execute("""
            UPDATE asset_depreciation_moves
            SET status = 'posted'
            WHERE id = ?
        """, (move_id,))

        conn.commit()
        asset_id = move["asset_id"]
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Post depreciation failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/fixed-assets/{asset_id}", status_code=302)


@router.post("/ui/accounting/fixed-assets/depreciation/{move_id}/reverse")
def reverse_depreciation_move(move_id: int):
    conn = get_conn()
    try:
        move = conn.execute("""
            SELECT *
            FROM asset_depreciation_moves
            WHERE id = ?
            LIMIT 1
        """, (move_id,)).fetchone()

        if not move:
            raise Exception("Depreciation move not found.")

        if safe(move["status"]).lower() != "posted":
            raise Exception("Only posted depreciation moves can be reversed.")

        if move["reversed_journal_id"]:
            raise Exception("Depreciation move already reversed.")

        reverse_id = reverse_journal_entry(conn, move["journal_id"])

        conn.execute("""
            UPDATE asset_depreciation_moves
            SET status = 'reversed',
                reversed_journal_id = ?
            WHERE id = ?
        """, (reverse_id, move_id))

        conn.commit()
        asset_id = move["asset_id"]
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reverse depreciation failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/fixed-assets/{asset_id}", status_code=302)


# =========================================================
# DISPOSAL
# =========================================================
@router.get("/ui/accounting/fixed-assets/{asset_id:int}/dispose", response_class=HTMLResponse)
def dispose_asset_form(request: Request, asset_id: int):
    conn = get_conn()
    asset = get_asset(conn, asset_id)
    if not asset:
        conn.close()
        return HTMLResponse("Asset not found.", status_code=404)

    if safe(asset["status"]).lower() != "running":
        conn.close()
        return HTMLResponse("Only running assets can be disposed.", status_code=400)

    accum_dep = get_posted_depreciation_total(conn, asset_id)
    nbv = get_asset_nbv(conn, asset)

    content = f"""
    <h3>Dispose Asset {safe(asset['code'])} - {safe(asset['name'])}</h3>

    <div class="card">
        <p><b>Cost:</b> {money(asset['cost'])}</p>
        <p><b>Accumulated Depreciation:</b> {money(accum_dep)}</p>
        <p><b>NBV:</b> {money(nbv)}</p>
    </div>

    <form method="post" action="/ui/accounting/fixed-assets/{asset_id}/dispose">
        <div class="form-grid">
            <div class="form-group">
                <label>Disposal Date</label>
                <input type="date" name="disposal_date" required>
            </div>

            <div class="form-group">
                <label>Proceeds</label>
                <input name="proceeds" value="0">
            </div>

            <div class="form-group">
                <label>Proceeds Account</label>
                <select name="proceeds_account_code">
                    {account_options()}
                </select>
            </div>

            <div class="form-group">
                <label>Gain Account</label>
                <select name="gain_account_code">
                    {account_options()}
                </select>
            </div>

            <div class="form-group">
                <label>Loss Account</label>
                <select name="loss_account_code">
                    {account_options()}
                </select>
            </div>

            <div class="form-group">
                <label>Note</label>
                <input name="note">
            </div>
        </div>

        <div class="form-actions">
            <button class="btn red" type="submit">Save Disposal Draft</button>
            <a class="btn gray" href="/ui/accounting/fixed-assets/{asset_id}">Back</a>
        </div>
    </form>
    """
    conn.close()
    return HTMLResponse(render_page("Dispose Asset", content, current_path=request.url.path))


@router.post("/ui/accounting/fixed-assets/{asset_id:int}/dispose")
def create_asset_disposal(
    asset_id: int,
    disposal_date: str = Form(""),
    proceeds: str = Form("0"),
    proceeds_account_code: str = Form(""),
    gain_account_code: str = Form(""),
    loss_account_code: str = Form(""),
    note: str = Form(""),
):
    proceeds_val = q2(proceeds)
    if proceeds_val < Decimal("0.00"):
        return HTMLResponse("Proceeds cannot be negative.", status_code=400)

    conn = get_conn()
    try:
        asset = get_asset(conn, asset_id)
        if not asset:
            raise Exception("Asset not found.")
        if safe(asset["status"]).lower() != "running":
            raise Exception("Only running assets can be disposed.")

        conn.execute("""
            INSERT INTO asset_disposals (
                asset_id, disposal_date, proceeds, proceeds_account_code,
                gain_account_code, loss_account_code, note, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')
        """, (
            asset_id,
            safe(disposal_date),
            float(proceeds_val),
            safe(proceeds_account_code),
            safe(gain_account_code),
            safe(loss_account_code),
            safe(note),
        ))
        disposal_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        create_asset_disposal_journal(conn, disposal_id)
        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Create disposal failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/fixed-assets/disposal/{disposal_id}", status_code=302)


@router.get("/ui/accounting/fixed-assets/disposal/{disposal_id}", response_class=HTMLResponse)
def open_asset_disposal(request: Request, disposal_id: int):
    conn = get_conn()
    disposal = get_disposal(conn, disposal_id)
    if not disposal:
        conn.close()
        return HTMLResponse("Disposal not found.", status_code=404)

    asset = get_asset(conn, disposal["asset_id"])
    if not asset:
        conn.close()
        return HTMLResponse("Asset not found.", status_code=404)

    accum_dep = get_posted_depreciation_total(conn, asset["id"])
    nbv = get_asset_nbv(conn, asset)
    proceeds_val = q2(disposal["proceeds"])
    result = q2(proceeds_val - nbv)

    result_label = "Gain" if result > Decimal("0.00") else "Loss" if result < Decimal("0.00") else "Break-even"

    content = f"""
    <div class="card">
        <h3>Asset Disposal</h3>
        <p><b>Asset:</b> {safe(asset['code'])} - {safe(asset['name'])}</p>
        <p><b>Date:</b> {safe(disposal['disposal_date'])}</p>
        <p><b>Proceeds:</b> {money(disposal['proceeds'])}</p>
        <p><b>NBV at Disposal:</b> {money(nbv)}</p>
        <p><b>{result_label}:</b> {money(abs(result))}</p>
        <p><b>Status:</b> {safe(disposal['status'])}</p>
        <p><b>Journal ID:</b> {safe(disposal['journal_id'])}</p>
        <p><b>Reverse Journal ID:</b> {safe(disposal['reversed_journal_id'])}</p>

        <div style="margin-top:20px;">
            {"<form method='post' action='/ui/accounting/fixed-assets/disposal/%s/post' style='display:inline;'><button class='btn green' type='submit'>Post Disposal</button></form>" % disposal_id if safe(disposal['status']).lower() == 'draft' else ""}
            <a class="btn gray" href="/ui/accounting/fixed-assets/{asset['id']}">Back to Asset</a>
        </div>
    </div>
    """
    conn.close()
    return HTMLResponse(render_page("Asset Disposal", content, current_path=request.url.path))


@router.post("/ui/accounting/fixed-assets/disposal/{disposal_id}/post")
def post_asset_disposal(disposal_id: int):
    conn = get_conn()
    try:
        disposal = get_disposal(conn, disposal_id)
        if not disposal:
            raise Exception("Disposal not found.")

        if safe(disposal["status"]).lower() != "draft":
            raise Exception("Only draft disposals can be posted.")

        if not disposal["journal_id"]:
            create_asset_disposal_journal(conn, disposal_id)
            disposal = get_disposal(conn, disposal_id)

        post_journal_entry(conn, disposal["journal_id"])

        conn.execute("""
            UPDATE asset_disposals
            SET status = 'posted'
            WHERE id = ?
        """, (disposal_id,))

        conn.execute("""
            UPDATE fixed_assets
            SET status = 'disposed',
                disposal_journal_id = ?
            WHERE id = ?
        """, (disposal["journal_id"], disposal["asset_id"]))

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Post disposal failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/fixed-assets/disposal/{disposal_id}", status_code=302)


@router.post("/ui/accounting/fixed-assets/disposal/{disposal_id}/reverse")
def reverse_asset_disposal(disposal_id: int):
    conn = get_conn()
    try:
        disposal = get_disposal(conn, disposal_id)
        if not disposal:
            raise Exception("Disposal not found.")

        if safe(disposal["status"]).lower() != "posted":
            raise Exception("Only posted disposals can be reversed.")

        if disposal["reversed_journal_id"]:
            raise Exception("Disposal already reversed.")

        reverse_id = reverse_journal_entry(conn, disposal["journal_id"])

        conn.execute("""
            UPDATE asset_disposals
            SET status = 'reversed',
                reversed_journal_id = ?
            WHERE id = ?
        """, (reverse_id, disposal_id))

        conn.execute("""
            UPDATE fixed_assets
            SET status = 'running',
                disposal_journal_id = NULL
            WHERE id = ?
        """, (disposal["asset_id"],))

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Reverse disposal failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse(f"/ui/accounting/fixed-assets/disposal/{disposal_id}", status_code=302)
