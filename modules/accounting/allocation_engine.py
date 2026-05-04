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


def safe_int(x, default=0):
    try:
        if x is None or str(x).strip() == "":
            return default
        return int(float(x))
    except Exception:
        return default


def ensure_column(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = [c["name"] for c in cols]
    if column_name not in names:
        conn.execute(alter_sql)


# =========================================================
# DB INIT / MIGRATION
# =========================================================
def ensure_allocation_tables():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_type TEXT NOT NULL,
            payment_id INTEGER NOT NULL,
            document_type TEXT NOT NULL,
            document_id INTEGER NOT NULL,
            allocated_amount REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    ensure_column(
        conn,
        "payment_allocations",
        "payment_type",
        "ALTER TABLE payment_allocations ADD COLUMN payment_type TEXT NOT NULL DEFAULT ''"
    )
    ensure_column(
        conn,
        "payment_allocations",
        "payment_id",
        "ALTER TABLE payment_allocations ADD COLUMN payment_id INTEGER NOT NULL DEFAULT 0"
    )
    ensure_column(
        conn,
        "payment_allocations",
        "document_type",
        "ALTER TABLE payment_allocations ADD COLUMN document_type TEXT NOT NULL DEFAULT ''"
    )
    ensure_column(
        conn,
        "payment_allocations",
        "document_id",
        "ALTER TABLE payment_allocations ADD COLUMN document_id INTEGER NOT NULL DEFAULT 0"
    )
    ensure_column(
        conn,
        "payment_allocations",
        "allocated_amount",
        "ALTER TABLE payment_allocations ADD COLUMN allocated_amount REAL DEFAULT 0"
    )
    ensure_column(
        conn,
        "payment_allocations",
        "created_at",
        "ALTER TABLE payment_allocations ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP"
    )

    conn.commit()
    conn.close()


ensure_allocation_tables()


# =========================================================
# LOW LEVEL READ HELPERS
# =========================================================
def get_payment_allocations(conn, payment_type: str, payment_id: int):
    return conn.execute("""
        SELECT *
        FROM payment_allocations
        WHERE payment_type = ?
          AND payment_id = ?
        ORDER BY id
    """, (safe(payment_type), payment_id)).fetchall()


def get_document_allocations(conn, document_type: str, document_id: int):
    return conn.execute("""
        SELECT *
        FROM payment_allocations
        WHERE document_type = ?
          AND document_id = ?
        ORDER BY id
    """, (safe(document_type), document_id)).fetchall()


def get_allocated_total_for_payment(conn, payment_type: str, payment_id: int):
    row = conn.execute("""
        SELECT COALESCE(SUM(allocated_amount), 0) AS total_allocated
        FROM payment_allocations
        WHERE payment_type = ?
          AND payment_id = ?
    """, (safe(payment_type), payment_id)).fetchone()

    return q2(row["total_allocated"] if row else 0)


def get_allocated_total_for_document(conn, document_type: str, document_id: int):
    row = conn.execute("""
        SELECT COALESCE(SUM(allocated_amount), 0) AS total_allocated
        FROM payment_allocations
        WHERE document_type = ?
          AND document_id = ?
    """, (safe(document_type), document_id)).fetchone()

    return q2(row["total_allocated"] if row else 0)


# =========================================================
# PAYMENT / DOCUMENT LOADERS
# =========================================================
def get_customer_payment(conn, payment_id: int):
    return conn.execute("""
        SELECT *
        FROM customer_payments
        WHERE id = ?
        LIMIT 1
    """, (payment_id,)).fetchone()


def get_vendor_payment(conn, payment_id: int):
    return conn.execute("""
        SELECT *
        FROM vendor_payments
        WHERE id = ?
        LIMIT 1
    """, (payment_id,)).fetchone()


def get_cash_voucher(conn, voucher_id: int):
    return conn.execute("""
        SELECT *
        FROM cash_vouchers
        WHERE id = ?
        LIMIT 1
    """, (voucher_id,)).fetchone()


def get_journal_allocation_source(conn, line_id: int):
    return conn.execute("""
        SELECT
            l.*,
            j.status AS journal_status,
            j.entry_date,
            j.entry_no,
            j.reference
        FROM journal_lines l
        JOIN journal_entries j ON j.id = l.journal_id
        WHERE l.id = ?
        LIMIT 1
    """, (line_id,)).fetchone()


def get_customer_invoice(conn, invoice_id: int):
    return conn.execute("""
        SELECT *
        FROM customer_invoices
        WHERE id = ?
        LIMIT 1
    """, (invoice_id,)).fetchone()


def get_vendor_bill(conn, bill_id: int):
    return conn.execute("""
        SELECT *
        FROM vendor_bills
        WHERE id = ?
        LIMIT 1
    """, (bill_id,)).fetchone()


def get_payment_total_amount(conn, payment_type: str, payment_id: int):
    payment_type = safe(payment_type)

    if payment_type == "customer_payment":
        row = get_customer_payment(conn, payment_id)
        if not row:
            raise Exception("Payment not found")
        return q2(row["amount"])
    elif payment_type == "vendor_payment":
        row = get_vendor_payment(conn, payment_id)
        if not row:
            raise Exception("Payment not found")
        return q2(row["amount"])
    elif payment_type == "cash_receipt":
        row = get_cash_voucher(conn, payment_id)
        if not row or safe(row["voucher_type"]).lower() != "receipt":
            raise Exception("Cash receipt voucher not found")
        return q2(row["amount"])
    elif payment_type == "cash_payment":
        row = get_cash_voucher(conn, payment_id)
        if not row or safe(row["voucher_type"]).lower() != "payment":
            raise Exception("Cash payment voucher not found")
        return q2(row["amount"])
    elif payment_type == "customer_opening_journal":
        row = get_journal_allocation_source(conn, payment_id)
        if not row:
            raise Exception("Opening journal source not found")
        return q2(D(row["credit"]) - D(row["debit"]))
    elif payment_type == "vendor_opening_journal":
        row = get_journal_allocation_source(conn, payment_id)
        if not row:
            raise Exception("Opening journal source not found")
        return q2(D(row["debit"]) - D(row["credit"]))
    else:
        raise Exception(f"Unsupported payment type: {payment_type}")


def get_document_total_amount(conn, document_type: str, document_id: int):
    document_type = safe(document_type)

    if document_type == "customer_invoice":
        row = get_customer_invoice(conn, document_id)
    elif document_type == "vendor_bill":
        row = get_vendor_bill(conn, document_id)
    else:
        raise Exception(f"Unsupported document type: {document_type}")

    if not row:
        raise Exception("Document not found")

    return q2(row["net_amount"])


def get_payment_unallocated_amount(conn, payment_type: str, payment_id: int):
    total_amount = get_payment_total_amount(conn, payment_type, payment_id)
    allocated = get_allocated_total_for_payment(conn, payment_type, payment_id)
    return q2(total_amount - allocated)


def get_document_open_amount(conn, document_type: str, document_id: int):
    total_amount = get_document_total_amount(conn, document_type, document_id)
    allocated = get_allocated_total_for_document(conn, document_type, document_id)
    open_amount = q2(total_amount - allocated)

    if open_amount < Decimal("0.00"):
        return Decimal("0.00")
    return open_amount


# =========================================================
# VALIDATION
# =========================================================
def validate_payment_document_match(conn, payment_type: str, payment_id: int, document_type: str, document_id: int):
    payment_type = safe(payment_type)
    document_type = safe(document_type)

    if payment_type == "customer_payment":
        payment = get_customer_payment(conn, payment_id)
        if not payment:
            raise Exception("Customer payment not found")

        if safe(payment["status"]).lower() != "posted":
            raise Exception("Only posted customer payments can be allocated")

        if document_type != "customer_invoice":
            raise Exception("Customer payment can only allocate to customer invoice")

        invoice = get_customer_invoice(conn, document_id)
        if not invoice:
            raise Exception("Customer invoice not found")

        if safe(invoice["status"]).lower() != "posted":
            raise Exception("Only posted customer invoices can receive allocations")

        if safe_int(payment["customer_id"]) != safe_int(invoice["customer_id"]):
            raise Exception("Payment customer does not match invoice customer")

    elif payment_type == "vendor_payment":
        payment = get_vendor_payment(conn, payment_id)
        if not payment:
            raise Exception("Vendor payment not found")

        if safe(payment["status"]).lower() != "posted":
            raise Exception("Only posted vendor payments can be allocated")

        if document_type != "vendor_bill":
            raise Exception("Vendor payment can only allocate to vendor bill")

        bill = get_vendor_bill(conn, document_id)
        if not bill:
            raise Exception("Vendor bill not found")

        if safe(bill["status"]).lower() != "posted":
            raise Exception("Only posted vendor bills can receive allocations")

        if safe_int(payment["vendor_id"]) != safe_int(bill["vendor_id"]):
            raise Exception("Payment vendor does not match bill vendor")

    elif payment_type == "cash_receipt":
        payment = get_cash_voucher(conn, payment_id)
        if not payment:
            raise Exception("Cash receipt voucher not found")

        if safe(payment["status"]).lower() != "posted":
            raise Exception("Only posted cash receipts can be allocated")

        if safe(payment["voucher_type"]).lower() != "receipt":
            raise Exception("Selected voucher is not a cash receipt")

        if safe(payment["party_type"]).lower() != "customer":
            raise Exception("Only customer cash receipts can allocate to customer invoices")

        if document_type != "customer_invoice":
            raise Exception("Cash receipt can only allocate to customer invoice")

        invoice = get_customer_invoice(conn, document_id)
        if not invoice:
            raise Exception("Customer invoice not found")

        if safe(invoice["status"]).lower() != "posted":
            raise Exception("Only posted customer invoices can receive allocations")

        if safe_int(payment["party_id"]) != safe_int(invoice["customer_id"]):
            raise Exception("Cash receipt customer does not match invoice customer")

    elif payment_type == "cash_payment":
        payment = get_cash_voucher(conn, payment_id)
        if not payment:
            raise Exception("Cash payment voucher not found")

        if safe(payment["status"]).lower() != "posted":
            raise Exception("Only posted cash payments can be allocated")

        if safe(payment["voucher_type"]).lower() != "payment":
            raise Exception("Selected voucher is not a cash payment")

        if safe(payment["party_type"]).lower() != "vendor":
            raise Exception("Only vendor cash payments can allocate to vendor bills")

        if document_type != "vendor_bill":
            raise Exception("Cash payment can only allocate to vendor bill")

        bill = get_vendor_bill(conn, document_id)
        if not bill:
            raise Exception("Vendor bill not found")

        if safe(bill["status"]).lower() != "posted":
            raise Exception("Only posted vendor bills can receive allocations")

        if safe_int(payment["party_id"]) != safe_int(bill["vendor_id"]):
            raise Exception("Cash payment vendor does not match bill vendor")

    elif payment_type == "customer_opening_journal":
        line = get_journal_allocation_source(conn, payment_id)
        if not line:
            raise Exception("Opening journal source not found")
        if safe(line["journal_status"]).lower() != "posted":
            raise Exception("Only posted journal sources can be allocated")
        if safe(line["partner_type"]).lower() != "customer":
            raise Exception("Selected opening source is not a customer line")
        if q2(D(line["credit"]) - D(line["debit"])) <= Decimal("0.00"):
            raise Exception("Selected customer opening line has no credit balance")
        if document_type != "customer_invoice":
            raise Exception("Opening customer balance can only allocate to customer invoice")
        invoice = get_customer_invoice(conn, document_id)
        if not invoice:
            raise Exception("Customer invoice not found")
        if safe(invoice["status"]).lower() != "posted":
            raise Exception("Only posted customer invoices can receive allocations")
        if safe_int(line["partner_id"]) != safe_int(invoice["customer_id"]):
            raise Exception("Opening balance customer does not match invoice customer")

    elif payment_type == "vendor_opening_journal":
        line = get_journal_allocation_source(conn, payment_id)
        if not line:
            raise Exception("Opening journal source not found")
        if safe(line["journal_status"]).lower() != "posted":
            raise Exception("Only posted journal sources can be allocated")
        if safe(line["partner_type"]).lower() != "vendor":
            raise Exception("Selected opening source is not a vendor line")
        if q2(D(line["debit"]) - D(line["credit"])) <= Decimal("0.00"):
            raise Exception("Selected vendor opening line has no debit balance")
        if document_type != "vendor_bill":
            raise Exception("Opening vendor balance can only allocate to vendor bill")
        bill = get_vendor_bill(conn, document_id)
        if not bill:
            raise Exception("Vendor bill not found")
        if safe(bill["status"]).lower() != "posted":
            raise Exception("Only posted vendor bills can receive allocations")
        if safe_int(line["partner_id"]) != safe_int(bill["vendor_id"]):
            raise Exception("Opening balance vendor does not match bill vendor")

    else:
        raise Exception(f"Unsupported payment type: {payment_type}")


# =========================================================
# CREATE / DELETE ALLOCATIONS
# =========================================================
def create_payment_allocation(
    conn,
    payment_type: str,
    payment_id: int,
    document_type: str,
    document_id: int,
    allocated_amount,
):
    payment_type = safe(payment_type)
    document_type = safe(document_type)
    payment_id = safe_int(payment_id)
    document_id = safe_int(document_id)
    amount = q2(allocated_amount)

    if payment_id <= 0:
        raise Exception("Payment ID is required")

    if document_id <= 0:
        raise Exception("Document ID is required")

    if amount <= Decimal("0.00"):
        raise Exception("Allocated amount must be greater than zero")

    validate_payment_document_match(conn, payment_type, payment_id, document_type, document_id)

    payment_unallocated = get_payment_unallocated_amount(conn, payment_type, payment_id)
    if amount > payment_unallocated:
        raise Exception(f"Allocated amount exceeds payment unallocated amount ({payment_unallocated})")

    document_open = get_document_open_amount(conn, document_type, document_id)
    if amount > document_open:
        raise Exception(f"Allocated amount exceeds document open amount ({document_open})")

    cur = conn.execute("""
        INSERT INTO payment_allocations (
            payment_type,
            payment_id,
            document_type,
            document_id,
            allocated_amount
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        payment_type,
        payment_id,
        document_type,
        document_id,
        float(amount),
    ))

    return cur.lastrowid


def delete_payment_allocations(conn, payment_type: str, payment_id: int):
    conn.execute("""
        DELETE FROM payment_allocations
        WHERE payment_type = ?
          AND payment_id = ?
    """, (safe(payment_type), safe_int(payment_id)))


def delete_document_allocations(conn, document_type: str, document_id: int):
    conn.execute("""
        DELETE FROM payment_allocations
        WHERE document_type = ?
          AND document_id = ?
    """, (safe(document_type), safe_int(document_id)))


# =========================================================
# AUTO-ALLOCATE FROM CURRENT SIMPLE MODEL
# =========================================================
def auto_allocate_customer_payment(conn, payment_id: int):
    payment = get_customer_payment(conn, payment_id)
    if not payment:
        raise Exception("Customer payment not found")

    if safe(payment["status"]).lower() != "posted":
        raise Exception("Only posted customer payments can be auto-allocated")

    invoice_id = safe_int(payment["invoice_id"])
    if invoice_id <= 0:
        return None

    existing = get_payment_allocations(conn, "customer_payment", payment_id)
    if existing:
        return None

    amount = q2(payment["amount"])
    if amount <= Decimal("0.00"):
        return None

    return create_payment_allocation(
        conn=conn,
        payment_type="customer_payment",
        payment_id=payment_id,
        document_type="customer_invoice",
        document_id=invoice_id,
        allocated_amount=amount,
    )


def auto_allocate_vendor_payment(conn, payment_id: int):
    payment = get_vendor_payment(conn, payment_id)
    if not payment:
        raise Exception("Vendor payment not found")

    if safe(payment["status"]).lower() != "posted":
        raise Exception("Only posted vendor payments can be auto-allocated")

    bill_id = safe_int(payment["bill_id"])
    if bill_id <= 0:
        return None

    existing = get_payment_allocations(conn, "vendor_payment", payment_id)
    if existing:
        return None

    amount = q2(payment["amount"])
    if amount <= Decimal("0.00"):
        return None

    return create_payment_allocation(
        conn=conn,
        payment_type="vendor_payment",
        payment_id=payment_id,
        document_type="vendor_bill",
        document_id=bill_id,
        allocated_amount=amount,
    )


# =========================================================
# PAYMENT STATUS REFRESH FROM ALLOCATIONS
# =========================================================
def refresh_customer_invoice_payment_status(conn, invoice_id: int):
    invoice = get_customer_invoice(conn, invoice_id)
    if not invoice:
        return

    if safe(invoice["status"]).lower() == "reversed":
        conn.execute("""
            UPDATE customer_invoices
            SET payment_status = 'cancelled'
            WHERE id = ?
        """, (invoice_id,))
        return

    total_amount = q2(invoice["net_amount"])
    allocated = get_allocated_total_for_document(conn, "customer_invoice", invoice_id)

    if allocated <= Decimal("0.00"):
        new_status = "unpaid"
    elif allocated < total_amount:
        new_status = "partial"
    else:
        new_status = "paid"

    conn.execute("""
        UPDATE customer_invoices
        SET payment_status = ?
        WHERE id = ?
    """, (new_status, invoice_id))


def refresh_vendor_bill_payment_status(conn, bill_id: int):
    bill = get_vendor_bill(conn, bill_id)
    if not bill:
        return

    if safe(bill["status"]).lower() == "reversed":
        conn.execute("""
            UPDATE vendor_bills
            SET payment_status = 'cancelled'
            WHERE id = ?
        """, (bill_id,))
        return

    total_amount = q2(bill["net_amount"])
    allocated = get_allocated_total_for_document(conn, "vendor_bill", bill_id)

    if allocated <= Decimal("0.00"):
        new_status = "unpaid"
    elif allocated < total_amount:
        new_status = "partial"
    else:
        new_status = "paid"

    conn.execute("""
        UPDATE vendor_bills
        SET payment_status = ?
        WHERE id = ?
    """, (new_status, bill_id))


# =========================================================
# UNAPPLIED BALANCES
# =========================================================
def get_customer_payment_unapplied_amount(conn, payment_id: int):
    return get_payment_unallocated_amount(conn, "customer_payment", payment_id)


def get_vendor_payment_unapplied_amount(conn, payment_id: int):
    return get_payment_unallocated_amount(conn, "vendor_payment", payment_id)


def get_customer_cash_receipt_unapplied_amount(conn, voucher_id: int):
    return get_payment_unallocated_amount(conn, "cash_receipt", voucher_id)


def get_vendor_cash_payment_unapplied_amount(conn, voucher_id: int):
    return get_payment_unallocated_amount(conn, "cash_payment", voucher_id)
