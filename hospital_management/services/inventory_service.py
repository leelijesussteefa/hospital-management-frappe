import frappe
from frappe import _
from frappe.utils import flt, nowdate, getdate
from erpnext.stock.utils import get_stock_balance

def get_pharmacy_settings():
    try:
        return frappe.get_single("Hospital Settings")
    except Exception:
        return frappe._dict({
            "default_company": "Bosco Soft",
            "default_warehouse": "Stores - BS",
            "low_stock_threshold": 50,
            "near_expiry_days": 60,
            "auto_generate_invoice": 1
        })

def get_company_and_account_defaults(company_name=None):
    """
    Automatically fetches the default company and its default income/receivable account.
    If not set, uses standard fallbacks to avoid account validation errors.
    """
    settings = None
    try:
        settings = frappe.get_single("Hospital Settings")
    except Exception:
        settings = None

    company = (
        company_name
        or (settings.get("default_company") if settings else None)
        or frappe.db.get_single_value("Global Defaults", "default_company")
        or frappe.db.get_value("Company", {"is_group": 0}, "name")
        or "Bosco Soft"
    )

    comp_doc = frappe.get_doc("Company", company) if frappe.db.exists("Company", company) else None
    abbr = comp_doc.abbr if comp_doc and getattr(comp_doc, "abbr", None) else "BS"

    income_account = comp_doc.default_income_account if comp_doc else None
    if not income_account:
        income_account = frappe.db.get_value(
            "Account",
            {"company": company, "root_type": "Income", "is_group": 0},
            "name"
        )
    if not income_account:
        income_account = frappe.db.get_value(
            "Account",
            {"company": company, "account_type": "Income Account", "is_group": 0},
            "name"
        )
    if not income_account:
        income_account = f"Sales - {abbr}"

    receivable_account = comp_doc.default_receivable_account if comp_doc else None
    if not receivable_account:
        receivable_account = frappe.db.get_value(
            "Account",
            {"company": company, "account_type": "Receivable", "is_group": 0},
            "name"
        )
    if not receivable_account:
        receivable_account = frappe.db.get_value(
            "Account",
            {"company": company, "root_type": "Asset", "is_group": 0},
            "name"
        )
    if not receivable_account:
        receivable_account = f"Debtors - {abbr}"

    cost_center = comp_doc.cost_center if comp_doc else None
    if not cost_center:
        cost_center = frappe.db.get_value(
            "Cost Center",
            {"company": company, "is_group": 0},
            "name"
        ) or f"Main - {abbr}"

    return {
        "company": company,
        "income_account": income_account,
        "receivable_account": receivable_account,
        "cost_center": cost_center
    }

def get_or_create_customer_for_patient(patient_identifier):
    """
    Checks if a Frappe Customer exists with the patient's name (e.g. Arthur Pendelton).
    If not found, creates a simple Customer record on the fly with ignore_permissions=True
    so invoice creation never fails due to missing customer.
    """
    patient_name = patient_identifier
    if frappe.db.exists("Patient", patient_identifier):
        patient_name = frappe.db.get_value("Patient", patient_identifier, "patient_name") or patient_identifier

    # Check by customer_name
    cust = frappe.db.get_value("Customer", {"customer_name": patient_name}, "name")
    if not cust:
        # Check by name
        if frappe.db.exists("Customer", patient_name):
            cust = patient_name
    if not cust and patient_identifier != patient_name:
        cust = frappe.db.get_value("Customer", {"customer_name": patient_identifier}, "name")
        if not cust and frappe.db.exists("Customer", patient_identifier):
            cust = patient_identifier

    if not cust:
        customer_group = "Individual" if frappe.db.exists("Customer Group", "Individual") else (
            frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or "All Customer Groups"
        )
        territory = "All Territories" if frappe.db.exists("Territory", "All Territories") else (
            frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"
        )

        customer_doc = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": patient_name,
            "customer_type": "Individual",
            "customer_group": customer_group,
            "territory": territory
        })
        customer_doc.flags.ignore_permissions = True
        customer_doc.insert(ignore_permissions=True)
        cust = customer_doc.name

    return cust

def get_warehouse_with_stock(item_code, required_qty=1.0, preferred_warehouse=None):
    """
    Queries tabBin to find the actual warehouse where the medicine has available stock (actual_qty > 0).
    If preferred_warehouse is specified and has stock >= required_qty, uses that warehouse.
    Otherwise picks warehouse with stock >= required_qty, or the warehouse with max actual_qty > 0.
    """
    if preferred_warehouse:
        bin_stock = frappe.db.sql("""
            SELECT actual_qty 
            FROM `tabBin` 
            WHERE item_code = %s AND warehouse = %s AND actual_qty > 0
        """, (item_code, preferred_warehouse), as_dict=True)
        if bin_stock and flt(bin_stock[0].actual_qty) >= flt(required_qty):
            return preferred_warehouse, flt(bin_stock[0].actual_qty)

    bins = frappe.db.sql("""
        SELECT warehouse, actual_qty 
        FROM `tabBin` 
        WHERE item_code = %s AND actual_qty > 0 
        ORDER BY actual_qty DESC
    """, (item_code,), as_dict=True)

    if bins:
        for b in bins:
            if flt(b.actual_qty) >= flt(required_qty):
                return b.warehouse, flt(b.actual_qty)
        return bins[0].warehouse, flt(bins[0].actual_qty)

    return None, 0.0

def get_medicine_stock_info(medicine_identifier, warehouse=None):
    """Returns real warehouse stock, pricing, batch, and alerts for a medicine."""
    med = None
    if frappe.db.exists("Medicine", medicine_identifier):
        med = frappe.get_doc("Medicine", medicine_identifier)
    else:
        med_name = frappe.db.get_value("Medicine", {"medicine_name": medicine_identifier}, "name")
        if med_name:
            med = frappe.get_doc("Medicine", med_name)
            
    if not med:
        frappe.throw(_("Medicine '{0}' not found in catalog.").format(medicine_identifier))
        
    settings = get_pharmacy_settings()
    pref_wh = warehouse or settings.default_warehouse or "Stores - BS"
    
    # Resolve item_code
    item_code = None
    for cand in [med.medicine_name.strip() if med.medicine_name else None,
                 med.medicine_code.strip() if med.medicine_code else None,
                 med.name]:
        if cand and frappe.db.exists("Item", cand):
            item_code = cand
            break
    if not item_code and med.medicine_name:
        item_code = med.medicine_name.strip()

    # Query tabBin to detect actual warehouse where item has available stock (actual_qty > 0)
    detected_warehouse, real_stock = get_warehouse_with_stock(item_code, required_qty=1.0, preferred_warehouse=pref_wh)
    
    if not detected_warehouse:
        detected_warehouse = pref_wh
        if frappe.db.exists("Item", item_code):
            try:
                real_stock = flt(get_stock_balance(item_code, detected_warehouse))
            except Exception:
                real_stock = 0.0
        else:
            real_stock = flt(med.quantity or 0)
    elif not frappe.db.exists("Warehouse", detected_warehouse):
        detected_warehouse = frappe.db.get_value("Warehouse", {"is_group": 0}, "name") or "Stores - BS"

    low_stock_threshold = flt(settings.low_stock_threshold or 50)
    is_low_stock = real_stock <= low_stock_threshold
    
    # Expiry check
    is_near_expiry = False
    is_expired = False
    if med.expiry_date:
        try:
            exp = getdate(med.expiry_date)
            today = getdate(nowdate())
            days_left = (exp - today).days
            if days_left <= 0:
                is_expired = True
            elif days_left <= (settings.near_expiry_days or 60):
                is_near_expiry = True
        except Exception:
            pass
            
    return {
        "name": med.name,
        "medicine_name": med.medicine_name,
        "item_code": item_code,
        "price": flt(med.price),
        "warehouse": detected_warehouse,
        "stock_quantity": real_stock,
        "catalog_quantity": flt(med.quantity or 0),
        "batch_number": med.batch_number or "N/A",
        "expiry_date": med.expiry_date or "N/A",
        "is_low_stock": is_low_stock,
        "is_near_expiry": is_near_expiry,
        "is_expired": is_expired
    }

def dispense_prescription_inventory(prescription_id, dispensed_items=None):
    """
    Validates prescription dispensing, deducts stock via ERPNext Stock Entry (Material Issue),
    updates Medicine catalog quantities, and updates prescription dispense status.
    Auto-detects actual warehouse from tabBin where stock is available.
    """
    if not frappe.db.exists("Prescription", prescription_id):
        frappe.throw(_("Prescription '{0}' does not exist.").format(prescription_id))
        
    rx = frappe.get_doc("Prescription", prescription_id)
    
    # Validation 1: Prevent duplicate dispensing
    if rx.dispense_status == "Dispensed":
        frappe.throw(_("Prescription '{0}' has already been dispensed.").format(prescription_id))
        
    # Validation 2: Handle cancelled prescriptions
    if rx.dispense_status == "Cancelled" or getattr(rx, "authorization_status", None) == "Cancelled":
        frappe.throw(_("Cannot dispense a cancelled prescription."))
        
    defaults = get_company_and_account_defaults()
    company = defaults["company"]

    # Determine items to dispense
    items_to_issue = []
    
    # If rx has child table items
    rx_items = rx.get("items") or []
    if not rx_items and getattr(rx, "medicine", None):
        rx_items = [frappe._dict({
            "medicine": rx.medicine,
            "quantity": 1.0
        })]
        
    if not rx_items:
        frappe.throw(_("Cannot dispense: No medicines listed in this prescription."))
        
    for item in rx_items:
        med_ref = item.medicine
        med_info = get_medicine_stock_info(med_ref)
        req_qty = flt(item.quantity) if flt(item.quantity) > 0 else 1.0
        
        # Validation 3: Check stock availability
        if med_info["stock_quantity"] < req_qty:
            frappe.throw(_("Insufficient stock for '{0}'. Available: {1}, Required: {2}").format(
                med_info["medicine_name"], med_info["stock_quantity"], req_qty
            ))
            
        items_to_issue.append({
            "item_code": med_info["item_code"],
            "medicine_doc": med_info["name"],
            "qty": req_qty,
            "price": med_info["price"],
            "batch_number": med_info["batch_number"],
            "warehouse": med_info["warehouse"]
        })
        
    # Create and submit ERPNext Stock Entry (Material Issue)
    first_warehouse = items_to_issue[0]["warehouse"] if items_to_issue else "Stores - BS"
    se_items = []
    for issue in items_to_issue:
        item_wh = issue.get("warehouse") or first_warehouse
        se_items.append({
            "item_code": issue["item_code"],
            "qty": issue["qty"],
            "s_warehouse": item_wh,
            "basic_rate": issue["price"],
            "conversion_factor": 1.0,
            "uom": "Nos",
            "stock_uom": "Nos"
        })
        
    se = frappe.get_doc({
        "doctype": "Stock Entry",
        "purpose": "Material Issue",
        "stock_entry_type": "Material Issue",
        "company": company,
        "from_warehouse": first_warehouse,
        "items": se_items
    })
    se.flags.ignore_permissions = True
    se.insert(ignore_permissions=True)
    se.submit()
    
    # Update Medicine catalog quantities
    for issue in items_to_issue:
        curr_qty = frappe.db.get_value("Medicine", issue["medicine_doc"], "quantity") or 0
        new_qty = max(0, curr_qty - int(issue["qty"]))
        frappe.db.set_value("Medicine", issue["medicine_doc"], "quantity", new_qty)
        
    # Mark Prescription as Dispensed
    rx.dispense_status = "Dispensed"
    rx.flags.ignore_permissions = True
    rx.save(ignore_permissions=True)
    
    frappe.db.commit()
    return {
        "status": "success",
        "prescription_id": prescription_id,
        "stock_entry": se.name,
        "dispensed_items": items_to_issue,
        "warehouse": first_warehouse,
        "company": company
    }
