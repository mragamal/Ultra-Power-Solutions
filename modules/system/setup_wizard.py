from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page
from modules.accounting.chart_generator import (
    generate_chart,
    apply_chart_to_db,
    get_default_account_mapping,
)

router = APIRouter()


def ensure_setup_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_setup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT,
            currency TEXT DEFAULT 'EGP',
            fiscal_year_start TEXT,
            activity_type TEXT,
            use_inventory INTEGER DEFAULT 0,
            use_cost_centers INTEGER DEFAULT 0,
            use_projects INTEGER DEFAULT 0,
            use_branches INTEGER DEFAULT 0,
            use_vat INTEGER DEFAULT 1,
            use_wht INTEGER DEFAULT 0,
            use_petty_cash INTEGER DEFAULT 1,
            chart_generated INTEGER DEFAULT 0,
            setup_completed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()


ensure_setup_tables()


def safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def safe_str(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def get_latest_setup():
    conn = get_conn()
    row = conn.execute("""
        SELECT *
        FROM system_setup
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    conn.close()
    return row


def set_setting_value(conn, key, value):
    existing = conn.execute("""
        SELECT key
        FROM settings
        WHERE key = ?
    """, (key,)).fetchone()

    if existing:
        conn.execute("""
            UPDATE settings
            SET value = ?
            WHERE key = ?
        """, (safe_str(value), key))
    else:
        conn.execute("""
            INSERT INTO settings (key, value)
            VALUES (?, ?)
        """, (key, safe_str(value)))


def bool_text(v):
    return "Yes" if safe_int(v, 0) == 1 else "No"


def checked(v):
    return "checked" if safe_int(v, 0) == 1 else ""


def selected(v, x):
    return "selected" if safe_str(v) == safe_str(x) else ""


def company_info_form(row=None):
    row = row or {}
    company_name = row["company_name"] if row and "company_name" in row.keys() else ""
    currency = row["currency"] if row and "currency" in row.keys() else "EGP"
    fiscal_year_start = row["fiscal_year_start"] if row and "fiscal_year_start" in row.keys() else ""
    activity_type = row["activity_type"] if row and "activity_type" in row.keys() else "service"

    return f"""
    <div class="card">
        <h2>Step 1 - Company Information</h2>

        <form method="post" action="/ui/system/setup/company">
            <div class="row">
                <div class="col">
                    <label>Company Name</label>
                    <input type="text" name="company_name" value="{company_name}" required>
                </div>
                <div class="col">
                    <label>Currency</label>
                    <select name="currency">
                        <option value="EGP" {selected(currency, "EGP")}>EGP</option>
                        <option value="USD" {selected(currency, "USD")}>USD</option>
                        <option value="SAR" {selected(currency, "SAR")}>SAR</option>
                        <option value="EUR" {selected(currency, "EUR")}>EUR</option>
                    </select>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Fiscal Year Start</label>
                    <input type="date" name="fiscal_year_start" value="{fiscal_year_start}" required>
                </div>
                <div class="col">
                    <label>Activity Type</label>
                    <select name="activity_type" required>
                        <option value="service" {selected(activity_type, "service")}>Service</option>
                        <option value="trading" {selected(activity_type, "trading")}>Trading</option>
                        <option value="manufacturing" {selected(activity_type, "manufacturing")}>Manufacturing</option>
                    </select>
                </div>
            </div>

            <div style="margin-top:20px;">
                <button class="btn green" type="submit">Save & Continue</button>
            </div>
        </form>
    </div>
    """


def features_form(row=None):
    row = row or {}

    return f"""
    <div class="card">
        <h2>Step 2 - Operational Features</h2>

        <form method="post" action="/ui/system/setup/features">
            <div class="row">
                <div class="col">
                    <label><input type="checkbox" name="use_inventory" value="1" {checked(row["use_inventory"] if row and "use_inventory" in row.keys() else 0)}> Use Inventory</label>
                </div>
                <div class="col">
                    <label><input type="checkbox" name="use_cost_centers" value="1" {checked(row["use_cost_centers"] if row and "use_cost_centers" in row.keys() else 0)}> Use Cost Centers</label>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label><input type="checkbox" name="use_projects" value="1" {checked(row["use_projects"] if row and "use_projects" in row.keys() else 0)}> Use Projects</label>
                </div>
                <div class="col">
                    <label><input type="checkbox" name="use_branches" value="1" {checked(row["use_branches"] if row and "use_branches" in row.keys() else 0)}> Use Branches</label>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label><input type="checkbox" name="use_vat" value="1" {checked(row["use_vat"] if row and "use_vat" in row.keys() else 1)}> Use VAT</label>
                </div>
                <div class="col">
                    <label><input type="checkbox" name="use_wht" value="1" {checked(row["use_wht"] if row and "use_wht" in row.keys() else 0)}> Use WHT</label>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label><input type="checkbox" name="use_petty_cash" value="1" {checked(row["use_petty_cash"] if row and "use_petty_cash" in row.keys() else 1)}> Use Petty Cash</label>
                </div>
                <div class="col"></div>
            </div>

            <div style="margin-top:20px;">
                <button class="btn green" type="submit">Save & Continue</button>
                <a class="btn gray" href="/ui/system/setup">Back</a>
            </div>
        </form>
    </div>
    """


def setup_summary_card(row):
    if not row:
        return """
        <div class="card">
            <h2>Setup Summary</h2>
            <p>No setup data found yet.</p>
        </div>
        """

    return f"""
    <div class="card">
        <h2>Setup Summary</h2>
        <p><b>Company:</b> {row['company_name'] or ''}</p>
        <p><b>Currency:</b> {row['currency'] or ''}</p>
        <p><b>Fiscal Year Start:</b> {row['fiscal_year_start'] or ''}</p>
        <p><b>Activity Type:</b> {row['activity_type'] or ''}</p>
        <hr>
        <p><b>Inventory:</b> {bool_text(row['use_inventory'])}</p>
        <p><b>Cost Centers:</b> {bool_text(row['use_cost_centers'])}</p>
        <p><b>Projects:</b> {bool_text(row['use_projects'])}</p>
        <p><b>Branches:</b> {bool_text(row['use_branches'])}</p>
        <p><b>VAT:</b> {bool_text(row['use_vat'])}</p>
        <p><b>WHT:</b> {bool_text(row['use_wht'])}</p>
        <p><b>Petty Cash:</b> {bool_text(row['use_petty_cash'])}</p>
    </div>
    """


def preview_table(rows):
    body = ""
    for r in rows[:200]:
        body += f"""
        <tr>
            <td>{r['code']}</td>
            <td>{r['name']}</td>
            <td>{r['type']}</td>
            <td>{r['parent_code']}</td>
            <td>{r['is_group']}</td>
            <td>{r['is_active']}</td>
        </tr>
        """

    return f"""
    <div class="card">
        <h2>Step 3 - Generated Chart Preview</h2>
        <table>
            <thead>
                <tr>
                    <th>Code</th>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Parent Code</th>
                    <th>Group</th>
                    <th>Active</th>
                </tr>
            </thead>
            <tbody>
                {body}
            </tbody>
        </table>
    </div>
    """


def mapping_preview_card(mapping: dict):
    return f"""
    <div class="card">
        <h2>Suggested Configuration Mapping</h2>
        <p><b>Cash Account:</b> {mapping.get('default_cash_account', '')}</p>
        <p><b>Bank Account:</b> {mapping.get('default_bank_account', '')}</p>
        <p><b>Petty Cash Account:</b> {mapping.get('default_petty_cash_account', '')}</p>
        <p><b>Customer Control Account:</b> {mapping.get('default_customer_account', '')}</p>
        <p><b>Vendor Control Account:</b> {mapping.get('default_vendor_account', '')}</p>
        <p><b>Sales Account:</b> {mapping.get('default_sales_account', '')}</p>
        <p><b>Sales VAT Account:</b> {mapping.get('default_sales_vat_account', '')}</p>
        <p><b>Purchase VAT Account:</b> {mapping.get('default_purchase_vat_account', '')}</p>
        <p><b>Sales WHT Account:</b> {mapping.get('default_sales_wht_account', '')}</p>
        <p><b>Purchase WHT Account:</b> {mapping.get('default_purchase_wht_account', '')}</p>
    </div>
    """


@router.get("/ui/system/setup", response_class=HTMLResponse)
def setup_home(request: Request):
    row = get_latest_setup()

    content = f"""
    {company_info_form(row)}
    {features_form(row)}
    {setup_summary_card(row)}
    """

    return HTMLResponse(
        render_page("System Setup", content, "en", current_path=str(request.url.path))
    )


@router.post("/ui/system/setup/company")
def save_company_info(
    company_name: str = Form(...),
    currency: str = Form(...),
    fiscal_year_start: str = Form(...),
    activity_type: str = Form(...),
):
    conn = get_conn()
    row = conn.execute("""
        SELECT *
        FROM system_setup
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    if row:
        conn.execute("""
            UPDATE system_setup
            SET company_name = ?, currency = ?, fiscal_year_start = ?, activity_type = ?
            WHERE id = ?
        """, (
            safe_str(company_name),
            safe_str(currency),
            safe_str(fiscal_year_start),
            safe_str(activity_type).lower(),
            row["id"],
        ))
    else:
        conn.execute("""
            INSERT INTO system_setup (
                company_name, currency, fiscal_year_start, activity_type
            )
            VALUES (?, ?, ?, ?)
        """, (
            safe_str(company_name),
            safe_str(currency),
            safe_str(fiscal_year_start),
            safe_str(activity_type).lower(),
        ))

    conn.commit()
    conn.close()

    return RedirectResponse("/ui/system/setup", status_code=302)


@router.post("/ui/system/setup/features")
def save_features(
    use_inventory: str = Form("0"),
    use_cost_centers: str = Form("0"),
    use_projects: str = Form("0"),
    use_branches: str = Form("0"),
    use_vat: str = Form("0"),
    use_wht: str = Form("0"),
    use_petty_cash: str = Form("0"),
):
    conn = get_conn()
    row = conn.execute("""
        SELECT *
        FROM system_setup
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    if row:
        conn.execute("""
            UPDATE system_setup
            SET use_inventory = ?, use_cost_centers = ?, use_projects = ?,
                use_branches = ?, use_vat = ?, use_wht = ?, use_petty_cash = ?
            WHERE id = ?
        """, (
            1 if use_inventory == "1" else 0,
            1 if use_cost_centers == "1" else 0,
            1 if use_projects == "1" else 0,
            1 if use_branches == "1" else 0,
            1 if use_vat == "1" else 0,
            1 if use_wht == "1" else 0,
            1 if use_petty_cash == "1" else 0,
            row["id"],
        ))
    else:
        conn.execute("""
            INSERT INTO system_setup (
                use_inventory, use_cost_centers, use_projects,
                use_branches, use_vat, use_wht, use_petty_cash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            1 if use_inventory == "1" else 0,
            1 if use_cost_centers == "1" else 0,
            1 if use_projects == "1" else 0,
            1 if use_branches == "1" else 0,
            1 if use_vat == "1" else 0,
            1 if use_wht == "1" else 0,
            1 if use_petty_cash == "1" else 0,
        ))

    conn.commit()
    conn.close()

    return RedirectResponse("/ui/system/setup/preview", status_code=302)


@router.get("/ui/system/setup/preview", response_class=HTMLResponse)
def setup_preview(request: Request):
    row = get_latest_setup()

    if not row:
        return RedirectResponse("/ui/system/setup", status_code=302)

    generated = generate_chart(
        activity_type=row["activity_type"] or "service",
        use_inventory=safe_int(row["use_inventory"], 0) == 1,
        use_cost_centers=safe_int(row["use_cost_centers"], 0) == 1,
        use_projects=safe_int(row["use_projects"], 0) == 1,
        use_branches=safe_int(row["use_branches"], 0) == 1,
        use_vat=safe_int(row["use_vat"], 0) == 1,
        use_wht=safe_int(row["use_wht"], 0) == 1,
        use_petty_cash=safe_int(row["use_petty_cash"], 0) == 1,
    )

    if not generated["ok"]:
        errors_html = "".join(f"<li>{e}</li>" for e in generated["errors"])
        content = f"""
        <div class="card">
            <h2>Chart Generation Errors</h2>
            <ul>{errors_html}</ul>
            <a class="btn gray" href="/ui/system/setup">Back</a>
        </div>
        """
        return HTMLResponse(
            render_page("Setup Preview", content, "en", current_path=str(request.url.path))
        )

    mapping = get_default_account_mapping(
        activity_type=row["activity_type"] or "service",
        use_vat=safe_int(row["use_vat"], 0) == 1,
        use_wht=safe_int(row["use_wht"], 0) == 1,
        use_petty_cash=safe_int(row["use_petty_cash"], 0) == 1,
    )

    content = f"""
    {setup_summary_card(row)}
    {mapping_preview_card(mapping)}
    {preview_table(generated['rows'])}

    <div class="card">
        <div style="display:flex;gap:10px;flex-wrap:wrap;">
            <form method="post" action="/ui/system/setup/apply" style="display:inline;">
                <button class="btn green" type="submit">Generate Chart & Save</button>
            </form>
            <a class="btn gray" href="/ui/system/setup">Back</a>
        </div>
    </div>
    """

    return HTMLResponse(
        render_page("Setup Preview", content, "en", current_path=str(request.url.path))
    )


@router.post("/ui/system/setup/apply")
def setup_apply():
    row = get_latest_setup()

    if not row:
        return RedirectResponse("/ui/system/setup", status_code=302)

    result = apply_chart_to_db(
        activity_type=row["activity_type"] or "service",
        use_inventory=safe_int(row["use_inventory"], 0) == 1,
        use_cost_centers=safe_int(row["use_cost_centers"], 0) == 1,
        use_projects=safe_int(row["use_projects"], 0) == 1,
        use_branches=safe_int(row["use_branches"], 0) == 1,
        use_vat=safe_int(row["use_vat"], 0) == 1,
        use_wht=safe_int(row["use_wht"], 0) == 1,
        use_petty_cash=safe_int(row["use_petty_cash"], 0) == 1,
    )

    if not result["ok"]:
        errors_html = "<br>".join(result["errors"])
        return HTMLResponse(errors_html, status_code=400)

    mapping = get_default_account_mapping(
        activity_type=row["activity_type"] or "service",
        use_vat=safe_int(row["use_vat"], 0) == 1,
        use_wht=safe_int(row["use_wht"], 0) == 1,
        use_petty_cash=safe_int(row["use_petty_cash"], 0) == 1,
    )

    conn = get_conn()

    for key, value in mapping.items():
        set_setting_value(conn, key, value)

    conn.execute("""
        UPDATE system_setup
        SET chart_generated = 1,
            setup_completed = 1
        WHERE id = ?
    """, (row["id"],))

    conn.commit()
    conn.close()

    return RedirectResponse("/ui/system/setup/done", status_code=302)


@router.get("/ui/system/setup/done", response_class=HTMLResponse)
def setup_done(request: Request):
    row = get_latest_setup()

    content = f"""
    <div class="card">
        <h2>Setup Completed</h2>
        <p><b>Company:</b> {row['company_name'] if row else ''}</p>
        <p>The chart of accounts has been generated successfully.</p>
        <p>Default configuration mapping has also been saved to the settings table.</p>

        <div style="margin-top:20px;display:flex;gap:10px;flex-wrap:wrap;">
            <a class="btn green" href="/ui/accounting/accounts">Open Chart of Accounts</a>
            <a class="btn gray" href="/ui/accounting/config">Open Configuration</a>
            <a class="btn orange" href="/ui/system/setup">Back to Setup</a>
        </div>
    </div>
    """

    return HTMLResponse(
        render_page("Setup Completed", content, "en", current_path=str(request.url.path))
    )