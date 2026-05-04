def seed_chart_of_accounts(db):

    accounts = [

        # ================= ASSETS =================
        ("100000", "Assets", "asset", None, 1, 0),
        ("110000", "Current Assets", "asset", "100000", 1, 0),

        ("111000", "Cash & Cash Equivalent", "asset", "110000", 1, 0),
        ("111100", "Main Cash", "asset", "111000", 0, 1),
        ("111200", "Petty Cash", "asset", "111000", 0, 1),
        ("111300", "Bank Accounts", "asset", "111000", 0, 1),

        ("112000", "Accounts Receivable", "asset", "110000", 1, 0),
        ("112100", "Customers Control", "asset", "112000", 0, 1),

        ("113000", "Inventory", "asset", "110000", 1, 0),
        ("113100", "Raw Materials", "asset", "113000", 0, 1),
        ("113200", "WIP", "asset", "113000", 0, 1),
        ("113300", "Finished Goods", "asset", "113000", 0, 1),

        ("120000", "Fixed Assets", "asset", "100000", 1, 0),
        ("123000", "Machinery", "asset", "120000", 0, 1),
        ("124000", "Vehicles", "asset", "120000", 0, 1),
        ("125000", "Furniture & Equipment", "asset", "120000", 0, 1),
        ("126000", "IT Equipment", "asset", "120000", 0, 1),

        ("129000", "Accumulated Depreciation", "asset", "120000", 1, 0),
        ("129200", "Acc Dep - Machinery", "asset", "129000", 0, 1),
        ("129300", "Acc Dep - Vehicles", "asset", "129000", 0, 1),
        ("129400", "Acc Dep - Furniture", "asset", "129000", 0, 1),
        ("129500", "Acc Dep - IT", "asset", "129000", 0, 1),

        # ================= LIABILITIES =================
        ("200000", "Liabilities", "liability", None, 1, 0),
        ("210000", "Current Liabilities", "liability", "200000", 1, 0),
        ("211000", "Accounts Payable", "liability", "210000", 0, 1),
        ("213000", "Taxes Payable", "liability", "210000", 0, 1),

        # ================= EQUITY =================
        ("300000", "Equity", "equity", None, 1, 0),
        ("310000", "Capital", "equity", "300000", 0, 1),
        ("320000", "Retained Earnings", "equity", "300000", 0, 1),

        # ================= REVENUE =================
        ("400000", "Revenue", "income", None, 1, 0),
        ("410000", "Sales Revenue", "income", "400000", 0, 1),

        # ================= EXPENSES =================
        ("600000", "Expenses", "expense", None, 1, 0),
        ("610000", "Administrative Expenses", "expense", "600000", 0, 1),

        ("670000", "Depreciation Expense", "expense", "600000", 1, 0),
        ("671000", "Dep - Machinery", "expense", "670000", 0, 1),
        ("672000", "Dep - Vehicles", "expense", "670000", 0, 1),
        ("673000", "Dep - Furniture", "expense", "670000", 0, 1),
        ("674000", "Dep - IT", "expense", "670000", 0, 1),

    ]

    for acc in accounts:
        db.execute("""
            INSERT OR IGNORE INTO accounts 
            (code, name, type, parent_code, is_group, allow_posting, active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, acc)

    db.commit()