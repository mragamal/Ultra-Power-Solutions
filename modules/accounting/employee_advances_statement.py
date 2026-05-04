from fastapi import Request
from fastapi.responses import HTMLResponse
from db import get_conn
from i18n import get_lang
from layout import render_page
from modules.hr.employees import safe
from html import escape

def tr(request: Request, en: str, ar: str) -> str:
    return ar if get_lang(request) == "ar" else en

def money(value):
    try:
        return f"{float(value or 0):,.2f}"
    except Exception:
        return "0.00"

def with_lang(request: Request, path: str) -> str:
    lang = get_lang(request)
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}lang={lang}" if lang == "ar" else path

def advance_paid_parts(conn, advance_id):
    payroll_row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM employee_advance_deductions
        WHERE advance_id = ?
        """,
        (advance_id,),
    ).fetchone()
    manual_row = conn.execute(
        """
        SELECT COALESCE(SUM(COALESCE(ei.paid_amount, 0)), 0) AS total
        FROM employee_advance_installments ei
        WHERE ei.advance_id = ?
          AND LOWER(COALESCE(ei.status, '')) = 'paid'
          AND NOT EXISTS (
              SELECT 1
              FROM employee_advance_deductions d
              WHERE d.advance_id = ei.advance_id
                AND d.deduction_month = ei.installment_month
                AND d.deduction_year = ei.installment_year
          )
        """,
        (advance_id,),
    ).fetchone()
    return float(payroll_row["total"] or 0), float(manual_row["total"] or 0)

def sync_statement_advance_statuses(conn):
    rows = conn.execute("SELECT * FROM employee_advances").fetchall()
    for row in rows:
        if safe(row["status"]).lower() == "cancelled":
            continue
        payroll_paid, manual_paid = advance_paid_parts(conn, row["id"])
        paid_before_start = float(row["paid_before_start"] or 0) if "paid_before_start" in row.keys() else 0.0
        balance = max(float(row["amount"] or 0) - payroll_paid - manual_paid - paid_before_start, 0)
        status = "closed" if balance <= 0.0001 else "active"
        conn.execute("UPDATE employee_advances SET status = ? WHERE id = ?", (status, row["id"]))

def advance_statement_ui(request: Request):
    lang = get_lang(request)
    employee_id = request.query_params.get("employee_id")
    date_from = request.query_params.get("date_from", "")
    date_to = request.query_params.get("date_to", "")

    conn = get_conn()
    sync_statement_advance_statuses(conn)
    conn.commit()
    
    # Get employees for filter
    employees = conn.execute("SELECT id, code, name FROM employees WHERE is_active = 1 ORDER BY code, name").fetchall()
    
    employee_options = '<option value="">' + tr(request, "-- All Employees --", "-- كل الموظفين --") + '</option>'
    for emp in employees:
        sel = "selected" if employee_id == str(emp["id"]) else ""
        label = f"{safe(emp['code'])} - {safe(emp['name'])}" if safe(emp["code"]) else safe(emp["name"])
        employee_options += f'<option value="{emp["id"]}" {sel}>{escape(label)}</option>'

    where_clauses = ["1 = 1"]
    params = []
    
    if employee_id and str(employee_id).strip():
        try:
            where_clauses.append("ea.employee_id = ?")
            params.append(int(employee_id))
        except ValueError:
            pass

    if date_from:
        where_clauses.append("ea.advance_date >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("ea.advance_date <= ?")
        params.append(date_to)

    try:
        advances = conn.execute(f"""
            SELECT ea.*, e.code as employee_code, e.name as employee_name,
                   jl.line_description AS journal_employee_name
            FROM employee_advances ea
            LEFT JOIN employees e ON e.id = ea.employee_id
            LEFT JOIN journal_lines jl ON jl.id = ea.journal_line_id
            WHERE {" AND ".join(where_clauses)}
            ORDER BY ea.advance_date DESC, ea.id DESC
        """, params).fetchall()
    except Exception as e:
        print(f"Error in advances statement query: {e}")
        advances = []

    body = ""
    total_amount = 0
    total_paid = 0
    total_balance = 0

    for adv in advances:
        payroll_paid, manual_paid = advance_paid_parts(conn, adv["id"])
        paid_before_start = float(adv["paid_before_start"] or 0) if "paid_before_start" in adv.keys() else 0.0
        paid = payroll_paid + manual_paid + paid_before_start
        balance = max(float(adv["amount"] or 0) - paid, 0)
        
        total_amount += float(adv["amount"] or 0)
        total_paid += paid
        total_balance += balance

        status_labels = {
            "active": tr(request, "Active", "نشطة"),
            "open": tr(request, "Open", "مفتوحة"),
            "closed": tr(request, "Closed", "مقفلة"),
            "cancelled": tr(request, "Cancelled", "ملغية"),
        }
        status_text = status_labels.get(safe(adv["status"]).lower(), safe(adv["status"]))
        status_cls = "green" if safe(adv["status"]).lower() == "closed" else "orange"

        emp_label = f"{safe(adv['employee_code'])} - {safe(adv['employee_name'])}" if safe(adv["employee_code"]) else (safe(adv["employee_name"]) or safe(adv["journal_employee_name"]) or f"Employee #{safe(adv['employee_id'])}")

        body += f"""
        <tr>
            <td>{escape(safe(adv['advance_no']))}</td>
            <td>{escape(safe(adv['advance_date']))}</td>
            <td>{escape(emp_label)}</td>
            <td class="number-cell">{money(adv['amount'])}</td>
            <td class="number-cell">{money(adv['installment_amount'])}</td>
            <td class="number-cell">{money(paid)}</td>
            <td class="number-cell">{money(balance)}</td>
            <td><span class="status-chip {status_cls}">{escape(status_text)}</span></td>
            <td><a class="btn blue" href="/ui/accounting/employee-advances/{adv['id']}" style="padding:2px 8px;font-size:12px;">{tr(request, "Details", "تفاصيل")}</a></td>
        </tr>
        """

    if not body:
        body = f"<tr><td colspan='9' style='text-align:center;'>{tr(request, 'No advances found.', 'لا توجد سلف.')}</td></tr>"

    conn.close()

    html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <h2>{tr(request, "Employee Advances Statement", "كشف حساب سلف الموظفين")}</h2>
            <a class="btn gray" href="/ui/accounting/employee-advances">{tr(request, "Back to List", "العودة للقائمة")}</a>
        </div>
        
        <form method="get" action="/ui/accounting/employee-advances/statement" style="margin-top:20px; background:#f9fafb; padding:15px; border-radius:8px;">
            <div style="display:flex; gap:15px; flex-wrap:wrap; align-items:flex-end;">
                <div style="flex:1; min-width:200px;">
                    <label style="display:block; margin-bottom:5px; font-size:13px;">{tr(request, "Employee", "الموظف")}</label>
                    <select name="employee_id" style="width:100%; padding:8px; border:1px solid #ddd; border-radius:4px;">
                        {employee_options}
                    </select>
                </div>
                <div>
                    <label style="display:block; margin-bottom:5px; font-size:13px;">{tr(request, "From", "من")}</label>
                    <input type="date" name="date_from" value="{date_from}" style="padding:7px; border:1px solid #ddd; border-radius:4px;">
                </div>
                <div>
                    <label style="display:block; margin-bottom:5px; font-size:13px;">{tr(request, "To", "إلى")}</label>
                    <input type="date" name="date_to" value="{date_to}" style="padding:7px; border:1px solid #ddd; border-radius:4px;">
                </div>
                <button type="submit" class="btn blue">{tr(request, "Filter", "تصفية")}</button>
                <a href="/ui/accounting/employee-advances/statement" class="btn gray">{tr(request, "Clear", "مسح")}</a>
            </div>
        </form>
    </div>

    <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:15px; margin-bottom:20px;">
        <div class="card" style="margin-bottom:0; text-align:center;">
            <div style="color:#6b7280; font-size:14px;">{tr(request, "Total Advances", "إجمالي السلف")}</div>
            <div style="font-size:24px; font-weight:bold; color:#1d4ed8;">{money(total_amount)}</div>
        </div>
        <div class="card" style="margin-bottom:0; text-align:center;">
            <div style="color:#6b7280; font-size:14px;">{tr(request, "Total Deducted", "إجمالي المخصوم")}</div>
            <div style="font-size:24px; font-weight:bold; color:#059669;">{money(total_paid)}</div>
        </div>
        <div class="card" style="margin-bottom:0; text-align:center;">
            <div style="color:#6b7280; font-size:14px;">{tr(request, "Remaining Balance", "الرصيد المتبقي")}</div>
            <div style="font-size:24px; font-weight:bold; color:#dc2626;">{money(total_balance)}</div>
        </div>
    </div>

    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>{tr(request, "No", "رقم السلفة")}</th>
                    <th>{tr(request, "Date", "التاريخ")}</th>
                    <th>{tr(request, "Employee", "الموظف")}</th>
                    <th>{tr(request, "Total", "القيمة")}</th>
                    <th>{tr(request, "Installment", "القسط")}</th>
                    <th>{tr(request, "Deducted", "المخصوم")}</th>
                    <th>{tr(request, "Balance", "الرصيد")}</th>
                    <th>{tr(request, "Status", "الحالة")}</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                {body}
            </tbody>
        </table>
    </div>
    """
    return HTMLResponse(render_page(tr(request, "Advances Statement", "كشف حساب السلف"), html, lang))
