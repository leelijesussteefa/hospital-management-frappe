import frappe

def get_context(context):
    user = frappe.session.user

    # 1. Guest user-ஐ frontend-க்கு திருப்பிவிடுதல்
    if not user or user == "Guest":
        frappe.local.flags.redirect_location = "/frontend"
        raise frappe.Redirect

    roles = [r.lower() for r in frappe.get_roles(user)]
    is_admin = "system manager" in roles or "administrator" in roles

    # 2. Logged-in Patient details எடுத்தல் (Case-insensitive)
    patient = frappe.db.get_value(
        "Patient",
        {"email": user},
        ["name", "patient_name", "gender", "phone"],
        as_dict=True
    )

    # Admin கணக்கில் லாகின் செய்து டெஸ்ட் செய்தால் முதல் நோயாளியை எடுக்கும்
    if not patient and is_admin:
        first_p = frappe.get_all(
            "Patient",
            fields=["name", "patient_name", "gender", "phone"],
            limit=1
        )
        if first_p:
            patient = first_p[0]

    if patient:
        context.patient = patient
        
        # Appointments
        context.appointments = frappe.get_all(
            "Appointments",
            filters={"patient": patient.name},
            fields=["name", "doctor", "appointment_date", "appointment_time", "status", "reason_for_visit"],
            order_by="appointment_date desc"
        )
        
        # Prescriptions
        context.prescriptions = frappe.get_all(
            "Prescription",
            filters={"patient": patient.name},
            fields=["name", "doctor", "creation"],
            order_by="creation desc"
        )
    else:
        context.patient = None
        context.appointments = []
        context.prescriptions = []

    # Dropdown-க்கான Doctors list
    context.doctors = frappe.get_all(
        "Doctor",
        fields=["name", "doctor_name", "department", "consultation_fee"],
        order_by="doctor_name asc"
    )

    context.no_cache = 1
    return context