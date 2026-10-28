import frappe
from hospital_management.api import get_user_landing_page, apply_no_cache_headers

def get_context(context):
    apply_no_cache_headers(context)
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.local.flags.redirect_location = "/frontend"
        raise frappe.Redirect

    roles = [r.lower() for r in frappe.get_roles(user)]
    is_admin = (
        user == "Administrator"
        or "system manager" in roles
        or "hospital administrator" in roles
        or "administrator" in roles
    )
    is_pharmacy = (
        "pharmacy staff" in roles
        or "pharmacist" in roles
        or user == "pharmacy@careplus.com"
        or "pharmacy" in user.lower()
    )

    if not is_pharmacy and not is_admin:
        frappe.local.flags.redirect_location = get_user_landing_page(user)
        raise frappe.Redirect

    context.user = user
    context.title = "CarePlus | Pharmacy Desk & Dispense Management"
    context.no_cache = 1
    return context
