import datetime
import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, nowdate, getdate

def record_vitals_entry(patient, appointment=None, recorded_by=None, bp_systolic=None, bp_diastolic=None,
                        pulse_rate=None, spo2=None, temperature=None, weight_kg=None, height_cm=None,
                        blood_sugar_fasting=None, blood_sugar_postprandial=None, blood_sugar_random=None, remarks=None):
    """
    Validates and creates a Patient Vitals document, calculating BMI and automated clinical alerts.
    """
    if not patient or not frappe.db.exists("Patient", patient):
        frappe.throw(_("Invalid or missing Patient."))
        
    user = recorded_by or frappe.session.user
    user_link = user if frappe.db.exists("User", user) else "Administrator"
    
    # Calculate BMI
    w = flt(weight_kg)
    h = flt(height_cm)
    bmi = 0.0
    bmi_status = "Normal"
    if w > 0 and h > 0:
        h_m = h / 100.0
        bmi = round(w / (h_m * h_m), 1)
        if bmi < 18.5:
            bmi_status = "Underweight"
        elif bmi <= 24.9:
            bmi_status = "Normal"
        elif bmi <= 29.9:
            bmi_status = "Overweight"
        else:
            bmi_status = "Obese"
            
    # Automated Clinical Warnings
    alerts = []
    sys_val = cint(bp_systolic)
    dia_val = cint(bp_diastolic)
    if sys_val > 0 or dia_val > 0:
        if sys_val >= 140 or dia_val >= 90:
            alerts.append(f"Hypertension Warning (BP: {sys_val}/{dia_val} mmHg)")
        elif sys_val < 90 or dia_val < 60:
            alerts.append(f"Hypotension Warning (BP: {sys_val}/{dia_val} mmHg)")
            
    spo2_val = flt(spo2)
    if spo2_val > 0 and spo2_val < 95.0:
        alerts.append(f"Hypoxia Alert: Low Oxygen Saturation ({spo2_val}%)")
        
    temp_val = flt(temperature)
    if temp_val > 99.5:
        alerts.append(f"Fever Alert: Elevated Body Temperature ({temp_val} deg F)")
        
    pulse_val = cint(pulse_rate)
    if pulse_val > 0:
        if pulse_val > 100:
            alerts.append(f"Tachycardia Alert: Elevated Heart Rate ({pulse_val} bpm)")
        elif pulse_val < 60:
            alerts.append(f"Bradycardia Alert: Low Heart Rate ({pulse_val} bpm)")
            
    sugar_fasting = flt(blood_sugar_fasting)
    sugar_remarks = "Normal"
    if sugar_fasting > 0:
        if sugar_fasting > 125:
            sugar_remarks = "High / Diabetic"
            alerts.append(f"Diabetic Warning: High Fasting Blood Sugar ({sugar_fasting} mg/dL)")
        elif sugar_fasting >= 100:
            sugar_remarks = "Pre-diabetic"
            
    full_remarks = remarks or ""
    if alerts:
        clinical_tag = " [CLINICAL ALERTS: " + "; ".join(alerts) + "]"
        if clinical_tag not in full_remarks:
            full_remarks = (full_remarks + clinical_tag).strip()
            
    doc = frappe.get_doc({
        "doctype": "Patient Vitals",
        "patient": patient,
        "appointment": appointment,
        "recorded_by": user_link,
        "recorded_date_time": now_datetime(),
        "bp_systolic": sys_val or None,
        "bp_diastolic": dia_val or None,
        "pulse_rate": pulse_val or None,
        "spo2": spo2_val or None,
        "temperature": temp_val or None,
        "weight_kg": w or None,
        "height_cm": h or None,
        "bmi": bmi or None,
        "bmi_status": bmi_status,
        "blood_sugar_fasting": sugar_fasting or None,
        "blood_sugar_postprandial": flt(blood_sugar_postprandial) or None,
        "blood_sugar_random": flt(blood_sugar_random) or None,
        "sugar_remarks": sugar_remarks,
        "remarks": full_remarks
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc

def record_consultation_entry(patient, doctor, appointment=None, symptoms=None, examination_findings=None,
                              clinical_observations=None, diagnosis=None, treatment=None, follow_up_date=None):
    """Creates a finalized Medical Record consultation note."""
    if not patient or not frappe.db.exists("Patient", patient):
        frappe.throw(_("Invalid or missing Patient."))
    if not doctor or not frappe.db.exists("Doctor", doctor):
        frappe.throw(_("Invalid or missing Doctor."))
        
    doc = frappe.get_doc({
        "doctype": "Medical Record",
        "patient": patient,
        "doctor": doctor,
        "appointment": appointment,
        "visit_date": nowdate(),
        "symptoms": symptoms or "",
        "examination_findings": examination_findings or "",
        "clinical_observations": clinical_observations or "",
        "diagnosis": diagnosis or "General Assessment",
        "treatment": treatment or "",
        "follow_up_date": follow_up_date or None,
        "consultation_status": "Finalized"
    })
    doc.insert(ignore_permissions=True)
    
    # If linked to appointment, advance appointment status to Completed
    if appointment and frappe.db.exists("Appointments", appointment):
        frappe.db.set_value("Appointments", appointment, "status", "Completed")
        
    frappe.db.commit()
    return doc

def record_nursing_note(patient, nurse, appointment=None, care_status="Routine", observations=None, handover_notes=None):
    """Creates an inpatient/outpatient nursing observation and shift handover note."""
    if not patient or not frappe.db.exists("Patient", patient):
        frappe.throw(_("Invalid or missing Patient."))
        
    doc = frappe.get_doc({
        "doctype": "Nursing Note",
        "patient": patient,
        "nurse": nurse,
        "appointment": appointment,
        "note_date_time": now_datetime(),
        "care_status": care_status or "Routine",
        "observations": observations or "Patient examined. Vitals within expected range.",
        "handover_notes": handover_notes or ""
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc

def record_medication_administration(patient, prescription, medicine, prescribed_dose=None,
                                    administered_dose=None, nurse=None, status="Administered", reason_for_omission=None):
    """Logs Medication Administration Record (MAR) by a nurse."""
    if not patient or not frappe.db.exists("Patient", patient):
        frappe.throw(_("Invalid or missing Patient."))
    if not prescription or not frappe.db.exists("Prescription", prescription):
        frappe.throw(_("Invalid or missing Prescription."))
    if not medicine or not frappe.db.exists("Medicine", medicine):
        frappe.throw(_("Invalid or missing Medicine."))
        
    if status == "Omitted" and not reason_for_omission:
        frappe.throw(_("A reason for omission must be specified when status is 'Omitted'."))
        
    doc = frappe.get_doc({
        "doctype": "Medication Administration",
        "patient": patient,
        "prescription": prescription,
        "medicine": medicine,
        "prescribed_dose": prescribed_dose or "1 dose",
        "administered_dose": administered_dose or prescribed_dose or "1 dose",
        "administration_time": now_datetime(),
        "nurse": nurse,
        "status": status or "Administered",
        "reason_for_omission": reason_for_omission or ""
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc
