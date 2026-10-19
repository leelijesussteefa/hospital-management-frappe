import frappe

def get_context(context):
    user = frappe.session.user

    # 1. Guest பயனரை frontend-க்கு அனுப்புதல்
    if not user or user == "Guest":
        frappe.local.flags.redirect_location = "/frontend"
        raise frappe.Redirect

    roles = [r.lower() for r in frappe.get_roles(user)]
    is_admin = "system manager" in roles or "administrator" in roles

    # 2. நோயாளிகள் தவறுதலாக உள்ளே வந்தால் patient dashboard-க்கு திருப்புதல்
    if "patient" in roles and "doctor" not in roles and not is_admin:
        frappe.local.flags.redirect_location = "/patient_dashboard"
        raise frappe.Redirect

    # 3. Logged-in Doctor விவரங்களை எடுத்தல்
    doctor = frappe.db.get_value(
        "Doctor",
        {"email": user},
        ["name", "doctor_name", "department", "specialization", "phone", "email"],
        as_dict=True
    )

    # அட்மினாக லாகின் செய்து டெஸ்ட் செய்தால் முதல் டாக்டரின் விவரங்களை எடுக்கும்
    if not doctor and is_admin:
        first_doc = frappe.get_all(
            "Doctor",
            fields=["name", "doctor_name", "department", "specialization", "phone", "email"],
            limit=1
        )
        if first_doc:
            doctor = first_doc[0]

    if doctor:
        context.doctor = doctor

        # டாக்டருக்கான அனைத்து Appointments
        context.appointments = frappe.get_all(
            "Appointments",
            filters={"doctor": doctor.name},
            fields=["name", "patient", "appointment_date", "appointment_time", "status", "reason_for_visit", "phone_number"],
            order_by="appointment_date desc"
        )

        # இன்றைய Appointments மட்டும்
        today = frappe.utils.today()
        context.todays_appointments = [
            a for a in context.appointments if str(a.get("appointment_date")) == str(today)
        ]

        # டாக்டரின் Prescriptions
        context.prescriptions = frappe.get_all(
            "Prescription",
            filters={"doctor": doctor.name},
            fields=["name", "patient", "prescription_date", "creation"],
            order_by="creation desc"
        )
    else:
        context.doctor = None
        context.appointments = []
        context.todays_appointments = []
        context.prescriptions = []

    context.no_cache = 1
    return context