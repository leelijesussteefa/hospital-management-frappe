from hospital_management.api import get_user_landing_page, apply_no_cache_headers
import frappe

def get_context(context):
    apply_no_cache_headers(context)
    user = frappe.session.user

    # Only redirect if strictly Guest
    if not user or user == "Guest":
        frappe.local.flags.redirect_location = "/frontend"
        raise frappe.Redirect

    roles = frappe.get_roles(user)
    roles_lower = [r.lower() for r in roles]
    is_doctor_doc = (
        frappe.db.exists("Doctor", {"user": user}) or 
        frappe.db.exists("Doctor", {"user_id": user}) or
        frappe.db.exists("Doctor", {"email": user}) or
        frappe.db.exists("Doctor", {"name": user}) or
        frappe.db.exists("Doctor", {"doctor_name": user}) or
        "Doctor" in roles or 
        "doctor" in roles or
        "doctor" in roles_lower or
        "Administrator" in roles or 
        "System Manager" in roles or
        user == "Administrator" or
        user == "amali@gmail.com"
    )

    if not is_doctor_doc:
        frappe.local.flags.redirect_location = get_user_landing_page(user)
        raise frappe.Redirect

    is_admin = (
        "system manager" in roles_lower
        or "administrator" in roles_lower
        or "hospital administrator" in roles_lower
        or user == "Administrator"
        or user in ("admin@careplus.com", "admin@careplus.hospital")
    )

    def set_ctx(k, v):
        try:
            context[k] = v
        except Exception:
            pass
        try:
            setattr(context, k, v)
        except Exception:
            pass

    set_ctx("is_doctor", True)
    set_ctx("is_patient", "patient" in roles_lower)
    set_ctx("is_admin", is_admin)

    # 1. Resolve requested doctor (if ?doctor=<name_or_id> parameter provided)
    form_dict = getattr(frappe, "form_dict", {}) or getattr(frappe.local, "form_dict", {}) or {}
    requested_doctor = form_dict.get("doctor")

    doctor = None
    if requested_doctor:
        doctor = frappe.db.get_value(
            "Doctor",
            requested_doctor,
            ["name", "doctor_name", "department", "specialization", "phone", "email", "consultation_fee", "is_active"],
            as_dict=True
        )
        if not doctor:
            doctor = frappe.db.get_value(
                "Doctor",
                {"doctor_name": requested_doctor},
                ["name", "doctor_name", "department", "specialization", "phone", "email", "consultation_fee", "is_active"],
                as_dict=True
            )
        if not doctor:
            doctor = frappe.db.get_value(
                "Doctor",
                {"email": requested_doctor},
                ["name", "doctor_name", "department", "specialization", "phone", "email", "consultation_fee", "is_active"],
                as_dict=True
            )

    # 2. If not explicitly requested, check logged-in Doctor
    if not doctor:
        doctor = frappe.db.get_value(
            "Doctor",
            {"email": user},
            ["name", "doctor_name", "department", "specialization", "phone", "email", "consultation_fee", "is_active"],
            as_dict=True
        )
    if not doctor:
        doctor = frappe.db.get_value(
            "Doctor",
            {"user_id": user},
            ["name", "doctor_name", "department", "specialization", "phone", "email", "consultation_fee", "is_active"],
            as_dict=True
        )
    if not doctor:
        doctor = frappe.db.get_value(
            "Doctor",
            {"name": user},
            ["name", "doctor_name", "department", "specialization", "phone", "email", "consultation_fee", "is_active"],
            as_dict=True
        )

    # 3. If admin viewing without a doctor parameter, default to the first doctor alphabetically
    if not doctor and is_admin:
        first_doc = frappe.get_all(
            "Doctor",
            fields=["name", "doctor_name", "department", "specialization", "phone", "email", "consultation_fee", "is_active"],
            order_by="doctor_name asc",
            limit=1
        )
        if first_doc:
            doctor = first_doc[0]

    if is_admin:
        all_docs = frappe.get_all(
            "Doctor",
            fields=["name", "doctor_name", "department", "specialization", "is_active"],
            order_by="doctor_name asc"
        )
        for d in all_docs:
            raw = (d.doctor_name or d.name or "").strip()
            while raw.lower().startswith("dr.") or raw.lower().startswith("dr "):
                raw = raw[3:].strip()
            d["formatted_name"] = f"Dr. {raw}" if raw else "Doctor"
        set_ctx("all_doctors", all_docs)

    # Fetch registered patients for modals
    try:
        pats = frappe.get_all(
            "Patient",
            fields=["name", "patient_name", "phone"],
            order_by="patient_name asc",
            limit=150
        )
        set_ctx("patients", pats)
    except Exception:
        set_ctx("patients", [])

    if doctor:
        raw_name = (doctor.get("doctor_name") or doctor.get("name") or "").strip()
        while raw_name.lower().startswith("dr.") or raw_name.lower().startswith("dr "):
            raw_name = raw_name[3:].strip()
        doctor["formatted_name"] = f"Dr. {raw_name}" if raw_name else "Doctor"
        set_ctx("doctor", doctor)

        candidate_docs = set()
        for f in [doctor.get("name"), doctor.get("doctor_name"), doctor.get("email"), doctor.get("user_id")]:
            if f and str(f).strip():
                val = str(f).strip()
                candidate_docs.add(val)
                if "@" not in val:
                    clean = val
                    while clean.lower().startswith("dr.") or clean.lower().startswith("dr "):
                        clean = clean[3:].strip()
                    if clean:
                        candidate_docs.add(clean)
                        candidate_docs.add(f"Dr. {clean}")
                        candidate_docs.add(f"Dr.{clean}")
                        candidate_docs.add(f"Dr {clean}")

        clean_doc_name = (doctor.get("doctor_name") or doctor.get("name") or "").strip()
        while clean_doc_name.lower().startswith("dr.") or clean_doc_name.lower().startswith("dr "):
            clean_doc_name = clean_doc_name[3:].strip()

        doc_filter = ["in", list(candidate_docs)]

        appts = frappe.get_all(
            "Appointments",
            filters={"doctor": doc_filter},
            fields=["name", "patient", "appointment_date", "appointment_time", "status", "reason_for_visit", "phone_number"],
            order_by="appointment_date desc, appointment_time desc"
        )
        set_ctx("appointments", appts)

        today = frappe.utils.today()
        todays_appts = [
            a for a in appts if str(a.get("appointment_date")) == str(today)
        ]
        set_ctx("todays_appointments", todays_appts)

        rxs = frappe.get_all(
            "Prescription",
            filters={"doctor": doc_filter},
            fields=["name", "patient", "prescription_date", "creation"],
            order_by="creation desc"
        )
        set_ctx("prescriptions", rxs)

        med_records = []
        try:
            if frappe.db.exists("DocType", "Medical Record"):
                med_records = frappe.get_all(
                    "Medical Record",
                    filters={"doctor": doc_filter},
                    fields=["name", "patient", "visit_date", "symptoms", "diagnosis", "treatment"],
                    order_by="visit_date desc, creation desc"
                )
                for mr in med_records:
                    if mr.get("patient"):
                        try:
                            mr["patient_name"] = frappe.db.get_value("Patient", mr["patient"], "patient_name") or mr["patient"]
                        except Exception:
                            mr["patient_name"] = mr["patient"]
        except Exception:
            med_records = []
        set_ctx("medical_records", med_records)
    else:
        set_ctx("doctor", None)
        set_ctx("appointments", [])
        set_ctx("todays_appointments", [])
        set_ctx("prescriptions", [])
        set_ctx("medical_records", [])

    set_ctx("no_cache", 1)
    return context
