from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from db import get_conn


# =========================================================
# HELPERS
# =========================================================
def D(x):
    try:
        return Decimal(str(x if x is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def q2(x):
    return D(x).quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)


def safe(x):
    return "" if x is None else str(x).strip()


# =========================================================
# DB INIT / MIGRATION
# =========================================================
def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        LIMIT 1
    """, (safe(table_name),)).fetchone()
    return row is not None


def ensure_journal_tables():
    conn = get_conn()

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
            reversed_from_id INTEGER,
            reversed_by_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS journal_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journal_id INTEGER,
            line_no INTEGER DEFAULT 1,
            line_description TEXT,
            account_code TEXT,
            debit REAL DEFAULT 0,
            credit REAL DEFAULT 0,
            partner_type TEXT,
            partner_id INTEGER
        )
    """)

    ensure_column(conn, "journal_entries", "entry_no", "ALTER TABLE journal_entries ADD COLUMN entry_no TEXT")
    ensure_column(conn, "journal_entries", "entry_date", "ALTER TABLE journal_entries ADD COLUMN entry_date TEXT")
    ensure_column(conn, "journal_entries", "description", "ALTER TABLE journal_entries ADD COLUMN description TEXT")
    ensure_column(conn, "journal_entries", "reference", "ALTER TABLE journal_entries ADD COLUMN reference TEXT")
    ensure_column(conn, "journal_entries", "status", "ALTER TABLE journal_entries ADD COLUMN status TEXT DEFAULT 'draft'")
    ensure_column(conn, "journal_entries", "source_type", "ALTER TABLE journal_entries ADD COLUMN source_type TEXT")
    ensure_column(conn, "journal_entries", "source_id", "ALTER TABLE journal_entries ADD COLUMN source_id INTEGER")
    ensure_column(conn, "journal_entries", "reversed_from_id", "ALTER TABLE journal_entries ADD COLUMN reversed_from_id INTEGER")
    ensure_column(conn, "journal_entries", "reversed_by_id", "ALTER TABLE journal_entries ADD COLUMN reversed_by_id INTEGER")
    ensure_column(conn, "journal_entries", "reversed_by_journal_id", "ALTER TABLE journal_entries ADD COLUMN reversed_by_journal_id INTEGER")
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


ensure_journal_tables()


# =========================================================
# ACCOUNT VALIDATION
# =========================================================
def get_account_row(conn, account_code: str):
    return conn.execute("""
        SELECT *
        FROM accounts
        WHERE code = ?
        LIMIT 1
    """, (safe(account_code),)).fetchone()


def validate_account_for_posting(conn, account_code: str):
    code = safe(account_code)
    if not code:
        raise Exception("Account code is required")

    account = get_account_row(conn, code)
    if not account:
        raise Exception(f"Account not found: {code}")

    is_active = int(account["is_active"] or 0) if "is_active" in account.keys() else 1
    allow_posting = int(account["allow_posting"] or 0) if "allow_posting" in account.keys() else 1
    is_group = int(account["is_group"] or 0) if "is_group" in account.keys() else 0

    if not is_active:
        raise Exception(f"Account is inactive: {code}")

    if is_group:
        raise Exception(f"Group account cannot be posted: {code}")

    if not allow_posting:
        raise Exception(f"Posting not allowed on account: {code}")

    return account


# =========================================================
# ENTRY HELPERS
# =========================================================
def next_entry_no(conn):
    row = conn.execute("""
        SELECT entry_no
        FROM journal_entries
        WHERE COALESCE(entry_no, '') <> ''
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    if not row or not row["entry_no"]:
        return "JV-0000001"

    try:
        last_num = int(str(row["entry_no"]).split("-")[-1])
    except Exception:
        last_num = 0

    return f"JV-{last_num + 1:07d}"


def get_journal_entry(conn, journal_id: int):
    return conn.execute("""
        SELECT *
        FROM journal_entries
        WHERE id = ?
        LIMIT 1
    """, (journal_id,)).fetchone()


def get_journal_lines(conn, journal_id: int):
    return conn.execute("""
        SELECT *
        FROM journal_lines
        WHERE journal_id = ?
        ORDER BY line_no, id
    """, (journal_id,)).fetchall()


def ensure_not_reversed(conn, journal_id: int):
    row = get_journal_entry(conn, journal_id)
    if not row:
        raise Exception("Journal entry not found")

    reversed_by_id = row["reversed_by_id"] if "reversed_by_id" in row.keys() else None
    reversed_by_journal_id = row["reversed_by_journal_id"] if "reversed_by_journal_id" in row.keys() else None

    if reversed_by_id or reversed_by_journal_id:
        raise Exception("Journal entry already reversed")


def _cleanup_vendor_bill_assets_for_reversal(conn, bill_id: int):
    if not bill_id or not table_exists(conn, "fixed_assets"):
        return

    ensure_column(conn, "fixed_assets", "source_vendor_bill_id", "ALTER TABLE fixed_assets ADD COLUMN source_vendor_bill_id INTEGER")
    ensure_column(conn, "vendor_bill_lines", "fixed_asset_id", "ALTER TABLE vendor_bill_lines ADD COLUMN fixed_asset_id INTEGER")

    protected_ids = set()
    if table_exists(conn, "asset_depreciation_moves"):
        for row in conn.execute("SELECT COALESCE(asset_id, 0) AS asset_id FROM asset_depreciation_moves").fetchall():
            protected_ids.add(int(row["asset_id"] or 0))
    if table_exists(conn, "asset_disposals"):
        for row in conn.execute("SELECT COALESCE(asset_id, 0) AS asset_id FROM asset_disposals").fetchall():
            protected_ids.add(int(row["asset_id"] or 0))

    assets = conn.execute("""
        SELECT id
        FROM fixed_assets
        WHERE source_vendor_bill_id = ?
    """, (bill_id,)).fetchall()

    for asset in assets:
        asset_id = int(asset["id"])
        if asset_id in protected_ids:
            conn.execute("UPDATE fixed_assets SET status = 'reversed' WHERE id = ?", (asset_id,))
        else:
            conn.execute("DELETE FROM fixed_assets WHERE id = ?", (asset_id,))

    conn.execute("""
        UPDATE vendor_bill_lines
        SET fixed_asset_id = NULL
        WHERE bill_id = ?
          AND fixed_asset_id NOT IN (
              SELECT id
              FROM fixed_assets
              WHERE source_vendor_bill_id = ?
          )
    """, (bill_id, bill_id))


def _sync_source_document_after_reverse(conn, entry, reverse_journal_id: int):
    source_type = safe(entry["source_type"]).lower()
    try:
        source_id = int(entry["source_id"] or 0)
    except Exception:
        source_id = 0

    if source_type == "vendor_bill" and source_id > 0 and table_exists(conn, "vendor_bills"):
        conn.execute("""
            UPDATE vendor_bills
            SET status = 'reversed',
                reversed_journal_id = ?,
                payment_status = 'cancelled'
            WHERE id = ?
        """, (reverse_journal_id, source_id))
        _cleanup_vendor_bill_assets_for_reversal(conn, source_id)


def validate_lines(conn, lines: list):
    if not lines or len(lines) == 0:
        raise Exception("Journal must contain at least one line")

    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")

    cleaned = []
    line_no = 1

    for raw in lines:
        account_code = safe(raw.get("account_code"))
        description = safe(raw.get("description"))
        partner_type = safe(raw.get("partner_type"))
        partner_id = raw.get("partner_id")

        debit = q2(raw.get("debit"))
        credit = q2(raw.get("credit"))

        validate_account_for_posting(conn, account_code)

        if debit < 0 or credit < 0:
            raise Exception(f"Negative amounts not allowed on line {line_no}")

        if debit == Decimal("0.00") and credit == Decimal("0.00"):
            raise Exception(f"Both debit and credit are zero on line {line_no}")

        if debit > Decimal("0.00") and credit > Decimal("0.00"):
            raise Exception(f"Line {line_no} cannot contain debit and credit together")

        total_debit += debit
        total_credit += credit

        cleaned.append({
            "line_no": line_no,
            "description": description,
            "account_code": account_code,
            "debit": debit,
            "credit": credit,
            "partner_type": partner_type,
            "partner_id": partner_id,
        })
        line_no += 1

    total_debit = q2(total_debit)
    total_credit = q2(total_credit)

    if total_debit != total_credit:
        raise Exception(f"Journal not balanced: DR={total_debit}, CR={total_credit}")

    return cleaned, total_debit, total_credit


# =========================================================
# CREATE DRAFT
# =========================================================
def create_journal_entry(
    conn,
    entry_date: str,
    description: str,
    reference: str,
    source_type: str,
    source_id: int,
    lines: list,
):
    cleaned_lines, _, _ = validate_lines(conn, lines)

    entry_no = next_entry_no(conn)

    cur = conn.execute("""
        INSERT INTO journal_entries (
            entry_no,
            entry_date,
            description,
            reference,
            status,
            source_type,
            source_id
        )
        VALUES (?, ?, ?, ?, 'draft', ?, ?)
    """, (
        entry_no,
        safe(entry_date),
        safe(description),
        safe(reference),
        safe(source_type),
        source_id,
    ))

    journal_id = cur.lastrowid

    for line in cleaned_lines:
        conn.execute("""
            INSERT INTO journal_lines (
                journal_id,
                line_no,
                line_description,
                account_code,
                debit,
                credit,
                partner_type,
                partner_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            journal_id,
            line["line_no"],
            line["description"],
            line["account_code"],
            float(line["debit"]),
            float(line["credit"]),
            line["partner_type"],
            line["partner_id"],
        ))

    return journal_id


def submit_journal_for_final_post(conn, journal_id: int):
    entry = get_journal_entry(conn, journal_id)

    if not entry:
        raise Exception("Journal entry not found")

    if safe(entry["status"]).lower() != "draft":
        raise Exception("Only draft journal entries can be submitted for final post")

    lines = get_journal_lines(conn, journal_id)
    if not lines:
        raise Exception("Journal entry has no lines")

    revalidated_lines = []
    for line in lines:
        revalidated_lines.append({
            "description": safe(line["line_description"]),
            "account_code": safe(line["account_code"]),
            "debit": line["debit"],
            "credit": line["credit"],
            "partner_type": safe(line["partner_type"]),
            "partner_id": line["partner_id"],
        })

    validate_lines(conn, revalidated_lines)

    conn.execute("""
        UPDATE journal_entries
        SET status = 'pending_final_post'
        WHERE id = ?
    """, (journal_id,))


# =========================================================
# DELETE DRAFT ONLY
# =========================================================
def delete_draft_journal_entry(conn, journal_id: int):
    entry = get_journal_entry(conn, journal_id)

    if not entry:
        raise Exception("Journal entry not found")

    if safe(entry["status"]).lower() not in ("draft", "pending_final_post"):
        raise Exception("Only draft or pending-final journal entries can be deleted")

    if entry["reversed_from_id"] or entry["reversed_by_id"]:
        raise Exception("Reversal-related journal entry cannot be deleted")

    conn.execute("DELETE FROM journal_lines WHERE journal_id = ?", (journal_id,))
    conn.execute("DELETE FROM journal_entries WHERE id = ?", (journal_id,))


# =========================================================
# POST
# =========================================================
def post_journal_entry(conn, journal_id: int):
    entry = get_journal_entry(conn, journal_id)

    if not entry:
        raise Exception("Journal entry not found")

    current_status = safe(entry["status"]).lower()

    if current_status == "posted":
        raise Exception("Journal entry already posted")

    if current_status not in ("draft", "pending_final_post"):
        raise Exception("Only draft or pending-final journal entries can be posted")

    lines = get_journal_lines(conn, journal_id)
    if not lines:
        raise Exception("Journal entry has no lines")

    revalidated_lines = []
    for line in lines:
        revalidated_lines.append({
            "description": safe(line["line_description"]),
            "account_code": safe(line["account_code"]),
            "debit": line["debit"],
            "credit": line["credit"],
            "partner_type": safe(line["partner_type"]),
            "partner_id": line["partner_id"],
        })

    validate_lines(conn, revalidated_lines)

    conn.execute("""
        UPDATE journal_entries
        SET status = 'posted'
        WHERE id = ?
    """, (journal_id,))


# =========================================================
# REVERSE
# =========================================================
def reverse_journal_entry(conn, journal_id: int):
    entry = get_journal_entry(conn, journal_id)

    if not entry:
        raise Exception("Journal entry not found")

    if safe(entry["status"]).lower() != "posted":
        raise Exception("Only posted journal entries can be reversed")

    ensure_not_reversed(conn, journal_id)

    original_lines = get_journal_lines(conn, journal_id)
    if not original_lines:
        raise Exception("Original journal entry has no lines")

    reversed_lines = []
    for line in original_lines:
        reversed_lines.append({
            "description": f"Reverse - {safe(line['line_description'])}",
            "account_code": safe(line["account_code"]),
            "debit": q2(line["credit"]),
            "credit": q2(line["debit"]),
            "partner_type": safe(line["partner_type"]),
            "partner_id": line["partner_id"],
        })

    reverse_journal_id = create_journal_entry(
        conn=conn,
        entry_date=safe(entry["entry_date"]),
        description=f"Reversal of {safe(entry['entry_no'])}",
        reference=f"REV-{safe(entry['entry_no'])}",
        source_type="journal_reverse",
        source_id=journal_id,
        lines=reversed_lines,
    )

    post_journal_entry(conn, reverse_journal_id)

    conn.execute("""
        UPDATE journal_entries
        SET reversed_by_id = ?,
            reversed_by_journal_id = ?
        WHERE id = ?
    """, (reverse_journal_id, reverse_journal_id, journal_id))

    conn.execute("""
        UPDATE journal_entries
        SET reversed_from_id = ?
        WHERE id = ?
    """, (journal_id, reverse_journal_id))

    _sync_source_document_after_reverse(conn, entry, reverse_journal_id)

    return reverse_journal_id


# =========================================================
# REBUILD DRAFT
# =========================================================
def rebuild_draft_journal_entry(
    conn,
    old_journal_id: int,
    entry_date: str,
    description: str,
    reference: str,
    source_type: str,
    source_id: int,
    lines: list,
):
    if old_journal_id:
        delete_draft_journal_entry(conn, old_journal_id)

    return create_journal_entry(
        conn=conn,
        entry_date=entry_date,
        description=description,
        reference=reference,
        source_type=source_type,
        source_id=source_id,
        lines=lines,
    )
