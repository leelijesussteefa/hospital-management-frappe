"""
CarePlus Hospital Management - Pharmacy API Module
Provides pharmacy dispensing, stock validation, and billing endpoints.
"""
import frappe
from hospital_management.api import (
    dispense_prescription as _dispense_prescription,
    dispense_prescription_and_bill as _dispense_prescription_and_bill,
    add_medicine as _add_medicine
)
from hospital_management.services.inventory_service import (
    get_medicine_stock_info,
    get_warehouse_with_stock,
    get_company_and_account_defaults,
    get_or_create_customer_for_patient
)

@frappe.whitelist(allow_guest=True)
def dispense_prescription_and_bill(prescription_id, payment_method="Cash", notes=None):
    """Dispense prescription and generate billing invoice with ignore_permissions=True."""
    return _dispense_prescription_and_bill(prescription_id, payment_method=payment_method, notes=notes)

@frappe.whitelist(allow_guest=True)
def dispense_prescription(prescription_id, payment_method="Cash", notes=None):
    """Alias for dispense_prescription_and_bill."""
    return _dispense_prescription(prescription_id, payment_method=payment_method, notes=notes)

@frappe.whitelist(allow_guest=True)
def add_medicine(medicine_name=None, batch_number=None, price=0.0, quantity=0, threshold=None, expiry_date=None, **kwargs):
    """Create or update medicine in inventory with ignore_permissions=True."""
    return _add_medicine(
        medicine_name=medicine_name,
        batch_number=batch_number,
        price=price,
        quantity=quantity,
        threshold=threshold,
        expiry_date=expiry_date,
        **kwargs
    )

__all__ = [
    "dispense_prescription",
    "dispense_prescription_and_bill",
    "add_medicine",
    "get_medicine_stock_info",
    "get_warehouse_with_stock",
    "get_company_and_account_defaults",
    "get_or_create_customer_for_patient"
]
