import frappe
from hospital_management.api import apply_no_cache_headers

def get_context(context):
    apply_no_cache_headers(context)
    user = frappe.session.user

    # 1. Guest redirect to frontend login
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
    is_doctor = (
        "doctor" in roles
        or frappe.db.exists("Doctor", {"user_id": user})
        or frappe.db.exists("Doctor", {"email": user})
    )
    is_nurse = (
        "nurse" in roles
        or frappe.db.exists("Nurse", {"user_id": user})
        or frappe.db.exists("Nurse", {"email": user})
    )
    is_pharmacy = "pharmacy staff" in roles or "pharmacist" in roles or user == "pharmacy@careplus.com"
    is_billing = any(r in roles for r in ["billing", "billing staff", "accounts user", "accounts manager"]) or user == "billing@careplus.com"

    # Route guards
    if is_doctor and not is_admin:
        frappe.local.flags.redirect_location = "/doctor_dashboard"
        raise frappe.Redirect
    if is_pharmacy and not is_admin:
        frappe.local.flags.redirect_location = "/pharmacy_dashboard"
        raise frappe.Redirect
    if is_billing and not is_admin:
        frappe.local.flags.redirect_location = "/billing"
        raise frappe.Redirect
    if is_nurse and not is_admin:
        frappe.local.flags.redirect_location = "/app/patient-vitals"
        raise frappe.Redirect

    context.is_doctor = is_doctor
    context.is_admin = is_admin

    # 2. Resolve Patient: check query parameter first if caller is admin/staff
    req_patient = frappe.form_dict.get("patient")
    context.requested_patient = req_patient

    patient = None
    if req_patient and (is_admin or is_doctor or is_nurse):
        if frappe.db.exists("Patient", req_patient):
            patient = frappe.db.get_value(
                "Patient",
                req_patient,
                ["name", "patient_name", "gender", "phone", "email", "blood_group", "date_of_birth", "status"],
                as_dict=True
            )
        elif frappe.db.exists("Patient", {"patient_name": req_patient}):
            patient = frappe.db.get_value(
                "Patient",
                {"patient_name": req_patient},
                ["name", "patient_name", "gender", "phone", "email", "blood_group", "date_of_birth", "status"],
                as_dict=True
            )
        elif frappe.db.exists("Patient", {"email": req_patient}):
            patient = frappe.db.get_value(
                "Patient",
                {"email": req_patient},
                ["name", "patient_name", "gender", "phone", "email", "blood_group", "date_of_birth", "status"],
                as_dict=True
            )

    # If not resolved from query param, resolve strictly for logged-in user
    if not patient:
        # By user_id
        patient = frappe.db.get_value(
            "Patient",
            {"user_id": user},
            ["name", "patient_name", "gender", "phone", "email", "blood_group", "date_of_birth", "status"],
            as_dict=True
        )
        if not patient:
            # By email
            patient = frappe.db.get_value(
                "Patient",
                {"email": user},
                ["name", "patient_name", "gender", "phone", "email", "blood_group", "date_of_birth", "status"],
                as_dict=True
            )
        if not patient and frappe.db.exists("Patient", user):
            patient = frappe.db.get_value(
                "Patient",
                user,
                ["name", "patient_name", "gender", "phone", "email", "blood_group", "date_of_birth", "status"],
                as_dict=True
            )

        # Admin preview fallback if Administrator and still no patient
        if not patient and is_admin:
            first_p = frappe.get_all(
                "Patient",
                fields=["name", "patient_name", "gender", "phone", "email", "blood_group", "date_of_birth", "status"],
                order_by="creation desc",
                limit=1
            )
            if first_p:
                patient = first_p[0]

    if patient:
        context.patient = patient
        
        # Appointments strictly for this patient
        context.appointments = frappe.get_all(
            "Appointments",
            filters={"patient": patient.name},
            fields=["name", "doctor", "appointment_date", "appointment_time", "status", "reason_for_visit", "phone_number"],
            order_by="appointment_date desc, appointment_time desc"
        )
        
        # Prescriptions strictly for this patient
        context.prescriptions = frappe.get_all(
            "Prescription",
            filters={"patient": patient.name},
            fields=["name", "doctor", "prescription_date", "dispense_status", "authorization_status", "creation"],
            order_by="creation desc"
        )
        
        # Vitals strictly for this patient
        context.vitals = frappe.get_all(
            "Patient Vitals",
            filters={"patient": patient.name},
            fields=["name", "recorded_date_time", "bp_systolic", "bp_diastolic", "pulse_rate", "spo2", "temperature", "weight_kg", "bmi", "remarks"],
            order_by="recorded_date_time desc",
            limit=10
        )
        
        # Invoices strictly for this patient
        context.invoices = frappe.get_all(
            "Invoice",
            filters={"patient": patient.name},
            fields=["name", "invoice_date", "grand_total", "paid_amount", "outstanding_amount", "payment_status"],
            order_by="invoice_date desc"
        )
    else:
        context.patient = None
        context.appointments = []
        context.prescriptions = []
        context.vitals = []
        context.invoices = []

    # Doctors for booking dropdown (includes is_active flag for on leave tagging & validation)
    context.doctors = frappe.get_all(
        "Doctor",
        fields=["name", "doctor_name", "department", "specialization", "consultation_fee", "is_active"],
        order_by="doctor_name asc"
    )

    # Hospital Settings
    try:
        context.hospital_settings = frappe.get_single("Hospital Settings")
    except Exception:
        context.hospital_settings = None

    context.no_cache = 1
    return context
