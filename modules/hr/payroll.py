from html import escape
from urllib.parse import quote
from datetime import date
from io import BytesIO
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import logging
import traceback

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from db import get_conn
from i18n import get_lang
from layout import render_page
from utils.accounting_helpers import get_setting_value
from modules.accounting.employee_advances import (
    allocate_payroll_advance_deductions,
    ensure_advances_tables,
    get_employee_due_advance_total,
    get_employee_due_advances,
    sync_advance_status,
)
from modules.accounting.accounting_engine import (
    create_journal_entry as create_accounting_journal_entry,
    submit_journal_for_final_post,
)
from modules.accounting.cash_vouchers import (
    create_draft_journal as create_cash_voucher_draft_journal,
    ensure_tables as ensure_cash_voucher_tables,
    next_voucher_no,
)
from modules.accounting.invoice_ai import attachment_gallery, attachments_from_form
from modules.hr.attendance import ensure_attendance_tables
from modules.hr.employees import ensure_employees_table, safe, to_float

router = APIRouter()


def trp(lang, en, ar):
    return ar if lang == "ar" else en


def money(value):
    try:
        return f"{float(value or 0):,.2f}"
    except Exception:
        return "0.00"


def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


def ensure_payroll_tables():
    ensure_employees_table()
    ensure_attendance_tables()
    ensure_advances_tables()
    conn = get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payroll_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payroll_no TEXT UNIQUE,
                payroll_month INTEGER,
                payroll_year INTEGER,
                period_from TEXT,
                period_to TEXT,
                payment_date TEXT,
                working_days_basis REAL DEFAULT 26,
                notes TEXT,
                status TEXT DEFAULT 'draft',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payroll_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payroll_run_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL,
                employee_code TEXT,
                employee_name TEXT,
                department TEXT,
                job_title TEXT,
                basic_salary REAL DEFAULT 0,
                housing_allowance REAL DEFAULT 0,
                transport_allowance REAL DEFAULT 0,
                other_allowance REAL DEFAULT 0,
                attendance_days REAL DEFAULT 0,
                absent_days REAL DEFAULT 0,
                worked_hours REAL DEFAULT 0,
                overtime_hours REAL DEFAULT 0,
                overtime_amount REAL DEFAULT 0,
                bonus_amount REAL DEFAULT 0,
                deduction_amount REAL DEFAULT 0,
                advance_deduction REAL DEFAULT 0,
                absence_deduction REAL DEFAULT 0,
                insurance_employee_amount REAL DEFAULT 0,
                insurance_employer_amount REAL DEFAULT 0,
                gross_amount REAL DEFAULT 0,
                net_amount REAL DEFAULT 0,
                remarks TEXT
            )
            """
        )

        ensure_column(conn, "payroll_runs", "payroll_no", "ALTER TABLE payroll_runs ADD COLUMN payroll_no TEXT")
        ensure_column(conn, "payroll_runs", "payroll_month", "ALTER TABLE payroll_runs ADD COLUMN payroll_month INTEGER")
        ensure_column(conn, "payroll_runs", "payroll_year", "ALTER TABLE payroll_runs ADD COLUMN payroll_year INTEGER")
        ensure_column(conn, "payroll_runs", "period_from", "ALTER TABLE payroll_runs ADD COLUMN period_from TEXT")
        ensure_column(conn, "payroll_runs", "period_to", "ALTER TABLE payroll_runs ADD COLUMN period_to TEXT")
        ensure_column(conn, "payroll_runs", "payment_date", "ALTER TABLE payroll_runs ADD COLUMN payment_date TEXT")
        ensure_column(conn, "payroll_runs", "working_days_basis", "ALTER TABLE payroll_runs ADD COLUMN working_days_basis REAL DEFAULT 26")
        ensure_column(conn, "payroll_runs", "notes", "ALTER TABLE payroll_runs ADD COLUMN notes TEXT")
        ensure_column(conn, "payroll_runs", "status", "ALTER TABLE payroll_runs ADD COLUMN status TEXT DEFAULT 'draft'")
        ensure_column(conn, "payroll_runs", "created_at", "ALTER TABLE payroll_runs ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")
        ensure_column(conn, "payroll_runs", "payroll_journal_id", "ALTER TABLE payroll_runs ADD COLUMN payroll_journal_id INTEGER")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payroll_salary_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payroll_run_id INTEGER NOT NULL,
                payment_date TEXT,
                source_type TEXT,
                account_code TEXT,
                custody_employee_id INTEGER,
                amount REAL DEFAULT 0,
                description TEXT,
                journal_id INTEGER,
                status TEXT DEFAULT 'posted',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column(conn, "payroll_salary_payments", "payroll_run_id", "ALTER TABLE payroll_salary_payments ADD COLUMN payroll_run_id INTEGER")
        ensure_column(conn, "payroll_salary_payments", "payment_date", "ALTER TABLE payroll_salary_payments ADD COLUMN payment_date TEXT")
        ensure_column(conn, "payroll_salary_payments", "source_type", "ALTER TABLE payroll_salary_payments ADD COLUMN source_type TEXT")
        ensure_column(conn, "payroll_salary_payments", "account_code", "ALTER TABLE payroll_salary_payments ADD COLUMN account_code TEXT")
        ensure_column(conn, "payroll_salary_payments", "custody_employee_id", "ALTER TABLE payroll_salary_payments ADD COLUMN custody_employee_id INTEGER")
        ensure_column(conn, "payroll_salary_payments", "amount", "ALTER TABLE payroll_salary_payments ADD COLUMN amount REAL DEFAULT 0")
        ensure_column(conn, "payroll_salary_payments", "description", "ALTER TABLE payroll_salary_payments ADD COLUMN description TEXT")
        ensure_column(conn, "payroll_salary_payments", "journal_id", "ALTER TABLE payroll_salary_payments ADD COLUMN journal_id INTEGER")
        ensure_column(conn, "payroll_salary_payments", "status", "ALTER TABLE payroll_salary_payments ADD COLUMN status TEXT DEFAULT 'posted'")
        ensure_column(conn, "payroll_salary_payments", "created_at", "ALTER TABLE payroll_salary_payments ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS employee_grants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grant_no TEXT UNIQUE,
                grant_date TEXT,
                description TEXT,
                calculation_type TEXT DEFAULT 'fixed',
                fixed_amount REAL DEFAULT 0,
                percent_rate REAL DEFAULT 0,
                salary_base TEXT DEFAULT 'fixed_salary',
                total_amount REAL DEFAULT 0,
                status TEXT DEFAULT 'draft',
                payment_voucher_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS employee_grant_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grant_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL,
                employee_code TEXT,
                employee_name TEXT,
                department TEXT,
                job_title TEXT,
                base_amount REAL DEFAULT 0,
                grant_amount REAL DEFAULT 0,
                notes TEXT
            )
            """
        )
        ensure_column(conn, "employee_grants", "grant_no", "ALTER TABLE employee_grants ADD COLUMN grant_no TEXT")
        ensure_column(conn, "employee_grants", "grant_date", "ALTER TABLE employee_grants ADD COLUMN grant_date TEXT")
        ensure_column(conn, "employee_grants", "description", "ALTER TABLE employee_grants ADD COLUMN description TEXT")
        ensure_column(conn, "employee_grants", "calculation_type", "ALTER TABLE employee_grants ADD COLUMN calculation_type TEXT DEFAULT 'fixed'")
        ensure_column(conn, "employee_grants", "fixed_amount", "ALTER TABLE employee_grants ADD COLUMN fixed_amount REAL DEFAULT 0")
        ensure_column(conn, "employee_grants", "percent_rate", "ALTER TABLE employee_grants ADD COLUMN percent_rate REAL DEFAULT 0")
        ensure_column(conn, "employee_grants", "salary_base", "ALTER TABLE employee_grants ADD COLUMN salary_base TEXT DEFAULT 'fixed_salary'")
        ensure_column(conn, "employee_grants", "total_amount", "ALTER TABLE employee_grants ADD COLUMN total_amount REAL DEFAULT 0")
        ensure_column(conn, "employee_grants", "status", "ALTER TABLE employee_grants ADD COLUMN status TEXT DEFAULT 'draft'")
        ensure_column(conn, "employee_grants", "payment_voucher_id", "ALTER TABLE employee_grants ADD COLUMN payment_voucher_id INTEGER")
        ensure_column(conn, "employee_grants", "created_at", "ALTER TABLE employee_grants ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")
        ensure_column(conn, "employee_grant_lines", "grant_id", "ALTER TABLE employee_grant_lines ADD COLUMN grant_id INTEGER")
        ensure_column(conn, "employee_grant_lines", "employee_id", "ALTER TABLE employee_grant_lines ADD COLUMN employee_id INTEGER")
        ensure_column(conn, "employee_grant_lines", "employee_code", "ALTER TABLE employee_grant_lines ADD COLUMN employee_code TEXT")
        ensure_column(conn, "employee_grant_lines", "employee_name", "ALTER TABLE employee_grant_lines ADD COLUMN employee_name TEXT")
        ensure_column(conn, "employee_grant_lines", "department", "ALTER TABLE employee_grant_lines ADD COLUMN department TEXT")
        ensure_column(conn, "employee_grant_lines", "job_title", "ALTER TABLE employee_grant_lines ADD COLUMN job_title TEXT")
        ensure_column(conn, "employee_grant_lines", "base_amount", "ALTER TABLE employee_grant_lines ADD COLUMN base_amount REAL DEFAULT 0")
        ensure_column(conn, "employee_grant_lines", "grant_amount", "ALTER TABLE employee_grant_lines ADD COLUMN grant_amount REAL DEFAULT 0")
        ensure_column(conn, "employee_grant_lines", "notes", "ALTER TABLE employee_grant_lines ADD COLUMN notes TEXT")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS employee_rewards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reward_no TEXT UNIQUE,
                reward_date TEXT,
                employee_id INTEGER NOT NULL,
                amount REAL DEFAULT 0,
                reason TEXT,
                attachment_url TEXT,
                attachment_name TEXT,
                status TEXT DEFAULT 'draft',
                payroll_run_id INTEGER,
                payroll_line_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column(conn, "employee_rewards", "reward_no", "ALTER TABLE employee_rewards ADD COLUMN reward_no TEXT")
        ensure_column(conn, "employee_rewards", "reward_date", "ALTER TABLE employee_rewards ADD COLUMN reward_date TEXT")
        ensure_column(conn, "employee_rewards", "employee_id", "ALTER TABLE employee_rewards ADD COLUMN employee_id INTEGER")
        ensure_column(conn, "employee_rewards", "amount", "ALTER TABLE employee_rewards ADD COLUMN amount REAL DEFAULT 0")
        ensure_column(conn, "employee_rewards", "reason", "ALTER TABLE employee_rewards ADD COLUMN reason TEXT")
        ensure_column(conn, "employee_rewards", "attachment_url", "ALTER TABLE employee_rewards ADD COLUMN attachment_url TEXT")
        ensure_column(conn, "employee_rewards", "attachment_name", "ALTER TABLE employee_rewards ADD COLUMN attachment_name TEXT")
        ensure_column(conn, "employee_rewards", "status", "ALTER TABLE employee_rewards ADD COLUMN status TEXT DEFAULT 'draft'")
        ensure_column(conn, "employee_rewards", "payroll_run_id", "ALTER TABLE employee_rewards ADD COLUMN payroll_run_id INTEGER")
        ensure_column(conn, "employee_rewards", "payroll_line_id", "ALTER TABLE employee_rewards ADD COLUMN payroll_line_id INTEGER")
        ensure_column(conn, "employee_rewards", "created_at", "ALTER TABLE employee_rewards ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS employee_penalties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                penalty_no TEXT UNIQUE,
                penalty_date TEXT,
                employee_id INTEGER NOT NULL,
                amount REAL DEFAULT 0,
                reason TEXT,
                attachment_url TEXT,
                attachment_name TEXT,
                status TEXT DEFAULT 'draft',
                payroll_run_id INTEGER,
                payroll_line_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column(conn, "employee_penalties", "penalty_no", "ALTER TABLE employee_penalties ADD COLUMN penalty_no TEXT")
        ensure_column(conn, "employee_penalties", "penalty_date", "ALTER TABLE employee_penalties ADD COLUMN penalty_date TEXT")
        ensure_column(conn, "employee_penalties", "employee_id", "ALTER TABLE employee_penalties ADD COLUMN employee_id INTEGER")
        ensure_column(conn, "employee_penalties", "amount", "ALTER TABLE employee_penalties ADD COLUMN amount REAL DEFAULT 0")
        ensure_column(conn, "employee_penalties", "reason", "ALTER TABLE employee_penalties ADD COLUMN reason TEXT")
        ensure_column(conn, "employee_penalties", "attachment_url", "ALTER TABLE employee_penalties ADD COLUMN attachment_url TEXT")
        ensure_column(conn, "employee_penalties", "attachment_name", "ALTER TABLE employee_penalties ADD COLUMN attachment_name TEXT")
        ensure_column(conn, "employee_penalties", "status", "ALTER TABLE employee_penalties ADD COLUMN status TEXT DEFAULT 'draft'")
        ensure_column(conn, "employee_penalties", "payroll_run_id", "ALTER TABLE employee_penalties ADD COLUMN payroll_run_id INTEGER")
        ensure_column(conn, "employee_penalties", "payroll_line_id", "ALTER TABLE employee_penalties ADD COLUMN payroll_line_id INTEGER")
        ensure_column(conn, "employee_penalties", "created_at", "ALTER TABLE employee_penalties ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        ensure_column(conn, "payroll_lines", "employee_code", "ALTER TABLE payroll_lines ADD COLUMN employee_code TEXT")
        ensure_column(conn, "payroll_lines", "employee_name", "ALTER TABLE payroll_lines ADD COLUMN employee_name TEXT")
        ensure_column(conn, "payroll_lines", "department", "ALTER TABLE payroll_lines ADD COLUMN department TEXT")
        ensure_column(conn, "payroll_lines", "job_title", "ALTER TABLE payroll_lines ADD COLUMN job_title TEXT")
        ensure_column(conn, "payroll_lines", "basic_salary", "ALTER TABLE payroll_lines ADD COLUMN basic_salary REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "housing_allowance", "ALTER TABLE payroll_lines ADD COLUMN housing_allowance REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "transport_allowance", "ALTER TABLE payroll_lines ADD COLUMN transport_allowance REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "other_allowance", "ALTER TABLE payroll_lines ADD COLUMN other_allowance REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "attendance_days", "ALTER TABLE payroll_lines ADD COLUMN attendance_days REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "absent_days", "ALTER TABLE payroll_lines ADD COLUMN absent_days REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "worked_hours", "ALTER TABLE payroll_lines ADD COLUMN worked_hours REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "overtime_hours", "ALTER TABLE payroll_lines ADD COLUMN overtime_hours REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "overtime_amount", "ALTER TABLE payroll_lines ADD COLUMN overtime_amount REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "bonus_amount", "ALTER TABLE payroll_lines ADD COLUMN bonus_amount REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "deduction_amount", "ALTER TABLE payroll_lines ADD COLUMN deduction_amount REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "advance_deduction", "ALTER TABLE payroll_lines ADD COLUMN advance_deduction REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "absence_deduction", "ALTER TABLE payroll_lines ADD COLUMN absence_deduction REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "insurance_employee_amount", "ALTER TABLE payroll_lines ADD COLUMN insurance_employee_amount REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "insurance_employer_amount", "ALTER TABLE payroll_lines ADD COLUMN insurance_employer_amount REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "gross_amount", "ALTER TABLE payroll_lines ADD COLUMN gross_amount REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "net_amount", "ALTER TABLE payroll_lines ADD COLUMN net_amount REAL DEFAULT 0")
        ensure_column(conn, "payroll_lines", "remarks", "ALTER TABLE payroll_lines ADD COLUMN remarks TEXT")

        conn.commit()
    finally:
        conn.close()


def next_payroll_no():
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT payroll_no
            FROM payroll_runs
            WHERE COALESCE(payroll_no, '') <> ''
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        last = safe(row["payroll_no"]) if row else ""
        if not last:
            return "PAY-0001"
        try:
            num = int(last.split("-")[-1])
        except Exception:
            num = 0
        return f"PAY-{num + 1:04d}"
    finally:
        conn.close()


def next_grant_no():
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT grant_no
            FROM employee_grants
            WHERE COALESCE(grant_no, '') <> ''
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        last = safe(row["grant_no"]) if row else ""
        if not last:
            return "GRANT-0001"
        try:
            num = int(last.split("-")[-1])
        except Exception:
            num = 0
        return f"GRANT-{num + 1:04d}"
    finally:
        conn.close()


def next_hr_adjustment_no(table_name, no_column, prefix):
    conn = get_conn()
    try:
        row = conn.execute(
            f"""
            SELECT {no_column} AS doc_no
            FROM {table_name}
            WHERE COALESCE({no_column}, '') <> ''
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        last = safe(row["doc_no"]) if row else ""
        if not last:
            return f"{prefix}-0001"
        try:
            num = int(last.split("-")[-1])
        except Exception:
            num = 0
        return f"{prefix}-{num + 1:04d}"
    finally:
        conn.close()


def next_reward_no():
    return next_hr_adjustment_no("employee_rewards", "reward_no", "REW")


def next_penalty_no():
    return next_hr_adjustment_no("employee_penalties", "penalty_no", "PEN")


def employee_select_options(conn, selected_id=""):
    rows = conn.execute(
        """
        SELECT id, code, name
        FROM employees
        WHERE COALESCE(is_active, 1) = 1
        ORDER BY code, name
        """
    ).fetchall()
    html = "<option value=''>-- Select Employee --</option>"
    for row in rows:
        selected = "selected" if safe(selected_id) == safe(row["id"]) else ""
        label = f"{safe(row['code'])} - {safe(row['name'])}"
        html += f"<option value='{int(row['id'])}' {selected}>{escape(label)}</option>"
    return html


def payroll_period_condition(date_column):
    return f"COALESCE({date_column}, '') >= ? AND COALESCE({date_column}, '') <= ?"


def employee_payroll_adjustments(conn, employee_id, period_from, period_to):
    rewards = conn.execute(
        f"""
        SELECT id, reward_no AS doc_no, amount, reason
        FROM employee_rewards
        WHERE employee_id = ?
          AND LOWER(COALESCE(status, 'draft')) IN ('draft', 'open')
          AND COALESCE(payroll_run_id, 0) = 0
          AND {payroll_period_condition('reward_date')}
        ORDER BY reward_date, id
        """,
        (employee_id, safe(period_from), safe(period_to)),
    ).fetchall()
    penalties = conn.execute(
        f"""
        SELECT id, penalty_no AS doc_no, amount, reason
        FROM employee_penalties
        WHERE employee_id = ?
          AND LOWER(COALESCE(status, 'draft')) IN ('draft', 'open')
          AND COALESCE(payroll_run_id, 0) = 0
          AND {payroll_period_condition('penalty_date')}
        ORDER BY penalty_date, id
        """,
        (employee_id, safe(period_from), safe(period_to)),
    ).fetchall()
    return rewards, penalties


def mark_payroll_adjustments_applied(conn, rewards, penalties, run_id, line_id):
    for row in rewards:
        conn.execute(
            """
            UPDATE employee_rewards
            SET status = 'applied', payroll_run_id = ?, payroll_line_id = ?
            WHERE id = ?
            """,
            (run_id, line_id, row["id"]),
        )
    for row in penalties:
        conn.execute(
            """
            UPDATE employee_penalties
            SET status = 'applied', payroll_run_id = ?, payroll_line_id = ?
            WHERE id = ?
            """,
            (run_id, line_id, row["id"]),
        )


def clear_payroll_hr_adjustments(conn, run_id):
    conn.execute(
        """
        UPDATE employee_rewards
        SET status = 'draft', payroll_run_id = NULL, payroll_line_id = NULL
        WHERE payroll_run_id = ?
        """,
        (run_id,),
    )
    conn.execute(
        """
        UPDATE employee_penalties
        SET status = 'draft', payroll_run_id = NULL, payroll_line_id = NULL
        WHERE payroll_run_id = ?
        """,
        (run_id,),
    )


def grant_base_amount(emp, salary_base):
    basic = float(emp["basic_salary"] or 0)
    fixed = (
        basic
        + float(emp["housing_allowance"] or 0)
        + float(emp["transport_allowance"] or 0)
        + float(emp["other_allowance"] or 0)
    )
    return basic if safe(salary_base) == "basic_salary" else fixed


def recalc_payroll_totals(conn, run_id):
    rows = conn.execute(
        """
        SELECT *
        FROM payroll_lines
        WHERE payroll_run_id = ?
        ORDER BY id
        """,
        (run_id,),
    ).fetchall()

    for row in rows:
        gross = (
            float(row["basic_salary"] or 0)
            + float(row["housing_allowance"] or 0)
            + float(row["transport_allowance"] or 0)
            + float(row["other_allowance"] or 0)
            + float(row["overtime_amount"] or 0)
            + float(row["bonus_amount"] or 0)
        )
        total_deductions = (
            float(row["deduction_amount"] or 0)
            + float(row["advance_deduction"] or 0)
            + float(row["absence_deduction"] or 0)
            + float(row["insurance_employee_amount"] or 0)
        )
        net = gross - total_deductions
        conn.execute(
            """
            UPDATE payroll_lines
            SET gross_amount = ?, net_amount = ?
            WHERE id = ?
            """,
            (gross, net, row["id"]),
        )


def account_label(conn, account_code):
    if not safe(account_code):
        return ""
    row = conn.execute(
        "SELECT code, name FROM accounts WHERE code = ? LIMIT 1",
        (safe(account_code),),
    ).fetchone()
    if not row:
        return safe(account_code)
    return f"{safe(row['code'])} - {safe(row['name'])}"


def liquidity_account_options(conn, selected_code=""):
    rows = conn.execute(
        """
        SELECT code, name
        FROM accounts
        WHERE COALESCE(is_active, 1) = 1
          AND COALESCE(is_group, 0) = 0
          AND COALESCE(allow_posting, 1) = 1
          AND (
              code LIKE '10201%'
              OR code LIKE '10202%'
              OR name LIKE '%خزين%'
              OR name LIKE '%بنك%'
              OR LOWER(name) LIKE '%cash%'
              OR LOWER(name) LIKE '%bank%'
          )
        ORDER BY code, name
        """
    ).fetchall()
    html = "<option value=''>-- Select Cash / Bank Account --</option>"
    for row in rows:
        selected = "selected" if safe(selected_code) == safe(row["code"]) else ""
        html += f"<option value='{escape(safe(row['code']))}' {selected}>{escape(safe(row['code']))} - {escape(safe(row['name']))}</option>"
    return html


def employee_options(conn, selected_id=""):
    rows = conn.execute(
        """
        SELECT id, code, COALESCE(name, employee_name, full_name, '') AS display_name
        FROM employees
        WHERE COALESCE(is_active, 1) = 1
        ORDER BY code, display_name
        """
    ).fetchall()
    html = "<option value=''>-- Select Employee --</option>"
    for row in rows:
        selected = "selected" if safe(selected_id) == safe(row["id"]) else ""
        label = " - ".join([p for p in [safe(row["code"]), safe(row["display_name"])] if p])
        html += f"<option value='{int(row['id'])}' {selected}>{escape(label)}</option>"
    return html


def payroll_paid_amount(conn, run_id):
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS paid
        FROM cash_vouchers
        WHERE LOWER(COALESCE(source_type, '')) = 'payroll_salary_payment'
          AND COALESCE(source_id, 0) = ?
          AND LOWER(COALESCE(status, 'draft')) <> 'reversed'
        """,
        (run_id,),
    ).fetchone()
    return float(row["paid"] or 0) if row else 0.0


def payroll_journal_final_posted(conn, run):
    journal_id = 0
    try:
        journal_id = int(run["payroll_journal_id"] or 0)
    except Exception:
        journal_id = 0
    if journal_id <= 0:
        return False
    row = conn.execute(
        "SELECT status FROM journal_entries WHERE id = ? LIMIT 1",
        (journal_id,),
    ).fetchone()
    return bool(row and safe(row["status"]).lower() == "posted")


def payroll_journal_id(run):
    try:
        return int(run["payroll_journal_id"] or 0)
    except Exception:
        return 0


def payroll_journal_status(conn, run):
    journal_id = payroll_journal_id(run)
    if journal_id <= 0:
        return ""
    row = conn.execute(
        "SELECT status FROM journal_entries WHERE id = ? LIMIT 1",
        (journal_id,),
    ).fetchone()
    return safe(row["status"]).lower() if row else ""


def delete_non_final_payroll_journal(conn, run):
    journal_id = payroll_journal_id(run)
    if journal_id <= 0:
        return
    status = payroll_journal_status(conn, run)
    if status == "posted":
        raise ValueError("Final posted payroll journal cannot be deleted.")
    conn.execute("DELETE FROM journal_lines WHERE journal_id = ?", (journal_id,))
    conn.execute("DELETE FROM journal_entries WHERE id = ?", (journal_id,))


def clear_payroll_advance_deductions(conn, run_id):
    deductions = conn.execute(
        """
        SELECT advance_id, deduction_month, deduction_year, SUM(amount) AS amount
        FROM employee_advance_deductions
        WHERE payroll_run_id = ?
        GROUP BY advance_id, deduction_month, deduction_year
        """,
        (run_id,),
    ).fetchall()
    for deduction in deductions:
        advance_id = int(deduction["advance_id"] or 0)
        month = int(deduction["deduction_month"] or 0)
        year = int(deduction["deduction_year"] or 0)
        amount = float(deduction["amount"] or 0)
        if advance_id <= 0 or month <= 0 or year <= 0 or amount <= 0:
            continue
        conn.execute(
            """
            UPDATE employee_advance_installments
            SET paid_amount = MAX(0, COALESCE(paid_amount, 0) - ?),
                status = CASE
                    WHEN MAX(0, COALESCE(paid_amount, 0) - ?) <= 0.001 THEN 'pending'
                    WHEN MAX(0, COALESCE(paid_amount, 0) - ?) >= COALESCE(planned_amount, 0) - 0.001 THEN 'paid'
                    ELSE status
                END
            WHERE advance_id = ?
              AND installment_month = ?
              AND installment_year = ?
            """,
            (amount, amount, amount, advance_id, month, year),
        )
        sync_advance_status(conn, advance_id)
    conn.execute("DELETE FROM employee_advance_deductions WHERE payroll_run_id = ?", (run_id,))


def payroll_payment_rows_html(conn, run_id):
    rows = conn.execute(
        """
        SELECT v.*,
               COALESCE(e.code, '') AS employee_code,
               COALESCE(e.name, e.employee_name, e.full_name, '') AS employee_name,
               j.entry_no
        FROM cash_vouchers v
        LEFT JOIN employees e ON e.id = v.expense_employee_id
        LEFT JOIN journal_entries j ON j.id = v.journal_id
        WHERE LOWER(COALESCE(v.source_type, '')) = 'payroll_salary_payment'
          AND COALESCE(v.source_id, 0) = ?
        ORDER BY v.id DESC
        """,
        (run_id,),
    ).fetchall()
    if not rows:
        return "<tr><td colspan='7' style='text-align:center;'>No salary payments yet.</td></tr>"

    html = ""
    for row in rows:
        source = "Employee Custody" if safe(row["expense_payment_source"]).lower() == "custody" else "Cash / Bank"
        employee = " - ".join([p for p in [safe(row["employee_code"]), safe(row["employee_name"])] if p])
        journal = (
            f"<a href='/ui/accounting/journal/{int(row['journal_id'])}'>{escape(safe(row['entry_no']))}</a>"
            if row["journal_id"] else ""
        )
        action = f"<a class='btn blue' style='padding:8px 12px;' href='/ui/accounting/cash-payments/{int(row['id'])}'>Open</a>"
        html += f"""
        <tr>
            <td>{escape(safe(row['voucher_date']))}</td>
            <td>{source}</td>
            <td>{escape(account_label(conn, row['liquidity_account_code']))}</td>
            <td>{escape(employee)}</td>
            <td class="number-cell">{money(row['amount'])}</td>
            <td>{escape(safe(row['status']))}</td>
            <td>{journal}</td>
            <td>{action}</td>
        </tr>
        """
    return html


def safe_filename(value):
    text = safe(value) or "employee"
    text = re.sub(r'[<>:"/\\|?*\r\n\t]+', " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120] or "employee"


def find_chrome_exe():
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for item in candidates:
        if item and os.path.exists(item):
            return item
    for name in ("chrome.exe", "msedge.exe"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def payslip_print_css():
    return """
        @page { size: A4; margin: 6mm; }
        * { box-sizing: border-box; }
        body { margin:0; color:#0b2d57; font-family: Arial, sans-serif; background:#eef3f8; }
        .toolbar { position:sticky; top:0; display:flex; justify-content:flex-end; gap:8px; padding:10px; background:#fff; border-bottom:1px solid #dbe5f2; }
        .toolbar button, .toolbar a { background:#159b57; color:#fff; border:0; border-radius:8px; padding:10px 18px; font-weight:800; cursor:pointer; text-decoration:none; }
        .toolbar a { background:#eef3f8; color:#0b2d57; border:1px solid #dbe5f2; }
        .slip { width: 198mm; min-height: 285mm; margin: 8px auto; background:#fff; border:1px solid #dbe5f2; padding:7mm; overflow:hidden; }
        .slip-head { display:flex; justify-content:space-between; align-items:flex-start; border-bottom:2px solid #0b2d57; padding-bottom:6px; }
        h1 { margin:0; font-size:22px; }
        h2 { margin:0 0 5px; font-size:13px; }
        .muted, .meta span, .totals span { color:#60789a; font-size:9px; font-weight:800; text-transform:uppercase; display:block; }
        .net-box { text-align:right; border:1px solid #dbe5f2; border-radius:7px; padding:7px 10px; min-width:125px; }
        .net-box strong { display:block; font-size:21px; margin-top:2px; }
        .meta { display:grid; grid-template-columns:repeat(4, 1fr); gap:5px; margin-top:8px; }
        .meta div, .totals div, .remarks { border:1px solid #dbe5f2; border-radius:7px; padding:5px 7px; min-height:38px; }
        .meta b { display:block; font-size:10.5px; margin-top:2px; min-height:13px; word-break:break-word; }
        .totals { display:grid; grid-template-columns:repeat(3, 1fr); gap:6px; margin-top:7px; }
        .totals b { display:block; font-size:18px; margin-top:2px; }
        .grid { display:grid; grid-template-columns:repeat(2, 1fr); gap:7px; margin-top:8px; }
        .grid > div { border:1px solid #dbe5f2; border-radius:7px; padding:7px; break-inside:avoid; }
        table { width:100%; border-collapse:collapse; font-size:10.5px; }
        td { padding:3.5px 3px; border-bottom:1px solid #eef3f8; }
        td:first-child { font-weight:700; }
        .num { text-align:right; font-weight:800; }
        html[dir="rtl"] .num { text-align:left; }
        html[dir="rtl"] .net-box { text-align:left; }
        .remarks { margin-top:7px; min-height:28px; font-size:10.5px; }
        .signatures { display:grid; grid-template-columns:repeat(3, 1fr); gap:18px; margin-top:19px; }
        .signatures div { border-top:1px solid #0b2d57; text-align:center; padding-top:6px; font-weight:800; font-size:11px; }
        @media print {
            body { background:#fff; }
            .toolbar { display:none; }
            .slip { margin:0; border:0; width:auto; min-height:auto; height:285mm; padding:0; }
        }
    """


def payslip_full_html(title, blocks, with_toolbar=True, lang="en", back_url=""):
    direction = "rtl" if lang == "ar" else "ltr"
    toolbar = ""
    if with_toolbar:
        back_link = f'<a href="{escape(back_url)}">{trp(lang, "Back", "رجوع")}</a>' if back_url else ""
        toolbar = f'<div class="toolbar"><button onclick="window.print()">{trp(lang, "Print / Save PDF", "طباعة / حفظ PDF")}</button>{back_link}</div>'
    return f"""
    <!doctype html>
    <html lang="{lang}" dir="{direction}">
    <head>
        <meta charset="utf-8">
        <title>{escape(title)}</title>
        <style>{payslip_print_css()}</style>
    </head>
    <body>
        {toolbar}
        {blocks}
    </body>
    </html>
    """


def attendance_summary_map(conn, period_from, period_to):
    rows = conn.execute(
        """
        SELECT
            employee_id,
            COUNT(DISTINCT attendance_date) AS attendance_days,
            COALESCE(SUM(worked_hours), 0) AS worked_hours,
            COALESCE(SUM(overtime_hours), 0) AS overtime_hours
        FROM attendance_logs
        WHERE attendance_date >= ?
          AND attendance_date <= ?
          AND LOWER(COALESCE(status, 'present')) IN (
              'present',
              'worked',
              'onsite',
              'late',
              'early_leave',
              'late_and_early_leave'
          )
        GROUP BY employee_id
        """,
        (safe(period_from), safe(period_to)),
    ).fetchall()
    return {
        int(row["employee_id"]): {
            "attendance_days": float(row["attendance_days"] or 0),
            "worked_hours": float(row["worked_hours"] or 0),
            "overtime_hours": float(row["overtime_hours"] or 0),
        }
        for row in rows
        if row["employee_id"] is not None
    }


def _advance_deduction_note(conn, employee_id: int, payroll_month: int, payroll_year: int, advance_deduction: float):
    due_advances = get_employee_due_advances(conn, employee_id, payroll_month, payroll_year)
    if advance_deduction > 0 and due_advances:
        labels = ", ".join(
            f"{safe(item.get('advance_no'))}:{money(item.get('due_amount', 0))}"
            for item in due_advances[:3]
        )
        if len(due_advances) > 3:
            labels += ", ..."
        return f"Advance deducted ({money(advance_deduction)}). Due: {labels}"

    active_advances = conn.execute(
        """
        SELECT advance_no, start_month, start_year
        FROM employee_advances
        WHERE employee_id = ?
          AND LOWER(COALESCE(status, 'active')) IN ('active', 'open')
        ORDER BY start_year, start_month, id
        """,
        (employee_id,),
    ).fetchall()
    if not active_advances:
        return "No active advances."

    current_period_key = int(payroll_year or 0) * 100 + int(payroll_month or 0)
    future_only = True
    nearest_no = ""
    nearest_month = 0
    nearest_year = 0
    nearest_key = 999999
    for row in active_advances:
        sm = int(row["start_month"] or 0)
        sy = int(row["start_year"] or 0)
        if sm <= 0 or sy <= 0:
            future_only = False
            continue
        k = sy * 100 + sm
        if k <= current_period_key:
            future_only = False
        if k < nearest_key:
            nearest_key = k
            nearest_no = safe(row["advance_no"])
            nearest_month = sm
            nearest_year = sy

    if future_only and nearest_no:
        return f"No deduction: advance starts at {nearest_month:02d}/{nearest_year} ({nearest_no})."
    return "No due installment for this payroll period."


ensure_payroll_tables()


@router.get("/ui/hr/payroll", response_class=HTMLResponse)
def payroll_list(request: Request):
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT pr.*,
                   COUNT(pl.id) AS employee_count,
                   COALESCE(SUM(pl.net_amount), 0) AS total_net
            FROM payroll_runs pr
            LEFT JOIN payroll_lines pl ON pl.payroll_run_id = pr.id
            GROUP BY pr.id
            ORDER BY pr.id DESC
            """
        ).fetchall()
        
        msg = safe(request.query_params.get("msg"))
        msg_html = f'<div class="msg success">{escape(msg)}</div>' if msg else ""
        
        body = ""
        for row in rows:
            status = safe(row["status"]).lower() or "draft"
            status_cls = "green" if status == "posted" else "orange"
            back_to_draft_btn = ""
            delete_btn = ""
            if status == "pending_final_post":
                back_to_draft_btn = (
                    f'<form method="post" action="/ui/hr/payroll/{row["id"]}/unpost" style="display:inline;" '
                    f'onsubmit="return confirm(\'Return this payroll to draft? The pending journal will be deleted.\');">'
                    f'<button class="btn orange" type="submit">Back to Draft</button></form>'
                )
            if status in ("draft", "pending_final_post"):
                delete_btn = (
                    f'<form method="post" action="/ui/hr/payroll/{row["id"]}/delete" style="display:inline;" '
                    f'onsubmit="return confirm(\'Delete this payroll? Pending journal and draft effects will be removed.\');">'
                    f'<button class="btn red" type="submit">Delete</button></form>'
                )
            body += f"""
            <tr>
                <td><a class="btn gray" href="/ui/hr/payroll/{row['id']}">{escape(safe(row['payroll_no']))}</a></td>
                <td>{int(row['payroll_month'] or 0):02d}/{row['payroll_year'] or ''}</td>
                <td>{escape(safe(row['period_from']))}</td>
                <td>{escape(safe(row['period_to']))}</td>
                <td>{escape(safe(row['payment_date']))}</td>
                <td>{int(row['employee_count'] or 0)}</td>
                <td class="number-cell">{money(row['total_net'])}</td>
                <td><span class="status-chip {status_cls}">{escape(safe(row['status']) or 'draft')}</span></td>
                <td>
                    <a class="btn blue" href="/ui/hr/payroll/{row['id']}">Open</a>
                    {back_to_draft_btn}
                    {delete_btn}
                </td>
            </tr>
            """
            
        if not body:
            body = "<tr><td colspan='9' style='text-align:center;'>No payroll runs found.</td></tr>"
            
        html = f"""
        <div class="card">
            {msg_html}
            <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
                <h2>Payroll</h2>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    <a class="btn orange" href="/ui/hr/employee-penalties">Employee Penalties</a>
                    <a class="btn blue" href="/ui/hr/employee-rewards">Employee Rewards</a>
                    <a class="btn blue" href="/ui/hr/employee-grants">Employee Grants</a>
                    <a class="btn green" href="/ui/hr/payroll/new">+ New Payroll Run</a>
                </div>
            </div>
        </div>
        
        <div class="card">
            <table>
                <tr>
                    <th>Payroll No</th>
                    <th>Month</th>
                    <th>Period From</th>
                    <th>Period To</th>
                    <th>Payment Date</th>
                    <th>Employees</th>
                    <th>Total Net</th>
                    <th>Status</th>
                    <th>Action</th>
                </tr>
                {body}
            </table>
        </div>
        """
        return HTMLResponse(render_page("Payroll", html, "en", current_path=request.url.path))
    finally:
        conn.close()


@router.get("/ui/hr/payroll/new", response_class=HTMLResponse)
def payroll_new(request: Request):
    html = f"""
    <div class="card">
        <h2>New Payroll Run</h2>
        <form method="post" action="/ui/hr/payroll/new">
            <div class="row">
                <div class="col">
                    <label>Payroll No</label>
                    <input name="payroll_no" value="{next_payroll_no()}" readonly>
                </div>
                <div class="col">
                    <label>Payroll Month</label>
                    <input type="number" min="1" max="12" name="payroll_month" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Payroll Year</label>
                    <input type="number" min="2020" max="2100" name="payroll_year" required>
                </div>
                <div class="col">
                    <label>Payment Date</label>
                    <input type="date" name="payment_date" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Period From</label>
                    <input type="date" name="period_from" required>
                </div>
                <div class="col">
                    <label>Period To</label>
                    <input type="date" name="period_to" required>
                </div>
            </div>

            <div class="row" style="margin-top:14px;">
                <div class="col">
                    <label>Working Days Basis</label>
                    <input type="number" step="0.01" name="working_days_basis" value="26" required>
                </div>
                <div class="col">
                    <label>Notes</label>
                    <input name="notes">
                </div>
            </div>

            <div class="card" style="margin-top:18px;">
                <h3>Payroll Engine</h3>
                <p class="section-note">This payroll run will calculate attendance days, absence deduction, overtime, and employee insurance deduction based on imported attendance / biometric logs for the selected period.</p>
            </div>

            <div style="margin-top:20px;">
                <button class="btn green" type="submit">Generate Payroll Draft</button>
                <a class="btn gray" href="/ui/hr/payroll">Back</a>
            </div>
        </form>
    </div>
    """
    return HTMLResponse(render_page("New Payroll", html, "en", current_path=request.url.path))


@router.post("/ui/hr/payroll/new")
def payroll_create(
    payroll_no: str = Form(""),
    payroll_month: int = Form(...),
    payroll_year: int = Form(...),
    period_from: str = Form(""),
    period_to: str = Form(""),
    payment_date: str = Form(""),
    working_days_basis: str = Form("26"),
    notes: str = Form(""),
):
    conn = get_conn()
    try:
        attendance_map = attendance_summary_map(conn, period_from, period_to)
        employees = conn.execute(
            """
            SELECT *
            FROM employees
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY code, name
            """
        ).fetchall()

        if not employees:
            return RedirectResponse("/ui/hr/payroll?msg=" + quote("No active employees found to generate payroll."), status_code=302)

        run_cur = conn.execute(
            """
            INSERT INTO payroll_runs (
                payroll_no, payroll_month, payroll_year, period_from, period_to, payment_date, notes, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')
            """,
            (
                safe(payroll_no) or next_payroll_no(),
                int(payroll_month or 0),
                int(payroll_year or 0),
                safe(period_from),
                safe(period_to),
                safe(payment_date),
                safe(notes),
            ),
        )
        run_id = run_cur.lastrowid
        basis_days = max(to_float(working_days_basis, 26), 1)
        conn.execute("UPDATE payroll_runs SET working_days_basis = ? WHERE id = ?", (basis_days, run_id))

        for emp in employees:
            att = attendance_map.get(int(emp["id"]), {})
            attendance_days = min(float(att.get("attendance_days", 0)), basis_days)
            absent_days = max(basis_days - attendance_days, 0)
            worked_hours = float(att.get("worked_hours", 0))
            overtime_hours = float(att.get("overtime_hours", 0))

            fixed_comp = (
                float(emp["basic_salary"] or 0)
                + float(emp["housing_allowance"] or 0)
                + float(emp["transport_allowance"] or 0)
                + float(emp["other_allowance"] or 0)
            )
            daily_rate = fixed_comp / basis_days if basis_days > 0 else 0
            expected_daily_hours = max(float(emp["expected_daily_hours"] or 8), 1)
            hourly_rate = (float(emp["basic_salary"] or 0) / basis_days / expected_daily_hours) if basis_days > 0 else 0
            overtime_amount = overtime_hours * hourly_rate
            absence_deduction = absent_days * daily_rate
            insurance_salary = float(emp["insurance_salary"] or 0) or float(emp["basic_salary"] or 0)
            insurance_employee_amount = 0.0
            insurance_employer_amount = 0.0
            if int(emp["insurance_applicable"] or 0) == 1:
                insurance_employee_amount = insurance_salary * (float(emp["insurance_employee_rate"] or 0) / 100.0)
                insurance_employer_amount = insurance_salary * (float(emp["insurance_employer_rate"] or 0) / 100.0)
            advance_deduction = get_employee_due_advance_total(conn, emp["id"], payroll_month, payroll_year)
            remarks = _advance_deduction_note(
                conn,
                int(emp["id"]),
                int(payroll_month or 0),
                int(payroll_year or 0),
                float(advance_deduction or 0),
            )
            rewards, penalties = employee_payroll_adjustments(conn, emp["id"], period_from, period_to)
            reward_amount = sum(float(row["amount"] or 0) for row in rewards)
            penalty_amount = sum(float(row["amount"] or 0) for row in penalties)
            notes = [remarks] if safe(remarks) else []
            if reward_amount:
                notes.append("Rewards: " + ", ".join(safe(row["doc_no"]) for row in rewards))
            if penalty_amount:
                notes.append("Penalties: " + ", ".join(safe(row["doc_no"]) for row in penalties))
            remarks = " | ".join(notes)

            line_cur = conn.execute(
                """
                INSERT INTO payroll_lines (
                    payroll_run_id, employee_id, employee_code, employee_name, department, job_title,
                    basic_salary, housing_allowance, transport_allowance, other_allowance,
                    attendance_days, absent_days, worked_hours, overtime_hours,
                    overtime_amount, bonus_amount, deduction_amount, advance_deduction, absence_deduction,
                    insurance_employee_amount, insurance_employer_amount,
                    gross_amount, net_amount, remarks
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    emp["id"],
                    safe(emp["code"]),
                    safe(emp["name"]),
                    safe(emp["department"]),
                    safe(emp["job_title"]),
                    float(emp["basic_salary"] or 0),
                    float(emp["housing_allowance"] or 0),
                    float(emp["transport_allowance"] or 0),
                    float(emp["other_allowance"] or 0),
                    attendance_days,
                    absent_days,
                    worked_hours,
                    overtime_hours,
                    overtime_amount,
                    reward_amount,
                    penalty_amount,
                    advance_deduction,
                    absence_deduction,
                    insurance_employee_amount,
                    insurance_employer_amount,
                    0,
                    0,
                    remarks,
                ),
            )
            mark_payroll_adjustments_applied(conn, rewards, penalties, run_id, line_cur.lastrowid)

        recalc_payroll_totals(conn, run_id)
        conn.commit()
        return RedirectResponse(f"/ui/hr/payroll/{run_id}", status_code=302)
    finally:
        conn.close()


@router.get("/ui/hr/payroll/{run_id}", response_class=HTMLResponse)
def payroll_open(request: Request, run_id: int):
    ensure_payroll_tables()
    lang = get_lang(request)
    lang_q = "?lang=ar" if lang == "ar" else ""
    lang_hidden = '<input type="hidden" name="lang" value="ar">' if lang == "ar" else ""
    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id = ? LIMIT 1", (run_id,)).fetchone()
        if not run:
            return HTMLResponse("Payroll run not found", status_code=404)

        lines = conn.execute(
            """
            SELECT *
            FROM payroll_lines
            WHERE payroll_run_id = ?
            ORDER BY employee_code, employee_name, id
            """,
            (run_id,),
        ).fetchall()

        total_gross = 0.0
        total_net = 0.0
        total_ded = 0.0
        total_employer_insurance = 0.0
        body = ""
        for line in lines:
            gross = float(line["gross_amount"] or 0)
            net = float(line["net_amount"] or 0)
            ded = (
                float(line["deduction_amount"] or 0)
                + float(line["advance_deduction"] or 0)
                + float(line["absence_deduction"] or 0)
                + float(line["insurance_employee_amount"] or 0)
            )
            total_gross += gross
            total_net += net
            total_ded += ded
            total_employer_insurance += float(line["insurance_employer_amount"] or 0)
            earnings_breakdown = (
                f"Basic: {money(line['basic_salary'])} | "
                f"Housing: {money(line['housing_allowance'])} | "
                f"Transport: {money(line['transport_allowance'])} | "
                f"Other: {money(line['other_allowance'])} | "
                f"OT: {money(line['overtime_amount'])} | "
                f"Bonus: {money(line['bonus_amount'])}"
            )
            deductions_breakdown = (
                f"Manual: {money(line['deduction_amount'])} | "
                f"Advance: {money(line['advance_deduction'])} | "
                f"Absence: {money(line['absence_deduction'])} | "
                f"Emp. Insurance: {money(line['insurance_employee_amount'])}"
            )
            body += f"""
            <tr>
                <td><input type="checkbox" name="line_id" value="{int(line['id'])}" class="payslip-select"></td>
                <td>{escape(safe(line['employee_code']))}</td>
                <td>{escape(safe(line['employee_name']))}</td>
                <td class="number-cell">{float(line['attendance_days'] or 0):,.2f}</td>
                <td class="number-cell">{float(line['absent_days'] or 0):,.2f}</td>
                <td class="number-cell">{float(line['overtime_hours'] or 0):,.2f}</td>
                <td>{escape(safe(line['department']))}</td>
                <td>{escape(safe(line['job_title']))}</td>
                <td class="number-cell">{money(gross)}</td>
                <td class="number-cell">{money(ded)}</td>
                <td class="number-cell">{money(net)}</td>
                <td>
                    <details>
                        <summary style="cursor:pointer; color:#1b57d0; font-weight:700;">View</summary>
                        <div style="margin-top:6px; font-size:12px; color:#4b5f7a; line-height:1.6;">
                            <div><b>Earnings:</b> {earnings_breakdown}</div>
                            <div><b>Deductions:</b> {deductions_breakdown}</div>
                        </div>
                    </details>
                </td>
                <td><a class="btn gray" style="padding:8px 12px;" href="/ui/hr/payroll/{run_id}/payslip/{line['id']}{lang_q}">{trp(lang, 'Payslip', 'قسيمة مرتب')}</a></td>
                <td>{escape(safe(line['remarks']))}</td>
            </tr>
            """

        if not body:
            body = "<tr><td colspan='14' style='text-align:center;'>No payroll lines found.</td></tr>"

        status = safe(run["status"]).lower() or "draft"
        unpost_button = ""
        if status == "pending_final_post":
            unpost_button = f"""
            <form method="post" action="/ui/hr/payroll/{run_id}/unpost" style="display:inline;" onsubmit="return confirm('Return this payroll to draft? The pending journal will be deleted.');">
                <button class="btn orange" type="submit">Back to Draft</button>
            </form>
            """

        action_buttons = f'<a class="btn blue" href="/ui/hr/payroll/{run_id}/edit">Edit Draft</a>' if status == "draft" else ""
        post_button = ""
        delete_button = ""
        if status == "draft":
            post_button = f"""
            <form method="post" action="/ui/hr/payroll/{run_id}/post" style="display:inline;">
                <button class="btn green" type="submit">Create Payroll Journal</button>
            </form>
            """
        if status in ("draft", "pending_final_post"):
            delete_button = f"""
            <form method="post" action="/ui/hr/payroll/{run_id}/delete" style="display:inline;" onsubmit="return confirm('Delete this payroll? Pending journal and draft effects will be removed.');">
                <button class="btn red" type="submit">Delete</button>
            </form>
            """

        journal_section = ""
        payroll_journal_posted = payroll_journal_final_posted(conn, run)
        if run["payroll_journal_id"]:
            journal_status = "Posted" if payroll_journal_posted else "Waiting Final Post"
            journal_section = f'<p><b>Journal Entry:</b> <a class="btn gray" href="/ui/accounting/journal/{run["payroll_journal_id"]}">View Journal Entry</a> <span class="status-chip {"green" if payroll_journal_posted else "orange"}">{journal_status}</span></p>'

        requested_amount = payroll_paid_amount(conn, run_id)
        remaining_amount = max(0.0, total_net - requested_amount)
        payment_section = ""
        if payroll_journal_posted:
            default_pay_date = safe(run["payment_date"]) or date.today().isoformat()
            payment_section = f"""
            <div class="card">
                <h2>Salary Payment Request</h2>
                <div class="kpi-grid">
                    <div class="kpi-card"><div class="kpi-label">Net Salaries</div><div class="kpi-value">{money(total_net)}</div></div>
                    <div class="kpi-card"><div class="kpi-label">Requested</div><div class="kpi-value">{money(requested_amount)}</div></div>
                    <div class="kpi-card"><div class="kpi-label">Remaining Request</div><div class="kpi-value">{money(remaining_amount)}</div></div>
                </div>
                <form method="post" action="/ui/hr/payroll/{run_id}/salary-payments" style="margin-top:16px;">
                    <div class="row">
                        <div class="col">
                            <label>Date</label>
                            <input type="date" name="payment_date" value="{escape(default_pay_date)}" required>
                        </div>
                        <div class="col">
                            <label>Payment From</label>
                            <select name="source_type" id="salary_payment_source" onchange="toggleSalaryPaymentSource()" required>
                                <option value="liquidity">Cash / Bank</option>
                                <option value="custody">Employee Custody</option>
                            </select>
                        </div>
                        <div class="col" id="salary_payment_account_wrap">
                            <label>Cash / Bank Account</label>
                            <select name="account_code" id="salary_payment_account">
                                {liquidity_account_options(conn)}
                            </select>
                        </div>
                        <div class="col" id="salary_payment_employee_wrap" style="display:none;">
                            <label>Custody Employee</label>
                            <select name="custody_employee_id" id="salary_payment_employee">
                                {employee_options(conn)}
                            </select>
                        </div>
                        <div class="col">
                            <label>Amount</label>
                            <input type="number" step="0.01" min="0.01" name="amount" value="{remaining_amount:.2f}" required>
                        </div>
                    </div>
                    <div style="margin-top:12px;">
                        <button class="btn green" type="submit">Create Payment Voucher</button>
                    </div>
                </form>
                <div class="table-wrap" style="margin-top:16px;">
                    <table>
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>From</th>
                                <th>Account</th>
                                <th>Custody Employee</th>
                                <th>Amount</th>
                                <th>Status</th>
                                <th>Journal</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody>{payroll_payment_rows_html(conn, run_id)}</tbody>
                    </table>
                </div>
            </div>
            <script>
            function toggleSalaryPaymentSource() {{
                const source = document.getElementById('salary_payment_source').value;
                const accountWrap = document.getElementById('salary_payment_account_wrap');
                const employeeWrap = document.getElementById('salary_payment_employee_wrap');
                const account = document.getElementById('salary_payment_account');
                const employee = document.getElementById('salary_payment_employee');
                const isCustody = source === 'custody';
                accountWrap.style.display = isCustody ? 'none' : 'block';
                employeeWrap.style.display = isCustody ? 'block' : 'none';
                account.required = !isCustody;
                employee.required = isCustody;
            }}
            toggleSalaryPaymentSource();
            </script>
            """

        status_cls = "green" if safe(run["status"]).lower() == "posted" else "orange"
        html = f"""
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;">
                <div>
                    <h2>Payroll {escape(safe(run['payroll_no']))}</h2>
                    <p><b>Month:</b> {int(run['payroll_month'] or 0):02d}/{run['payroll_year'] or ''}</p>
                    <p><b>Period:</b> {escape(safe(run['period_from']))} to {escape(safe(run['period_to']))}</p>
                    <p><b>Payment Date:</b> {escape(safe(run['payment_date']))}</p>
                    <p><b>Working Days Basis:</b> {float(run['working_days_basis'] or 0):,.2f}</p>
                    <p><b>Status:</b> <span class="status-chip {status_cls}">{escape(safe(run['status']))}</span></p>
                    {journal_section}
                    <p><b>Notes:</b> {escape(safe(run['notes']))}</p>
                </div>
                <div class="kpi-grid" style="min-width:280px;">
                    <div class="kpi-card">
                        <div class="kpi-label">Employees</div>
                        <div class="kpi-value">{len(lines)}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Gross</div>
                        <div class="kpi-value">{money(total_gross)}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Deductions</div>
                        <div class="kpi-value">{money(total_ded)}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Net</div>
                        <div class="kpi-value">{money(total_net)}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Employer Insurance</div>
                        <div class="kpi-value">{money(total_employer_insurance)}</div>
                    </div>
                </div>
            </div>

            <div style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap;">
                <a class="btn gray" href="/ui/hr/payroll">Back</a>
                {action_buttons}
                {post_button}
                {unpost_button}
                {delete_button}
            </div>
        </div>

        {payment_section}

        <div class="card">
            <form method="get" action="/ui/hr/payroll/{run_id}/payslips/print" target="_blank">
                {lang_hidden}
                <div style="display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;margin-bottom:12px;">
                    <label style="display:flex;gap:6px;align-items:center;font-weight:800;">
                        <input type="checkbox" id="select_all_payslips" onclick="document.querySelectorAll('.payslip-select').forEach(cb => cb.checked = this.checked);">
                        {trp(lang, 'Select All', 'تحديد الكل')}
                    </label>
                    <button class="btn blue" type="submit">{trp(lang, 'Print Selected Payslips', 'طباعة المحدد')}</button>
                    <a class="btn gray" target="_blank" href="/ui/hr/payroll/{run_id}/payslips/print?all=1{'&lang=ar' if lang == 'ar' else ''}">{trp(lang, 'Print All', 'طباعة الكل')}</a>
                    <button class="btn green" type="submit" formaction="/ui/hr/payroll/{run_id}/payslips/download.zip">{trp(lang, 'Download Selected PDFs', 'تحميل المحدد PDF')}</button>
                    <a class="btn green" href="/ui/hr/payroll/{run_id}/payslips/download.zip?all=1{'&lang=ar' if lang == 'ar' else ''}">{trp(lang, 'Download All PDFs', 'تحميل الكل PDF')}</a>
                </div>
                <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Select</th>
                            <th>Code</th>
                            <th>Name</th>
                            <th>Attendance</th>
                            <th>Absent</th>
                            <th>OT Hours</th>
                            <th>Department</th>
                            <th>Job Title</th>
                            <th>Gross</th>
                            <th>Deductions</th>
                            <th>Net</th>
                            <th>Breakdown</th>
                            <th>Payslip</th>
                            <th>Advance Note</th>
                        </tr>
                    </thead>
                    <tbody>
                        {body}
                    </tbody>
                </table>
                </div>
            </form>
        </div>
        """
        return HTMLResponse(render_page("Payroll", html, "en", current_path=request.url.path))
    finally:
        conn.close()


@router.post("/ui/hr/payroll/{run_id}/salary-payments")
async def payroll_salary_payment(
    run_id: int,
    payment_date: str = Form(""),
    source_type: str = Form(""),
    account_code: str = Form(""),
    custody_employee_id: str = Form(""),
    amount: str = Form(""),
):
    ensure_payroll_tables()
    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id = ? LIMIT 1", (run_id,)).fetchone()
        if not run:
            return HTMLResponse("Payroll run not found", status_code=404)
        if not payroll_journal_final_posted(conn, run):
            return RedirectResponse(
                f"/ui/hr/payroll/{run_id}?msg=" + quote("Final post the payroll journal from Journal screen first."),
                status_code=302,
            )

        total_row = conn.execute(
            "SELECT COALESCE(SUM(net_amount), 0) AS total_net FROM payroll_lines WHERE payroll_run_id = ?",
            (run_id,),
        ).fetchone()
        total_net = float(total_row["total_net"] or 0)
        paid_amount = payroll_paid_amount(conn, run_id)
        remaining = max(0.0, total_net - paid_amount)
        pay_amount = to_float(amount)
        if pay_amount <= 0:
            return RedirectResponse(f"/ui/hr/payroll/{run_id}?msg=" + quote("Payment amount must be greater than zero."), status_code=302)
        if pay_amount > remaining + 0.01:
            return RedirectResponse(f"/ui/hr/payroll/{run_id}?msg=" + quote("Payment amount is greater than remaining salaries."), status_code=302)

        source = safe(source_type).lower()
        custody_emp_id = int(custody_employee_id or 0) if safe(custody_employee_id).isdigit() else 0
        if source == "custody":
            if custody_emp_id <= 0:
                return RedirectResponse(f"/ui/hr/payroll/{run_id}?msg=" + quote("Select custody employee."), status_code=302)
            credit_account = get_setting_value("employee_custody_account", "1020504", conn=conn)
            payment_source = "custody"
        else:
            source = "liquidity"
            credit_account = safe(account_code)
            payment_source = "liquidity"
            if not credit_account:
                return RedirectResponse(f"/ui/hr/payroll/{run_id}?msg=" + quote("Select cash or bank account."), status_code=302)

        payable_acct = get_setting_value("payroll_payable_account", "201020108", conn=conn)
        pay_date = safe(payment_date) or safe(run["payment_date"]) or date.today().isoformat()
        description = f"Salary payment request {safe(run['payroll_no'])}"
        ensure_cash_voucher_tables()
        voucher_no = next_voucher_no("payment")
        cur = conn.execute(
            """
            INSERT INTO cash_vouchers (
                voucher_type, voucher_no, voucher_date, party_name, party_type, party_id,
                liquidity_account_code, counter_account_code, amount, description, signature_name,
                source_type, source_id, expense_payment_source, expense_employee_id, status
            )
            VALUES ('payment', ?, ?, ?, 'other', NULL, ?, ?, ?, ?, '', 'payroll_salary_payment', ?, ?, ?, 'draft')
            """,
            (
                voucher_no,
                pay_date,
                f"Payroll {safe(run['payroll_no'])}",
                credit_account,
                payable_acct,
                pay_amount,
                description,
                run_id,
                payment_source,
                custody_emp_id if payment_source == "custody" else None,
            ),
        )
        voucher_id = cur.lastrowid
        journal_id = create_cash_voucher_draft_journal(conn, voucher_id)
        conn.execute(
            """
            INSERT INTO payroll_salary_payments (
                payroll_run_id, payment_date, source_type, account_code,
                custody_employee_id, amount, description, journal_id, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft_voucher')
            """,
            (
                run_id,
                pay_date,
                payment_source,
                credit_account,
                custody_emp_id if payment_source == "custody" else None,
                pay_amount,
                description,
                journal_id,
            ),
        )
        conn.commit()
        return RedirectResponse(
            f"/ui/hr/payroll/{run_id}?msg=" + quote(f"Payment voucher {voucher_no} created. Execute it from Cash Payments module."),
            status_code=302,
        )
    except Exception as e:
        conn.rollback()
        logging.error(f"Error creating salary payment: {e}\n{traceback.format_exc()}")
        return HTMLResponse(f"<pre>Error creating salary payment: {escape(str(e))}</pre>", status_code=500)
    finally:
        conn.close()


@router.post("/ui/hr/payroll/{run_id}/salary-payments/{payment_id}/cancel")
def payroll_salary_payment_cancel(run_id: int, payment_id: int):
    ensure_payroll_tables()
    conn = get_conn()
    try:
        payment = conn.execute(
            "SELECT * FROM payroll_salary_payments WHERE id = ? AND payroll_run_id = ? LIMIT 1",
            (payment_id, run_id),
        ).fetchone()
        if not payment:
            return RedirectResponse(f"/ui/hr/payroll/{run_id}?msg=" + quote("Payment not found."), status_code=302)
        journal_id = int(payment["journal_id"] or 0)
        if journal_id:
            conn.execute("DELETE FROM journal_lines WHERE journal_id = ?", (journal_id,))
            conn.execute("DELETE FROM journal_entries WHERE id = ?", (journal_id,))
        conn.execute("UPDATE payroll_salary_payments SET status = 'cancelled', journal_id = NULL WHERE id = ?", (payment_id,))
        conn.commit()
        return RedirectResponse(f"/ui/hr/payroll/{run_id}?msg=" + quote("Salary payment cancelled."), status_code=302)
    except Exception as e:
        conn.rollback()
        logging.error(f"Error cancelling salary payment: {e}")
        return HTMLResponse(f"<pre>Error cancelling salary payment: {escape(str(e))}</pre>", status_code=500)
    finally:
        conn.close()


def payslip_print_block(conn, run, line, page_break=True, lang="en"):
    employee = conn.execute(
        """
        SELECT code, name, department, job_title, payment_method, bank_name, bank_account,
               national_id, insurance_number
        FROM employees
        WHERE id = ?
        LIMIT 1
        """,
        (line["employee_id"],),
    ).fetchone()
    advance_rows = conn.execute(
        """
        SELECT ead.amount, ea.advance_no
        FROM employee_advance_deductions ead
        LEFT JOIN employee_advances ea ON ea.id = ead.advance_id
        WHERE ead.payroll_run_id = ?
          AND ead.payroll_line_id = ?
        ORDER BY ead.id
        """,
        (run["id"], line["id"]),
    ).fetchall()

    def rows(items):
        html = ""
        for label, value in items:
            html += f"<tr><td>{escape(label)}</td><td class='num'>{money(value)}</td></tr>"
        return html

    L = {
        "payslip": trp(lang, "Payslip", "قسيمة مرتب"),
        "net_salary": trp(lang, "Net Salary", "صافي المرتب"),
        "employee": trp(lang, "Employee", "الموظف"),
        "department": trp(lang, "Department", "القسم"),
        "job_title": trp(lang, "Job Title", "الوظيفة"),
        "period": trp(lang, "Period", "الفترة"),
        "payment_date": trp(lang, "Payment Date", "تاريخ الصرف"),
        "payment_method": trp(lang, "Payment Method", "طريقة الدفع"),
        "bank": trp(lang, "Bank", "البنك"),
        "bank_account": trp(lang, "Bank Account", "الحساب البنكي"),
        "national_id": trp(lang, "National ID", "الرقم القومي"),
        "insurance_no": trp(lang, "Insurance No", "رقم التأمين"),
        "gross": trp(lang, "Gross", "الإجمالي"),
        "deductions": trp(lang, "Deductions", "الخصومات"),
        "employer_insurance": trp(lang, "Employer Insurance", "تأمينات الشركة"),
        "earnings": trp(lang, "Earnings", "الاستحقاقات"),
        "attendance": trp(lang, "Attendance", "الحضور"),
        "advance_details": trp(lang, "Advance Details", "تفاصيل السلف"),
        "remarks": trp(lang, "Remarks", "ملاحظات"),
        "prepared_by": trp(lang, "Prepared By", "إعداد"),
        "reviewed_by": trp(lang, "Reviewed By", "مراجعة"),
        "employee_signature": trp(lang, "Employee Signature", "توقيع الموظف"),
        "no_advance": trp(lang, "No advance details.", "لا توجد تفاصيل سلف."),
    }

    employee_code = safe(line["employee_code"]) or (safe(employee["code"]) if employee else "")
    employee_name = safe(line["employee_name"]) or (safe(employee["name"]) if employee else "")
    department = safe(line["department"]) or (safe(employee["department"]) if employee else "")
    job_title = safe(line["job_title"]) or (safe(employee["job_title"]) if employee else "")
    payment_method = safe(employee["payment_method"]) if employee else ""
    bank_name = safe(employee["bank_name"]) if employee else ""
    bank_account = safe(employee["bank_account"]) if employee else ""
    national_id = safe(employee["national_id"]) if employee else ""
    insurance_number = safe(employee["insurance_number"]) if employee else ""

    working_days = float(run["working_days_basis"] or 0)
    attendance_days = float(line["attendance_days"] or 0)
    absent_days = float(line["absent_days"] or 0)
    worked_hours = float(line["worked_hours"] or 0)
    overtime_hours = float(line["overtime_hours"] or 0)
    fixed_salary = (
        float(line["basic_salary"] or 0)
        + float(line["housing_allowance"] or 0)
        + float(line["transport_allowance"] or 0)
        + float(line["other_allowance"] or 0)
    )
    daily_rate = fixed_salary / working_days if working_days > 0 else 0
    hourly_rate = float(line["overtime_amount"] or 0) / overtime_hours if overtime_hours > 0 else 0
    total_deductions = float(line["gross_amount"] or 0) - float(line["net_amount"] or 0)

    earnings_rows = rows([
        (trp(lang, "Basic Salary", "الراتب الأساسي"), line["basic_salary"]),
        (trp(lang, "Housing Allowance", "بدل سكن"), line["housing_allowance"]),
        (trp(lang, "Transport Allowance", "بدل انتقال"), line["transport_allowance"]),
        (trp(lang, "Other Allowance", "بدلات أخرى"), line["other_allowance"]),
        (trp(lang, "Overtime", "إضافي"), line["overtime_amount"]),
        (trp(lang, "Bonus", "مكافأة"), line["bonus_amount"]),
    ])
    deduction_rows = rows([
        (trp(lang, "Manual Deduction", "خصم يدوي"), line["deduction_amount"]),
        (trp(lang, "Advance Deduction", "خصم السلفة"), line["advance_deduction"]),
        (trp(lang, "Absence Deduction", "خصم الغياب"), line["absence_deduction"]),
        (trp(lang, "Employee Insurance", "تأمينات الموظف"), line["insurance_employee_amount"]),
    ])
    attendance_rows = rows([
        (trp(lang, "Working Days Basis", "أساس أيام العمل"), working_days),
        (trp(lang, "Attendance Days", "أيام الحضور"), attendance_days),
        (trp(lang, "Absent Days", "أيام الغياب"), absent_days),
        (trp(lang, "Worked Hours", "ساعات العمل"), worked_hours),
        (trp(lang, "Overtime Hours", "ساعات الإضافي"), overtime_hours),
        (trp(lang, "Daily Rate", "قيمة اليوم"), daily_rate),
        (trp(lang, "Overtime Hour Rate", "قيمة ساعة الإضافي"), hourly_rate),
    ])
    advance_rows_html = ""
    for row in advance_rows:
        advance_rows_html += f"<tr><td>{escape(safe(row['advance_no']) or trp(lang, 'Advance', 'سلفة'))}</td><td class='num'>{money(row['amount'])}</td></tr>"
    if not advance_rows_html:
        advance_rows_html = f"<tr><td colspan='2'>{L['no_advance']}</td></tr>"

    break_style = " page-break-after: always;" if page_break else ""
    return f"""
    <section class="slip" style="{break_style}">
        <div class="slip-head">
            <div>
                <h1>{L['payslip']}</h1>
                <div class="muted">{escape(safe(run['payroll_no']))} | {int(run['payroll_month'] or 0):02d}/{escape(safe(run['payroll_year']))}</div>
            </div>
            <div class="net-box">
                <div>{L['net_salary']}</div>
                <strong>{money(line['net_amount'])}</strong>
            </div>
        </div>
        <div class="meta">
            <div><span>{L['employee']}</span><b>{escape(employee_code)} - {escape(employee_name)}</b></div>
            <div><span>{L['department']}</span><b>{escape(department)}</b></div>
            <div><span>{L['job_title']}</span><b>{escape(job_title)}</b></div>
            <div><span>{L['period']}</span><b>{escape(safe(run['period_from']))} {"إلى" if lang == "ar" else "to"} {escape(safe(run['period_to']))}</b></div>
            <div><span>{L['payment_date']}</span><b>{escape(safe(run['payment_date']))}</b></div>
            <div><span>{L['payment_method']}</span><b>{escape(payment_method)}</b></div>
            <div><span>{L['bank']}</span><b>{escape(bank_name)}</b></div>
            <div><span>{L['bank_account']}</span><b>{escape(bank_account)}</b></div>
            <div><span>{L['national_id']}</span><b>{escape(national_id)}</b></div>
            <div><span>{L['insurance_no']}</span><b>{escape(insurance_number)}</b></div>
        </div>
        <div class="totals">
            <div><span>{L['gross']}</span><b>{money(line['gross_amount'])}</b></div>
            <div><span>{L['deductions']}</span><b>{money(total_deductions)}</b></div>
            <div><span>{L['employer_insurance']}</span><b>{money(line['insurance_employer_amount'])}</b></div>
        </div>
        <div class="grid">
            <div><h2>{L['earnings']}</h2><table>{earnings_rows}</table></div>
            <div><h2>{L['deductions']}</h2><table>{deduction_rows}</table></div>
            <div><h2>{L['attendance']}</h2><table>{attendance_rows}</table></div>
            <div><h2>{L['advance_details']}</h2><table>{advance_rows_html}</table></div>
        </div>
        <div class="remarks"><b>{L['remarks']}:</b> {escape(safe(line['remarks']))}</div>
        <div class="signatures">
            <div>{L['prepared_by']}</div>
            <div>{L['reviewed_by']}</div>
            <div>{L['employee_signature']}</div>
        </div>
    </section>
    """


@router.get("/ui/hr/payroll/{run_id}/payslips/print", response_class=HTMLResponse)
def payroll_payslips_print(request: Request, run_id: int, line_id: list[int] = Query(default=[]), all: str = ""):
    ensure_payroll_tables()
    lang = get_lang(request)
    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id = ? LIMIT 1", (run_id,)).fetchone()
        if not run:
            return HTMLResponse("Payroll run not found", status_code=404)
        params = []
        if safe(all) == "1":
            where = "payroll_run_id = ?"
            params = [run_id]
        else:
            ids = [int(x) for x in line_id if int(x or 0) > 0]
            if not ids:
                return HTMLResponse("No payslips selected", status_code=400)
            placeholders = ",".join(["?"] * len(ids))
            where = f"payroll_run_id = ? AND id IN ({placeholders})"
            params = [run_id] + ids
        lines = conn.execute(
            f"SELECT * FROM payroll_lines WHERE {where} ORDER BY employee_code, employee_name, id",
            params,
        ).fetchall()
        blocks = ""
        for idx, line in enumerate(lines):
            blocks += payslip_print_block(conn, run, line, page_break=idx < len(lines) - 1, lang=lang)
        html = payslip_full_html(
            trp(lang, "Payslips", "قسائم المرتبات") + f" {safe(run['payroll_no'])}",
            blocks,
            with_toolbar=True,
            lang=lang,
        )
        return HTMLResponse(html)
    finally:
        conn.close()


@router.get("/ui/hr/payroll/{run_id}/payslips/download.zip")
def payroll_payslips_download(request: Request, run_id: int, line_id: list[int] = Query(default=[]), all: str = ""):
    ensure_payroll_tables()
    lang = get_lang(request)
    chrome = find_chrome_exe()
    if not chrome:
        return HTMLResponse("Chrome or Edge is required on this machine to export payslips as PDF.", status_code=500)

    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id = ? LIMIT 1", (run_id,)).fetchone()
        if not run:
            return HTMLResponse("Payroll run not found", status_code=404)

        params = []
        if safe(all) == "1":
            where = "payroll_run_id = ?"
            params = [run_id]
        else:
            ids = [int(x) for x in line_id if int(x or 0) > 0]
            if not ids:
                return HTMLResponse("No payslips selected", status_code=400)
            placeholders = ",".join(["?"] * len(ids))
            where = f"payroll_run_id = ? AND id IN ({placeholders})"
            params = [run_id] + ids

        lines = conn.execute(
            f"SELECT * FROM payroll_lines WHERE {where} ORDER BY employee_code, employee_name, id",
            params,
        ).fetchall()
        if not lines:
            return HTMLResponse("No payslips found", status_code=404)

        zip_buffer = BytesIO()
        month = f"{int(run['payroll_month'] or 0):02d}"
        year = safe(run["payroll_year"])
        with tempfile.TemporaryDirectory(prefix="payroll_payslips_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            pdf_files = []
            for line in lines:
                employee_name = safe(line["employee_name"]) or safe(line["employee_code"]) or f"employee_{line['id']}"
                base_name = safe_filename(f"{employee_name}_{month}_{year}")
                html_path = tmp_path / f"{base_name}_{line['id']}.html"
                pdf_path = tmp_path / f"{base_name}.pdf"
                block = payslip_print_block(conn, run, line, page_break=False, lang=lang)
                html_path.write_text(
                    payslip_full_html(trp(lang, "Payslip", "قسيمة مرتب") + f" {employee_name}", block, with_toolbar=False, lang=lang),
                    encoding="utf-8",
                )
                profile_dir = tmp_path / f"profile_{line['id']}"
                cmd = [
                    chrome,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--no-pdf-header-footer",
                    "--print-to-pdf-no-header",
                    f"--user-data-dir={profile_dir}",
                    f"--print-to-pdf={pdf_path}",
                    html_path.as_uri(),
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
                if not pdf_path.exists() or pdf_path.stat().st_size <= 0:
                    raise Exception(f"PDF was not created for {employee_name}")
                pdf_files.append(pdf_path)

            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                used_names = set()
                for pdf_path in pdf_files:
                    arc_name = pdf_path.name
                    if arc_name in used_names:
                        stem = pdf_path.stem
                        suffix = 2
                        while f"{stem}_{suffix}.pdf" in used_names:
                            suffix += 1
                        arc_name = f"{stem}_{suffix}.pdf"
                    used_names.add(arc_name)
                    zf.write(pdf_path, arc_name)

        zip_buffer.seek(0)
        zip_name = safe_filename(f"{trp(lang, 'Payslips', 'قسائم المرتبات')}_{safe(run['payroll_no'])}_{month}_{year}") + ".zip"
        headers = {"Content-Disposition": f'attachment; filename="{zip_name}"'}
        return StreamingResponse(zip_buffer, media_type="application/zip", headers=headers)
    except subprocess.TimeoutExpired:
        return HTMLResponse("PDF export timed out. Try fewer payslips at once.", status_code=500)
    except Exception as e:
        logging.error(f"Payslip PDF export error: {e}\n{traceback.format_exc()}")
        return HTMLResponse(f"Payslip PDF export error: {escape(str(e))}", status_code=500)
    finally:
        conn.close()


@router.get("/ui/hr/payroll/{run_id}/payslip/{line_id}", response_class=HTMLResponse)
def payroll_payslip(request: Request, run_id: int, line_id: int):
    ensure_payroll_tables()
    lang = get_lang(request)
    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id = ? LIMIT 1", (run_id,)).fetchone()
        line = conn.execute(
            "SELECT * FROM payroll_lines WHERE id = ? AND payroll_run_id = ? LIMIT 1",
            (line_id, run_id),
        ).fetchone()
        if not run or not line:
            return HTMLResponse("Payslip not found", status_code=404)

        block = payslip_print_block(conn, run, line, page_break=False, lang=lang)
        html = payslip_full_html(
            trp(lang, "Payslip", "قسيمة مرتب") + f" {safe(line['employee_code'])}",
            block,
            with_toolbar=True,
            lang=lang,
            back_url=f"/ui/hr/payroll/{run_id}{'?lang=ar' if lang == 'ar' else ''}",
        )
        return HTMLResponse(html)

        employee = conn.execute(
            """
            SELECT code, name, department, job_title, payment_method, bank_name, bank_account,
                   national_id, insurance_number
            FROM employees
            WHERE id = ?
            LIMIT 1
            """,
            (line["employee_id"],),
        ).fetchone()

        advance_rows = conn.execute(
            """
            SELECT ead.amount, ea.advance_no
            FROM employee_advance_deductions ead
            LEFT JOIN employee_advances ea ON ea.id = ead.advance_id
            WHERE ead.payroll_run_id = ?
              AND ead.payroll_line_id = ?
            ORDER BY ead.id
            """,
            (run_id, line_id),
        ).fetchall()

        def detail_rows(items, show_zero=True):
            rows = ""
            for label, value in items:
                amount = float(value or 0)
                if not show_zero and abs(amount) < 0.0001:
                    continue
                rows += f"""
                <tr>
                    <td>{escape(label)}</td>
                    <td class="number-cell">{money(amount)}</td>
                </tr>
                """
            return rows or "<tr><td colspan='2'>No values.</td></tr>"

        working_days = float(run["working_days_basis"] or 0)
        attendance_days = float(line["attendance_days"] or 0)
        absent_days = float(line["absent_days"] or 0)
        worked_hours = float(line["worked_hours"] or 0)
        overtime_hours = float(line["overtime_hours"] or 0)
        fixed_salary = (
            float(line["basic_salary"] or 0)
            + float(line["housing_allowance"] or 0)
            + float(line["transport_allowance"] or 0)
            + float(line["other_allowance"] or 0)
        )
        daily_rate = fixed_salary / working_days if working_days > 0 else 0
        hourly_rate = float(line["overtime_amount"] or 0) / overtime_hours if overtime_hours > 0 else 0
        total_deductions = float(line["gross_amount"] or 0) - float(line["net_amount"] or 0)

        earnings = [
            ("Basic Salary", line["basic_salary"]),
            ("Housing Allowance", line["housing_allowance"]),
            ("Transport Allowance", line["transport_allowance"]),
            ("Other Allowance", line["other_allowance"]),
            ("Overtime", line["overtime_amount"]),
            ("Bonus", line["bonus_amount"]),
        ]
        deductions = [
            ("Manual Deduction", line["deduction_amount"]),
            ("Advance Deduction", line["advance_deduction"]),
            ("Absence Deduction", line["absence_deduction"]),
            ("Employee Insurance", line["insurance_employee_amount"]),
        ]
        earnings_rows = detail_rows(earnings)
        deduction_rows = detail_rows(deductions)
        attendance_rows = detail_rows([
            ("Working Days Basis", working_days),
            ("Attendance Days", attendance_days),
            ("Absent Days", absent_days),
            ("Worked Hours", worked_hours),
            ("Overtime Hours", overtime_hours),
            ("Daily Rate", daily_rate),
            ("Overtime Hour Rate", hourly_rate),
        ])
        employer_rows = detail_rows([
            ("Employer Insurance", line["insurance_employer_amount"]),
        ])
        advance_detail_rows = ""
        for row in advance_rows:
            advance_detail_rows += f"""
            <tr>
                <td>{escape(safe(row['advance_no']) or 'Advance')}</td>
                <td class="number-cell">{money(row['amount'])}</td>
            </tr>
            """
        if not advance_detail_rows:
            advance_detail_rows = "<tr><td colspan='2'>No advance allocation details.</td></tr>"

        employee_code = safe(line["employee_code"]) or (safe(employee["code"]) if employee else "")
        employee_name = safe(line["employee_name"]) or (safe(employee["name"]) if employee else "")
        department = safe(line["department"]) or (safe(employee["department"]) if employee else "")
        job_title = safe(line["job_title"]) or (safe(employee["job_title"]) if employee else "")
        payment_method = safe(employee["payment_method"]) if employee else ""
        bank_name = safe(employee["bank_name"]) if employee else ""
        bank_account = safe(employee["bank_account"]) if employee else ""
        national_id = safe(employee["national_id"]) if employee else ""
        insurance_number = safe(employee["insurance_number"]) if employee else ""

        html = f"""
        <style>
        .payslip-sheet {{ max-width: 1120px; margin: 0 auto; }}
        .payslip-title {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; flex-wrap:wrap; }}
        .payslip-meta {{ display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:10px; margin-top:16px; }}
        .payslip-box {{ border:1px solid #dbe5f2; border-radius:10px; padding:12px 14px; background:#fff; min-height:70px; }}
        .payslip-label {{ color:#60789a; font-size:12px; font-weight:800; text-transform:uppercase; }}
        .payslip-value {{ color:#0b2d57; font-size:16px; font-weight:800; margin-top:6px; word-break:break-word; }}
        .payslip-grid {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:14px; }}
        .payslip-table h3 {{ margin:0 0 10px; }}
        .payslip-table table td:first-child {{ font-weight:700; color:#17355c; }}
        .payslip-table table td {{ padding:11px 12px; }}
        .net-strip {{ display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:12px; margin-top:14px; }}
        .signature-row {{ display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:24px; margin-top:34px; }}
        .signature-line {{ border-top:1px solid #17355c; padding-top:8px; text-align:center; font-weight:800; color:#17355c; }}
        @media print {{
            .top-actions, .sidebar, .app-sidebar, nav, .no-print {{ display:none !important; }}
            body {{ background:#fff !important; }}
            .card {{ box-shadow:none !important; border:1px solid #ddd; }}
            .payslip-sheet {{ max-width:none; }}
            .payslip-box {{ break-inside:avoid; }}
        }}
        @media (max-width: 900px) {{
            .payslip-meta, .payslip-grid, .net-strip, .signature-row {{ grid-template-columns:1fr; }}
        }}
        </style>
        <div class="payslip-sheet">
        <div class="card">
            <div class="payslip-title">
                <div>
                    <h2>Payslip - {escape(employee_name)}</h2>
                    <p><b>Payroll:</b> {escape(safe(run['payroll_no']))} | <b>Month:</b> {int(run['payroll_month'] or 0):02d}/{escape(safe(run['payroll_year']))}</p>
                    <p><b>Period:</b> {escape(safe(run['period_from']))} to {escape(safe(run['period_to']))}</p>
                </div>
                <div class="no-print" style="display:flex;gap:8px;">
                    <button class="btn green" onclick="window.print()">Print</button>
                    <a class="btn gray" href="/ui/hr/payroll/{run_id}">Back</a>
                </div>
            </div>
            <div class="payslip-meta">
                <div class="payslip-box"><div class="payslip-label">Employee</div><div class="payslip-value">{escape(employee_code)} - {escape(employee_name)}</div></div>
                <div class="payslip-box"><div class="payslip-label">Department</div><div class="payslip-value">{escape(department)}</div></div>
                <div class="payslip-box"><div class="payslip-label">Job Title</div><div class="payslip-value">{escape(job_title)}</div></div>
                <div class="payslip-box"><div class="payslip-label">Payment Date</div><div class="payslip-value">{escape(safe(run['payment_date']))}</div></div>
                <div class="payslip-box"><div class="payslip-label">Payment Method</div><div class="payslip-value">{escape(payment_method)}</div></div>
                <div class="payslip-box"><div class="payslip-label">Bank</div><div class="payslip-value">{escape(bank_name)}</div></div>
                <div class="payslip-box"><div class="payslip-label">Bank Account</div><div class="payslip-value">{escape(bank_account)}</div></div>
                <div class="payslip-box"><div class="payslip-label">National / Insurance</div><div class="payslip-value">{escape(national_id)} {escape(insurance_number)}</div></div>
            </div>
        </div>
        <div class="card">
            <div class="payslip-grid">
                <div class="payslip-table">
                    <h3>Earnings</h3>
                    <table><tbody>{earnings_rows}</tbody></table>
                </div>
                <div class="payslip-table">
                    <h3>Deductions</h3>
                    <table><tbody>{deduction_rows}</tbody></table>
                </div>
            </div>
            <div class="net-strip">
                <div class="payslip-box"><div class="payslip-label">Gross Salary</div><div class="payslip-value">{money(line['gross_amount'])}</div></div>
                <div class="payslip-box"><div class="payslip-label">Total Deductions</div><div class="payslip-value">{money(total_deductions)}</div></div>
                <div class="payslip-box"><div class="payslip-label">Net Salary</div><div class="payslip-value">{money(line['net_amount'])}</div></div>
            </div>
        </div>
        <div class="card">
            <div class="payslip-grid">
                <div class="payslip-table">
                    <h3>Attendance Calculation</h3>
                    <table><tbody>{attendance_rows}</tbody></table>
                </div>
                <div class="payslip-table">
                    <h3>Advance Details</h3>
                    <table><tbody>{advance_detail_rows}</tbody></table>
                </div>
                <div class="payslip-table">
                    <h3>Company Cost</h3>
                    <table><tbody>{employer_rows}</tbody></table>
                </div>
                <div class="payslip-table">
                    <h3>Remarks</h3>
                    <table><tbody><tr><td>{escape(safe(line['remarks']))}</td></tr></tbody></table>
                </div>
            </div>
            <div class="signature-row">
                <div class="signature-line">Prepared By</div>
                <div class="signature-line">Reviewed By</div>
                <div class="signature-line">Employee Signature</div>
            </div>
        </div>
        </div>
        """
        return HTMLResponse(render_page("Payslip", html, "en", current_path=request.url.path))
    finally:
        conn.close()


def hr_adjustment_list_page(request, kind):
    ensure_payroll_tables()
    is_reward = kind == "reward"
    table = "employee_rewards" if is_reward else "employee_penalties"
    no_col = "reward_no" if is_reward else "penalty_no"
    date_col = "reward_date" if is_reward else "penalty_date"
    base_path = "/ui/hr/employee-rewards" if is_reward else "/ui/hr/employee-penalties"
    title = "Employee Rewards" if is_reward else "Employee Penalties"
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT a.*, e.code AS employee_code, e.name AS employee_name
            FROM {table} a
            LEFT JOIN employees e ON e.id = a.employee_id
            ORDER BY a.id DESC
            """
        ).fetchall()
        msg = safe(request.query_params.get("msg"))
        msg_html = f'<div class="msg success">{escape(msg)}</div>' if msg else ""
        body = ""
        for row in rows:
            status = safe(row["status"]) or "draft"
            status_cls = "green" if status.lower() == "applied" else "orange"
            actions = f'<a class="btn blue" href="{base_path}/{row["id"]}">Open</a>'
            if status.lower() in ("draft", "open"):
                actions += f"""
                    <form method="post" action="{base_path}/{row['id']}/delete" style="display:inline;"
                          onsubmit="return confirm('Delete this draft record?');">
                        <button class="btn red" type="submit">Delete</button>
                    </form>
                """
            body += f"""
            <tr>
                <td><a class="btn gray" href="{base_path}/{row['id']}">{escape(safe(row[no_col]))}</a></td>
                <td>{escape(safe(row[date_col]))}</td>
                <td>{escape(safe(row['employee_code']))} - {escape(safe(row['employee_name']))}</td>
                <td class="number-cell">{money(row['amount'])}</td>
                <td>{escape(safe(row['reason']))}</td>
                <td><span class="status-chip {status_cls}">{escape(status)}</span></td>
                <td>{actions}</td>
            </tr>
            """
        if not body:
            body = "<tr><td colspan='7' style='text-align:center;'>No records found.</td></tr>"
        html = f"""
        <div class="card">
            {msg_html}
            <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
                <h2>{title}</h2>
                <a class="btn green" href="{base_path}/new">+ New</a>
            </div>
        </div>
        <div class="card">
            <table>
                <tr>
                    <th>No</th><th>Date</th><th>Employee</th><th>Amount</th><th>Reason</th><th>Status</th><th>Action</th>
                </tr>
                {body}
            </table>
        </div>
        """
        return HTMLResponse(render_page(title, html, "en", current_path=request.url.path))
    finally:
        conn.close()


def hr_adjustment_new_page(request, kind, error=""):
    ensure_payroll_tables()
    is_reward = kind == "reward"
    base_path = "/ui/hr/employee-rewards" if is_reward else "/ui/hr/employee-penalties"
    title = "New Employee Reward" if is_reward else "New Employee Penalty"
    doc_no = next_reward_no() if is_reward else next_penalty_no()
    conn = get_conn()
    try:
        error_html = f'<div class="msg error">{escape(error)}</div>' if error else ""
        html = f"""
        <div class="card">
            {error_html}
            <h2>{title}</h2>
            <form method="post" action="{base_path}/new" enctype="multipart/form-data">
                <div class="row">
                    <div class="col">
                        <label>No</label>
                        <input name="doc_no" value="{doc_no}" readonly>
                    </div>
                    <div class="col">
                        <label>Date</label>
                        <input type="date" name="doc_date" value="{date.today().isoformat()}" required>
                    </div>
                    <div class="col">
                        <label>Employee</label>
                        <select name="employee_id" required>{employee_select_options(conn)}</select>
                    </div>
                </div>
                <div class="row">
                    <div class="col">
                        <label>Amount</label>
                        <input type="number" step="0.01" min="0.01" name="amount" required>
                    </div>
                    <div class="col">
                        <label>Attachment</label>
                        <input type="file" name="invoice_attachments" accept=".pdf,image/*" required>
                    </div>
                </div>
                <label>Reason</label>
                <input name="reason">
                <div style="margin-top:16px;">
                    <button class="btn green" type="submit">Save</button>
                    <a class="btn gray" href="{base_path}">Back</a>
                </div>
            </form>
        </div>
        """
        return HTMLResponse(render_page(title, html, "en", current_path=request.url.path))
    finally:
        conn.close()


async def hr_adjustment_create(request, kind):
    ensure_payroll_tables()
    is_reward = kind == "reward"
    table = "employee_rewards" if is_reward else "employee_penalties"
    no_col = "reward_no" if is_reward else "penalty_no"
    date_col = "reward_date" if is_reward else "penalty_date"
    base_path = "/ui/hr/employee-rewards" if is_reward else "/ui/hr/employee-penalties"
    form = await request.form()
    attachments = await attachments_from_form(form)
    if not attachments:
        return hr_adjustment_new_page(request, kind, "Attachment is required.")
    doc_no = safe(form.get("doc_no")) or (next_reward_no() if is_reward else next_penalty_no())
    doc_date = safe(form.get("doc_date")) or date.today().isoformat()
    employee_id = int(to_float(form.get("employee_id")))
    amount = to_float(form.get("amount"))
    reason = safe(form.get("reason"))
    if not employee_id or amount <= 0:
        return hr_adjustment_new_page(request, kind, "Employee and amount are required.")
    first = attachments[0]
    conn = get_conn()
    try:
        cur = conn.execute(
            f"""
            INSERT INTO {table} (
                {no_col}, {date_col}, employee_id, amount, reason,
                attachment_url, attachment_name, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')
            """,
            (doc_no, doc_date, employee_id, amount, reason, first["file_url"], first["file_name"]),
        )
        conn.commit()
        return RedirectResponse(f"{base_path}/{cur.lastrowid}?msg=" + quote("Saved."), status_code=302)
    except Exception as e:
        conn.rollback()
        return HTMLResponse(f"Save error: {escape(str(e))}", status_code=400)
    finally:
        conn.close()


def hr_adjustment_open_page(request, kind, record_id):
    ensure_payroll_tables()
    is_reward = kind == "reward"
    table = "employee_rewards" if is_reward else "employee_penalties"
    no_col = "reward_no" if is_reward else "penalty_no"
    date_col = "reward_date" if is_reward else "penalty_date"
    base_path = "/ui/hr/employee-rewards" if is_reward else "/ui/hr/employee-penalties"
    title = "Employee Reward" if is_reward else "Employee Penalty"
    conn = get_conn()
    try:
        row = conn.execute(
            f"""
            SELECT a.*, e.code AS employee_code, e.name AS employee_name
            FROM {table} a
            LEFT JOIN employees e ON e.id = a.employee_id
            WHERE a.id = ?
            LIMIT 1
            """,
            (record_id,),
        ).fetchone()
        if not row:
            return HTMLResponse("Record not found", status_code=404)
        msg = safe(request.query_params.get("msg"))
        msg_html = f'<div class="msg success">{escape(msg)}</div>' if msg else ""
        actions = ""
        if safe(row["status"]).lower() in ("draft", "open"):
            actions = f"""
                <form method="post" action="{base_path}/{record_id}/delete" style="display:inline;"
                      onsubmit="return confirm('Delete this draft record?');">
                    <button class="btn red" type="submit">Delete</button>
                </form>
            """
        attachments_html = attachment_gallery([{"file_url": row["attachment_url"], "file_name": row["attachment_name"]}])
        html = f"""
        <div class="card">
            {msg_html}
            <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start;">
                <div>
                    <h2>{title} {escape(safe(row[no_col]))}</h2>
                    <p><b>Date:</b> {escape(safe(row[date_col]))}</p>
                    <p><b>Employee:</b> {escape(safe(row['employee_code']))} - {escape(safe(row['employee_name']))}</p>
                    <p><b>Amount:</b> {money(row['amount'])}</p>
                    <p><b>Reason:</b> {escape(safe(row['reason']))}</p>
                    <p><b>Status:</b> <span class="status-chip orange">{escape(safe(row['status']))}</span></p>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    {actions}
                    <a class="btn gray" href="{base_path}">Back</a>
                </div>
            </div>
        </div>
        <div class="card">
            <h3>Attachment</h3>
            {attachments_html}
        </div>
        """
        return HTMLResponse(render_page(title, html, "en", current_path=request.url.path))
    finally:
        conn.close()


def hr_adjustment_delete(kind, record_id):
    ensure_payroll_tables()
    is_reward = kind == "reward"
    table = "employee_rewards" if is_reward else "employee_penalties"
    base_path = "/ui/hr/employee-rewards" if is_reward else "/ui/hr/employee-penalties"
    conn = get_conn()
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ? LIMIT 1", (record_id,)).fetchone()
        if not row:
            return RedirectResponse(f"{base_path}?msg=" + quote("Record not found."), status_code=302)
        if safe(row["status"]).lower() not in ("draft", "open"):
            return RedirectResponse(f"{base_path}?msg=" + quote("Applied records cannot be deleted."), status_code=302)
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
        conn.commit()
        return RedirectResponse(f"{base_path}?msg=" + quote("Deleted."), status_code=302)
    except Exception as e:
        conn.rollback()
        return HTMLResponse(f"Delete error: {escape(str(e))}", status_code=400)
    finally:
        conn.close()


@router.get("/ui/hr/employee-rewards", response_class=HTMLResponse)
def employee_rewards_list(request: Request):
    return hr_adjustment_list_page(request, "reward")


@router.get("/ui/hr/employee-rewards/new", response_class=HTMLResponse)
def employee_reward_new(request: Request):
    return hr_adjustment_new_page(request, "reward")


@router.post("/ui/hr/employee-rewards/new")
async def employee_reward_create(request: Request):
    return await hr_adjustment_create(request, "reward")


@router.get("/ui/hr/employee-rewards/{record_id}", response_class=HTMLResponse)
def employee_reward_open(request: Request, record_id: int):
    return hr_adjustment_open_page(request, "reward", record_id)


@router.post("/ui/hr/employee-rewards/{record_id}/delete")
def employee_reward_delete(record_id: int):
    return hr_adjustment_delete("reward", record_id)


@router.get("/ui/hr/employee-penalties", response_class=HTMLResponse)
def employee_penalties_list(request: Request):
    return hr_adjustment_list_page(request, "penalty")


@router.get("/ui/hr/employee-penalties/new", response_class=HTMLResponse)
def employee_penalty_new(request: Request):
    return hr_adjustment_new_page(request, "penalty")


@router.post("/ui/hr/employee-penalties/new")
async def employee_penalty_create(request: Request):
    return await hr_adjustment_create(request, "penalty")


@router.get("/ui/hr/employee-penalties/{record_id}", response_class=HTMLResponse)
def employee_penalty_open(request: Request, record_id: int):
    return hr_adjustment_open_page(request, "penalty", record_id)


@router.post("/ui/hr/employee-penalties/{record_id}/delete")
def employee_penalty_delete(record_id: int):
    return hr_adjustment_delete("penalty", record_id)


@router.get("/ui/hr/employee-grants", response_class=HTMLResponse)
def employee_grants_list(request: Request):
    ensure_payroll_tables()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT g.*, COUNT(l.id) AS employee_count
            FROM employee_grants g
            LEFT JOIN employee_grant_lines l ON l.grant_id = g.id
            GROUP BY g.id
            ORDER BY g.id DESC
            """
        ).fetchall()
        msg = safe(request.query_params.get("msg"))
        msg_html = f'<div class="msg success">{escape(msg)}</div>' if msg else ""
        body = ""
        for row in rows:
            status_cls = "green" if safe(row["status"]).lower() in ("payment_requested", "paid") else "orange"
            draft_actions = ""
            if safe(row["status"]).lower() == "draft":
                draft_actions = f"""
                    <a class="btn gray" href="/ui/hr/employee-grants/{row['id']}/edit">Edit</a>
                    <form method="post" action="/ui/hr/employee-grants/{row['id']}/delete" style="display:inline;"
                          onsubmit="return confirm('Delete this draft grant?');">
                        <button class="btn red" type="submit">Delete</button>
                    </form>
                """
            body += f"""
            <tr>
                <td><a class="btn gray" href="/ui/hr/employee-grants/{row['id']}">{escape(safe(row['grant_no']))}</a></td>
                <td>{escape(safe(row['grant_date']))}</td>
                <td>{escape(safe(row['description']))}</td>
                <td>{escape(safe(row['calculation_type']))}</td>
                <td>{int(row['employee_count'] or 0)}</td>
                <td class="number-cell">{money(row['total_amount'])}</td>
                <td><span class="status-chip {status_cls}">{escape(safe(row['status']))}</span></td>
                <td>
                    <a class="btn blue" href="/ui/hr/employee-grants/{row['id']}">Open</a>
                    {draft_actions}
                </td>
            </tr>
            """
        if not body:
            body = "<tr><td colspan='8' style='text-align:center;'>No grants found.</td></tr>"
        html = f"""
        <div class="card">
            {msg_html}
            <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
                <h2>Employee Grants</h2>
                <a class="btn green" href="/ui/hr/employee-grants/new">+ New Grant</a>
            </div>
        </div>
        <div class="card">
            <table>
                <tr>
                    <th>No</th>
                    <th>Date</th>
                    <th>Description</th>
                    <th>Calculation</th>
                    <th>Employees</th>
                    <th>Total</th>
                    <th>Status</th>
                    <th>Action</th>
                </tr>
                {body}
            </table>
        </div>
        """
        return HTMLResponse(render_page("Employee Grants", html, "en", current_path=request.url.path))
    finally:
        conn.close()


@router.get("/ui/hr/employee-grants/new", response_class=HTMLResponse)
def employee_grant_new(request: Request):
    ensure_payroll_tables()
    today = date.today().isoformat()
    html = f"""
    <div class="card">
        <h2>New Employee Grant</h2>
        <form method="post" action="/ui/hr/employee-grants/new">
            <div class="row">
                <div class="col">
                    <label>Grant No</label>
                    <input name="grant_no" value="{next_grant_no()}" readonly>
                </div>
                <div class="col">
                    <label>Date</label>
                    <input type="date" name="grant_date" value="{today}" required>
                </div>
                <div class="col">
                    <label>Description</label>
                    <input name="description" placeholder="Bonus / Eid grant / exceptional grant">
                </div>
            </div>
            <div class="row">
                <div class="col">
                    <label>Apply To</label>
                    <select name="employee_scope">
                        <option value="all_active">All Active Employees</option>
                    </select>
                </div>
                <div class="col">
                    <label>Calculation</label>
                    <select name="calculation_type" id="grant_calc_type" onchange="toggleGrantCalc()">
                        <option value="fixed">Fixed Amount For Each Employee</option>
                        <option value="percent">Percentage From Salary</option>
                    </select>
                </div>
                <div class="col" id="grant_fixed_wrap">
                    <label>Fixed Amount</label>
                    <input type="number" step="0.01" min="0" name="fixed_amount" value="0">
                </div>
                <div class="col" id="grant_percent_wrap" style="display:none;">
                    <label>Percent %</label>
                    <input type="number" step="0.01" min="0" name="percent_rate" value="0">
                </div>
                <div class="col" id="grant_base_wrap" style="display:none;">
                    <label>Salary Base</label>
                    <select name="salary_base">
                        <option value="fixed_salary">Fixed Salary + Allowances</option>
                        <option value="basic_salary">Basic Salary Only</option>
                    </select>
                </div>
            </div>
            <div style="margin-top:16px;">
                <button class="btn green" type="submit">Create Grant Lines</button>
                <a class="btn gray" href="/ui/hr/employee-grants">Back</a>
            </div>
        </form>
    </div>
    <script>
    function toggleGrantCalc() {{
        const type = document.getElementById('grant_calc_type').value;
        document.getElementById('grant_fixed_wrap').style.display = type === 'fixed' ? 'block' : 'none';
        document.getElementById('grant_percent_wrap').style.display = type === 'percent' ? 'block' : 'none';
        document.getElementById('grant_base_wrap').style.display = type === 'percent' ? 'block' : 'none';
    }}
    toggleGrantCalc();
    </script>
    """
    return HTMLResponse(render_page("New Employee Grant", html, "en", current_path=request.url.path))


@router.post("/ui/hr/employee-grants/new")
async def employee_grant_create(request: Request):
    ensure_payroll_tables()
    form = await request.form()
    grant_no = safe(form.get("grant_no")) or next_grant_no()
    grant_date = safe(form.get("grant_date")) or date.today().isoformat()
    description = safe(form.get("description"))
    calculation_type = safe(form.get("calculation_type")).lower() or "fixed"
    fixed_amount = to_float(form.get("fixed_amount"))
    percent_rate = to_float(form.get("percent_rate"))
    salary_base = safe(form.get("salary_base")) or "fixed_salary"
    conn = get_conn()
    try:
        employees = conn.execute(
            """
            SELECT id, code, name, department, job_title,
                   basic_salary, housing_allowance, transport_allowance, other_allowance
            FROM employees
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY code, name
            """
        ).fetchall()
        if not employees:
            return HTMLResponse("No active employees found.", status_code=400)
        cur = conn.execute(
            """
            INSERT INTO employee_grants (
                grant_no, grant_date, description, calculation_type,
                fixed_amount, percent_rate, salary_base, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')
            """,
            (grant_no, grant_date, description, calculation_type, fixed_amount, percent_rate, salary_base),
        )
        grant_id = cur.lastrowid
        total = 0.0
        for emp in employees:
            base = grant_base_amount(emp, salary_base)
            amount = fixed_amount if calculation_type == "fixed" else (base * percent_rate / 100.0)
            amount = round(float(amount or 0), 2)
            if amount <= 0:
                continue
            total += amount
            conn.execute(
                """
                INSERT INTO employee_grant_lines (
                    grant_id, employee_id, employee_code, employee_name, department, job_title,
                    base_amount, grant_amount, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')
                """,
                (
                    grant_id,
                    emp["id"],
                    safe(emp["code"]),
                    safe(emp["name"]),
                    safe(emp["department"]),
                    safe(emp["job_title"]),
                    base,
                    amount,
                ),
            )
        conn.execute("UPDATE employee_grants SET total_amount = ? WHERE id = ?", (total, grant_id))
        conn.commit()
        return RedirectResponse(f"/ui/hr/employee-grants/{grant_id}", status_code=302)
    except Exception as e:
        conn.rollback()
        return HTMLResponse(f"Grant save error: {escape(str(e))}", status_code=400)
    finally:
        conn.close()


@router.get("/ui/hr/employee-grants/{grant_id}/edit", response_class=HTMLResponse)
def employee_grant_edit(request: Request, grant_id: int):
    ensure_payroll_tables()
    conn = get_conn()
    try:
        grant = conn.execute("SELECT * FROM employee_grants WHERE id = ? LIMIT 1", (grant_id,)).fetchone()
        if not grant:
            return HTMLResponse("Grant not found", status_code=404)
        if safe(grant["status"]).lower() != "draft":
            return RedirectResponse("/ui/hr/employee-grants?msg=" + quote("Only draft grants can be edited."), status_code=302)
        calc_type = safe(grant["calculation_type"]).lower() or "fixed"
        fixed_selected = "selected" if calc_type == "fixed" else ""
        percent_selected = "selected" if calc_type == "percent" else ""
        fixed_display = "block" if calc_type == "fixed" else "none"
        percent_display = "block" if calc_type == "percent" else "none"
        fixed_base_selected = "selected" if safe(grant["salary_base"]) != "basic_salary" else ""
        basic_base_selected = "selected" if safe(grant["salary_base"]) == "basic_salary" else ""
        html = f"""
        <div class="card">
            <h2>Edit Employee Grant</h2>
            <form method="post" action="/ui/hr/employee-grants/{grant_id}/edit">
                <div class="row">
                    <div class="col">
                        <label>Grant No</label>
                        <input name="grant_no" value="{escape(safe(grant['grant_no']))}" readonly>
                    </div>
                    <div class="col">
                        <label>Date</label>
                        <input type="date" name="grant_date" value="{escape(safe(grant['grant_date']))}" required>
                    </div>
                    <div class="col">
                        <label>Description</label>
                        <input name="description" value="{escape(safe(grant['description']))}">
                    </div>
                </div>
                <div class="row">
                    <div class="col">
                        <label>Apply To</label>
                        <select name="employee_scope">
                            <option value="all_active">All Active Employees</option>
                        </select>
                    </div>
                    <div class="col">
                        <label>Calculation</label>
                        <select name="calculation_type" id="grant_calc_type" onchange="toggleGrantCalc()">
                            <option value="fixed" {fixed_selected}>Fixed Amount For Each Employee</option>
                            <option value="percent" {percent_selected}>Percentage From Salary</option>
                        </select>
                    </div>
                    <div class="col" id="grant_fixed_wrap" style="display:{fixed_display};">
                        <label>Fixed Amount</label>
                        <input type="number" step="0.01" min="0" name="fixed_amount" value="{float(grant['fixed_amount'] or 0)}">
                    </div>
                    <div class="col" id="grant_percent_wrap" style="display:{percent_display};">
                        <label>Percent %</label>
                        <input type="number" step="0.01" min="0" name="percent_rate" value="{float(grant['percent_rate'] or 0)}">
                    </div>
                    <div class="col" id="grant_base_wrap" style="display:{percent_display};">
                        <label>Salary Base</label>
                        <select name="salary_base">
                            <option value="fixed_salary" {fixed_base_selected}>Fixed Salary + Allowances</option>
                            <option value="basic_salary" {basic_base_selected}>Basic Salary Only</option>
                        </select>
                    </div>
                </div>
                <div style="margin-top:16px;">
                    <button class="btn green" type="submit">Save</button>
                    <a class="btn gray" href="/ui/hr/employee-grants/{grant_id}">Back</a>
                </div>
            </form>
        </div>
        <script>
        function toggleGrantCalc() {{
            const type = document.getElementById('grant_calc_type').value;
            document.getElementById('grant_fixed_wrap').style.display = type === 'fixed' ? 'block' : 'none';
            document.getElementById('grant_percent_wrap').style.display = type === 'percent' ? 'block' : 'none';
            document.getElementById('grant_base_wrap').style.display = type === 'percent' ? 'block' : 'none';
        }}
        </script>
        """
        return HTMLResponse(render_page("Edit Employee Grant", html, "en", current_path=request.url.path))
    finally:
        conn.close()


@router.post("/ui/hr/employee-grants/{grant_id}/edit")
async def employee_grant_update(request: Request, grant_id: int):
    ensure_payroll_tables()
    form = await request.form()
    grant_date = safe(form.get("grant_date")) or date.today().isoformat()
    description = safe(form.get("description"))
    calculation_type = safe(form.get("calculation_type")).lower() or "fixed"
    fixed_amount = to_float(form.get("fixed_amount"))
    percent_rate = to_float(form.get("percent_rate"))
    salary_base = safe(form.get("salary_base")) or "fixed_salary"
    conn = get_conn()
    try:
        grant = conn.execute("SELECT * FROM employee_grants WHERE id = ? LIMIT 1", (grant_id,)).fetchone()
        if not grant:
            return HTMLResponse("Grant not found", status_code=404)
        if safe(grant["status"]).lower() != "draft":
            return RedirectResponse("/ui/hr/employee-grants?msg=" + quote("Only draft grants can be edited."), status_code=302)
        employees = conn.execute(
            """
            SELECT id, code, name, department, job_title,
                   basic_salary, housing_allowance, transport_allowance, other_allowance
            FROM employees
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY code, name
            """
        ).fetchall()
        if not employees:
            return HTMLResponse("No active employees found.", status_code=400)
        conn.execute("DELETE FROM employee_grant_lines WHERE grant_id = ?", (grant_id,))
        total = 0.0
        for emp in employees:
            base = grant_base_amount(emp, salary_base)
            amount = fixed_amount if calculation_type == "fixed" else (base * percent_rate / 100.0)
            amount = round(float(amount or 0), 2)
            if amount <= 0:
                continue
            total += amount
            conn.execute(
                """
                INSERT INTO employee_grant_lines (
                    grant_id, employee_id, employee_code, employee_name, department, job_title,
                    base_amount, grant_amount, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')
                """,
                (
                    grant_id,
                    emp["id"],
                    safe(emp["code"]),
                    safe(emp["name"]),
                    safe(emp["department"]),
                    safe(emp["job_title"]),
                    base,
                    amount,
                ),
            )
        conn.execute(
            """
            UPDATE employee_grants
            SET grant_date = ?, description = ?, calculation_type = ?,
                fixed_amount = ?, percent_rate = ?, salary_base = ?, total_amount = ?
            WHERE id = ?
            """,
            (grant_date, description, calculation_type, fixed_amount, percent_rate, salary_base, total, grant_id),
        )
        conn.commit()
        return RedirectResponse(f"/ui/hr/employee-grants/{grant_id}?msg=" + quote("Grant updated."), status_code=302)
    except Exception as e:
        conn.rollback()
        return HTMLResponse(f"Grant update error: {escape(str(e))}", status_code=400)
    finally:
        conn.close()


@router.post("/ui/hr/employee-grants/{grant_id}/delete")
def employee_grant_delete(grant_id: int):
    ensure_payroll_tables()
    conn = get_conn()
    try:
        grant = conn.execute("SELECT * FROM employee_grants WHERE id = ? LIMIT 1", (grant_id,)).fetchone()
        if not grant:
            return RedirectResponse("/ui/hr/employee-grants?msg=" + quote("Grant not found."), status_code=302)
        if safe(grant["status"]).lower() != "draft":
            return RedirectResponse("/ui/hr/employee-grants?msg=" + quote("Only draft grants can be deleted."), status_code=302)
        conn.execute("DELETE FROM employee_grant_lines WHERE grant_id = ?", (grant_id,))
        conn.execute("DELETE FROM employee_grants WHERE id = ?", (grant_id,))
        conn.commit()
        return RedirectResponse("/ui/hr/employee-grants?msg=" + quote("Draft grant deleted."), status_code=302)
    except Exception as e:
        conn.rollback()
        return HTMLResponse(f"Grant delete error: {escape(str(e))}", status_code=400)
    finally:
        conn.close()


@router.get("/ui/hr/employee-grants/{grant_id}", response_class=HTMLResponse)
def employee_grant_open(request: Request, grant_id: int):
    ensure_payroll_tables()
    conn = get_conn()
    try:
        grant = conn.execute("SELECT * FROM employee_grants WHERE id = ? LIMIT 1", (grant_id,)).fetchone()
        if not grant:
            return HTMLResponse("Grant not found", status_code=404)
        lines = conn.execute(
            "SELECT * FROM employee_grant_lines WHERE grant_id = ? ORDER BY employee_code, employee_name, id",
            (grant_id,),
        ).fetchall()
        rows = ""
        for line in lines:
            rows += f"""
            <tr>
                <td>{escape(safe(line['employee_code']))}</td>
                <td>{escape(safe(line['employee_name']))}</td>
                <td>{escape(safe(line['department']))}</td>
                <td>{escape(safe(line['job_title']))}</td>
                <td class="number-cell">{money(line['base_amount'])}</td>
                <td class="number-cell">{money(line['grant_amount'])}</td>
            </tr>
            """
        if not rows:
            rows = "<tr><td colspan='6' style='text-align:center;'>No grant lines.</td></tr>"
        voucher_link = ""
        if grant["payment_voucher_id"]:
            voucher_link = f"<a class='btn blue' href='/ui/accounting/cash-payments/{int(grant['payment_voucher_id'])}'>Open Payment Voucher</a>"
        create_payment = ""
        if not grant["payment_voucher_id"] and float(grant["total_amount"] or 0) > 0:
            create_payment = f"""
            <form method="post" action="/ui/hr/employee-grants/{grant_id}/create-payment-voucher" style="display:inline;">
                <button class="btn green" type="submit">Create Payment Voucher</button>
            </form>
            """
        draft_actions = ""
        if safe(grant["status"]).lower() == "draft":
            draft_actions = f"""
                <a class="btn blue" href="/ui/hr/employee-grants/{grant_id}/edit">Edit</a>
                <form method="post" action="/ui/hr/employee-grants/{grant_id}/delete" style="display:inline;"
                      onsubmit="return confirm('Delete this draft grant?');">
                    <button class="btn red" type="submit">Delete</button>
                </form>
            """
        html = f"""
        <div class="card">
            <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start;">
                <div>
                    <h2>Employee Grant {escape(safe(grant['grant_no']))}</h2>
                    <p><b>Date:</b> {escape(safe(grant['grant_date']))}</p>
                    <p><b>Description:</b> {escape(safe(grant['description']))}</p>
                    <p><b>Calculation:</b> {escape(safe(grant['calculation_type']))}</p>
                    <p><b>Total:</b> {money(grant['total_amount'])}</p>
                    <p><b>Status:</b> <span class="status-chip orange">{escape(safe(grant['status']))}</span></p>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    {draft_actions}
                    {create_payment}
                    {voucher_link}
                    <a class="btn gray" href="/ui/hr/employee-grants">Back</a>
                </div>
            </div>
        </div>
        <div class="card">
            <table>
                <tr>
                    <th>Code</th>
                    <th>Employee</th>
                    <th>Department</th>
                    <th>Job Title</th>
                    <th>Base Amount</th>
                    <th>Grant Amount</th>
                </tr>
                {rows}
            </table>
        </div>
        """
        return HTMLResponse(render_page("Employee Grant", html, "en", current_path=request.url.path))
    finally:
        conn.close()


@router.post("/ui/hr/employee-grants/{grant_id}/create-payment-voucher")
def employee_grant_create_payment_voucher(grant_id: int):
    ensure_payroll_tables()
    ensure_cash_voucher_tables()
    conn = get_conn()
    try:
        grant = conn.execute("SELECT * FROM employee_grants WHERE id = ? LIMIT 1", (grant_id,)).fetchone()
        if not grant:
            return HTMLResponse("Grant not found", status_code=404)
        if grant["payment_voucher_id"]:
            return RedirectResponse(f"/ui/accounting/cash-payments/{int(grant['payment_voucher_id'])}", status_code=302)
        total = float(grant["total_amount"] or 0)
        if total <= 0:
            return RedirectResponse(f"/ui/hr/employee-grants/{grant_id}?msg=" + quote("Grant total is zero."), status_code=302)
        liquidity_account = get_setting_value("default_cash_account", "1020101", conn=conn)
        grant_expense_account = get_setting_value(
            "employee_grant_expense_account",
            get_setting_value("payroll_bonus_account", "60105", conn=conn),
            conn=conn,
        )
        voucher_no = next_voucher_no("payment")
        cur = conn.execute(
            """
            INSERT INTO cash_vouchers (
                voucher_type, voucher_no, voucher_date, party_name, party_type, party_id,
                liquidity_account_code, counter_account_code, amount, description, signature_name,
                source_type, source_id, status
            )
            VALUES ('payment', ?, ?, ?, 'other', NULL, ?, ?, ?, ?, '', 'employee_grant', ?, 'draft')
            """,
            (
                voucher_no,
                safe(grant["grant_date"]) or date.today().isoformat(),
                f"Employee Grant {safe(grant['grant_no'])}",
                liquidity_account,
                grant_expense_account,
                total,
                safe(grant["description"]) or f"Employee Grant {safe(grant['grant_no'])}",
                grant_id,
            ),
        )
        voucher_id = cur.lastrowid
        create_cash_voucher_draft_journal(conn, voucher_id)
        conn.execute(
            "UPDATE employee_grants SET status = 'payment_requested', payment_voucher_id = ? WHERE id = ?",
            (voucher_id, grant_id),
        )
        conn.commit()
        return RedirectResponse(
            f"/ui/hr/employee-grants/{grant_id}?msg=" + quote(f"Payment voucher {voucher_no} created. Execute it from Cash Payments module."),
            status_code=302,
        )
    except Exception as e:
        conn.rollback()
        return HTMLResponse(f"Payment voucher error: {escape(str(e))}", status_code=400)
    finally:
        conn.close()


@router.get("/ui/hr/payroll/{run_id}/edit", response_class=HTMLResponse)
def payroll_edit(request: Request, run_id: int):
    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id = ? LIMIT 1", (run_id,)).fetchone()
        if not run:
            return HTMLResponse("Payroll run not found", status_code=404)
        if safe(run["status"]).lower() != "draft":
            return RedirectResponse("/ui/hr/payroll?msg=" + quote("Only draft payroll can be edited."), status_code=302)

        lines = conn.execute(
            """
            SELECT *
            FROM payroll_lines
            WHERE payroll_run_id = ?
            ORDER BY employee_code, employee_name, id
            """,
            (run_id,),
        ).fetchall()

        rows_html = ""
        for line in lines:
            rows_html += f"""
            <tr>
                <td>{escape(safe(line['employee_code']))}<input type="hidden" name="line_id_{line['id']}" value="{line['id']}"></td>
                <td>{escape(safe(line['employee_name']))}</td>
                <td class="number-cell">{float(line['attendance_days'] or 0):,.2f}</td>
                <td class="number-cell">{float(line['absent_days'] or 0):,.2f}</td>
                <td class="number-cell">{float(line['overtime_hours'] or 0):,.2f}</td>
                <td class="number-cell">{money(line['basic_salary'])}</td>
                <td class="number-cell">{money(line['housing_allowance'])}</td>
                <td class="number-cell">{money(line['transport_allowance'])}</td>
                <td class="number-cell">{money(line['other_allowance'])}</td>
                <td><input class="line-input line-overtime" type="number" step="0.01" name="overtime_amount_{line['id']}" value="{safe(line['overtime_amount'] or '0')}"></td>
                <td><input class="line-input line-bonus" type="number" step="0.01" name="bonus_amount_{line['id']}" value="{safe(line['bonus_amount'] or '0')}"></td>
                <td><input class="line-input line-deduction" type="number" step="0.01" name="deduction_amount_{line['id']}" value="{safe(line['deduction_amount'] or '0')}"></td>
                <td><input class="line-input line-advance" type="number" step="0.01" name="advance_deduction_{line['id']}" value="{safe(line['advance_deduction'] or '0')}"></td>
                <td><input class="line-input line-absence" type="number" step="0.01" name="absence_deduction_{line['id']}" value="{safe(line['absence_deduction'] or '0')}"></td>
                <td class="number-cell">{money(line['insurance_employee_amount'])}</td>
                <td class="number-cell">{money(line['insurance_employer_amount'])}</td>
                <td><input name="remarks_{line['id']}" value="{escape(safe(line['remarks']))}"></td>
                <td class="number-cell line-gross">{money(line['gross_amount'])}</td>
                <td class="number-cell line-net">{money(line['net_amount'])}</td>
            </tr>
            """

        html = f"""
        <div class="card">
            <h2>Edit Payroll Draft {escape(safe(run['payroll_no']))}</h2>
            <p class="section-note">Attendance days, absent days, overtime hours, and insurance are calculated automatically from employee setup and biometric attendance. You can still adjust overtime amount, bonuses, and manual deductions before posting.</p>
        </div>

        <div class="card">
            <form method="post" action="/ui/hr/payroll/{run_id}/edit">
                <div class="table-wrap">
                    <table id="payrollEditTable">
                        <tr>
                            <th>Code</th>
                            <th>Name</th>
                            <th>Attendance</th>
                            <th>Absent</th>
                            <th>OT Hours</th>
                            <th>Basic</th>
                            <th>Housing</th>
                            <th>Transport</th>
                            <th>Other</th>
                            <th>Overtime</th>
                            <th>Bonus</th>
                            <th>Deduction</th>
                            <th>Advance</th>
                            <th>Absence</th>
                            <th>Emp. Insurance</th>
                            <th>Comp. Insurance</th>
                            <th>Remarks</th>
                            <th>Gross</th>
                            <th>Net</th>
                        </tr>
                        {rows_html}
                    </table>
                </div>

                <div style="margin-top:18px;">
                    <button class="btn green" type="submit">Save Draft</button>
                    <a class="btn gray" href="/ui/hr/payroll/{run_id}">Back</a>
                </div>
            </form>
        </div>

        <script>
        function toNum(value) {{
            const n = parseFloat(value || "0");
            return isNaN(n) ? 0 : n;
        }}

        function fmt(value) {{
            return value.toLocaleString(undefined, {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
        }}

        function recalcRow(row) {{
            const basic = toNum(row.children[5].innerText.replace(/,/g, ""));
            const housing = toNum(row.children[6].innerText.replace(/,/g, ""));
            const transport = toNum(row.children[7].innerText.replace(/,/g, ""));
            const other = toNum(row.children[8].innerText.replace(/,/g, ""));
            const overtime = toNum(row.querySelector(".line-overtime")?.value);
            const bonus = toNum(row.querySelector(".line-bonus")?.value);
            const deduction = toNum(row.querySelector(".line-deduction")?.value);
            const advance = toNum(row.querySelector(".line-advance")?.value);
            const absence = toNum(row.querySelector(".line-absence")?.value);
            const insurance = toNum(row.children[14].innerText.replace(/,/g, ""));
            const gross = basic + housing + transport + other + overtime + bonus;
            const net = gross - deduction - advance - absence - insurance;
            row.querySelector(".line-gross").innerText = fmt(gross);
            row.querySelector(".line-net").innerText = fmt(net);
        }}

        window.addEventListener("DOMContentLoaded", function() {{
            document.querySelectorAll("#payrollEditTable tr").forEach(function(row) {{
                row.querySelectorAll(".line-input").forEach(function(input) {{
                    input.addEventListener("input", function() {{ recalcRow(row); }});
                }});
            }});
        }});
        </script>
        """
        return HTMLResponse(render_page("Edit Payroll", html, "en", current_path=request.url.path))
    finally:
        conn.close()


@router.post("/ui/hr/payroll/{run_id}/edit")
async def payroll_update(request: Request, run_id: int):
    form = await request.form()
    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id = ? LIMIT 1", (run_id,)).fetchone()
        if not run:
            return HTMLResponse("Payroll run not found", status_code=404)
        if safe(run["status"]).lower() != "draft":
            return RedirectResponse("/ui/hr/payroll?msg=" + quote("Only draft payroll can be edited."), status_code=302)

        line_ids = conn.execute("SELECT id FROM payroll_lines WHERE payroll_run_id = ?", (run_id,)).fetchall()
        for row in line_ids:
            line_id = row["id"]
            conn.execute(
                """
                UPDATE payroll_lines
                SET overtime_amount = ?,
                    bonus_amount = ?,
                    deduction_amount = ?,
                    advance_deduction = ?,
                    absence_deduction = ?,
                    remarks = ?
                WHERE id = ?
                """,
                (
                    to_float(form.get(f"overtime_amount_{line_id}")),
                    to_float(form.get(f"bonus_amount_{line_id}")),
                    to_float(form.get(f"deduction_amount_{line_id}")),
                    to_float(form.get(f"advance_deduction_{line_id}")),
                    to_float(form.get(f"absence_deduction_{line_id}")),
                    safe(form.get(f"remarks_{line_id}")),
                    line_id,
                ),
            )

        recalc_payroll_totals(conn, run_id)
        conn.commit()
        return RedirectResponse(f"/ui/hr/payroll/{run_id}", status_code=302)
    finally:
        conn.close()


@router.post("/ui/hr/payroll/{run_id}/post")
def payroll_post(run_id: int):
    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id = ? LIMIT 1", (run_id,)).fetchone()
        if not run:
            return HTMLResponse("Payroll run not found", status_code=404)
        if safe(run["status"]).lower() != "draft":
            return RedirectResponse("/ui/hr/payroll?msg=" + quote("Only draft payroll can be posted."), status_code=302)

        allocate_payroll_advance_deductions(conn, run_id)

        lines = conn.execute(
            """
            SELECT
                COALESCE(SUM(basic_salary), 0) AS total_basic,
                COALESCE(SUM(housing_allowance), 0) AS total_housing,
                COALESCE(SUM(transport_allowance), 0) AS total_transport,
                COALESCE(SUM(other_allowance), 0) AS total_other,
                COALESCE(SUM(overtime_amount), 0) AS total_overtime,
                COALESCE(SUM(bonus_amount), 0) AS total_bonus,
                COALESCE(SUM(absence_deduction), 0) AS total_absence,
                COALESCE(SUM(advance_deduction), 0) AS total_advance,
                COALESCE(SUM(insurance_employee_amount), 0) AS total_insurance_employee,
                COALESCE(SUM(insurance_employer_amount), 0) AS total_insurance_employer,
                COALESCE(SUM(net_amount), 0) AS total_net
            FROM payroll_lines
            WHERE payroll_run_id = ?
            """,
            (run_id,),
        ).fetchone()

        total_basic = float(lines["total_basic"] or 0)
        total_housing = float(lines["total_housing"] or 0)
        total_transport = float(lines["total_transport"] or 0)
        total_other = float(lines["total_other"] or 0)
        total_overtime = float(lines["total_overtime"] or 0)
        total_bonus = float(lines["total_bonus"] or 0)
        total_absence = float(lines["total_absence"] or 0)
        total_advance = float(lines["total_advance"] or 0)
        total_insurance_employee = float(lines["total_insurance_employee"] or 0)
        total_insurance_employer = float(lines["total_insurance_employer"] or 0)
        total_net = float(lines["total_net"] or 0)

        salary_acct = get_setting_value("payroll_salary_account", "", conn=conn)
        housing_acct = get_setting_value("payroll_housing_account", "", conn=conn)
        transport_acct = get_setting_value("payroll_transport_account", "", conn=conn)
        other_allowance_acct = get_setting_value("payroll_other_allowance_account", "", conn=conn)
        overtime_acct = get_setting_value("payroll_overtime_account", "", conn=conn)
        bonus_acct = get_setting_value("payroll_bonus_account", "", conn=conn)
        absence_acct = get_setting_value("payroll_absence_account", "", conn=conn)
        advance_acct = get_setting_value("payroll_advance_account", "", conn=conn)
        insurance_employee_acct = get_setting_value("payroll_insurance_employee_account", "", conn=conn)
        insurance_employer_acct = get_setting_value("payroll_insurance_employer_account", "", conn=conn)
        payable_acct = get_setting_value("payroll_payable_account", "201020108", conn=conn)

        journal_lines = []
        line_no = 1

        if total_basic > 0 and salary_acct:
            journal_lines.append({
                "line_no": line_no,
                "line_description": f"Payroll {safe(run['payroll_no'])} - Basic Salary",
                "account_code": salary_acct,
                "debit": total_basic,
                "credit": 0,
                "partner_type": "",
                "partner_id": None,
            })
            line_no += 1

        if total_housing > 0 and housing_acct:
            journal_lines.append({
                "line_no": line_no,
                "line_description": f"Payroll {safe(run['payroll_no'])} - Housing Allowance",
                "account_code": housing_acct,
                "debit": total_housing,
                "credit": 0,
                "partner_type": "",
                "partner_id": None,
            })
            line_no += 1

        if total_transport > 0 and transport_acct:
            journal_lines.append({
                "line_no": line_no,
                "line_description": f"Payroll {safe(run['payroll_no'])} - Transport Allowance",
                "account_code": transport_acct,
                "debit": total_transport,
                "credit": 0,
                "partner_type": "",
                "partner_id": None,
            })
            line_no += 1

        if total_other > 0 and other_allowance_acct:
            journal_lines.append({
                "line_no": line_no,
                "line_description": f"Payroll {safe(run['payroll_no'])} - Other Allowances",
                "account_code": other_allowance_acct,
                "debit": total_other,
                "credit": 0,
                "partner_type": "",
                "partner_id": None,
            })
            line_no += 1

        if total_overtime > 0 and overtime_acct:
            journal_lines.append({
                "line_no": line_no,
                "line_description": f"Payroll {safe(run['payroll_no'])} - Overtime",
                "account_code": overtime_acct,
                "debit": total_overtime,
                "credit": 0,
                "partner_type": "",
                "partner_id": None,
            })
            line_no += 1

        if total_bonus > 0 and bonus_acct:
            journal_lines.append({
                "line_no": line_no,
                "line_description": f"Payroll {safe(run['payroll_no'])} - Bonuses",
                "account_code": bonus_acct,
                "debit": total_bonus,
                "credit": 0,
                "partner_type": "",
                "partner_id": None,
            })
            line_no += 1

        if total_absence > 0 and absence_acct:
            journal_lines.append({
                "line_no": line_no,
                "line_description": f"Payroll {safe(run['payroll_no'])} - Absence Deduction",
                "account_code": absence_acct,
                "debit": 0,
                "credit": total_absence,
                "partner_type": "",
                "partner_id": None,
            })
            line_no += 1

        if total_insurance_employee > 0 and insurance_employee_acct:
            journal_lines.append({
                "line_no": line_no,
                "line_description": f"Payroll {safe(run['payroll_no'])} - Employee Insurance",
                "account_code": insurance_employee_acct,
                "debit": 0,
                "credit": total_insurance_employee,
                "partner_type": "",
                "partner_id": None,
            })
            line_no += 1

        if total_advance > 0 and advance_acct:
            journal_lines.append({
                "line_no": line_no,
                "line_description": f"Payroll {safe(run['payroll_no'])} - Advance Deduction",
                "account_code": advance_acct,
                "debit": 0,
                "credit": total_advance,
                "partner_type": "",
                "partner_id": None,
            })
            line_no += 1

        if total_insurance_employer > 0 and insurance_employer_acct:
            journal_lines.append({
                "line_no": line_no,
                "line_description": f"Payroll {safe(run['payroll_no'])} - Employer Insurance Expense",
                "account_code": insurance_employer_acct,
                "debit": total_insurance_employer,
                "credit": 0,
                "partner_type": "",
                "partner_id": None,
            })
            line_no += 1
            
            # Credit to balance employer insurance expense (using accrued expenses account)
            accrued_expenses_acct = get_setting_value("accrued_expenses_account", "201020108", conn=conn)
            if accrued_expenses_acct:
                journal_lines.append({
                    "line_no": line_no,
                    "line_description": f"Payroll {safe(run['payroll_no'])} - Employer Insurance Payable",
                    "account_code": accrued_expenses_acct,
                    "debit": 0,
                    "credit": total_insurance_employer,
                    "partner_type": "",
                    "partner_id": None,
                })
                line_no += 1

        # Calculate actual net payment (should be positive)
        # Employer insurance is company expense, NOT part of net payment to employees
        actual_net_payment = max(0, total_basic + total_housing + total_transport + total_other + total_overtime + total_bonus 
                                - total_absence - total_advance - total_insurance_employee)
        
        if actual_net_payment > 0 and payable_acct:
            journal_lines.append({
                "line_no": line_no,
                "line_description": f"Payroll {safe(run['payroll_no'])} - Net Salaries Payable",
                "account_code": payable_acct,
                "debit": 0,
                "credit": actual_net_payment,
                "partner_type": "",
                "partner_id": None,
            })

        journal_id = None
        if journal_lines:
            accounting_lines = []
            for item in journal_lines:
                accounting_lines.append({
                    "description": item.get("line_description", ""),
                    "account_code": item.get("account_code", ""),
                    "debit": item.get("debit", 0),
                    "credit": item.get("credit", 0),
                    "partner_type": item.get("partner_type", ""),
                    "partner_id": item.get("partner_id"),
                })
            journal_id = create_accounting_journal_entry(
                conn=conn,
                entry_date=safe(run["payment_date"]),
                description=f"Payroll {safe(run['payroll_no'])} - {int(run['payroll_month'] or 0):02d}/{run['payroll_year'] or ''}",
                reference=safe(run["payroll_no"]),
                source_type="payroll_run",
                source_id=run_id,
                lines=accounting_lines,
            )
            submit_journal_for_final_post(conn, journal_id)

        conn.execute(
            "UPDATE payroll_runs SET status = 'pending_final_post', payroll_journal_id = ? WHERE id = ?",
            (journal_id, run_id),
        )
        conn.commit()
        return RedirectResponse(f"/ui/hr/payroll/{run_id}", status_code=302)
    except Exception as e:
        import traceback
        err_msg = f"Error posting payroll: {e}\n{traceback.format_exc()}"
        logging.error(err_msg)
        conn.rollback()
        return HTMLResponse(f"<pre>{err_msg}</pre>", status_code=500)
    finally:
        conn.close()


@router.post("/ui/hr/payroll/{run_id}/unpost")
def payroll_unpost(run_id: int):
    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id = ? LIMIT 1", (run_id,)).fetchone()
        if not run:
            return HTMLResponse("Payroll run not found", status_code=404)
        status = safe(run["status"]).lower() or "draft"
        if status == "draft":
            return RedirectResponse(f"/ui/hr/payroll/{run_id}", status_code=302)
        if status != "pending_final_post":
            return RedirectResponse("/ui/hr/payroll?msg=" + quote("Only payroll waiting final post can be returned to draft."), status_code=302)
        if payroll_journal_status(conn, run) == "posted":
            return RedirectResponse(f"/ui/hr/payroll/{run_id}?msg=" + quote("Final posted payroll cannot be returned to draft."), status_code=302)

        paid_amount = payroll_paid_amount(conn, run_id)
        if paid_amount > 0:
            return RedirectResponse(f"/ui/hr/payroll/{run_id}?msg=" + quote("Cancel salary payments before unposting payroll."), status_code=302)

        delete_non_final_payroll_journal(conn, run)
        clear_payroll_advance_deductions(conn, run_id)
        clear_payroll_hr_adjustments(conn, run_id)

        conn.execute(
            "UPDATE payroll_runs SET status = 'draft', payroll_journal_id = NULL WHERE id = ?",
            (run_id,),
        )
        conn.commit()
        return RedirectResponse(f"/ui/hr/payroll/{run_id}", status_code=302)
    except Exception as e:
        logging.error(f"Error unposting payroll: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post("/ui/hr/payroll/{run_id}/delete")
def payroll_delete(run_id: int):
    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id = ? LIMIT 1", (run_id,)).fetchone()
        if not run:
            return HTMLResponse("Payroll run not found", status_code=404)
        status = safe(run["status"]).lower() or "draft"
        if status not in ("draft", "pending_final_post"):
            return RedirectResponse("/ui/hr/payroll?msg=" + quote("Only draft or waiting final post payroll can be deleted."), status_code=302)
        if payroll_journal_status(conn, run) == "posted":
            return RedirectResponse(f"/ui/hr/payroll/{run_id}?msg=" + quote("Final posted payroll cannot be deleted."), status_code=302)
        paid_amount = payroll_paid_amount(conn, run_id)
        if paid_amount > 0:
            return RedirectResponse(f"/ui/hr/payroll/{run_id}?msg=" + quote("Cancel salary payments before deleting payroll."), status_code=302)

        delete_non_final_payroll_journal(conn, run)
        clear_payroll_advance_deductions(conn, run_id)
        clear_payroll_hr_adjustments(conn, run_id)
        conn.execute("DELETE FROM payroll_lines WHERE payroll_run_id = ?", (run_id,))
        conn.execute("DELETE FROM payroll_runs WHERE id = ?", (run_id,))
        conn.commit()
        return RedirectResponse("/ui/hr/payroll", status_code=302)
    except Exception as e:
        logging.error(f"Error deleting payroll: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()
