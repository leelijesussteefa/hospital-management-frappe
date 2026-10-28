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
    is_staff = any(r in roles for r in ["system manager", "administrator", "receptionist", "doctor", "billing", "billing staff", "accounts user", "accounts manager"]) or user == "billing@careplus.com" or "billing" in user.lower()

    if not is_staff and not is_admin:
        frappe.local.flags.redirect_location = get_user_landing_page(user)
        raise frappe.Redirect

    context.user = user
    context.is_staff = is_staff
    context.roles = roles
    context.title = "CarePlus | Billing & Invoicing Management"
    context.no_cache = 1
    return context
