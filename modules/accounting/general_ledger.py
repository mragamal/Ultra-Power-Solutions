from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db import get_conn
from layout import render_page
from modules.accounting.reports import report_tabs

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


def account_options(selected_code=""):
    conn = get_conn()
    rows = conn.execute("""
        SELECT code, name
        FROM accounts
        WHERE COALESCE(is_active, 1) = 1
          AND COALESCE(is_group, 0) = 0
        ORDER BY code, name
    """).fetchall()
    conn.close()

    html = "<option value=''>All Accounts</option>"
    for r in rows:
        selected = "selected" if str(selected_code or "") == str(r["code"] or "") else ""
        html += f"<option value='{r['code']}' {selected}>{r['code']} - {r['name']}</option>"
    return html


def get_opening_balance(conn, account_code, date_from):
    if not date_from or not account_code:
        return 0.0

    row = conn.execute("""
        SELECT
            COALESCE(SUM(jl.debit), 0) AS debit_total,
            COALESCE(SUM(jl.credit), 0) AS credit_total
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE LOWER(COALESCE(je.status, '')) = 'posted'
          AND COALESCE(jl.account_code, '') = ?
          AND COALESCE(je.entry_date, '') < ?
    """, (account_code, date_from)).fetchone()

    return float(row["debit_total"] or 0) - float(row["credit_total"] or 0)


@router.get("/ui/accounting/general-ledger", response_class=HTMLResponse)
@router.get("/ui/accounting/general_ledger", response_class=HTMLResponse)
def general_ledger(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    account_code: str = "",
    embed: int = 0,
):
    conn = get_conn()

    query = """
        SELECT
            COALESCE(je.entry_date, '') AS entry_date,
            COALESCE(je.entry_no, '') AS entry_no,
            COALESCE(je.reference, '') AS reference,
            COALESCE(je.description, '') AS journal_description,
            COALESCE(jl.line_description, '') AS line_description,
            COALESCE(jl.account_code, '') AS account_code,
            COALESCE(a.name, '') AS account_name,
            COALESCE(jl.debit, 0) AS debit,
            COALESCE(jl.credit, 0) AS credit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        LEFT JOIN accounts a ON a.code = jl.account_code
        WHERE LOWER(COALESCE(je.status, '')) = 'posted'
    """
    params = []

    if date_from:
        query += " AND COALESCE(je.entry_date, '') >= ?"
        params.append(date_from)

    if date_to:
        query += " AND COALESCE(je.entry_date, '') <= ?"
        params.append(date_to)

    if account_code:
        query += " AND COALESCE(jl.account_code, '') = ?"
        params.append(account_code)

    query += """
        ORDER BY
            COALESCE(je.entry_date, ''),
            je.id,
            COALESCE(jl.line_no, 0),
            jl.id
    """

    rows = conn.execute(query, params).fetchall()

    running_balance = get_opening_balance(conn, account_code, date_from) if account_code else 0.0
    opening_balance = running_balance if account_code else 0.0

    lines_html = ""

    if account_code and date_from:
        lines_html += f"""
        <tr class="summary-row">
            <td></td>
            <td>B/F</td>
            <td></td>
            <td>{account_code}</td>
            <td>Balance Brought Forward</td>
            <td class="text-right">{money(running_balance) if running_balance > 0 else '0.00'}</td>
            <td class="text-right">{money(abs(running_balance)) if running_balance < 0 else '0.00'}</td>
            <td class="text-right">{money(running_balance)}</td>
        </tr>
        """

    total_debit = 0.0
    total_credit = 0.0
    journal_count = set()
    account_count = set()

    for r in rows:
        debit = float(r["debit"] or 0)
        credit = float(r["credit"] or 0)
        total_debit += debit
        total_credit += credit
        if r["entry_no"]:
            journal_count.add(str(r["entry_no"]))
        if r["account_code"]:
            account_count.add(str(r["account_code"]))

        if account_code:
            running_balance += debit - credit
            balance_text = money(running_balance)
        else:
            balance_text = ""

        description = r["line_description"] or r["journal_description"] or ""
        acc_display = f"{r['account_code']} - {r['account_name']}" if r["account_name"] else (r["account_code"] or "")

        lines_html += f"""
        <tr>
            <td>{r['entry_date']}</td>
            <td>{r['entry_no']}</td>
            <td>{r['reference']}</td>
            <td>{acc_display}</td>
            <td>{description}</td>
            <td class="text-right">{money(debit)}</td>
            <td class="text-right">{money(credit)}</td>
            <td class="text-right">{balance_text}</td>
        </tr>
        """

    if not lines_html:
        lines_html = """
        <tr>
            <td colspan="8" class="empty-state">No posted journal movements found for the selected filters.</td>
        </tr>
        """

    selected_account_label = account_code or "All Accounts"
    selected_period = f"{date_from or 'Beginning'} to {date_to or 'Today'}"
    closing_balance = running_balance if account_code else (total_debit - total_credit)

    conn.close()

    content = f"""
    <div class="list-shell">
        <div class="card">
            <div class="list-header">
                <div class="list-title">
                    <h2>General Ledger</h2>
                    <p>Review posted journal lines with an invoice-style working layout that is faster to read and filter.</p>
                </div>
                <div class="table-summary">
                    <span class="summary-pill">Account: {selected_account_label}</span>
                    <span class="summary-pill">Period: {selected_period}</span>
                </div>
            </div>

            <div style="margin-top:16px;">
                {report_tabs("gl")}
            </div>
        </div>

        <div class="card">
            <div class="toolbar" style="margin-bottom:14px;">
                <div>
                    <h3 class="sub-title">Filters</h3>
                    <div class="section-note">Choose period and account, then refresh the ledger lines exactly like a working transaction list.</div>
                </div>
                <div class="table-summary">
                    <span class="summary-pill">Lines: {len(rows)}</span>
                    <span class="summary-pill">Journals: {len(journal_count)}</span>
                </div>
            </div>

            <form method="get">
                <div class="filter-grid" style="grid-template-columns: 1fr 1fr 1.4fr;">
                    <div class="form-group">
                        <label>From Date</label>
                        <input type="date" name="date_from" value="{date_from}">
                    </div>
                    <div class="form-group">
                        <label>To Date</label>
                        <input type="date" name="date_to" value="{date_to}">
                    </div>
                    <div class="form-group">
                        <label>Account</label>
                        <select id="account_code" name="account_code">
                            {account_options(account_code)}
                        </select>
                    </div>
                </div>

                <div class="filter-actions">
                    <button class="btn blue" type="submit">Filter</button>
                    <a href="/ui/accounting/general-ledger" class="btn gray">Clear</a>
                    <a href="/ui/accounting/export-center" class="btn gray">Export</a>
                </div>
            </form>
        </div>

        <div class="card">
            <div class="toolbar" style="margin-bottom:16px;">
                <div>
                    <h3 class="sub-title">Ledger Lines</h3>
                    <div class="section-note">Posted movements with debit, credit, reference, and running balance in one clean table.</div>
                </div>
                <div class="table-summary">
                    <span class="summary-pill">Opening: {money(opening_balance)}</span>
                    <span class="summary-pill">Debit: {money(total_debit)}</span>
                    <span class="summary-pill">Credit: {money(total_credit)}</span>
                    <span class="summary-pill">Closing: {money(closing_balance)}</span>
                </div>
            </div>

            <div class="table-wrap">
                <table class="table">
                    <thead>
                        <tr>
                            <th>Date</th>
                            <th>Journal #</th>
                            <th>Reference</th>
                            <th>Account</th>
                            <th>Description</th>
                            <th class="text-right">Debit</th>
                            <th class="text-right">Credit</th>
                            <th class="text-right">Balance</th>
                        </tr>
                    </thead>
                    <tbody>
                        {lines_html}
                    </tbody>
                </table>
            </div>

            <div class="table-summary" style="margin-top:15px;">
                <span class="summary-pill">Accounts in Result: {len(account_count) if account_count else 'All'}</span>
                <span class="summary-pill">Total Debit: {money(total_debit)}</span>
                <span class="summary-pill">Total Credit: {money(total_credit)}</span>
            </div>
        </div>
    </div>
    """

    if int(embed or 0) == 1:
        return HTMLResponse(content)

    return HTMLResponse(
        render_page("General Ledger", content, "en", request.url.path)
    )
