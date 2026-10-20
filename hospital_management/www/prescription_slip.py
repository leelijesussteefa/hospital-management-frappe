import frappe

def get_context(context):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.local.flags.redirect_location = "/frontend"
        raise frappe.Redirect

    prescription_id = frappe.form_dict.get("name") or frappe.form_dict.get("prescription_id")
    appointment_id = frappe.form_dict.get("appointment") or frappe.form_dict.get("appointment_id")

    context.prescription_id = prescription_id or ""
    context.appointment_id = appointment_id or ""
    context.title = f"CarePlus | Medical Prescription {prescription_id or ''}"
