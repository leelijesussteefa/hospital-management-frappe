import frappe
from hospital_management.api import (
    get_user_landing_page,
    HOSPITAL_ROOMS_CATALOG,
    WARD_RATES,
    get_occupied_rooms,
    apply_no_cache_headers,
)

def get_context(context):
    apply_no_cache_headers(context)
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.local.flags.redirect_location = "/frontend"
        raise frappe.Redirect

    roles = [r.lower() for r in frappe.get_roles(user)]
    is_admin = (
        user in ("Administrator", "admin@careplus.com", "admin@careplus.hospital")
        or "system manager" in roles
        or "hospital administrator" in roles
        or "administrator" in roles
        or "admin" in user.lower()
    )

    if not is_admin:
        frappe.local.flags.redirect_location = get_user_landing_page(user)
        raise frappe.Redirect

    context.user = user
    context.title = "CarePlus | Admin Master Portal"
    context.is_admin = True

    # 1. Doctors
    context.doctors = frappe.get_all(
        "Doctor",
        fields=["name", "doctor_name", "department", "specialization", "phone", "email", "consultation_fee", "is_active", "shift_start_time", "shift_end_time"],
        order_by="doctor_name asc"
    )
    for doc in context.doctors:
        raw = (doc.doctor_name or doc.name or "").strip()
        while raw.lower().startswith("dr.") or raw.lower().startswith("dr "):
            raw = raw[3:].strip()
        doc["clean_name"] = raw
        doc["formatted_name"] = f"Dr. {raw}" if raw else "Doctor"

    # 2. Departments for modal dropdown
    context.departments = frappe.get_all(
        "Departments",
        fields=["name", "department_name"],
        order_by="department_name asc"
    )

    # 3. Patients (Including Inpatient / Outpatient & Room Bed Status)
    context.patients = frappe.get_all(
        "Patient",
        fields=[
            "name", "patient_name", "gender", "phone", "email", "blood_group",
            "date_of_birth", "patient_type", "room_number", "ward_type",
            "admission_date", "discharge_date", "room_rate", "status", "creation"
        ],
        order_by="creation desc",
        limit=200
    )
    for p in context.patients:
        p["is_inpatient"] = (p.get("patient_type") == "Inpatient")
        p["type_label"] = "Inpatient (IPD)" if p["is_inpatient"] else "Outpatient (OPD)"
        p["is_active_ipd"] = (p["is_inpatient"] and p.get("status") != "Discharged" and bool(p.get("room_number")))

    # Room & Bed Configuration
    occupied_map = get_occupied_rooms()
    context.rooms_catalog = HOSPITAL_ROOMS_CATALOG
    context.ward_rates = WARD_RATES
    context.occupied_rooms = list(occupied_map.keys())

    # 4. Prescriptions
    context.prescriptions = frappe.get_all(
        "Prescription",
        fields=["name", "patient", "doctor", "prescription_date", "dispense_status", "authorization_status", "creation"],
        order_by="creation desc",
        limit=100
    )

    # 5. Invoices
    context.invoices = frappe.get_all(
        "Invoice",
        fields=["name", "patient", "doctor", "invoice_date", "grand_total", "paid_amount", "outstanding_amount", "payment_status", "creation"],
        order_by="creation desc",
        limit=100
    )

    # 6. Top Metrics Calculations
    total_patients = frappe.db.count("Patient")
    ipd_patients = sum(1 for p in context.patients if p.get("is_inpatient") and p.get("status") != "Discharged")
    opd_patients = total_patients - ipd_patients
    active_doctors = frappe.db.count("Doctor", {"is_active": 1})
    total_doctors = len(context.doctors)
    pending_rx = frappe.db.count("Prescription", {"dispense_status": ["!=", "Dispensed"]})
    dispensed_rx = frappe.db.count("Prescription", {"dispense_status": "Dispensed"})
    total_invoices = frappe.db.count("Invoice")

    rev = frappe.db.sql("SELECT SUM(paid_amount) as paid, SUM(grand_total) as total, SUM(outstanding_amount) as outstanding FROM tabInvoice", as_dict=True)
    rev_row = rev[0] if rev else {}
    revenue_collected = float(rev_row.get("paid") or 0.0)
    total_invoiced = float(rev_row.get("total") or 0.0)
    outstanding_total = float(rev_row.get("outstanding") or 0.0)

    context.metrics = {
        "total_patients": total_patients,
        "ipd_patients": ipd_patients,
        "opd_patients": opd_patients,
        "active_doctors": active_doctors,
        "total_doctors": total_doctors,
        "pending_prescriptions": pending_rx,
        "dispensed_prescriptions": dispensed_rx,
        "total_invoices": total_invoices,
        "revenue_collected": revenue_collected,
        "total_invoiced": total_invoiced,
        "outstanding_total": outstanding_total,
        "occupied_beds": len(occupied_map),
        "total_beds": len(HOSPITAL_ROOMS_CATALOG),
        "available_beds": len(HOSPITAL_ROOMS_CATALOG) - len(occupied_map),
    }

    context.no_cache = 1
    return context
