from db import get_conn
from modules.accounting.chart_templates import build_template


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
    """, (table_name,)).fetchone()
    return row is not None


def column_exists(conn, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(r["name"] == column_name for r in rows)


def ensure_accounts_table():
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
        if not column_exists(conn, "accounts", col):
            conn.execute(stmt)

    conn.commit()
    conn.close()


def get_account_id_by_code(conn, code: str):
    row = conn.execute("""
        SELECT id
        FROM accounts
        WHERE code = ?
        LIMIT 1
    """, (code,)).fetchone()
    return row["id"] if row else None


def normalize_row(row: dict):
    return {
        "code": str(row.get("code", "") or "").strip(),
        "name": str(row.get("name", "") or "").strip(),
        "type": str(row.get("type", "") or "").strip(),
        "parent_code": str(row.get("parent_code", "") or "").strip(),
        "level1": str(row.get("level1", "") or "").strip(),
        "level2": str(row.get("level2", "") or "").strip(),
        "statement_type": str(row.get("statement_type", "") or "").strip(),
        "is_group": int(row.get("is_group", 0) or 0),
        "allow_posting": int(row.get("allow_posting", 1) or 0),
        "is_active": int(row.get("is_active", 1) or 0),
    }


def insert_or_update_account(conn, row: dict, overwrite_names_and_types: bool = True):
    row = normalize_row(row)

    existing = conn.execute("""
        SELECT id
        FROM accounts
        WHERE code = ?
        LIMIT 1
    """, (row["code"],)).fetchone()

    if existing:
        if overwrite_names_and_types:
            conn.execute("""
                UPDATE accounts
                SET
                    name = ?,
                    type = ?,
                    level1 = ?,
                    level2 = ?,
                    statement_type = ?,
                    is_group = ?,
                    allow_posting = ?,
                    is_active = ?
                WHERE id = ?
            """, (
                row["name"],
                row["type"],
                row["level1"],
                row["level2"],
                row["statement_type"],
                row["is_group"],
                row["allow_posting"],
                row["is_active"],
                existing["id"],
            ))
        return existing["id"], "updated"

    cur = conn.execute("""
        INSERT INTO accounts (
            code,
            name,
            type,
            parent_id,
            level1,
            level2,
            statement_type,
            is_group,
            allow_posting,
            is_active
        )
        VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
    """, (
        row["code"],
        row["name"],
        row["type"],
        row["level1"],
        row["level2"],
        row["statement_type"],
        row["is_group"],
        row["allow_posting"],
        row["is_active"],
    ))
    return cur.lastrowid, "inserted"


def link_parent_ids(conn, rows: list[dict]):
    for raw in rows:
        row = normalize_row(raw)
        parent_code = row["parent_code"]

        if not parent_code:
            conn.execute("""
                UPDATE accounts
                SET parent_id = NULL
                WHERE code = ?
            """, (row["code"],))
            continue

        parent_id = get_account_id_by_code(conn, parent_code)
        if parent_id:
            conn.execute("""
                UPDATE accounts
                SET parent_id = ?
                WHERE code = ?
            """, (parent_id, row["code"]))


def validate_generated_template(rows: list[dict]):
    errors = []
    codes = set()

    for raw in rows:
        row = normalize_row(raw)

        if not row["code"]:
            errors.append("Account code is required.")
        if not row["name"]:
            errors.append(f"Account name is required for code {row['code'] or '[blank]'}")
        if not row["type"]:
            errors.append(f"Account type is required for code {row['code'] or '[blank]'}")

        if row["code"] in codes:
            errors.append(f"Duplicate account code in generated template: {row['code']}")
        codes.add(row["code"])

        if row["is_group"] not in [0, 1]:
            errors.append(f"is_group must be 0 or 1 for code {row['code']}")
        if row["allow_posting"] not in [0, 1]:
            errors.append(f"allow_posting must be 0 or 1 for code {row['code']}")
        if row["is_active"] not in [0, 1]:
            errors.append(f"is_active must be 0 or 1 for code {row['code']}")

        if row["is_group"] == 1 and row["allow_posting"] == 1:
            errors.append(f"Group account cannot allow posting: {row['code']}")

    all_codes = {normalize_row(r)["code"] for r in rows if normalize_row(r)["code"]}

    for raw in rows:
        row = normalize_row(raw)
        if row["parent_code"] and row["parent_code"] not in all_codes:
            errors.append(
                f"Parent code '{row['parent_code']}' for account '{row['code']}' not found in generated template."
            )

    return errors


def enrich_template_rows(rows: list[dict]):
    """
    Ensures every row has the fields required by the current accounts engine.
    If chart_templates already provides them, we keep them.
    Otherwise, we infer safe defaults.
    """
    out = []

    for raw in rows:
        row = normalize_row(raw)

        # Safe defaults
        if row["is_group"] == 1:
            row["allow_posting"] = 0
        elif row["allow_posting"] not in [0, 1]:
            row["allow_posting"] = 1

        if not row["statement_type"]:
            t = row["type"].strip().lower()
            if t in [
                "asset", "assets",
                "liability", "liabilities",
                "equity", "owner's equity", "owners equity",
                "current asset", "fixed asset", "non-current asset",
                "current liability", "non-current liability"
            ]:
                row["statement_type"] = "balance_sheet"
            elif t in [
                "income", "revenue", "other income",
                "cogs", "cost of goods sold", "cost of revenue",
                "expense", "administrative expenses", "selling expenses",
                "financial expenses", "other expenses",
                "g&a", "depreciation expense", "tcow", "other dr balances"
            ]:
                row["statement_type"] = "profit_loss"

        out.append(row)

    return out


def generate_chart(
    activity_type: str,
    use_inventory: bool = False,
    use_cost_centers: bool = False,
    use_projects: bool = False,
    use_branches: bool = False,
    use_vat: bool = False,
    use_wht: bool = False,
    use_petty_cash: bool = False,
    include_fixed_assets: bool = True,
    include_receivables_payables: bool = True,
):
    """
    Generates chart rows only (does not save to DB).
    """
    rows = build_template(
        activity_type=activity_type,
        use_inventory=use_inventory,
        use_cost_centers=use_cost_centers,
        use_projects=use_projects,
        use_branches=use_branches,
        use_vat=use_vat,
        use_wht=use_wht,
        use_petty_cash=use_petty_cash,
        include_fixed_assets=include_fixed_assets,
        include_receivables_payables=include_receivables_payables,
    )

    rows = enrich_template_rows(rows)

    errors = validate_generated_template(rows)
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "rows": rows,
    }


def apply_chart_to_db(
    activity_type: str,
    use_inventory: bool = False,
    use_cost_centers: bool = False,
    use_projects: bool = False,
    use_branches: bool = False,
    use_vat: bool = False,
    use_wht: bool = False,
    use_petty_cash: bool = False,
    include_fixed_assets: bool = True,
    include_receivables_payables: bool = True,
    overwrite_names_and_types: bool = True,
):
    """
    Generates and saves chart accounts into DB.
    - Inserts missing accounts
    - Updates existing accounts if overwrite_names_and_types=True
    - Resolves parent_id from parent_code after insert/update
    """
    ensure_accounts_table()

    generated = generate_chart(
        activity_type=activity_type,
        use_inventory=use_inventory,
        use_cost_centers=use_cost_centers,
        use_projects=use_projects,
        use_branches=use_branches,
        use_vat=use_vat,
        use_wht=use_wht,
        use_petty_cash=use_petty_cash,
        include_fixed_assets=include_fixed_assets,
        include_receivables_payables=include_receivables_payables,
    )

    if not generated["ok"]:
        return generated

    rows = generated["rows"]
    conn = get_conn()

    inserted_count = 0
    updated_count = 0

    for row in rows:
        _, action = insert_or_update_account(
            conn=conn,
            row=row,
            overwrite_names_and_types=overwrite_names_and_types,
        )
        if action == "inserted":
            inserted_count += 1
        elif action == "updated":
            updated_count += 1

    conn.commit()

    link_parent_ids(conn, rows)

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "errors": [],
        "rows": rows,
        "inserted_count": inserted_count,
        "updated_count": updated_count,
        "total_count": len(rows),
    }


def get_default_account_mapping(activity_type: str, use_vat: bool, use_wht: bool, use_petty_cash: bool):
    """
    Suggests default account codes for configuration after chart generation.
    This does not save anything; it only returns suggested mapping.
    """
    activity_type = str(activity_type or "").strip().lower()

    mapping = {
        "default_cash_account": "111100" if use_petty_cash else "111000",
        "default_bank_account": "111150" if use_petty_cash else "111000",
        "default_petty_cash_account": "111200" if use_petty_cash else "",
        "customer_control_account": "112100",
        "vendor_control_account": "211100",
        "sales_revenue_account_code": "411000",
        "output_vat_account": "212100" if use_vat else "",
        "input_vat_account": "114100" if use_vat else "",
        "wht_receivable_account": "114200" if use_wht else "",
        "wht_payable_account": "212200" if use_wht else "",
        "customer_invoice_prefix": "INV",
        "vendor_bill_prefix": "VBILL",
        "customer_payment_prefix": "CP",
        "vendor_payment_prefix": "VP",
        "journal_prefix": "JV",
    }

    if activity_type == "service":
        mapping["sales_revenue_account_code"] = "411000"
    elif activity_type == "trading":
        mapping["sales_revenue_account_code"] = "411000"
    elif activity_type == "manufacturing":
        mapping["sales_revenue_account_code"] = "411000"

    return mapping