from copy import deepcopy


def account(
    code: str,
    name: str,
    acc_type: str,
    parent_code: str = "",
    is_group: int = 0,
    is_active: int = 1,
):
    return {
        "code": str(code).strip(),
        "name": str(name).strip(),
        "type": str(acc_type).strip().lower(),
        "parent_code": str(parent_code).strip(),
        "is_group": int(is_group),
        "is_active": int(is_active),
    }


# =========================================================
# 1) BACKBONE TEMPLATE
# =========================================================
BACKBONE_TEMPLATE = [
    # Assets
    account("100000", "Assets", "asset", "", 1),
    account("110000", "Current Assets", "asset", "100000", 1),
    account("111000", "Cash and Cash Equivalents", "asset", "110000", 1),
    account("112000", "Trade and Other Receivables", "asset", "110000", 1),
    account("114000", "Other Current Assets", "asset", "110000", 1),
    account("120000", "Non-Current Assets", "asset", "100000", 1),

    # Liabilities
    account("200000", "Liabilities", "liability", "", 1),
    account("210000", "Current Liabilities", "liability", "200000", 1),
    account("211000", "Trade and Other Payables", "liability", "210000", 1),
    account("212000", "Taxes Payable", "liability", "210000", 1),
    account("220000", "Non-Current Liabilities", "liability", "200000", 1),

    # Equity
    account("300000", "Equity", "equity", "", 1),

    # Revenue
    account("400000", "Revenue", "income", "", 1),
    account("410000", "Operating Revenue", "income", "400000", 1),
    account("420000", "Other Revenue", "income", "400000", 1),

    # Cost of Revenue
    account("500000", "Cost of Revenue", "cogs", "", 1),

    # Expenses
    account("600000", "Expenses", "g&a", "", 1),
    account("610000", "Selling and Distribution Expenses", "g&a", "600000", 1),
    account("620000", "General and Administrative Expenses", "g&a", "600000", 1),
    account("630000", "Finance Costs", "g&a", "600000", 1),
    account("640000", "Other Expenses", "g&a", "600000", 1),
]


# =========================================================
# 2) SERVICE TEMPLATE
# =========================================================
SERVICE_TEMPLATE = [
    # Revenue
    account("411000", "Service Revenue", "income", "410000"),
    account("412000", "Maintenance Revenue", "income", "410000"),
    account("413000", "Project Revenue", "income", "410000"),
    account("414000", "Installation Revenue", "income", "410000"),

    # Cost of revenue
    account("510000", "Direct Service Cost", "cogs", "500000", 1),
    account("511000", "Technicians Cost", "cogs", "510000"),
    account("512000", "Site Materials Cost", "cogs", "510000"),
    account("513000", "Spare Parts Consumed", "cogs", "510000"),
    account("514000", "Subcontractor Cost", "cogs", "510000"),
    account("515000", "Travel and Site Visits Cost", "cogs", "510000"),

    # Expenses
    account("621000", "Administrative Salaries", "g&a", "620000"),
    account("622000", "Office Rent", "g&a", "620000"),
    account("623000", "Utilities Expense", "g&a", "620000"),
    account("624000", "Transportation Expense", "g&a", "620000"),
    account("625000", "Communication Expense", "g&a", "620000"),
    account("626000", "Office Supplies", "g&a", "620000"),
    account("627000", "Marketing Expense", "g&a", "610000"),
    account("628000", "Professional Fees", "g&a", "620000"),
]


# =========================================================
# 3) TRADING TEMPLATE
# =========================================================
TRADING_TEMPLATE = [
    # Receivables / Payables
    account("112100", "Customers Control Account", "asset", "112000"),
    account("211100", "Vendors Control Account", "liability", "211000"),

    # Revenue
    account("411000", "Sales Revenue", "income", "410000"),
    account("412000", "Sales Discounts", "income", "410000"),
    account("413000", "Sales Returns", "income", "410000"),

    # Cost of revenue
    account("510000", "Trading Cost of Revenue", "cogs", "500000", 1),
    account("511000", "Cost of Goods Sold", "cogs", "510000"),
    account("512000", "Purchase Price Variance", "cogs", "510000"),
    account("513000", "Inventory Write Down", "cogs", "510000"),

    # Expenses
    account("611000", "Selling Expense", "g&a", "610000"),
    account("612000", "Freight Out", "g&a", "610000"),
    account("613000", "Warehousing Expense", "g&a", "610000"),
    account("614000", "Distribution Expense", "g&a", "610000"),
    account("621000", "Administrative Salaries", "g&a", "620000"),
    account("622000", "Office Rent", "g&a", "620000"),
    account("623000", "Utilities Expense", "g&a", "620000"),
]


# =========================================================
# 4) MANUFACTURING TEMPLATE
# =========================================================
MANUFACTURING_TEMPLATE = [
    # Revenue
    account("411000", "Manufacturing Sales Revenue", "income", "410000"),
    account("412000", "Finished Goods Sales Returns", "income", "410000"),

    # Cost of revenue
    account("510000", "Manufacturing Cost of Revenue", "cogs", "500000", 1),
    account("511000", "Raw Materials Consumed", "cogs", "510000"),
    account("512000", "Direct Labor", "cogs", "510000"),
    account("513000", "Manufacturing Overhead", "cogs", "510000"),
    account("514000", "Factory Utilities", "cogs", "510000"),
    account("515000", "Factory Maintenance", "cogs", "510000"),
    account("516000", "Production Supplies", "cogs", "510000"),
    account("517000", "Factory Depreciation", "cogs", "510000"),
    account("518000", "Cost of Goods Manufactured", "cogs", "510000"),
    account("519000", "Cost of Goods Sold", "cogs", "510000"),

    # Expenses
    account("621000", "Administrative Salaries", "g&a", "620000"),
    account("622000", "Office Rent", "g&a", "620000"),
    account("623000", "Office Utilities", "g&a", "620000"),
    account("624000", "Selling Expense", "g&a", "610000"),
    account("625000", "Marketing Expense", "g&a", "610000"),
]


# =========================================================
# 5) FEATURE TEMPLATES
# =========================================================
FEATURE_INVENTORY_TEMPLATE = [
    account("113000", "Inventory", "asset", "110000", 1),
    account("113100", "Inventory - Main Store", "asset", "113000"),
    account("113200", "Inventory Adjustment", "asset", "113000"),
    account("113300", "Goods in Transit", "asset", "113000"),
]

FEATURE_TRADING_INVENTORY_TEMPLATE = [
    account("113000", "Inventory", "asset", "110000", 1),
    account("113100", "Inventory - Main Warehouse", "asset", "113000"),
    account("113200", "Inventory - Branches", "asset", "113000"),
    account("113300", "Goods in Transit", "asset", "113000"),
]

FEATURE_MANUFACTURING_INVENTORY_TEMPLATE = [
    account("113000", "Inventory", "asset", "110000", 1),
    account("113100", "Raw Materials Inventory", "asset", "113000"),
    account("113200", "Packing Materials Inventory", "asset", "113000"),
    account("113300", "Work In Progress", "asset", "113000"),
    account("113400", "Finished Goods", "asset", "113000"),
    account("113500", "Scrap Inventory", "asset", "113000"),
]

FEATURE_VAT_TEMPLATE = [
    account("114100", "Input VAT Recoverable", "asset", "114000"),
    account("212100", "Output VAT Payable", "liability", "212000"),
]

FEATURE_WHT_TEMPLATE = [
    account("114200", "WHT Receivable", "asset", "114000"),
    account("212200", "WHT Payable", "liability", "212000"),
]

FEATURE_PETTY_CASH_TEMPLATE = [
    account("111100", "Main Cash", "asset", "111000"),
    account("111150", "Main Bank", "asset", "111000"),
    account("111200", "Petty Cash", "asset", "111000"),
    account("111300", "Employees Advances and Imprests", "asset", "111000"),
]

FEATURE_BRANCHES_TEMPLATE = [
    account("111400", "Cash at Branches", "asset", "111000"),
    account("113900", "Inventory at Branches", "asset", "113000"),
]

FEATURE_PROJECTS_TEMPLATE = [
    account("114300", "Project Advances", "asset", "114000"),
    account("211300", "Project Retentions", "liability", "211000"),
]

FEATURE_COST_CENTERS_TEMPLATE = [
    # intentionally empty:
    # cost centers should usually be handled as a dimension, not a chart account
]

FEATURE_RECEIVABLES_PAYABLES_TEMPLATE = [
    account("112100", "Customers Control Account", "asset", "112000"),
    account("211100", "Vendors Control Account", "liability", "211000"),
]

FEATURE_FIXED_ASSETS_TEMPLATE = [
    account("121000", "Property, Plant and Equipment", "asset", "120000", 1),
    account("121100", "Furniture and Fixtures", "asset", "121000"),
    account("121200", "Computers and Equipment", "asset", "121000"),
    account("121300", "Vehicles", "asset", "121000"),
    account("121900", "Accumulated Depreciation", "accumulated depreciation", "120000"),
    account("624500", "Depreciation Expense", "depreciation expense", "620000"),
]


# =========================================================
# 6) TEMPLATE REGISTRY
# =========================================================
ACTIVITY_TEMPLATES = {
    "service": SERVICE_TEMPLATE,
    "trading": TRADING_TEMPLATE,
    "manufacturing": MANUFACTURING_TEMPLATE,
}

FEATURE_TEMPLATES = {
    "inventory": FEATURE_INVENTORY_TEMPLATE,
    "vat": FEATURE_VAT_TEMPLATE,
    "wht": FEATURE_WHT_TEMPLATE,
    "petty_cash": FEATURE_PETTY_CASH_TEMPLATE,
    "branches": FEATURE_BRANCHES_TEMPLATE,
    "projects": FEATURE_PROJECTS_TEMPLATE,
    "cost_centers": FEATURE_COST_CENTERS_TEMPLATE,
    "receivables_payables": FEATURE_RECEIVABLES_PAYABLES_TEMPLATE,
    "fixed_assets": FEATURE_FIXED_ASSETS_TEMPLATE,
}


# =========================================================
# 7) HELPERS FOR GENERATOR
# =========================================================
def get_backbone_template():
    return deepcopy(BACKBONE_TEMPLATE)


def get_activity_template(activity_type: str):
    return deepcopy(ACTIVITY_TEMPLATES.get(str(activity_type).strip().lower(), []))


def get_feature_template(feature_name: str):
    return deepcopy(FEATURE_TEMPLATES.get(str(feature_name).strip().lower(), []))


def get_inventory_template_for_activity(activity_type: str):
    activity_type = str(activity_type).strip().lower()

    if activity_type == "manufacturing":
        return deepcopy(FEATURE_MANUFACTURING_INVENTORY_TEMPLATE)

    if activity_type == "trading":
        return deepcopy(FEATURE_TRADING_INVENTORY_TEMPLATE)

    return deepcopy(FEATURE_INVENTORY_TEMPLATE)


def merge_templates(*template_lists):
    merged = []
    seen_codes = set()

    for template_list in template_lists:
        for row in template_list:
            code = str(row.get("code", "")).strip()
            if not code:
                continue
            if code in seen_codes:
                continue

            merged.append({
                "code": code,
                "name": str(row.get("name", "")).strip(),
                "type": str(row.get("type", "")).strip().lower(),
                "parent_code": str(row.get("parent_code", "")).strip(),
                "is_group": int(row.get("is_group", 0) or 0),
                "is_active": int(row.get("is_active", 1) or 1),
            })
            seen_codes.add(code)

    return merged


def build_template(
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
    templates = [
        get_backbone_template(),
        get_activity_template(activity_type),
    ]

    if include_receivables_payables:
        templates.append(get_feature_template("receivables_payables"))

    if include_fixed_assets:
        templates.append(get_feature_template("fixed_assets"))

    if use_inventory:
        templates.append(get_inventory_template_for_activity(activity_type))

    if use_cost_centers:
        templates.append(get_feature_template("cost_centers"))

    if use_projects:
        templates.append(get_feature_template("projects"))

    if use_branches:
        templates.append(get_feature_template("branches"))

    if use_vat:
        templates.append(get_feature_template("vat"))

    if use_wht:
        templates.append(get_feature_template("wht"))

    if use_petty_cash:
        templates.append(get_feature_template("petty_cash"))

    return merge_templates(*templates)