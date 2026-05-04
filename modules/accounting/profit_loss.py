from io import BytesIO

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
import openpyxl

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


def classify_account(row):
    acc_type = norm(row["type"])
    level1 = norm(row["level1"]) if "level1" in row.keys() else ""
    level2 = norm(row["level2"]) if "level2" in row.keys() else ""
    statement_type = norm(row["statement_type"]) if "statement_type" in row.keys() else ""
    name = norm(row["name"])

    # Respect explicit account classification first.
    if statement_type in ["balance_sheet", "balance sheet", "statement of financial position"]:
        return None
    if statement_type not in ["", "profit_loss", "profit and loss", "p&l", "income statement"]:
        return None

    # Any balance-sheet flavor in type/level1 should be excluded
    # (e.g. current asset, non current liability, owners equity).
    bs_tokens = ["asset", "liabil", "equity"]
    if any(token in acc_type for token in bs_tokens):
        return None
    if any(token in level1 for token in bs_tokens):
        return None

    # Revenue
    if acc_type in ["income", "revenue", "other income"]:
        return "revenue"
    if level1 == "revenue":
        return "revenue"
    if level2 in ["sales", "service revenue"]:
        return "revenue"
    if "revenue" in name or "sales" in name:
        return "revenue"

    # Cost of Revenue / COGS
    if acc_type in ["cogs", "cost of goods sold", "cost of revenue"]:
        return "cogs"
    if level1 in ["cost of goods sold", "cost of revenue"]:
        return "cogs"
    if level2 in ["direct materials", "direct labor", "factory overheads"]:
        return "cogs"
    if "cost of goods sold" in name or "cost of revenue" in name:
        return "cogs"

    # Operating expenses
    if acc_type in [
        "expense",
        "administrative expenses",
        "selling expenses",
        "financial expenses",
        "other expenses",
        "g&a",
        "depreciation expense",
        "tcow",
        "other dr balances",
    ]:
        return "opex"

    if level1 in [
        "administrative expenses",
        "selling expenses",
        "financial expenses",
        "other expenses",
    ]:
        return "opex"

    if level2 in [
        "hospitality",
        "rent",
        "utilities",
        "transportation",
        "depreciation",
        "maintenance",
        "office expenses",
        "interest",
        "miscellaneous",
    ]:
        return "opex"

    if statement_type in ["profit and loss", "p&l", "income statement"]:
        if acc_type not in ["income", "revenue", "cogs", "cost of goods sold", "cost of revenue"]:
            return "opex"

    return None


def get_pl_rows(conn, date_from="", date_to=""):
    sql = """
        SELECT
            a.code,
            a.name,
            a.type,
            a.level1,
            a.level2,
            a.statement_type,
            COALESCE(SUM(jl.debit), 0) AS total_debit,
            COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM accounts a
        JOIN journal_lines jl ON jl.account_code = a.code
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE LOWER(COALESCE(je.status, '')) = 'posted'
          AND COALESCE(a.is_group, 0) = 0
          AND COALESCE(a.is_active, 1) = 1
          AND LOWER(REPLACE(COALESCE(a.statement_type, ''), '_', ' ')) IN (
              'profit loss',
              'profit and loss',
              'p&l',
              'income statement'
          )
    """
    params = []

    if date_from:
        sql += " AND COALESCE(je.entry_date, '') >= ?"
        params.append(date_from)

    if date_to:
        sql += " AND COALESCE(je.entry_date, '') <= ?"
        params.append(date_to)

    sql += """
        GROUP BY a.code, a.name, a.type, a.level1, a.level2, a.statement_type
        ORDER BY a.code
    """

    return conn.execute(sql, params).fetchall()


def build_section(title, rows, natural_side):
    body = ""
    total = 0.0

    for r in rows:
        debit = float(r["total_debit"] or 0)
        credit = float(r["total_credit"] or 0)

        if natural_side == "credit":
            amount = credit - debit
        else:
            amount = debit - credit

        total += amount

        body += f"""
        <tr>
            <td>{r['code'] or ''}</td>
            <td>{r['name'] or ''}</td>
            <td>{money(amount)}</td>
        </tr>
        """

    if not body:
        body = """
        <tr>
            <td colspan="3" style="text-align:center;">No data</td>
        </tr>
        """

    html = f"""
    <div class="card">
        <h3>{title}</h3>
        <table>
            <tr>
                <th>Code</th>
                <th>Account Name</th>
                <th>Amount</th>
            </tr>
            {body}
            <tr style="font-weight:800;background:#f9fafb;">
                <td colspan="2">TOTAL</td>
                <td>{money(total)}</td>
            </tr>
        </table>
    </div>
    """

    return html, total


def build_pl_data(date_from: str = "", date_to: str = ""):
    conn = get_conn()
    all_rows = get_pl_rows(conn, date_from, date_to)
    conn.close()

    revenue_rows = []
    cogs_rows = []
    opex_rows = []

    for row in all_rows:
        category = classify_account(row)
        if category == "revenue":
            revenue_rows.append(row)
        elif category == "cogs":
            cogs_rows.append(row)
        elif category == "opex":
            opex_rows.append(row)

    total_income = sum((float(r["total_credit"] or 0) - float(r["total_debit"] or 0) for r in revenue_rows), 0.0)
    total_cogs = sum((float(r["total_debit"] or 0) - float(r["total_credit"] or 0) for r in cogs_rows), 0.0)
    total_opex = sum((float(r["total_debit"] or 0) - float(r["total_credit"] or 0) for r in opex_rows), 0.0)
    gross_profit = total_income - total_cogs
    net_profit = gross_profit - total_opex

    return {
        "revenue_rows": revenue_rows,
        "cogs_rows": cogs_rows,
        "opex_rows": opex_rows,
        "total_income": total_income,
        "total_cogs": total_cogs,
        "total_opex": total_opex,
        "gross_profit": gross_profit,
        "net_profit": net_profit,
    }


@router.get("/ui/accounting/profit-loss", response_class=HTMLResponse)
@router.get("/ui/accounting/profit_loss", response_class=HTMLResponse)
def profit_loss(request: Request, date_from: str = "", date_to: str = "", embed: int = 0):
    data = build_pl_data(date_from, date_to)
    revenue_rows = data["revenue_rows"]
    cogs_rows = data["cogs_rows"]
    opex_rows = data["opex_rows"]

    income_html, total_income = build_section("Revenue", revenue_rows, "credit")
    cogs_html, total_cogs = build_section("Cost of Revenue", cogs_rows, "debit")
    opex_html, total_opex = build_section("Operating Expenses", opex_rows, "debit")

    gross_profit = data["gross_profit"]
    net_profit = data["net_profit"]

    filter_html = f"""
    <div class="card">
        <form method="get">
            <div class="row">
                <div class="col">
                    <label>From Date</label>
                    <input type="date" name="date_from" value="{date_from}">
                </div>
                <div class="col">
                    <label>To Date</label>
                    <input type="date" name="date_to" value="{date_to}">
                </div>
            </div>

            <div style="margin-top:14px;">
                <button class="btn green" type="submit">Show Profit & Loss</button>
                <a class="btn gray" href="/ui/accounting/profit-loss">Clear</a>
                <a class="btn blue" href="/ui/accounting/profit-loss/export.xlsx?date_from={date_from}&date_to={date_to}">Export Excel</a>
                <a class="btn blue" href="/ui/accounting/profit-loss/export.pdf?date_from={date_from}&date_to={date_to}" target="_blank">Export PDF</a>
            </div>
        </form>
    </div>
    """

    summary_html = f"""
    <div class="card">
        <div class="row">
            <div class="col"><p><b>Total Revenue:</b> {money(total_income)}</p></div>
            <div class="col"><p><b>Total Cost of Revenue:</b> {money(total_cogs)}</p></div>
        </div>
        <div class="row">
            <div class="col"><p><b>Gross Profit:</b> {money(gross_profit)}</p></div>
            <div class="col"><p><b>Total Operating Expenses:</b> {money(total_opex)}</p></div>
        </div>
        <div class="row">
            <div class="col"><p><b>Net Profit / Loss:</b> {money(net_profit)}</p></div>
            <div class="col"></div>
        </div>
    </div>
    """

    net_label = "Net Profit" if net_profit >= 0 else "Net Loss"
    result_html = f"""
    <div class="card">
        <table>
            <tr>
                <th>Result</th>
                <th>Amount</th>
            </tr>
            <tr style="font-weight:800;background:#f9fafb;">
                <td>{net_label}</td>
                <td>{money(net_profit)}</td>
            </tr>
        </table>
    </div>
    """

    content = f"""
    <h2>Profit & Loss</h2>
    {filter_html}
    {summary_html}
    {income_html}
    {cogs_html}
    {opex_html}
    {result_html}
    """

    if int(embed or 0) == 1:
        return HTMLResponse(content)

    return HTMLResponse(
        render_page("Profit & Loss", content, "en", current_path=request.url.path)
    )


@router.get("/ui/accounting/profit-loss/export.xlsx")
def profit_loss_export_xlsx(date_from: str = "", date_to: str = ""):
    data = build_pl_data(date_from, date_to)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Profit and Loss"

    ws.append(["Profit & Loss Report"])
    ws.append(["From", date_from or ""])
    ws.append(["To", date_to or ""])
    ws.append([])

    def write_section(title, rows, natural_side):
        ws.append([title])
        ws.append(["Code", "Account Name", "Amount"])
        section_total = 0.0
        for r in rows:
            debit = float(r["total_debit"] or 0)
            credit = float(r["total_credit"] or 0)
            amount = (credit - debit) if natural_side == "credit" else (debit - credit)
            section_total += amount
            ws.append([r["code"] or "", r["name"] or "", amount])
        ws.append(["", "TOTAL", section_total])
        ws.append([])

    write_section("Revenue", data["revenue_rows"], "credit")
    write_section("Cost of Revenue", data["cogs_rows"], "debit")
    write_section("Operating Expenses", data["opex_rows"], "debit")

    ws.append(["Summary"])
    ws.append(["Total Revenue", data["total_income"]])
    ws.append(["Total Cost of Revenue", data["total_cogs"]])
    ws.append(["Gross Profit", data["gross_profit"]])
    ws.append(["Total Operating Expenses", data["total_opex"]])
    ws.append(["Net Profit / Loss", data["net_profit"]])

    out = BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=profit_loss_report.xlsx"},
    )


@router.get("/ui/accounting/profit-loss/export.pdf", response_class=HTMLResponse)
def profit_loss_export_pdf(date_from: str = "", date_to: str = ""):
    data = build_pl_data(date_from, date_to)
    net_label = "Net Profit" if data["net_profit"] >= 0 else "Net Loss"

    def section_rows(rows, natural_side):
        body = ""
        section_total = 0.0
        for r in rows:
            debit = float(r["total_debit"] or 0)
            credit = float(r["total_credit"] or 0)
            amount = (credit - debit) if natural_side == "credit" else (debit - credit)
            section_total += amount
            body += f"<tr><td>{r['code'] or ''}</td><td>{r['name'] or ''}</td><td>{money(amount)}</td></tr>"
        if not body:
            body = "<tr><td colspan='3' style='text-align:center;'>No data</td></tr>"
        body += f"<tr style='font-weight:700;background:#f3f4f6;'><td colspan='2'>TOTAL</td><td>{money(section_total)}</td></tr>"
        return body

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Profit & Loss PDF</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; color: #111827; }}
            h1 {{ margin: 0 0 12px 0; }}
            .meta {{ margin-bottom: 16px; font-size: 13px; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 16px; }}
            th, td {{ border: 1px solid #d1d5db; padding: 8px; text-align: left; }}
            th {{ background: #f9fafb; }}
            .final {{ font-weight: 800; background: #eef2ff; }}
            @media print {{
                .no-print {{ display: none; }}
            }}
        </style>
    </head>
    <body>
        <div class="no-print" style="margin-bottom:12px;">
            <button onclick="window.print()">Print / Save as PDF</button>
        </div>
        <h1>Profit & Loss</h1>
        <div class="meta">From: {date_from or '-'} | To: {date_to or '-'}</div>

        <h3>Revenue</h3>
        <table>
            <tr><th>Code</th><th>Account Name</th><th>Amount</th></tr>
            {section_rows(data["revenue_rows"], "credit")}
        </table>

        <h3>Cost of Revenue</h3>
        <table>
            <tr><th>Code</th><th>Account Name</th><th>Amount</th></tr>
            {section_rows(data["cogs_rows"], "debit")}
        </table>

        <h3>Operating Expenses</h3>
        <table>
            <tr><th>Code</th><th>Account Name</th><th>Amount</th></tr>
            {section_rows(data["opex_rows"], "debit")}
        </table>

        <table>
            <tr><th>Metric</th><th>Amount</th></tr>
            <tr><td>Total Revenue</td><td>{money(data["total_income"])}</td></tr>
            <tr><td>Total Cost of Revenue</td><td>{money(data["total_cogs"])}</td></tr>
            <tr><td>Gross Profit</td><td>{money(data["gross_profit"])}</td></tr>
            <tr><td>Total Operating Expenses</td><td>{money(data["total_opex"])}</td></tr>
            <tr class="final"><td>{net_label}</td><td>{money(data["net_profit"])}</td></tr>
        </table>
    </body>
    </html>
    """
    return HTMLResponse(html)
