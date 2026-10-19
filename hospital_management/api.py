import frappe
from frappe import _


# --------------------------------------------------
# 1. AUTH & LOGOUT
# --------------------------------------------------
@frappe.whitelist(allow_guest=True)
def login_user(usr, pwd):
    try:
        login_manager = frappe.auth.LoginManager()
        login_manager.authenticate(user=usr, pwd=pwd)
        login_manager.post_login()
    except frappe.AuthenticationError:
        frappe.clear_messages()
        return {"status": "error", "message": "Invalid username or password"}

    user = frappe.session.user
    roles = frappe.get_roles(user)

    is_doctor = (
        frappe.db.exists("Doctor", {"email": ("like", user)})
        or frappe.db.exists("Doctor", {"name": ("like", user)})
        or frappe.db.exists("Doctor", {"doctor_name": ("like", user)})
    )

    is_patient = (
        frappe.db.exists("Patient", {"email": ("like", user)})
        or frappe.db.exists("Patient", {"name": ("like", user)})
    )

    if is_doctor or "Doctor" in roles or user == "Administrator" or "System Manager" in roles:
        redirect_url = "/doctor_dashboard"
    elif is_patient or "Patient" in roles:
        redirect_url = "/patient_dashboard"
    else:
        redirect_url = "/patient_dashboard"

    return {
        "status": "success",
        "message": "Logged In",
        "redirect_to": redirect_url
    }


@frappe.whitelist()
def logout():
    frappe.local.login_manager.logout()
    return {"status": "success", "message": "Logged Out"}


# --------------------------------------------------
# 2. DOCTOR LIST & APPOINTMENT BOOKING
# --------------------------------------------------
@frappe.whitelist(allow_guest=True)
def get_doctors_list():
    return frappe.get_all(
        "Doctor",
        fields=["name", "doctor_name", "department", "specialization", "consultation_fee"],
        order_by="doctor_name asc"
    )


@frappe.whitelist()
def book_appointment(doctor, appointment_date, appointment_time, reason_for_visit=None, phone_number=None):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Please login to book an appointment"), frappe.PermissionError)

    patient_id = frappe.db.get_value("Patient", {"email": user}, "name")
    if not patient_id:
        patient_id = frappe.db.get_value("Patient", {"name": user}, "name")

    if not patient_id:
        first_patient = frappe.get_all("Patient", fields=["name"], limit=1)
        if first_patient:
            patient_id = first_patient[0].name
        else:
            frappe.throw(_("No patient record found to link with appointment"))

    clean_doctor = doctor.split(" (")[0].strip() if doctor else doctor
    doctor_id = frappe.db.get_value("Doctor", {"doctor_name": clean_doctor}, "name")
    if not doctor_id:
        doctor_id = frappe.db.get_value("Doctor", clean_doctor, "name") or clean_doctor

    doc = frappe.get_doc({
        "doctype": "Appointments",
        "patient": patient_id,
        "doctor": doctor_id,
        "appointment_date": appointment_date,
        "appointment_time": appointment_time,
        "reason_for_visit": reason_for_visit or "",
        "phone_number": phone_number or "",
        "status": "Scheduled"
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success",
        "message": "Appointment booked successfully",
        "appointment": doc.name
    }


# Frontend-ல் 'book_patient_appointment' என்று அழைத்தாலும் வேலை செய்ய இது சேர்க்கப்பட்டுள்ளது:
@frappe.whitelist()
def book_patient_appointment(doctor, appointment_date, appointment_time, reason_for_visit=None, phone_number=None):
    return book_appointment(doctor, appointment_date, appointment_time, reason_for_visit, phone_number)


# --------------------------------------------------
# 3. PATIENT DASHBOARD DATA
# --------------------------------------------------
@frappe.whitelist()
def get_patient_dashboard_data():
    user = frappe.session.user
    if not user or user == "Guest":
        return {"status": "not_found", "message": "Please login first"}

    patient = frappe.db.get_value(
        "Patient",
        {"email": user},
        ["name", "patient_name", "phone", "email"],
        as_dict=True
    )

    if not patient:
        patient = frappe.db.get_value(
            "Patient",
            {"name": user},
            ["name", "patient_name", "phone", "email"],
            as_dict=True
        )

    if not patient:
        first_patient = frappe.get_all(
            "Patient",
            fields=["name", "patient_name", "phone", "email"],
            order_by="creation desc",
            limit=1
        )
        if first_patient:
            patient = first_patient[0]

    if not patient:
        return {"status": "not_found", "message": "No patient record exists in database"}

    appointments = frappe.get_all(
        "Appointments",
        filters={"patient": patient.name},
        fields=["name", "doctor", "appointment_date", "appointment_time", "status", "reason_for_visit", "phone_number"],
        order_by="appointment_date desc, appointment_time desc"
    )

    prescriptions = frappe.get_all(
        "Prescription",
        filters={"patient": patient.name},
        fields=["name", "doctor", "prescription_date", "medicine", "dosage", "frequency", "instructions"],
        order_by="prescription_date desc"
    )

    return {
        "status": "success",
        "patient": patient,
        "appointments": appointments,
        "prescriptions": prescriptions
    }


# --------------------------------------------------
# 4. DOCTOR DASHBOARD DATA
# --------------------------------------------------
@frappe.whitelist()
def get_doctor_dashboard_data():
    user = frappe.session.user
    if not user or user == "Guest":
        return {"status": "not_found", "message": "Please login first"}

    doctor = frappe.db.get_value(
        "Doctor",
        {"email": user},
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

    if not doctor and ("System Manager" in frappe.get_roles(user) or user == "Administrator"):
        first_doc = frappe.get_all(
            "Doctor",
            fields=["name", "doctor_name", "department", "specialization", "phone", "email", "consultation_fee", "is_active"],
            order_by="creation asc",
            limit=1
        )
        if first_doc:
            doctor = first_doc[0]

    if not doctor:
        return {"status": "not_found", "message": "No doctor record linked"}

    today = frappe.utils.today()
    doc_filter = ["in", [doctor.name, doctor.doctor_name]]

    todays_appointments = frappe.get_all(
        "Appointments",
        filters={"doctor": doc_filter, "appointment_date": today},
        fields=["name", "patient", "appointment_time", "reason_for_visit", "status"],
        order_by="appointment_time asc"
    )

    all_appointments = frappe.get_all(
        "Appointments",
        filters={"doctor": doc_filter},
        fields=["name", "patient", "appointment_date", "appointment_time", "reason_for_visit", "status", "phone_number"],
        order_by="appointment_date desc, appointment_time desc"
    )

    for a in all_appointments:
        a["consultation_fee"] = doctor.consultation_fee or 0

    prescriptions = frappe.get_all(
        "Prescription",
        filters={"doctor": doc_filter},
        fields=["name", "patient", "prescription_date", "medicine", "dosage", "frequency"],
        order_by="prescription_date desc"
    )

    medical_records = frappe.get_all(
        "Medical Record",
        filters={"doctor": doc_filter},
        fields=["name", "patient", "visit_date", "symptoms", "diagnosis", "treatment"],
        order_by="visit_date desc"
    ) if frappe.db.exists("DocType", "Medical Record") else []

    total_patients_seen = frappe.db.count("Appointments", {
        "doctor": doc_filter,
        "status": ("in", ["Completed", "Confirmed"])
    })

    stats = {
        "today_appointments": len(todays_appointments),
        "scheduled_appointments": frappe.db.count("Appointments", {"doctor": doc_filter, "status": "Scheduled"}),
        "total_patients_seen": total_patients_seen,
        "total_prescriptions": len(prescriptions),
        "total_records": len(medical_records)
    }

    return {
        "status": "success",
        "doctor": doctor,
        "stats": stats,
        "todays_appointments": todays_appointments,
        "appointments": all_appointments,
        "prescriptions": prescriptions,
        "medical_records": medical_records
    }


# --------------------------------------------------
# 5. APPOINTMENT STATUS UPDATE
# --------------------------------------------------
@frappe.whitelist()
def update_appointment_status(appointment_id, status):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    if not frappe.db.exists("Appointments", appointment_id):
        return {"status": "error", "message": "Appointment not found"}

    frappe.db.set_value("Appointments", appointment_id, "status", status)
    frappe.db.commit()

    return {"status": "success", "message": f"Appointment status updated to {status}"}


# --------------------------------------------------
# 6. WRITE PRESCRIPTION
# --------------------------------------------------
@frappe.whitelist()
def add_patient_prescription(patient, doctor, medicine, appointment=None, dosage="1 tablet", frequency="As Needed", instructions="After food"):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    patient_id = frappe.db.get_value("Patient", {"patient_name": patient}, "name")
    if not patient_id:
        patient_id = frappe.db.get_value("Patient", patient, "name") or patient

    doctor_id = frappe.db.get_value("Doctor", {"doctor_name": doctor}, "name")
    if not doctor_id:
        doctor_id = frappe.db.get_value("Doctor", doctor, "name") or doctor

    medicine_val = medicine
    if frappe.db.exists("DocType", "Medicine"):
        med_id = frappe.db.get_value("Medicine", {"medicine_name": medicine}, "name")
        if not med_id:
            med_id = frappe.db.get_value("Medicine", medicine, "name")
        if not med_id:
            first_med = frappe.get_all("Medicine", limit=1)
            if first_med:
                med_id = first_med[0].name
        if med_id:
            medicine_val = med_id

    doc_data = {
        "doctype": "Prescription",
        "patient": patient_id,
        "doctor": doctor_id,
        "prescription_date": frappe.utils.today(),
        "medicine": medicine_val,
        "dosage": dosage,
        "frequency": frequency,
        "instructions": instructions
    }

    if appointment and frappe.db.exists("Appointments", appointment):
        meta = frappe.get_meta("Prescription")
        if meta.has_field("appointments"):
            doc_data["appointments"] = appointment
        elif meta.has_field("appointment"):
            doc_data["appointment"] = appointment

    doc = frappe.get_doc(doc_data)
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {"status": "success", "message": "Prescription created", "data": doc.as_dict()}


# --------------------------------------------------
# 7. GET MEDICINES LIST
# --------------------------------------------------
@frappe.whitelist()
def get_medicines_list():
    if not frappe.db.exists("DocType", "Medicine"):
        return []

    meta = frappe.get_meta("Medicine")
    fields = ["name"]
    if meta.has_field("medicine_name"):
        fields.append("medicine_name")

    medicines = frappe.get_all("Medicine", fields=fields, order_by="name asc")
    return medicines