import sqlite3
import os
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.getenv("ERP_DB_PATH", os.path.join(BASE_DIR, "erp.db"))


def get_conn():
    conn = sqlite3.connect(DB_NAME, timeout=30)  # زيادة المهلة لـ 30 ثانية
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")    # تفعيل وضع WAL لتحسين الأداء ومنع القفل
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_db():
    return get_conn()


@contextmanager
def db_cursor():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def table_columns(conn, table_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [row["name"] for row in rows]


def ensure_column(conn, table_name: str, column_name: str, alter_sql: str):
    if column_name not in table_columns(conn, table_name):
        conn.execute(alter_sql)


def init_db():
    with db_cursor() as conn:
        # =========================
        # SYSTEM SETUP
        # =========================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_setup (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT,
                company_name_ar TEXT,
                logo_path TEXT,
                base_currency TEXT DEFAULT 'EGP',
                fiscal_year_start TEXT,
                business_type TEXT DEFAULT 'service',
                country TEXT DEFAULT 'Egypt',
                city TEXT,
                address TEXT,
                phone TEXT,
                email TEXT,
                tax_no TEXT,
                is_initialized INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # =========================
        # SETTINGS
        # =========================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS accounting_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                value TEXT,
                description TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # =========================
        # ACCOUNTS
        # =========================
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
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY(parent_id) REFERENCES accounts(id)
            )
        """)

        ensure_column(conn, "accounts", "level1", "ALTER TABLE accounts ADD COLUMN level1 TEXT")
        ensure_column(conn, "accounts", "level2", "ALTER TABLE accounts ADD COLUMN level2 TEXT")
        ensure_column(conn, "accounts", "statement_type", "ALTER TABLE accounts ADD COLUMN statement_type TEXT")
        ensure_column(conn, "accounts", "is_group", "ALTER TABLE accounts ADD COLUMN is_group INTEGER DEFAULT 0")
        ensure_column(conn, "accounts", "allow_posting", "ALTER TABLE accounts ADD COLUMN allow_posting INTEGER DEFAULT 1")
        ensure_column(conn, "accounts", "is_active", "ALTER TABLE accounts ADD COLUMN is_active INTEGER DEFAULT 1")

        # =========================
        # PARTNERS (UNIFIED)
        # =========================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                name TEXT NOT NULL,
                partner_type TEXT NOT NULL DEFAULT 'customer',
                phone TEXT,
                email TEXT,
                address TEXT,
                tax_no TEXT,
                payment_term_days INTEGER DEFAULT 0,
                opening_balance REAL DEFAULT 0,
                account_code TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # compatibility with old code that used "type"
        ensure_column(conn, "partners", "partner_type", "ALTER TABLE partners ADD COLUMN partner_type TEXT DEFAULT 'customer'")
        ensure_column(conn, "partners", "phone", "ALTER TABLE partners ADD COLUMN phone TEXT")
        ensure_column(conn, "partners", "email", "ALTER TABLE partners ADD COLUMN email TEXT")
        ensure_column(conn, "partners", "address", "ALTER TABLE partners ADD COLUMN address TEXT")
        ensure_column(conn, "partners", "tax_no", "ALTER TABLE partners ADD COLUMN tax_no TEXT")
        ensure_column(conn, "partners", "payment_term_days", "ALTER TABLE partners ADD COLUMN payment_term_days INTEGER DEFAULT 0")
        ensure_column(conn, "partners", "opening_balance", "ALTER TABLE partners ADD COLUMN opening_balance REAL DEFAULT 0")
        ensure_column(conn, "partners", "account_code", "ALTER TABLE partners ADD COLUMN account_code TEXT")
        ensure_column(conn, "partners", "is_active", "ALTER TABLE partners ADD COLUMN is_active INTEGER DEFAULT 1")

        # migrate old "type" values into partner_type if needed
        cols = table_columns(conn, "partners")
        if "type" in cols:
            conn.execute("""
                UPDATE partners
                SET partner_type = COALESCE(NULLIF(TRIM(partner_type), ''), TRIM(type), 'customer')
                WHERE COALESCE(NULLIF(TRIM(partner_type), ''), '') = ''
            """)

        # =========================
        # COST CENTERS
        # =========================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cost_centers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                name TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            )
        """)

        # =========================
        # JOURNAL ENTRIES
        # =========================
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
                reversed_by_journal_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        ensure_column(conn, "journal_entries", "reference", "ALTER TABLE journal_entries ADD COLUMN reference TEXT")
        ensure_column(conn, "journal_entries", "status", "ALTER TABLE journal_entries ADD COLUMN status TEXT DEFAULT 'draft'")
        ensure_column(conn, "journal_entries", "source_type", "ALTER TABLE journal_entries ADD COLUMN source_type TEXT")
        ensure_column(conn, "journal_entries", "source_id", "ALTER TABLE journal_entries ADD COLUMN source_id INTEGER")
        ensure_column(conn, "journal_entries", "reversed_by_journal_id", "ALTER TABLE journal_entries ADD COLUMN reversed_by_journal_id INTEGER")
        ensure_column(conn, "journal_entries", "created_at", "ALTER TABLE journal_entries ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        # =========================
        # JOURNAL LINES
        # =========================
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
                partner_id INTEGER,
                cost_center_id INTEGER,
                FOREIGN KEY(journal_id) REFERENCES journal_entries(id) ON DELETE CASCADE
            )
        """)

        ensure_column(conn, "journal_lines", "line_no", "ALTER TABLE journal_lines ADD COLUMN line_no INTEGER DEFAULT 1")
        ensure_column(conn, "journal_lines", "line_description", "ALTER TABLE journal_lines ADD COLUMN line_description TEXT")
        ensure_column(conn, "journal_lines", "account_code", "ALTER TABLE journal_lines ADD COLUMN account_code TEXT")
        ensure_column(conn, "journal_lines", "debit", "ALTER TABLE journal_lines ADD COLUMN debit REAL DEFAULT 0")
        ensure_column(conn, "journal_lines", "credit", "ALTER TABLE journal_lines ADD COLUMN credit REAL DEFAULT 0")
        ensure_column(conn, "journal_lines", "partner_type", "ALTER TABLE journal_lines ADD COLUMN partner_type TEXT")
        ensure_column(conn, "journal_lines", "partner_id", "ALTER TABLE journal_lines ADD COLUMN partner_id INTEGER")
        ensure_column(conn, "journal_lines", "cost_center_id", "ALTER TABLE journal_lines ADD COLUMN cost_center_id INTEGER")

        # =========================
        # USERS / ROLES
        # =========================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                name TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                full_name TEXT,
                password_hash TEXT NOT NULL,
                role_code TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS role_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_code TEXT NOT NULL,
                module_code TEXT NOT NULL,
                can_view INTEGER DEFAULT 0,
                can_create INTEGER DEFAULT 0,
                can_edit INTEGER DEFAULT 0,
                can_delete INTEGER DEFAULT 0,
                can_approve INTEGER DEFAULT 0,
                can_post INTEGER DEFAULT 0
            )
        """)

        # =========================
        # AUDIT / ACTIVITY
        # =========================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                notes TEXT,
                done_by TEXT,
                done_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                notes TEXT,
                done_by TEXT DEFAULT 'admin',
                done_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


def reset_partner_type_from_old_type():
    with db_cursor() as conn:
        cols = table_columns(conn, "partners")
        if "type" in cols and "partner_type" in cols:
            conn.execute("""
                UPDATE partners
                SET partner_type = COALESCE(NULLIF(TRIM(partner_type), ''), NULLIF(TRIM(type), ''), 'customer')
            """)


if __name__ == "__main__":
    init_db()
    reset_partner_type_from_old_type()
    print("Database initialized successfully.")
