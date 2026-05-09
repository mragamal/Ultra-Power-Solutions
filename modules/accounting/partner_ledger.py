from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from urllib.parse import urlencode

from db import get_conn
from layout import render_page

router = APIRouter()


def money(x):
    try:
        return f"{float(x or 0):,.2f}"
    except Exception:
        return "0.00"


def action_url(path, params):
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return path + ("?" + urlencode(clean) if clean else "")


def get_partners(conn, partner_type: str):
    pt = (partner_type or "").strip().lower()

    # unified partners table
    try:
        rows = conn.execute("""
            SELECT id, code, name
            FROM partners
            WHERE LOWER(COALESCE(partner_type,'')) = LOWER(?)
              AND COALESCE(is_active,1) = 1
            ORDER BY name
        """, (pt,)).fetchall()

        if rows:
            return [
                {
                    "id": r["id"],
                    "text": f"{r['code']} - {r['name']}" if r["code"] else (r["name"] or "")
                }
                for r in rows
            ]
    except Exception:
        pass

    # fallback: vendors
    if pt == "vendor":
        try:
            rows = conn.execute("""
                SELECT id, code, name
                FROM vendors
                ORDER BY name
            """).fetchall()

            return [
                {
                    "id": r["id"],
                    "text": f"{r['code']} - {r['name']}" if r["code"] else (r["name"] or "")
                }
                for r in rows
            ]
        except Exception:
            pass

    # fallback: customers
    if pt == "customer":
        try:
            rows = conn.execute("""
                SELECT id, code, name
                FROM customers
                ORDER BY name
            """).fetchall()

            return [
                {
                    "id": r["id"],
                    "text": f"{r['code']} - {r['name']}" if r["code"] else (r["name"] or "")
                }
                for r in rows
            ]
        except Exception:
            pass

    # fallback: employees
    if pt == "employee":
        try:
            rows = conn.execute("""
                SELECT id, code, name
                FROM employees
                ORDER BY name
            """).fetchall()

            out = []
            for r in rows:
                code = r["code"] if "code" in r.keys() else ""
                name = r["name"] if "name" in r.keys() else ""
                label = f"{code} - {name}" if code else name
                out.append({"id": r["id"], "text": label})
            return out
        except Exception:
            pass

    return []


def get_partner_display(conn, partner_type: str, partner_id: str):
    try:
        pid = int(partner_id)
    except Exception:
        return ""

    items = get_partners(conn, partner_type)
    for item in items:
        try:
            if int(item["id"]) == pid:
                return item["text"]
        except Exception:
            pass
    return ""


def get_partner_account_code(conn, partner_type: str, partner_id: str):
    try:
        pid = int(partner_id)
    except Exception:
        return None

    # unified partners table
    try:
        row = conn.execute("""
            SELECT account_code
            FROM partners
            WHERE id = ?
            LIMIT 1
        """, (pid,)).fetchone()
        if row and "account_code" in row.keys() and row["account_code"]:
            return str(row["account_code"])
    except Exception:
        pass

    pt = (partner_type or "").strip().lower()

    # fallback tables
    if pt == "customer":
        for col in ["account_code", "account"]:
            try:
                row = conn.execute(f"""
                    SELECT {col} AS account_code
                    FROM customers
                    WHERE id = ?
                    LIMIT 1
                """, (pid,)).fetchone()
                if row and row["account_code"]:
                    return str(row["account_code"])
            except Exception:
                pass

    if pt == "vendor":
        for col in ["account_code", "account"]:
            try:
                row = conn.execute(f"""
                    SELECT {col} AS account_code
                    FROM vendors
                    WHERE id = ?
                    LIMIT 1
                """, (pid,)).fetchone()
                if row and row["account_code"]:
                    return str(row["account_code"])
            except Exception:
                pass

    if pt == "employee":
        for col in ["account_code", "custody_account", "account"]:
            try:
                row = conn.execute(f"""
                    SELECT {col} AS account_code
                    FROM employees
                    WHERE id = ?
                    LIMIT 1
                """, (pid,)).fetchone()
                if row and row["account_code"]:
                    return str(row["account_code"])
            except Exception:
                pass

    return None


def account_label(conn, account_code):
    if not account_code:
        return ""
    try:
        row = conn.execute("""
            SELECT code, name
            FROM accounts
            WHERE code = ?
            LIMIT 1
        """, (account_code,)).fetchone()
        if row:
            return f"{row['code']} - {row['name']}"
    except Exception:
        pass
    return str(account_code)


def get_opening_balance(conn, partner_type: str, partner_id: str, filter_account: str, date_from: str):
    if not date_from or not partner_id or not partner_type:
        return 0.0

    sql = """
        SELECT
            COALESCE(SUM(l.debit), 0) AS total_debit,
            COALESCE(SUM(l.credit), 0) AS total_credit
        FROM journal_lines l
        JOIN journal_entries j ON j.id = l.journal_id
        WHERE LOWER(COALESCE(j.status,'')) = 'posted'
          AND l.partner_id = ?
          AND LOWER(COALESCE(l.partner_type,'')) = LOWER(?)
          AND COALESCE(j.entry_date,'') < ?
    """
    params = [partner_id, partner_type, date_from]

    # For employees, only filter by account if one was explicitly requested
    if filter_account:
        sql += " AND COALESCE(l.account_code,'') = ?"
        params.append(filter_account)
    elif partner_type.lower() != 'employee':
        # For other partners, we might want to default to an account if needed,
        # but usually, we want the full balance if no account is specified.
        # However, the previous logic used partner_account_code (which could be default).
        # Let's keep it flexible.
        pass

    row = conn.execute(sql, params).fetchone()
    return float(row["total_debit"] or 0) - float(row["total_credit"] or 0)


def get_partner_opening_balance(conn, partner_type: str, partner_id: str, date_from: str, filter_account: str = ""):
    if not date_from or not partner_type:
        return 0.0

    sql = """
        SELECT
            COALESCE(SUM(l.debit), 0) AS total_debit,
            COALESCE(SUM(l.credit), 0) AS total_credit
        FROM journal_lines l
        JOIN journal_entries j ON j.id = l.journal_id
        WHERE LOWER(COALESCE(j.status,'')) = 'posted'
          AND LOWER(COALESCE(l.partner_type,'')) = LOWER(?)
          AND COALESCE(j.entry_date,'') < ?
    """
    params = [partner_type, date_from]

    if partner_id:
        sql += " AND l.partner_id = ?"
        params.append(partner_id)

    if filter_account:
        sql += " AND COALESCE(l.account_code,'') = ?"
        params.append(filter_account)

    row = conn.execute(sql, params).fetchone()
    return float(row["total_debit"] or 0) - float(row["total_credit"] or 0)


def get_partner_summary_rows(conn, partner_type: str, date_from: str = "", date_to: str = "", filter_account: str = ""):
    sql = """
        SELECT
            l.partner_type,
            l.partner_id,
            COALESCE(SUM(l.debit), 0) AS total_debit,
            COALESCE(SUM(l.credit), 0) AS total_credit
        FROM journal_lines l
        JOIN journal_entries j ON j.id = l.journal_id
        WHERE LOWER(COALESCE(j.status,'')) = 'posted'
          AND LOWER(COALESCE(l.partner_type,'')) = LOWER(?)
          AND COALESCE(l.partner_id, 0) > 0
    """
    params = [partner_type]

    if filter_account:
        sql += " AND COALESCE(l.account_code,'') = ?"
        params.append(filter_account)

    if date_from:
        sql += " AND COALESCE(j.entry_date,'') >= ?"
        params.append(date_from)

    if date_to:
        sql += " AND COALESCE(j.entry_date,'') <= ?"
        params.append(date_to)

    sql += " GROUP BY LOWER(COALESCE(l.partner_type,'')), l.partner_id ORDER BY l.partner_id"
    return conn.execute(sql, params).fetchall()


@router.get("/ui/accounting/partner-ledger/partners")
def partner_options_api(partner_type: str = ""):
    conn = get_conn()
    items = get_partners(conn, partner_type)
    conn.close()
    return JSONResponse({"items": items})


@router.get("/ui/accounting/partner-ledger", response_class=HTMLResponse)
def partner_ledger(
    request: Request,
    partner_type: str = "",
    partner_id: str = "",
    date_from: str = "",
    date_to: str = "",
    embed: int = 0,
):
    conn = get_conn()

    partner_type = (partner_type or "").strip().lower()
    partner_id = (partner_id or "").strip()
    date_from = (date_from or "").strip()
    date_to = (date_to or "").strip()

    rows = []
    total_debit = 0.0
    total_credit = 0.0
    balance = 0.0
    opening_balance = 0.0
    partner_text = ""
    partner_account_code = None

    if partner_type:
        partner_text = get_partner_display(conn, partner_type, partner_id)
        partner_account_code = get_partner_account_code(conn, partner_type, partner_id) if partner_id else None
        filter_account = request.query_params.get("account_code", "").strip()

        if partner_id:
            opening_balance = get_partner_opening_balance(conn, partner_type, partner_id, date_from, filter_account)
            balance = opening_balance

            sql = """
                SELECT
                    j.id AS journal_id,
                    j.entry_date,
                    j.entry_no,
                    j.reference,
                    j.description,
                    l.line_description,
                    l.partner_type,
                    l.partner_id,
                    l.account_code,
                    l.debit,
                    l.credit
                FROM journal_lines l
                JOIN journal_entries j ON j.id = l.journal_id
                WHERE LOWER(COALESCE(j.status,'')) = 'posted'
                  AND LOWER(COALESCE(l.partner_type,'')) = LOWER(?)
            """
            params = [partner_type]

            sql += " AND l.partner_id = ?"
            params.append(partner_id)

            if filter_account:
                sql += " AND COALESCE(l.account_code,'') = ?"
                params.append(filter_account)

            if date_from:
                sql += " AND COALESCE(j.entry_date,'') >= ?"
                params.append(date_from)

            if date_to:
                sql += " AND COALESCE(j.entry_date,'') <= ?"
                params.append(date_to)

            sql += " ORDER BY j.entry_date, j.id, COALESCE(l.line_no,0), l.id"
            rows = conn.execute(sql, params).fetchall()
        else:
            rows = get_partner_summary_rows(conn, partner_type, date_from, date_to, filter_account)

    body = ""

    if partner_type and partner_id and date_from:
        body += f"""
        <tr style="background:#f3f4f6;font-weight:700;">
            <td></td>
            <td></td>
            <td>B/F</td>
            <td></td>
            <td>Opening Balance</td>
            <td>{account_label(conn, partner_account_code) if partner_account_code else ''}</td>
            <td>{money(opening_balance) if opening_balance > 0 else '0.00'}</td>
            <td>{money(abs(opening_balance)) if opening_balance < 0 else '0.00'}</td>
            <td>{money(opening_balance)}</td>
            <td></td>
        </tr>
        """

    if partner_id:
        for r in rows:
            debit = float(r["debit"] or 0)
            credit = float(r["credit"] or 0)

            total_debit += debit
            total_credit += credit
            balance += debit - credit

            description = r["line_description"] or r["description"] or ""
            row_partner = get_partner_display(conn, r["partner_type"], r["partner_id"])

            body += f"""
            <tr>
                <td>{r['entry_date'] or ''}</td>
                <td>{row_partner}</td>
                <td>{r['entry_no'] or ''}</td>
                <td>{r['reference'] or ''}</td>
                <td>{description}</td>
                <td>{account_label(conn, r['account_code'])}</td>
                <td>{money(debit)}</td>
                <td>{money(credit)}</td>
                <td>{money(balance)}</td>
                <td><a class="btn gray" href="/ui/accounting/journal/{r['journal_id']}">Open</a></td>
            </tr>
            """

    if partner_type and not partner_id and rows:
        body = ""
        total_debit = 0.0
        total_credit = 0.0
        for r in rows:
            debit = float(r["total_debit"] or 0)
            credit = float(r["total_credit"] or 0)
            row_balance = debit - credit
            total_debit += debit
            total_credit += credit
            row_partner = get_partner_display(conn, r["partner_type"], r["partner_id"])
            ledger_href = action_url("/ui/accounting/partner-ledger", {
                "partner_type": partner_type,
                "partner_id": r["partner_id"],
                "date_from": date_from,
                "date_to": date_to,
                "account_code": filter_account,
            })
            journals_href = action_url("/ui/accounting/journal", {
                "status": "posted",
                "partner_type": partner_type,
                "partner_id": r["partner_id"],
                "date_from": date_from,
                "date_to": date_to,
            })
            body += f"""
            <tr>
                <td>{row_partner}</td>
                <td>{money(debit)}</td>
                <td>{money(credit)}</td>
                <td>{money(row_balance)}</td>
                <td>
                    <a class="btn gray" href="{journals_href}">Journals</a>
                    <a class="btn blue" href="{ledger_href}">Ledger</a>
                </td>
            </tr>
            """
        closing_balance = total_debit - total_credit

    if not body:
        body = """
        <tr>
            <td colspan="10" style="text-align:center;">No ledger movements found.</td>
        </tr>
        """

    closing_balance = opening_balance + total_debit - total_credit

    customer_selected = "selected" if partner_type == "customer" else ""
    vendor_selected = "selected" if partner_type == "vendor" else ""
    employee_selected = "selected" if partner_type == "employee" else ""

    partner_options_html = "<option value=''>All Partners</option>"
    if partner_type:
        items = get_partners(conn, partner_type)
        for item in items:
            sel = "selected" if str(item["id"]) == str(partner_id) else ""
            partner_options_html += f"<option value='{item['id']}' {sel}>{item['text']}</option>"

    html = f"""
    <div class="card">
        <h2>Partner Ledger</h2>

        <form method="get">
            <div class="row">
                <div class="col">
                    <label>Type</label>
                    <select name="partner_type" id="ptype">
                        <option value="">Select</option>
                        <option value="customer" {customer_selected}>Customer</option>
                        <option value="vendor" {vendor_selected}>Vendor</option>
                        <option value="employee" {employee_selected}>Employee</option>
                    </select>
                </div>

                <div class="col">
                    <label>Partner</label>
                    <select name="partner_id" id="partner">
                        {partner_options_html}
                    </select>
                </div>

                <div class="col">
                    <label>From</label>
                    <input type="date" name="date_from" value="{date_from}">
                </div>

                <div class="col">
                    <label>To</label>
                    <input type="date" name="date_to" value="{date_to}">
                </div>
            </div>

            <button class="btn green" style="margin-top:10px;">Show</button>
            <a class="btn gray" style="margin-top:10px;" href="/ui/accounting/partner-ledger">Clear</a>
            <a class="btn gray" style="margin-top:10px;" href="/ui/accounting/export-center">Export</a>
        </form>

        <div class="card" style="margin-top:15px;">
            <p><b>Type:</b> {partner_type or ''}</p>
            <p><b>Partner:</b> {partner_text or 'All Partners' if partner_type and not partner_id else partner_text}</p>
            <p><b>Partner Account:</b> {partner_account_code or ''}</p>
            <p><b>Opening Balance:</b> {money(opening_balance)}</p>
            <p><b>Total Debit:</b> {money(total_debit)}</p>
            <p><b>Total Credit:</b> {money(total_credit)}</p>
            <p><b>Closing Balance:</b> {money(closing_balance)}</p>
        </div>

        <table style="margin-top:20px;">
            {'''
            <tr>
                <th>Partner</th>
                <th>Debit</th>
                <th>Credit</th>
                <th>Balance</th>
                <th>Open</th>
            </tr>
            ''' if partner_type and not partner_id else '''
            <tr>
                <th>Date</th>
                <th>Partner</th>
                <th>Entry</th>
                <th>Reference</th>
                <th>Description</th>
                <th>Account</th>
                <th>Debit</th>
                <th>Credit</th>
                <th>Balance</th>
                <th>Open</th>
            </tr>
            '''}
            {body}
        </table>
    </div>

    <script>
    async function reloadPartners(selectedValue = "") {{
        const ptype = document.getElementById("ptype").value || "";
        const partner = document.getElementById("partner");

        partner.innerHTML = "<option value=''>All Partners</option>";

        if (!ptype) {{
            const prev0 = partner.previousElementSibling;
            if (prev0 && prev0.tagName !== "SELECT") {{
                prev0.remove();
                partner.dataset.searchReady = "0";
            }}
            setupSearchableSelect("partner");
            return;
        }}

        const res = await fetch(`/ui/accounting/partner-ledger/partners?partner_type=${{encodeURIComponent(ptype)}}`);
        const data = await res.json();

        partner.innerHTML = "<option value=''>All Partners</option>";

        (data.items || []).forEach(item => {{
            const opt = document.createElement("option");
            opt.value = item.id;
            opt.text = item.text;
            if (String(item.id) === String(selectedValue)) {{
                opt.selected = true;
            }}
            partner.appendChild(opt);
        }});

        const prev = partner.previousElementSibling;
        if (prev && prev.tagName !== "SELECT") {{
            prev.remove();
            partner.dataset.searchReady = "0";
        }}

        setupSearchableSelect("partner");
    }}

    window.addEventListener("DOMContentLoaded", function() {{
        setupSearchableSelect("ptype");
        setupSearchableSelect("partner");

        const typeSelect = document.getElementById("ptype");
        typeSelect.addEventListener("change", function() {{
            reloadPartners("");
        }});

        reloadPartners("{partner_id}");
    }});
    </script>
    """

    conn.close()
    if int(embed or 0) == 1:
        return HTMLResponse(html)

    return HTMLResponse(render_page("Partner Ledger", html, "en", current_path=request.url.path))
