import frappe

def get_context(context):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.local.flags.redirect_location = "/frontend"
        raise frappe.Redirect

    roles = [r.lower() for r in frappe.get_roles(user)]
    is_staff = any(r in roles for r in ["system manager", "administrator", "receptionist", "doctor"])

    context.user = user
    context.is_staff = is_staff
    context.roles = roles
    context.title = "CarePlus | Billing & Invoicing Management"
