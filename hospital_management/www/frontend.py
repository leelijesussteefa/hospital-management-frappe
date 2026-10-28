import frappe

def get_context(context):
    user = frappe.session.user
    if user and user != "Guest":
        roles = [r.lower() for r in frappe.get_roles(user)]
        is_admin = user == "Administrator" or "system manager" in roles or "administrator" in roles
        is_doctor = (
            "doctor" in roles
            or "healthcare practitioner" in roles
            or frappe.db.exists("Doctor", {"email": user})
            or frappe.db.exists("Doctor", {"email": ("like", f"%{user}%")})
            or frappe.db.exists("Doctor", {"name": user})
            or frappe.db.exists("Doctor", {"doctor_name": user})
        )
        is_patient = (
            "patient" in roles
            or frappe.db.exists("Patient", {"email": user})
            or frappe.db.exists("Patient", {"email": ("like", f"%{user}%")})
            or frappe.db.exists("Patient", {"name": user})
            or frappe.db.exists("Patient", {"patient_name": user})
        )

        if is_admin:
            frappe.local.flags.redirect_location = "/admin_dashboard"
            raise frappe.Redirect
        elif is_doctor:
            frappe.local.flags.redirect_location = "/doctor_dashboard"
            raise frappe.Redirect
        elif is_patient:
            frappe.local.flags.redirect_location = "/patient_dashboard"
            raise frappe.Redirect
        else:
            frappe.local.flags.redirect_location = "/patient_dashboard"
            raise frappe.Redirect

    try:
        context.no_cache = 1
    except AttributeError:
        if isinstance(context, dict):
            context["no_cache"] = 1
    return context
