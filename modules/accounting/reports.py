import csv
from io import BytesIO, StringIO
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from db import get_conn
from layout import render_page
from i18n import get_lang
import openpyxl

router = APIRouter()

REPORT_ITEMS = [
    {
        "key": "gl",
        "title": "General Ledger",
        "href": "/ui/accounting/general-ledger",
        "icon": "/static/icons/journal.svg",
        "desc": "Posted journals by account with running balance.",
    },
    {
        "key": "tb",
        "title": "Trial Balance",
        "href": "/ui/accounting/trial-balance",
        "icon": "/static/icons/chart-accounts.svg",
        "desc": "Opening, period, and closing balances.",
    },
    {
        "key": "pl",
        "title": "Profit & Loss",
        "href": "/ui/accounting/profit-loss",
        "icon": "/static/icons/reports.svg",
        "desc": "Revenue, cost of revenue, and operating expenses.",
    },
    {
        "key": "bs",
        "title": "Balance Sheet",
        "href": "/ui/accounting/balance-sheet",
        "icon": "/static/icons/configuration.svg",
        "desc": "Assets, liabilities, and equity.",
    },
    {
        "key": "partner",
        "title": "Partner Ledger",
        "href": "/ui/accounting/partner-ledger",
        "icon": "/static/icons/customers.svg",
        "desc": "Customer, vendor, and employee partner movements.",
    },
    {
        "key": "aging",
        "title": "Aging",
        "href": "/ui/accounting/aging",
        "icon": "/static/icons/customer-statement.svg",
        "desc": "Open receivables and payables by age bucket.",
    },
    {
        "key": "dues",
        "title": "Monthly Dues",
        "href": "/ui/accounting/monthly-dues",
        "icon": "/static/icons/vendor-statement.svg",
        "desc": "Due invoices and bills by month.",
    },
    {
        "key": "petty_cash",
        "title": "Petty Cash Statement",
        "href": "/ui/accounting/petty-cash/statement",
        "icon": "/static/icons/petty-cash.svg",
        "desc": "Employee custody statement and movement balance.",
    },
    {
        "key": "advances",
        "title": "Employee Advances Statement",
        "href": "/ui/accounting/employee-advances/statement",
        "icon": "/static/icons/customer-statement.svg",
        "desc": "Detailed statement of employee advances and deductions.",
    },
    {
        "key": "assets",
        "title": "Asset Register",
        "href": "/ui/accounting/fixed-assets/statement",
        "icon": "/static/icons/fixed-assets.svg",
        "desc": "Asset register, depreciation, and movement statement.",
    },
]


def _safe_export_name(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name or "").strip())
    return value.strip("_") or "export"


def _list_exportable_tables():
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """).fetchall()
        return [str(r["name"]) for r in rows]
    finally:
        conn.close()


def _fetch_table_rows(table_name: str):
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table_name or ""):
        raise Exception("Invalid table name")

    conn = get_conn()
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        headers = [c["name"] for c in cols]
        if not headers:
            raise Exception("Table has no columns")
        rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
        data = [[row[h] for h in headers] for row in rows]
        return headers, data
    finally:
        conn.close()


def report_tabs(active_key: str) -> str:
    tabs = [("Reports Home", "/ui/accounting/reports", "home")] + [
        (item["title"], item["href"], item["key"]) for item in REPORT_ITEMS
    ]
    tabs.append(("Export Center", "/ui/accounting/export-center", "export_center"))

    html = '<div class="page-tabs">'
    for label, href, key in tabs:
        cls = "page-tab active" if key == active_key else "page-tab"
        html += f'<a class="{cls}" href="{href}">{label}</a>'
    html += "</div>"
    return html


def report_cards(selected_key: str = "gl") -> str:
    html = """
    <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;">
    """
    for item in REPORT_ITEMS:
        is_active = item["key"] == selected_key
        border = "#1b57d0" if is_active else "#dfe6f1"
        bg = (
            "linear-gradient(135deg, #edf4ff 0%, #ffffff 55%, #f6fbff 100%)"
            if is_active else
            "linear-gradient(180deg, #ffffff 0%, #fbfcff 100%)"
        )
        title_color = "#1b57d0" if is_active else "#16335d"
        shadow = "0 14px 34px rgba(27,87,208,0.12)" if is_active else "0 8px 24px rgba(15,35,95,0.05)"
        icon_bg = "#eaf2ff" if is_active else "#f4f7fb"
        icon_border = "#cfe0ff" if is_active else "#e2e8f2"
        label = "Active" if is_active else "Report"
        label_color = "#1b57d0" if is_active else "#7a8ea9"

        html += f"""
        <a href="{item['href']}" style="display:block;padding:18px 14px;border-radius:20px;border:1px solid {border};background:{bg};text-align:left;color:inherit;box-shadow:{shadow};position:relative;overflow:hidden;">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;">
                <div style="width:56px;height:56px;border-radius:18px;background:{icon_bg};display:flex;align-items:center;justify-content:center;border:1px solid {icon_border};">
                    <img src="{item['icon']}" alt="{item['title']}" style="width:32px;height:32px;object-fit:contain;display:block;">
                </div>
                <div style="font-size:11px;font-weight:800;color:{label_color};text-transform:uppercase;letter-spacing:.8px;">{label}</div>
            </div>
            <div style="font-size:16px;font-weight:800;color:{title_color};line-height:1.35;margin-bottom:6px;">{item['title']}</div>
            <div style="font-size:13px;line-height:1.55;color:#6680a0;">{item['desc']}</div>
        </a>
        """
    html += "</div>"
    return html


def get_selected_report(report_key: str):
    for item in REPORT_ITEMS:
        if item["key"] == report_key:
            return item
    return REPORT_ITEMS[0]


@router.get("/ui/accounting/reports", response_class=HTMLResponse)
def reports_home(request: Request, report: str = "gl"):
    lang = get_lang(request)
    selected = get_selected_report(report)

    content = f"""
    <div class="list-shell">
        <div class="card">
            <div style="padding:24px;border-radius:22px;border:1px solid #dfe6f1;background:radial-gradient(circle at top right, rgba(42,103,234,0.14), transparent 28%), linear-gradient(135deg, #ffffff 0%, #f7faff 100%);box-shadow:0 14px 34px rgba(15,35,95,0.08);">
                <div class="toolbar" style="align-items:flex-start;gap:18px;">
                    <div style="max-width:760px;">
                        <div style="display:inline-flex;align-items:center;gap:8px;padding:6px 10px;border-radius:999px;background:#eaf2ff;color:#1b57d0;font-size:12px;font-weight:800;margin-bottom:12px;">Accounting Reports</div>
                        <h2 style="font-size:30px;line-height:1.1;color:#13315c;margin-bottom:0;">Reports Center</h2>
                    </div>
                    <div class="table-summary">
                        <span class="summary-pill">Selected: {selected['title']}</span>
                        <span class="summary-pill">{len(REPORT_ITEMS)} Reports</span>
                    </div>
                </div>

                <div style="margin-top:20px;">
                    {report_cards(selected['key'])}
                </div>
                <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap;">
                    <a class="btn blue" href="/ui/accounting/export-center">Export Center</a>
                    <a class="btn gray" href="/ui/accounting/journal/export">Export Journal (Excel)</a>
                    <a class="btn gray" href="/ui/accounting/profit-loss/export.xlsx">Export P&L (Excel)</a>
                    <a class="btn gray" href="/ui/accounting/profit-loss/export.pdf" target="_blank">Export P&L (PDF)</a>
                </div>
            </div>
        </div>
    </div>
    """

    return HTMLResponse(
        render_page("Reports", content, lang, current_path=str(request.url.path))
    )


@router.get("/ui/accounting/export-center", response_class=HTMLResponse)
def export_center(request: Request):
    lang = get_lang(request)
    tables = _list_exportable_tables()
    options = "".join([f"<option value='{t}'>{t}</option>" for t in tables])

    content = f"""
    <div class="list-shell">
        <div class="card">
            <div class="toolbar">
                <h3 class="sub-title">Export Center</h3>
                <a class="btn gray" href="/ui/accounting/reports">Back</a>
            </div>

            <div class="card" style="margin-top:14px;">
                <h3>Reports Export</h3>
                <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;">
                    <a class="btn blue" href="/ui/accounting/journal/export">Journal Excel</a>
                    <a class="btn blue" href="/ui/accounting/profit-loss/export.xlsx">Profit &amp; Loss Excel</a>
                    <a class="btn blue" href="/ui/accounting/profit-loss/export.pdf" target="_blank">Profit &amp; Loss PDF</a>
                </div>
            </div>

            <div class="card" style="margin-top:14px;">
                <h3>Data Export (Any Table)</h3>
                <form method="get" action="/ui/accounting/export-data" style="margin-top:10px;">
                    <div class="row">
                        <div class="col">
                            <label>Table</label>
                            <select name="table" required>{options}</select>
                        </div>
                        <div class="col">
                            <label>Format</label>
                            <select name="format">
                                <option value="xlsx">Excel (.xlsx)</option>
                                <option value="csv">CSV (.csv)</option>
                            </select>
                        </div>
                    </div>
                    <div style="margin-top:14px;">
                        <button class="btn green" type="submit">Download Data</button>
                    </div>
                </form>
            </div>
        </div>
    </div>
    """
    return HTMLResponse(render_page("Export Center", content, lang, current_path=str(request.url.path)))


@router.get("/ui/accounting/export-data")
def export_data(table: str, format: str = "xlsx"):
    available = set(_list_exportable_tables())
    if table not in available:
        return HTMLResponse("Invalid table name.", status_code=400)

    headers, rows = _fetch_table_rows(table)
    export_name = _safe_export_name(table)
    fmt = (format or "xlsx").strip().lower()

    if fmt == "csv":
        stream = StringIO()
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)
        out = BytesIO(stream.getvalue().encode("utf-8-sig"))
        out.seek(0)
        return StreamingResponse(
            out,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{export_name}.csv"'},
        )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = export_name[:31] or "data"
    ws.append(headers)
    for row in rows:
        ws.append(row)

    out = BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{export_name}.xlsx"'},
    )
