from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db import get_conn
from layout import render_page

router = APIRouter()


def money(x):
    try:
        return f"{float(x or 0):,.2f}"
    except Exception:
        return "0.00"


def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


def ensure_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            name TEXT NOT NULL,
            type TEXT,
            parent_id INTEGER,
            level1 TEXT,
            level2 TEXT,
            statement_type TEXT,
            is_group INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            allow_posting INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_no TEXT,
            entry_date TEXT,
            description TEXT,
            reference TEXT,
            status TEXT DEFAULT 'draft',
            source_type TEXT,
            source_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS journal_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journal_id INTEGER NOT NULL,
            line_no INTEGER DEFAULT 1,
            line_description TEXT,
            account_code TEXT,
            debit REAL DEFAULT 0,
            credit REAL DEFAULT 0,
            partner_type TEXT,
            partner_id INTEGER
        )
    """)

    ensure_column(conn, "accounts", "code", "ALTER TABLE accounts ADD COLUMN code TEXT")
    ensure_column(conn, "accounts", "name", "ALTER TABLE accounts ADD COLUMN name TEXT")
    ensure_column(conn, "accounts", "type", "ALTER TABLE accounts ADD COLUMN type TEXT")
    ensure_column(conn, "accounts", "parent_id", "ALTER TABLE accounts ADD COLUMN parent_id INTEGER")
    ensure_column(conn, "accounts", "level1", "ALTER TABLE accounts ADD COLUMN level1 TEXT")
    ensure_column(conn, "accounts", "level2", "ALTER TABLE accounts ADD COLUMN level2 TEXT")
    ensure_column(conn, "accounts", "statement_type", "ALTER TABLE accounts ADD COLUMN statement_type TEXT")
    ensure_column(conn, "accounts", "is_group", "ALTER TABLE accounts ADD COLUMN is_group INTEGER DEFAULT 0")
    ensure_column(conn, "accounts", "is_active", "ALTER TABLE accounts ADD COLUMN is_active INTEGER DEFAULT 1")
    ensure_column(conn, "accounts", "allow_posting", "ALTER TABLE accounts ADD COLUMN allow_posting INTEGER DEFAULT 1")

    ensure_column(conn, "journal_entries", "entry_no", "ALTER TABLE journal_entries ADD COLUMN entry_no TEXT")
    ensure_column(conn, "journal_entries", "entry_date", "ALTER TABLE journal_entries ADD COLUMN entry_date TEXT")
    ensure_column(conn, "journal_entries", "description", "ALTER TABLE journal_entries ADD COLUMN description TEXT")
    ensure_column(conn, "journal_entries", "reference", "ALTER TABLE journal_entries ADD COLUMN reference TEXT")
    ensure_column(conn, "journal_entries", "status", "ALTER TABLE journal_entries ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "journal_entries", "source_type", "ALTER TABLE journal_entries ADD COLUMN source_type TEXT")
    ensure_column(conn, "journal_entries", "source_id", "ALTER TABLE journal_entries ADD COLUMN source_id INTEGER")
    ensure_column(conn, "journal_entries", "created_at", "ALTER TABLE journal_entries ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "journal_lines", "journal_id", "ALTER TABLE journal_lines ADD COLUMN journal_id INTEGER")
    ensure_column(conn, "journal_lines", "line_no", "ALTER TABLE journal_lines ADD COLUMN line_no INTEGER DEFAULT 1")
    ensure_column(conn, "journal_lines", "line_description", "ALTER TABLE journal_lines ADD COLUMN line_description TEXT")
    ensure_column(conn, "journal_lines", "account_code", "ALTER TABLE journal_lines ADD COLUMN account_code TEXT")
    ensure_column(conn, "journal_lines", "debit", "ALTER TABLE journal_lines ADD COLUMN debit REAL DEFAULT 0")
    ensure_column(conn, "journal_lines", "credit", "ALTER TABLE journal_lines ADD COLUMN credit REAL DEFAULT 0")
    ensure_column(conn, "journal_lines", "partner_type", "ALTER TABLE journal_lines ADD COLUMN partner_type TEXT")
    ensure_column(conn, "journal_lines", "partner_id", "ALTER TABLE journal_lines ADD COLUMN partner_id INTEGER")

    conn.commit()
    conn.close()


ensure_tables()


def norm(x):
    return (str(x or "")).strip().lower()


def classify_balance_sheet_account(row):
    acc_type = norm(row["type"])
    level1 = norm(row["level1"]) if "level1" in row.keys() else ""
    statement_type = norm(row["statement_type"]) if "statement_type" in row.keys() else ""
    name = norm(row["name"])

    # Assets
    if acc_type in ["asset", "assets", "current asset", "fixed asset", "non-current asset"]:
        return "asset"
    if level1 in ["assets", "current assets", "non-current assets", "fixed assets"]:
        return "asset"
    if statement_type in ["balance sheet", "statement of financial position"] and "asset" in level1:
        return "asset"
    if "cash" in name or "bank" in name or "receivable" in name or "inventory" in name or "asset" in name:
        if acc_type not in ["liability", "equity", "income", "expense"]:
            return "asset"

    # Liabilities
    if acc_type in ["liability", "liabilities", "current liability", "non-current liability"]:
        return "liability"
    if level1 in ["liabilities", "current liabilities", "non-current liabilities"]:
        return "liability"
    if statement_type in ["balance sheet", "statement of financial position"] and "liabil" in level1:
        return "liability"
    if "payable" in name or "accrual" in name or "liability" in name:
        if acc_type not in ["asset", "equity", "income", "expense"]:
            return "liability"

    # Equity
    if acc_type in ["equity", "owner's equity", "owners equity"]:
        return "equity"
    if level1 in ["equity", "owner's equity", "owners equity"]:
        return "equity"
    if statement_type in ["balance sheet", "statement of financial position"] and "equity" in level1:
        return "equity"
    if "capital" in name or "drawing" in name or "equity" in name or "retained earnings" in name:
        if acc_type not in ["asset", "liability", "income", "expense"]:
            return "equity"

    return None


def get_account_balances(conn, date_to=""):
    params = []
    if date_to:
        debit_expr = "COALESCE(SUM(CASE WHEN je.id IS NOT NULL AND LOWER(COALESCE(je.status,'')) = 'posted' AND SUBSTR(COALESCE(je.entry_date,''),1,10) <= ? THEN jl.debit ELSE 0 END),0) AS debit"
        credit_expr = "COALESCE(SUM(CASE WHEN je.id IS NOT NULL AND LOWER(COALESCE(je.status,'')) = 'posted' AND SUBSTR(COALESCE(je.entry_date,''),1,10) <= ? THEN jl.credit ELSE 0 END),0) AS credit"
        params.extend([date_to, date_to])
    else:
        debit_expr = "COALESCE(SUM(CASE WHEN je.id IS NOT NULL AND LOWER(COALESCE(je.status,'')) = 'posted' THEN jl.debit ELSE 0 END),0) AS debit"
        credit_expr = "COALESCE(SUM(CASE WHEN je.id IS NOT NULL AND LOWER(COALESCE(je.status,'')) = 'posted' THEN jl.credit ELSE 0 END),0) AS credit"

    sql = f"""
        SELECT
            a.code,
            a.name,
            a.type,
            a.level1,
            a.level2,
            a.statement_type,
            {debit_expr},
            {credit_expr}
        FROM accounts a
        LEFT JOIN journal_lines jl ON jl.account_code = a.code
        LEFT JOIN journal_entries je ON je.id = jl.journal_id
        WHERE COALESCE(a.is_active,1) = 1
          AND COALESCE(a.is_group,0) = 0
        GROUP BY a.code, a.name, a.type, a.level1, a.level2, a.statement_type
        ORDER BY a.code
    """

    rows = conn.execute(sql, params).fetchall()

    data = []
    for r in rows:
        classification = classify_balance_sheet_account(r)
        if not classification:
            continue

        debit = float(r["debit"] or 0)
        credit = float(r["credit"] or 0)

        if classification == "asset":
            balance = debit - credit
        else:
            balance = credit - debit

        if abs(balance) < 0.0001:
            continue

        data.append({
            "code": r["code"],
            "name": r["name"],
            "type": r["type"],
            "classification": classification,
            "balance": balance,
        })

    return data


def calculate_retained_earnings(conn, date_to=""):
    sql = """
        SELECT
            a.code,
            a.name,
            a.type,
            a.level1,
            a.level2,
            a.statement_type,
            COALESCE(SUM(jl.debit),0) AS debit,
            COALESCE(SUM(jl.credit),0) AS credit
        FROM accounts a
        JOIN journal_lines jl ON jl.account_code = a.code
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE LOWER(COALESCE(je.status,'')) = 'posted'
          AND COALESCE(a.is_group,0) = 0
          AND COALESCE(a.is_active,1) = 1
    """
    params = []

    if date_to:
        sql += " AND COALESCE(je.entry_date,'') <= ?"
        params.append(date_to)

    sql += """
        GROUP BY a.code, a.name, a.type, a.level1, a.level2, a.statement_type
    """

    rows = conn.execute(sql, params).fetchall()

    revenue_total = 0.0
    cogs_total = 0.0
    opex_total = 0.0

    for r in rows:
        acc_type = norm(r["type"])
        level1 = norm(r["level1"]) if "level1" in r.keys() else ""
        level2 = norm(r["level2"]) if "level2" in r.keys() else ""
        statement_type = norm(r["statement_type"]) if "statement_type" in r.keys() else ""
        name = norm(r["name"])

        debit = float(r["debit"] or 0)
        credit = float(r["credit"] or 0)

        # Revenue
        is_revenue = (
            acc_type in ["income", "revenue", "other income"]
            or level1 == "revenue"
            or level2 in ["sales", "service revenue"]
            or "revenue" in name
            or "sales" in name
        )

        # COGS
        is_cogs = (
            acc_type in ["cogs", "cost of goods sold", "cost of revenue"]
            or level1 in ["cost of goods sold", "cost of revenue"]
            or level2 in ["direct materials", "direct labor", "factory overheads"]
            or "cost of goods sold" in name
            or "cost of revenue" in name
        )

        # OPEX
        is_opex = (
            acc_type in [
                "expense",
                "administrative expenses",
                "selling expenses",
                "financial expenses",
                "other expenses",
                "g&a",
                "depreciation expense",
                "tcow",
                "other dr balances",
            ]
            or level1 in [
                "administrative expenses",
                "selling expenses",
                "financial expenses",
                "other expenses",
            ]
            or level2 in [
                "hospitality",
                "rent",
                "utilities",
                "transportation",
                "depreciation",
                "maintenance",
                "office expenses",
                "interest",
                "miscellaneous",
            ]
            or (
                statement_type in ["profit and loss", "p&l", "income statement"]
                and not is_revenue
                and not is_cogs
            )
        )

        if is_revenue:
            revenue_total += credit - debit
        elif is_cogs:
            cogs_total += debit - credit
        elif is_opex:
            opex_total += debit - credit

    return revenue_total - cogs_total - opex_total


def render_rows(rows):
    html = ""
    for r in rows:
        html += f"""
        <tr>
            <td>{r['code']}</td>
            <td>{r['name']}</td>
            <td>{money(r['balance'])}</td>
        </tr>
        """
    if not html:
        html = """
        <tr>
            <td colspan="3" style="text-align:center;">No data</td>
        </tr>
        """
    return html


@router.get("/ui/accounting/balance-sheet", response_class=HTMLResponse)
def balance_sheet(request: Request, date_to: str = "", embed: int = 0):
    conn = get_conn()

    data = get_account_balances(conn, date_to)

    assets = []
    liabilities = []
    equity = []

    for acc in data:
        if acc["classification"] == "asset":
            assets.append(acc)
        elif acc["classification"] == "liability":
            liabilities.append(acc)
        elif acc["classification"] == "equity":
            equity.append(acc)

    retained = calculate_retained_earnings(conn, date_to)

    total_assets = sum(a["balance"] for a in assets)
    total_liabilities = sum(l["balance"] for l in liabilities)
    total_equity = sum(e["balance"] for e in equity) + retained

    conn.close()

    content = f"""
    <div class="card">
        <h2>Balance Sheet</h2>

        <form method="get">
            <label>As of Date</label>
            <input type="date" name="date_to" value="{date_to}">
            <br><br>
            <button class="btn green">Show</button>
            <a class="btn gray" href="/ui/accounting/balance-sheet">Clear</a>
            <a class="btn gray" href="/ui/accounting/export-center">Export</a>
        </form>
    </div>

    <div class="card">
        <h3>Assets</h3>
        <table>
            <tr><th>Code</th><th>Name</th><th>Amount</th></tr>
            {render_rows(assets)}
            <tr><th colspan="2">Total Assets</th><th>{money(total_assets)}</th></tr>
        </table>
    </div>

    <div class="card">
        <h3>Liabilities</h3>
        <table>
            <tr><th>Code</th><th>Name</th><th>Amount</th></tr>
            {render_rows(liabilities)}
            <tr><th colspan="2">Total Liabilities</th><th>{money(total_liabilities)}</th></tr>
        </table>
    </div>

    <div class="card">
        <h3>Equity</h3>
        <table>
            <tr><th>Code</th><th>Name</th><th>Amount</th></tr>
            {render_rows(equity)}
            <tr>
                <td></td>
                <td>Retained Earnings</td>
                <td>{money(retained)}</td>
            </tr>
            <tr><th colspan="2">Total Equity</th><th>{money(total_equity)}</th></tr>
        </table>
    </div>

    <div class="card">
        <h2>
            Assets = {money(total_assets)} |
            Liabilities + Equity = {money(total_liabilities + total_equity)}
        </h2>
    </div>
    """

    if int(embed or 0) == 1:
        return HTMLResponse(content)

    return HTMLResponse(render_page("Balance Sheet", content, "en", request.url.path))
