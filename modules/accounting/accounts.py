from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_conn
from layout import render_page

router = APIRouter()


# =========================================================
# HELPERS
# =========================================================
def safe(x):
    return "" if x is None else str(x).strip()


def to_int_flag(v):
    try:
        return 1 if int(v or 0) == 1 else 0
    except Exception:
        return 0


def yes_no(v):
    return "Yes" if int(v or 0) == 1 else "No"


def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


# =========================================================
# DB INIT
# =========================================================
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
            allow_posting INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1
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

    alter_statements = [
        ("code", "ALTER TABLE accounts ADD COLUMN code TEXT"),
        ("name", "ALTER TABLE accounts ADD COLUMN name TEXT"),
        ("type", "ALTER TABLE accounts ADD COLUMN type TEXT"),
        ("parent_id", "ALTER TABLE accounts ADD COLUMN parent_id INTEGER"),
        ("level1", "ALTER TABLE accounts ADD COLUMN level1 TEXT"),
        ("level2", "ALTER TABLE accounts ADD COLUMN level2 TEXT"),
        ("statement_type", "ALTER TABLE accounts ADD COLUMN statement_type TEXT"),
        ("is_group", "ALTER TABLE accounts ADD COLUMN is_group INTEGER DEFAULT 0"),
        ("allow_posting", "ALTER TABLE accounts ADD COLUMN allow_posting INTEGER DEFAULT 1"),
        ("is_active", "ALTER TABLE accounts ADD COLUMN is_active INTEGER DEFAULT 1"),
    ]

    for col, stmt in alter_statements:
        ensure_column(conn, "accounts", col, stmt)

    conn.commit()
    conn.close()


ensure_tables()


# =========================================================
# LOOKUPS
# =========================================================
ACCOUNT_TYPES = [
    "asset",
    "current asset",
    "fixed asset",
    "non-current asset",
    "liability",
    "current liability",
    "non-current liability",
    "equity",
    "income",
    "revenue",
    "other income",
    "cogs",
    "cost of goods sold",
    "cost of revenue",
    "expense",
    "administrative expenses",
    "selling expenses",
    "financial expenses",
    "other expenses",
    "g&a",
    "depreciation expense",
    "accumulated depreciation",
]

LEVEL1_OPTIONS = [
    "assets",
    "current assets",
    "non-current assets",
    "fixed assets",
    "liabilities",
    "current liabilities",
    "non-current liabilities",
    "equity",
    "revenue",
    "cost of goods sold",
    "cost of revenue",
    "administrative expenses",
    "selling expenses",
    "financial expenses",
    "other expenses",
]

LEVEL2_OPTIONS = [
    "cash",
    "bank",
    "petty cash",
    "accounts receivable",
    "inventory",
    "fixed assets",
    "accounts payable",
    "vat",
    "wht",
    "capital",
    "sales",
    "service revenue",
    "direct materials",
    "direct labor",
    "factory overheads",
    "rent",
    "utilities",
    "transportation",
    "maintenance",
    "office expenses",
    "depreciation",
    "interest",
    "miscellaneous",
]

STATEMENT_TYPES = [
    "balance_sheet",
    "profit_loss",
]


def option_html(options, selected_value=""):
    html = "<option value=''>Select</option>"
    for opt in options:
        selected = "selected" if safe(selected_value).lower() == safe(opt).lower() else ""
        html += f"<option value='{opt}' {selected}>{opt}</option>"
    return html


def get_account_row(conn, account_id: int):
    return conn.execute("""
        SELECT *
        FROM accounts
        WHERE id = ?
        LIMIT 1
    """, (account_id,)).fetchone()


def get_account_by_code(conn, code: str):
    return conn.execute("""
        SELECT *
        FROM accounts
        WHERE code = ?
        LIMIT 1
    """, (safe(code),)).fetchone()


def parent_account_options(selected_id=None, exclude_id=None):
    conn = get_conn()
    sql = """
        SELECT id, code, name
        FROM accounts
        WHERE COALESCE(is_active,1) = 1
    """
    params = []

    if exclude_id:
        sql += " AND id <> ?"
        params.append(exclude_id)

    sql += " ORDER BY code, name"
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    html = "<option value=''>No Parent</option>"
    for r in rows:
        selected = "selected" if str(selected_id or "") == str(r["id"]) else ""
        html += f"<option value='{r['id']}' {selected}>{safe(r['code'])} - {safe(r['name'])}</option>"
    return html


def has_children(conn, account_id: int) -> bool:
    row = conn.execute("""
        SELECT id
        FROM accounts
        WHERE parent_id = ?
        LIMIT 1
    """, (account_id,)).fetchone()
    return row is not None


def has_journal_lines(conn, account_code: str) -> bool:
    row = conn.execute("""
        SELECT id
        FROM journal_lines
        WHERE COALESCE(account_code,'') = ?
        LIMIT 1
    """, (safe(account_code),)).fetchone()
    return row is not None


def infer_statement_type(acc_type: str, level1: str):
    t = safe(acc_type).lower()
    l1 = safe(level1).lower()

    if t in [
        "asset", "current asset", "fixed asset", "non-current asset",
        "liability", "current liability", "non-current liability",
        "equity"
    ]:
        return "balance_sheet"

    if l1 in [
        "assets", "current assets", "non-current assets", "fixed assets",
        "liabilities", "current liabilities", "non-current liabilities",
        "equity"
    ]:
        return "balance_sheet"

    if t in [
        "income", "revenue", "other income", "cogs", "cost of goods sold",
        "cost of revenue", "expense", "administrative expenses",
        "selling expenses", "financial expenses", "other expenses", "g&a",
        "depreciation expense"
    ]:
        return "profit_loss"

    if l1 in [
        "revenue", "cost of goods sold", "cost of revenue",
        "administrative expenses", "selling expenses",
        "financial expenses", "other expenses"
    ]:
        return "profit_loss"

    return ""


def next_account_code():
    conn = get_conn()
    rows = conn.execute("""
        SELECT code
        FROM accounts
        WHERE COALESCE(code, '') <> ''
        ORDER BY id DESC
    """).fetchall()
    conn.close()

    numeric_values = []
    prefixed_values = []

    for row in rows:
        code = safe(row["code"])
        if not code:
            continue
        if code.isdigit():
            numeric_values.append(int(code))
            continue

        parts = code.split("-")
        if len(parts) >= 2 and parts[-1].isdigit():
            prefixed_values.append((parts[0], int(parts[-1])))

    if numeric_values:
        return str(max(numeric_values) + 1)

    if prefixed_values:
        prefix, last_num = prefixed_values[0]
        return f"{prefix}-{last_num + 1:04d}"

    return "100001"


# =========================================================
# FORM RENDER
# =========================================================
def render_account_form(
    request: Request,
    values=None,
    row_id=None,
    readonly=False,
    error_message="",
    success_message="",
):
    values = values or {}

    values = {
        "code": values.get("code", "") or next_account_code(),
        "name": values.get("name", ""),
        "type": values.get("type", ""),
        "parent_id": values.get("parent_id", ""),
        "level1": values.get("level1", ""),
        "level2": values.get("level2", ""),
        "statement_type": values.get("statement_type", ""),
        "is_group": int(values.get("is_group", 0) or 0),
        "allow_posting": int(values.get("allow_posting", 1) or 0),
        "is_active": int(values.get("is_active", 1) or 0),
    }

    form_action = "/ui/accounting/accounts/new" if not row_id else f"/ui/accounting/accounts/{row_id}/edit"
    form_title = "View Account" if readonly else ("Edit Account" if row_id else "New Account")
    read_attr = "readonly" if readonly else ""
    disabled_attr = "disabled" if readonly else ""
    code_read_attr = "readonly"

    alert_html = ""
    if error_message:
        alert_html += f"""
        <div class="card" style="border-left:4px solid #dc2626;">
            <div style="color:#991b1b;font-weight:700;">{error_message}</div>
        </div>
        """
    if success_message:
        alert_html += f"""
        <div class="card" style="border-left:4px solid #16a34a;">
            <div style="color:#166534;font-weight:700;">{success_message}</div>
        </div>
        """

    save_button = "" if readonly else '<button class="btn green" type="submit">Save</button>'

    content = f"""
    {alert_html}

    <div class="card">
        <h2>{form_title}</h2>

        <form method="post" action="{form_action}">
            <div class="form-grid">
                <div class="form-group">
                    <label>Code</label>
                    <input type="text" name="code" value="{safe(values['code'])}" {code_read_attr} required>
                </div>

                <div class="form-group">
                    <label>Name</label>
                    <input type="text" name="name" value="{safe(values['name'])}" {read_attr} required>
                </div>

                <div class="form-group">
                    <label>Type</label>
                    <select name="type" id="acc_type" {disabled_attr}>
                        {option_html(ACCOUNT_TYPES, values["type"])}
                    </select>
                    {"<input type='hidden' name='type' value='%s'>" % safe(values['type']) if readonly else ""}
                </div>

                <div class="form-group">
                    <label>Parent Account</label>
                    <select name="parent_id" id="parent_id" {disabled_attr}>
                        {parent_account_options(values["parent_id"], exclude_id=row_id)}
                    </select>
                    {"<input type='hidden' name='parent_id' value='%s'>" % safe(values['parent_id']) if readonly else ""}
                </div>

                <div class="form-group">
                    <label>Level 1</label>
                    <select name="level1" id="level1" {disabled_attr}>
                        {option_html(LEVEL1_OPTIONS, values["level1"])}
                    </select>
                    {"<input type='hidden' name='level1' value='%s'>" % safe(values['level1']) if readonly else ""}
                </div>

                <div class="form-group">
                    <label>Level 2</label>
                    <select name="level2" id="level2" {disabled_attr}>
                        {option_html(LEVEL2_OPTIONS, values["level2"])}
                    </select>
                    {"<input type='hidden' name='level2' value='%s'>" % safe(values['level2']) if readonly else ""}
                </div>

                <div class="form-group">
                    <label>Statement Type</label>
                    <select name="statement_type" id="statement_type" {disabled_attr}>
                        {option_html(STATEMENT_TYPES, values["statement_type"])}
                    </select>
                    {"<input type='hidden' name='statement_type' value='%s'>" % safe(values['statement_type']) if readonly else ""}
                </div>

                <div class="form-group">
                    <label><input type="checkbox" name="is_group" value="1" {"checked" if values["is_group"] else ""} {disabled_attr}> Group Account</label>
                    {"<input type='hidden' name='is_group' value='%s'>" % values['is_group'] if readonly else ""}
                </div>

                <div class="form-group">
                    <label><input type="checkbox" name="allow_posting" value="1" {"checked" if values["allow_posting"] else ""} {disabled_attr}> Allow Posting</label>
                    {"<input type='hidden' name='allow_posting' value='%s'>" % values['allow_posting'] if readonly else ""}
                </div>

                <div class="form-group">
                    <label><input type="checkbox" name="is_active" value="1" {"checked" if values["is_active"] else ""} {disabled_attr}> Active</label>
                    {"<input type='hidden' name='is_active' value='%s'>" % values['is_active'] if readonly else ""}
                </div>
            </div>

            <div class="form-actions" style="margin-top:18px;">
                {save_button}
                <a class="btn gray" href="/ui/accounting/accounts">Back</a>
            </div>
        </form>
    </div>

    <script>
    function setupSearchableSelect(selectId) {{
        const select = document.getElementById(selectId);
        if (!select || select.dataset.searchReady === "1") return;

        select.dataset.searchReady = "1";
        select.style.display = "none";

        const wrapper = document.createElement("div");
        wrapper.style.position = "relative";
        wrapper.style.width = "100%";

        const input = document.createElement("input");
        input.type = "text";
        input.placeholder = "Type first letters...";
        input.style.width = "100%";
        input.style.padding = "10px 12px";
        input.style.border = "1px solid #d1d5db";
        input.style.borderRadius = "10px";
        input.style.autocomplete = "off";
        input.style.boxSizing = "border-box";

        const dropdown = document.createElement("div");
        dropdown.style.position = "absolute";
        dropdown.style.top = "100%";
        dropdown.style.left = "0";
        dropdown.style.right = "0";
        dropdown.style.background = "#fff";
        dropdown.style.border = "1px solid #d1d5db";
        dropdown.style.borderRadius = "10px";
        dropdown.style.maxHeight = "220px";
        dropdown.style.overflowY = "auto";
        dropdown.style.zIndex = "9999";
        dropdown.style.display = "none";
        dropdown.style.marginTop = "4px";

        function syncFromSelect() {{
            const opt = select.options[select.selectedIndex];
            input.value = (opt && opt.value) ? opt.text : "";
        }}

        function renderOptions(filterText) {{
            const q = (filterText || "").toLowerCase().trim();
            dropdown.innerHTML = "";

            const opts = Array.from(select.options).filter(opt => {{
                if (!opt.value && selectId === "parent_id") return true;
                return !q || opt.text.toLowerCase().includes(q);
            }});

            if (!opts.length) {{
                dropdown.style.display = "none";
                return;
            }}

            opts.forEach(opt => {{
                const item = document.createElement("div");
                item.textContent = opt.text;
                item.style.padding = "10px 12px";
                item.style.cursor = "pointer";
                item.style.borderBottom = "1px solid #eee";

                item.onmouseenter = function() {{
                    item.style.background = "#f3f4f6";
                }};
                item.onmouseleave = function() {{
                    item.style.background = "#fff";
                }};
                item.onclick = function() {{
                    select.value = opt.value;
                    input.value = opt.text;
                    dropdown.style.display = "none";
                    select.dispatchEvent(new Event("change"));
                }};

                dropdown.appendChild(item);
            }});

            dropdown.style.display = "block";
        }}

        input.addEventListener("focus", function() {{
            renderOptions(input.value);
        }});

        input.addEventListener("input", function() {{
            renderOptions(input.value);
        }});

        document.addEventListener("click", function(e) {{
            if (!wrapper.contains(e.target)) {{
                dropdown.style.display = "none";
            }}
        }});

        select.parentNode.insertBefore(wrapper, select);
        wrapper.appendChild(input);
        wrapper.appendChild(dropdown);
        syncFromSelect();
    }}

    function inferStatementType() {{
        const type = (document.getElementById("acc_type")?.value || "").toLowerCase();
        const level1 = (document.getElementById("level1")?.value || "").toLowerCase();
        const st = document.getElementById("statement_type");

        if (!st || st.value) return;

        const bsTypes = ["asset","current asset","fixed asset","non-current asset","liability","current liability","non-current liability","equity"];
        const bsL1 = ["assets","current assets","non-current assets","fixed assets","liabilities","current liabilities","non-current liabilities","equity"];

        const plTypes = ["income","revenue","other income","cogs","cost of goods sold","cost of revenue","expense","administrative expenses","selling expenses","financial expenses","other expenses","g&a","depreciation expense"];
        const plL1 = ["revenue","cost of goods sold","cost of revenue","administrative expenses","selling expenses","financial expenses","other expenses"];

        if (bsTypes.includes(type) || bsL1.includes(level1)) {{
            st.value = "balance_sheet";
        }} else if (plTypes.includes(type) || plL1.includes(level1)) {{
            st.value = "profit_loss";
        }}
    }}

    function handleGroupPostingRule() {{
        const isGroup = document.querySelector("input[name='is_group']");
        const allowPosting = document.querySelector("input[name='allow_posting']");

        if (!isGroup || !allowPosting) return;

        if (isGroup.checked) {{
            allowPosting.checked = false;
            allowPosting.disabled = true;
        }} else {{
            allowPosting.disabled = false;
        }}
    }}

    document.addEventListener("DOMContentLoaded", function() {{
        setupSearchableSelect("parent_id");
        handleGroupPostingRule();

        const isGroup = document.querySelector("input[name='is_group']");
        if (isGroup) {{
            isGroup.addEventListener("change", handleGroupPostingRule);
        }}

        const accType = document.getElementById("acc_type");
        const level1 = document.getElementById("level1");
        if (accType) accType.addEventListener("change", inferStatementType);
        if (level1) level1.addEventListener("change", inferStatementType);
    }});
    </script>
    """

    return HTMLResponse(render_page(form_title, content, "en", current_path=request.url.path))


# =========================================================
# LIST PAGE
# =========================================================
@router.get("/ui/accounting/accounts", response_class=HTMLResponse)
def accounts_list(request: Request, q: str = "", acc_type: str = "", active: str = ""):
    conn = get_conn()

    query = """
        SELECT
            a.*,
            p.code AS parent_code,
            p.name AS parent_name
        FROM accounts a
        LEFT JOIN accounts p ON p.id = a.parent_id
        WHERE 1 = 1
    """
    params = []

    if safe(q):
        query += " AND (LOWER(COALESCE(a.code,'')) LIKE ? OR LOWER(COALESCE(a.name,'')) LIKE ?)"
        like_q = f"%{safe(q).lower()}%"
        params.extend([like_q, like_q])

    if safe(acc_type):
        query += " AND LOWER(COALESCE(a.type,'')) = ?"
        params.append(safe(acc_type).lower())

    if safe(active) in ["0", "1"]:
        query += " AND COALESCE(a.is_active,1) = ?"
        params.append(int(active))

    query += " ORDER BY a.code, a.name"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    body = ""
    for r in rows:
        parent_display = ""
        if r["parent_code"]:
            parent_display = f"{safe(r['parent_code'])} - {safe(r['parent_name'])}"

        body += f"""
        <tr>
            <td>{safe(r['code'])}</td>
            <td>{safe(r['name'])}</td>
            <td>{safe(r['type'])}</td>
            <td>{parent_display}</td>
            <td>{safe(r['level1'])}</td>
            <td>{safe(r['level2'])}</td>
            <td>{safe(r['statement_type'])}</td>
            <td>{yes_no(r['is_group'])}</td>
            <td>{yes_no(r['allow_posting'])}</td>
            <td>{yes_no(r['is_active'])}</td>
            <td>
                <div class="action-strip">
                    <a class="action-btn blue" href="/ui/accounting/accounts/{r['id']}/view">View</a>
                    <a class="action-btn green" href="/ui/accounting/accounts/{r['id']}/edit">Edit</a>
                    <form method="post" action="/ui/accounting/accounts/{r['id']}/delete" style="display:inline;">
                        <button class="action-btn red" type="submit" onclick="return confirm('Delete this account?')">Delete</button>
                    </form>
                </div>
            </td>
        </tr>
        """

    if not body:
        body = "<tr><td colspan='11' style='text-align:center;'>No accounts found.</td></tr>"

    content = f"""
    <div class="card">
        <div class="toolbar">
            <div>
                <div class="section-title">Accounts</div>
                <div class="muted">Manage chart of accounts structure and posting rules.</div>
            </div>
            <div>
                <a class="btn green" href="/ui/accounting/accounts/new">+ New Account</a>
            </div>
        </div>

        <form method="get" style="margin-top:18px;">
            <div class="form-grid">
                <div class="form-group">
                    <label>Search</label>
                    <input type="text" name="q" value="{safe(q)}">
                </div>

                <div class="form-group">
                    <label>Type</label>
                    <select name="acc_type">
                        <option value=''>All</option>
                        {option_html(ACCOUNT_TYPES, acc_type).replace("<option value=''>Select</option>", "")}
                    </select>
                </div>

                <div class="form-group">
                    <label>Active</label>
                    <select name="active">
                        <option value='' {"selected" if active == "" else ""}>All</option>
                        <option value='1' {"selected" if active == "1" else ""}>Active</option>
                        <option value='0' {"selected" if active == "0" else ""}>Inactive</option>
                    </select>
                </div>
            </div>

            <div class="form-actions" style="margin-top:16px;">
                <button class="btn green" type="submit">Filter</button>
                <a class="btn gray" href="/ui/accounting/accounts">Clear</a>
            </div>
        </form>
    </div>

    <div class="card">
        <table>
            <tr>
                <th>Code</th>
                <th>Name</th>
                <th>Type</th>
                <th>Parent</th>
                <th>Level 1</th>
                <th>Level 2</th>
                <th>Statement</th>
                <th>Group</th>
                <th>Posting</th>
                <th>Active</th>
                <th>Action</th>
            </tr>
            {body}
        </table>
    </div>
    """

    return HTMLResponse(render_page("Accounts", content, "en", current_path=request.url.path))


# =========================================================
# NEW
# =========================================================
@router.get("/ui/accounting/accounts/new", response_class=HTMLResponse)
def account_new_page(request: Request):
    return render_account_form(request)


@router.post("/ui/accounting/accounts/new")
def account_create(
    request: Request,
    code: str = Form(""),
    name: str = Form(""),
    type: str = Form(""),
    parent_id: str = Form(""),
    level1: str = Form(""),
    level2: str = Form(""),
    statement_type: str = Form(""),
    is_group: int = Form(0),
    allow_posting: int = Form(0),
    is_active: int = Form(0),
):
    values = {
        "code": safe(code),
        "name": safe(name),
        "type": safe(type),
        "parent_id": safe(parent_id),
        "level1": safe(level1),
        "level2": safe(level2),
        "statement_type": safe(statement_type) or infer_statement_type(type, level1),
        "is_group": to_int_flag(is_group),
        "allow_posting": to_int_flag(allow_posting),
        "is_active": to_int_flag(is_active),
    }

    if not values["code"]:
        values["code"] = next_account_code()
    if not values["name"]:
        return render_account_form(request, values=values, error_message="Name is required.")
    if not values["type"]:
        return render_account_form(request, values=values, error_message="Type is required.")

    if values["is_group"] == 1 and values["allow_posting"] == 1:
        values["allow_posting"] = 0

    conn = get_conn()
    try:
        existing = get_account_by_code(conn, values["code"])
        if existing:
            conn.close()
            return render_account_form(request, values=values, error_message="Account code already exists.")

        final_parent_id = None
        if safe(values["parent_id"]):
            try:
                final_parent_id = int(values["parent_id"])
            except Exception:
                conn.close()
                return render_account_form(request, values=values, error_message="Invalid parent account.")

        conn.execute("""
            INSERT INTO accounts (
                code, name, type, parent_id, level1, level2, statement_type,
                is_group, allow_posting, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            values["code"],
            values["name"],
            values["type"],
            final_parent_id,
            values["level1"],
            values["level2"],
            values["statement_type"],
            values["is_group"],
            values["allow_posting"],
            values["is_active"],
        ))

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return render_account_form(request, values=values, error_message=f"Create failed: {str(e)}")

    conn.close()
    return RedirectResponse("/ui/accounting/accounts", status_code=303)


# =========================================================
# VIEW
# =========================================================
@router.get("/ui/accounting/accounts/{row_id}/view", response_class=HTMLResponse)
def account_view(request: Request, row_id: int):
    conn = get_conn()
    row = get_account_row(conn, row_id)
    conn.close()

    if not row:
        return HTMLResponse("Account not found", status_code=404)

    return render_account_form(request, values=dict(row), row_id=row_id, readonly=True)


# =========================================================
# EDIT
# =========================================================
@router.get("/ui/accounting/accounts/{row_id}/edit", response_class=HTMLResponse)
def account_edit_page(request: Request, row_id: int):
    conn = get_conn()
    row = get_account_row(conn, row_id)
    conn.close()

    if not row:
        return HTMLResponse("Account not found", status_code=404)

    return render_account_form(request, values=dict(row), row_id=row_id)


@router.post("/ui/accounting/accounts/{row_id}/edit")
def account_edit(
    request: Request,
    row_id: int,
    code: str = Form(""),
    name: str = Form(""),
    type: str = Form(""),
    parent_id: str = Form(""),
    level1: str = Form(""),
    level2: str = Form(""),
    statement_type: str = Form(""),
    is_group: int = Form(0),
    allow_posting: int = Form(0),
    is_active: int = Form(0),
):
    values = {
        "code": safe(code),
        "name": safe(name),
        "type": safe(type),
        "parent_id": safe(parent_id),
        "level1": safe(level1),
        "level2": safe(level2),
        "statement_type": safe(statement_type) or infer_statement_type(type, level1),
        "is_group": to_int_flag(is_group),
        "allow_posting": to_int_flag(allow_posting),
        "is_active": to_int_flag(is_active),
    }

    conn = get_conn()
    existing = get_account_row(conn, row_id)
    conn.close()

    if not existing:
        return HTMLResponse("Account not found", status_code=404)

    if not values["code"]:
        values["code"] = safe(existing["code"]) or next_account_code()
    if not values["name"]:
        return render_account_form(request, values=values, row_id=row_id, error_message="Name is required.")
    if not values["type"]:
        return render_account_form(request, values=values, row_id=row_id, error_message="Type is required.")

    if values["is_group"] == 1 and values["allow_posting"] == 1:
        values["allow_posting"] = 0

    conn = get_conn()
    try:
        code_owner = get_account_by_code(conn, values["code"])
        if code_owner and int(code_owner["id"]) != int(row_id):
            conn.close()
            return render_account_form(request, values=values, row_id=row_id, error_message="Account code already exists.")

        final_parent_id = None
        if safe(values["parent_id"]):
            try:
                final_parent_id = int(values["parent_id"])
            except Exception:
                conn.close()
                return render_account_form(request, values=values, row_id=row_id, error_message="Invalid parent account.")

            if final_parent_id == row_id:
                conn.close()
                return render_account_form(request, values=values, row_id=row_id, error_message="Account cannot be parent of itself.")

        old_code = safe(existing["code"])
        if old_code != values["code"] and has_journal_lines(conn, old_code):
            conn.close()
            return render_account_form(
                request,
                values=values,
                row_id=row_id,
                error_message="Cannot change account code because journal lines already use this account."
            )

        conn.execute("""
            UPDATE accounts
            SET
                code = ?,
                name = ?,
                type = ?,
                parent_id = ?,
                level1 = ?,
                level2 = ?,
                statement_type = ?,
                is_group = ?,
                allow_posting = ?,
                is_active = ?
            WHERE id = ?
        """, (
            values["code"],
            values["name"],
            values["type"],
            final_parent_id,
            values["level1"],
            values["level2"],
            values["statement_type"],
            values["is_group"],
            values["allow_posting"],
            values["is_active"],
            row_id,
        ))

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return render_account_form(request, values=values, row_id=row_id, error_message=f"Update failed: {str(e)}")

    conn.close()
    return RedirectResponse("/ui/accounting/accounts", status_code=303)


# =========================================================
# DELETE
# =========================================================
@router.post("/ui/accounting/accounts/{row_id}/delete")
def account_delete(row_id: int):
    conn = get_conn()
    try:
        row = get_account_row(conn, row_id)
        if not row:
            conn.close()
            return HTMLResponse("Account not found", status_code=404)

        if has_children(conn, row_id):
            conn.close()
            return HTMLResponse("Cannot delete account because it has child accounts.", status_code=400)

        if has_journal_lines(conn, row["code"]):
            conn.close()
            return HTMLResponse("Cannot delete account because journal lines already use it.", status_code=400)

        conn.execute("DELETE FROM accounts WHERE id = ?", (row_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return HTMLResponse(f"Delete failed: {str(e)}", status_code=400)

    conn.close()
    return RedirectResponse("/ui/accounting/accounts", status_code=303)
