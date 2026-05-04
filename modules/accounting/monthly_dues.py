from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from db import get_conn
from layout import render_page
from modules.accounting.allocation_engine import get_document_open_amount

router = APIRouter()


def money(x):
    try:
        return f"{float(x or 0):,.2f}"
    except Exception:
        return "0.00"


def month_options(selected=""):
    html = '<option value="">Select Month</option>'
    months = [
        ("1", "January"),
        ("2", "February"),
        ("3", "March"),
        ("4", "April"),
        ("5", "May"),
        ("6", "June"),
        ("7", "July"),
        ("8", "August"),
        ("9", "September"),
        ("10", "October"),
        ("11", "November"),
        ("12", "December"),
    ]
    for val, label in months:
        sel = "selected" if str(selected) == str(val) else ""
        html += f'<option value="{val}" {sel}>{label}</option>'
    return html


def year_options(selected=""):
    from datetime import datetime
    current_year = datetime.today().year
    html = '<option value="">Select Year</option>'
    for y in range(current_year - 3, current_year + 4):
        sel = "selected" if str(selected) == str(y) else ""
        html += f'<option value="{y}" {sel}>{y}</option>'
    return html


def get_partners(conn, partner_type: str):
    rows = conn.execute("""
        SELECT id, code, name
        FROM partners
        WHERE LOWER(COALESCE(partner_type,'')) = LOWER(?)
          AND COALESCE(is_active,1) = 1
        ORDER BY name
    """, (partner_type,)).fetchall()

    result = []
    for r in rows:
        label = f"{r['code']} - {r['name']}" if r["code"] else (r["name"] or "")
        result.append({
            "id": r["id"],
            "text": label
        })
    return result


@router.get("/ui/accounting/monthly-dues/partners")
def monthly_dues_partners_api(partner_type: str = ""):
    conn = get_conn()
    items = get_partners(conn, (partner_type or "").strip().lower())
    conn.close()
    return JSONResponse({"items": items})


def get_period_bounds(year: int, month: int):
    from datetime import date
    if month == 12:
        return date(year, month, 1).isoformat(), date(year + 1, 1, 1).isoformat()
    return date(year, month, 1).isoformat(), date(year, month + 1, 1).isoformat()


@router.get("/ui/accounting/monthly-dues", response_class=HTMLResponse)
def monthly_dues_page(
    request: Request,
    partner_type: str = "",
    partner_id: str = "",
    month: str = "",
    year: str = "",
    embed: int = 0,
):
    conn = get_conn()

    partner_type = (partner_type or "").strip().lower()
    partner_id = (partner_id or "").strip()
    month = (month or "").strip()
    year = (year or "").strip()

    summary_rows_html = ""
    detail_rows_html = ""
    partner_options_html = '<option value="">Select Partner</option>'

    if partner_type:
        items = get_partners(conn, partner_type)
        for item in items:
            sel = "selected" if str(item["id"]) == str(partner_id) else ""
            partner_options_html += f"<option value='{item['id']}' {sel}>{item['text']}</option>"

    total_due = 0.0

    if partner_type and month and year:
        try:
            month_no = int(month)
            year_no = int(year)
            start_date, next_month_date = get_period_bounds(year_no, month_no)
        except Exception:
            start_date, next_month_date = None, None

        if start_date and next_month_date:
            if partner_type == "customer":
                sql = """
                    SELECT
                        i.id,
                        i.invoice_no AS doc_no,
                        i.invoice_date AS doc_date,
                        i.due_date,
                        i.customer_id AS pid,
                        p.code,
                        p.name,
                        COALESCE(i.net_amount, i.total_amount, 0) AS doc_total
                    FROM customer_invoices i
                    LEFT JOIN partners p ON p.id = i.customer_id
                    WHERE LOWER(COALESCE(i.status,'')) = 'posted'
                      AND COALESCE(i.reversed_journal_id, 0) = 0
                      AND i.due_date >= ?
                      AND i.due_date < ?
                """
                params = [start_date, next_month_date]

                if partner_id:
                    sql += " AND i.customer_id = ?"
                    params.append(partner_id)

                sql += " ORDER BY p.name, i.due_date, i.id"

                docs = conn.execute(sql, params).fetchall()
                summary = {}

                for d in docs:
                    outstanding = float(get_document_open_amount(conn, "customer_invoice", d["id"]) or 0)
                    if outstanding <= 0:
                        continue

                    allocated = float((float(d["doc_total"] or 0) - outstanding))

                    pid = d["pid"]
                    code = d["code"] or ""
                    name = d["name"] or ""
                    label = f"{code} - {name}" if code else name

                    if pid not in summary:
                        summary[pid] = {
                            "label": label,
                            "amount": 0.0
                        }

                    summary[pid]["amount"] += outstanding
                    total_due += outstanding

                    detail_rows_html += f"""
                    <tr>
                        <td>{label}</td>
                        <td>{d['doc_no'] or ''}</td>
                        <td>{d['doc_date'] or ''}</td>
                        <td>{d['due_date'] or ''}</td>
                        <td>{money(d['doc_total'])}</td>
                        <td>{money(allocated)}</td>
                        <td>{money(outstanding)}</td>
                    </tr>
                    """

                for _, row in summary.items():
                    summary_rows_html += f"""
                    <tr>
                        <td>{row['label']}</td>
                        <td>{money(row['amount'])}</td>
                    </tr>
                    """

            elif partner_type == "vendor":
                sql = """
                    SELECT
                        b.id,
                        b.bill_no AS doc_no,
                        b.bill_date AS doc_date,
                        b.due_date,
                        b.vendor_id AS pid,
                        p.code,
                        p.name,
                        COALESCE(b.net_amount, b.total_amount, 0) AS doc_total
                    FROM vendor_bills b
                    LEFT JOIN partners p ON p.id = b.vendor_id
                    WHERE LOWER(COALESCE(b.status,'')) = 'posted'
                      AND COALESCE(b.reversed_journal_id, 0) = 0
                      AND b.due_date >= ?
                      AND b.due_date < ?
                """
                params = [start_date, next_month_date]

                if partner_id:
                    sql += " AND b.vendor_id = ?"
                    params.append(partner_id)

                sql += " ORDER BY p.name, b.due_date, b.id"

                docs = conn.execute(sql, params).fetchall()
                summary = {}

                for d in docs:
                    outstanding = float(get_document_open_amount(conn, "vendor_bill", d["id"]) or 0)
                    if outstanding <= 0:
                        continue

                    allocated = float((float(d["doc_total"] or 0) - outstanding))

                    pid = d["pid"]
                    code = d["code"] or ""
                    name = d["name"] or ""
                    label = f"{code} - {name}" if code else name

                    if pid not in summary:
                        summary[pid] = {
                            "label": label,
                            "amount": 0.0
                        }

                    summary[pid]["amount"] += outstanding
                    total_due += outstanding

                    detail_rows_html += f"""
                    <tr>
                        <td>{label}</td>
                        <td>{d['doc_no'] or ''}</td>
                        <td>{d['doc_date'] or ''}</td>
                        <td>{d['due_date'] or ''}</td>
                        <td>{money(d['doc_total'])}</td>
                        <td>{money(allocated)}</td>
                        <td>{money(outstanding)}</td>
                    </tr>
                    """

                for _, row in summary.items():
                    summary_rows_html += f"""
                    <tr>
                        <td>{row['label']}</td>
                        <td>{money(row['amount'])}</td>
                    </tr>
                    """

    customer_selected = "selected" if partner_type == "customer" else ""
    vendor_selected = "selected" if partner_type == "vendor" else ""

    if not summary_rows_html:
        summary_rows_html = """
        <tr>
            <td colspan="2" style="text-align:center;">No monthly dues found.</td>
        </tr>
        """

    if not detail_rows_html:
        detail_rows_html = """
        <tr>
            <td colspan="7" style="text-align:center;">No due documents found.</td>
        </tr>
        """

    html = f"""
    <div class="card">
        <h2>Monthly Dues</h2>

        <form method="get">
            <div class="row">
                <div class="col">
                    <label>Type</label>
                    <select name="partner_type" id="ptype">
                        <option value="">Select</option>
                        <option value="customer" {customer_selected}>Customer</option>
                        <option value="vendor" {vendor_selected}>Vendor</option>
                    </select>
                </div>

                <div class="col">
                    <label>Partner</label>
                    <select name="partner_id" id="partner">
                        {partner_options_html}
                    </select>
                </div>

                <div class="col">
                    <label>Month</label>
                    <select name="month" id="month">
                        {month_options(month)}
                    </select>
                </div>

                <div class="col">
                    <label>Year</label>
                    <select name="year" id="year">
                        {year_options(year)}
                    </select>
                </div>
            </div>

            <div style="margin-top:12px;">
                <button class="btn green" type="submit">Show</button>
                <a class="btn gray" href="/ui/accounting/monthly-dues">Clear</a>
                <a class="btn gray" href="/ui/accounting/export-center">Export</a>
            </div>
        </form>
    </div>

    <div class="card">
        <h3 style="margin-top:0;">Summary</h3>
        <p><b>Total Due:</b> {money(total_due)}</p>

        <table style="margin-top:16px;">
            <tr>
                <th>Partner</th>
                <th>Due Amount</th>
            </tr>
            {summary_rows_html}
        </table>
    </div>

    <div class="card">
        <h3 style="margin-top:0;">Details</h3>

        <table style="margin-top:16px;">
            <tr>
                <th>Partner</th>
                <th>Document No</th>
                <th>Document Date</th>
                <th>Due Date</th>
                <th>Document Total</th>
                <th>Allocated</th>
                <th>Outstanding</th>
            </tr>
            {detail_rows_html}
        </table>
    </div>

    <script>
    async function loadPartners(selectedId=null) {{
        const type = document.getElementById("ptype").value;
        const sel = document.getElementById("partner");

        if (!type) {{
            sel.innerHTML = "<option value=''>Select Partner</option>";
            return;
        }}

        const res = await fetch(`/ui/accounting/monthly-dues/partners?partner_type=${{type}}`);
        const data = await res.json();

        sel.innerHTML = "<option value=''>Select Partner</option>";

        data.items.forEach(i => {{
            const opt = document.createElement("option");
            opt.value = i.id;
            opt.text = i.text;

            if (selectedId && String(selectedId) === String(i.id)) {{
                opt.selected = true;
            }}

            sel.appendChild(opt);
        }});
    }}

    document.getElementById("ptype").addEventListener("change", function() {{
        loadPartners("");
    }});

    window.onload = function() {{
        loadPartners("{partner_id}");
    }};
    </script>
    """

    conn.close()
    if int(embed or 0) == 1:
        return HTMLResponse(html)

    return HTMLResponse(render_page("Monthly Dues", html, "en", request.url.path))
