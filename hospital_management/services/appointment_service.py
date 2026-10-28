import datetime
import frappe
from frappe import _
from frappe.utils import getdate, nowdate, time_diff_in_seconds, add_days

def get_hospital_settings():
    try:
        return frappe.get_single("Hospital Settings")
    except Exception:
        return frappe._dict({
            "default_slot_duration": 30,
            "max_advance_booking_days": 30,
            "enable_reminders": 1
        })

def _parse_time_to_minutes(val, default_str="09:00:00"):
    if not val:
        val = default_str
    if isinstance(val, datetime.timedelta):
        return int(val.total_seconds() // 60)
    if isinstance(val, (datetime.time, datetime.datetime)):
        return val.hour * 60 + val.minute
    s = str(val).strip()
    parts = s.split(":")
    if len(parts) >= 2:
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            pass
    return 9 * 60

def _format_minutes_to_time(minutes):
    hh = (minutes // 60) % 24
    mm = minutes % 60
    return f"{hh:02d}:{mm:02d}:00"

def get_doctor_schedule_info(doctor_name):
    doc = frappe.db.get_value(
        "Doctor",
        doctor_name,
        [
            "shift_start_time", "shift_end_time", "slot_duration_mins",
            "break_start_time", "break_end_time", "working_days", "consultation_fee", "is_active"
        ],
        as_dict=True
    )
    if not doc:
        frappe.throw(_("Doctor '{0}' not found.").format(doctor_name))
        
    settings = get_hospital_settings()
    slot_dur = doc.get("slot_duration_mins") or settings.get("default_slot_duration") or 30
    if slot_dur <= 0:
        slot_dur = 30
        
    working_days = [d.strip().lower() for d in (doc.get("working_days") or "Monday, Tuesday, Wednesday, Thursday, Friday, Saturday").split(",")]
    
    return {
        "shift_start": _parse_time_to_minutes(doc.get("shift_start_time"), "09:00:00"),
        "shift_end": _parse_time_to_minutes(doc.get("shift_end_time"), "18:00:00"),
        "slot_duration": slot_dur,
        "break_start": _parse_time_to_minutes(doc.get("break_start_time"), "13:00:00"),
        "break_end": _parse_time_to_minutes(doc.get("break_end_time"), "14:00:00"),
        "working_days": working_days,
        "consultation_fee": doc.get("consultation_fee") or 0.0,
        "is_active": doc.get("is_active")
    }

def get_available_slots(doctor_name, appointment_date_str):
    """Calculates all free, non-overlapping booking slots for a doctor on a specific date."""
    sched = get_doctor_schedule_info(doctor_name)
    app_date = getdate(appointment_date_str)
    day_name = app_date.strftime("%A").lower()
    
    # Check if doctor works on this day
    if day_name not in sched["working_days"]:
        return []
        
    # Check advance booking window
    settings = get_hospital_settings()
    max_days = settings.get("max_advance_booking_days") or 30
    today = getdate(nowdate())
    if app_date < today:
        return []
    if (app_date - today).days > max_days:
        return []
        
    # Existing non-cancelled appointments
    existing_appts = frappe.get_all(
        "Appointments",
        filters={
            "doctor": doctor_name,
            "appointment_date": appointment_date_str,
            "status": ["not in", ["Cancelled", "No Show"]]
        },
        fields=["appointment_time"]
    )
    booked_mins = set()
    for ea in existing_appts:
        booked_mins.add(_parse_time_to_minutes(ea.get("appointment_time")))
        
    slots = []
    curr = sched["shift_start"]
    shift_end = sched["shift_end"]
    slot_dur = sched["slot_duration"]
    b_start = sched["break_start"]
    b_end = sched["break_end"]
    
    now_dt = frappe.utils.now_datetime()
    is_today = (app_date == today)
    now_mins = now_dt.hour * 60 + now_dt.minute
    
    while curr + slot_dur <= shift_end:
        # Check if overlaps with lunch / break
        if not (curr >= b_start and curr < b_end):
            # Check if in the past for today
            if not (is_today and curr <= now_mins):
                if curr not in booked_mins:
                    time_str = _format_minutes_to_time(curr)
                    # Pretty 12h label
                    dt_temp = datetime.datetime.strptime(time_str, "%H:%M:%S")
                    label = dt_temp.strftime("%I:%M %p")
                    slots.append({
                        "time": time_str,
                        "label": label,
                        "minutes": curr
                    })
        curr += slot_dur
        
    return slots

def validate_and_book_appointment(patient_name, doctor_name, appointment_date, appointment_time, reason_for_visit=None, phone_number=None):
    """Validates parameters server-side and books an appointment atomically."""
    if not patient_name or not frappe.db.exists("Patient", patient_name):
        frappe.throw(_("Invalid or missing Patient."))
    if not doctor_name or not frappe.db.exists("Doctor", doctor_name):
        frappe.throw(_("Invalid or missing Doctor."))
        
    app_date = getdate(appointment_date)
    today = getdate(nowdate())
    if app_date < today:
        frappe.throw(_("Appointments cannot be scheduled in the past."))
        
    settings = get_hospital_settings()
    max_days = settings.get("max_advance_booking_days") or 30
    if (app_date - today).days > max_days:
        frappe.throw(_("Appointments cannot be scheduled more than {0} days in advance.").format(max_days))
        
    sched = get_doctor_schedule_info(doctor_name)
    day_name = app_date.strftime("%A").lower()
    if day_name not in sched["working_days"]:
        frappe.throw(_("Doctor '{0}' does not consult on {1}.").format(doctor_name, day_name.capitalize()))
        
    req_mins = _parse_time_to_minutes(appointment_time)
    if req_mins < sched["shift_start"] or req_mins + sched["slot_duration"] > sched["shift_end"]:
        frappe.throw(_("Selected appointment time is outside Doctor's consultation hours."))
        
    if req_mins >= sched["break_start"] and req_mins < sched["break_end"]:
        frappe.throw(_("Selected time falls during the Doctor's scheduled break time."))
        
    time_fmt = _format_minutes_to_time(req_mins)
    
    # Concurrency Lock / Collision Detection
    # Using SQL select for update or direct verification
    collision = frappe.db.sql(
        """
        SELECT name FROM `tabAppointments`
        WHERE doctor = %s AND appointment_date = %s AND appointment_time = %s
        AND status NOT IN ('Cancelled', 'No Show')
        FOR UPDATE
        """,
        (doctor_name, app_date, time_fmt),
        as_dict=True
    )
    if collision:
        frappe.throw(_("This slot ({0} at {1}) has just been booked. Please choose another slot.").format(app_date, time_fmt))
        
    patient_phone = phone_number or frappe.db.get_value("Patient", patient_name, "phone")
    fee = sched["consultation_fee"]
    
    doc = frappe.get_doc({
        "doctype": "Appointments",
        "patient": patient_name,
        "doctor": doctor_name,
        "appointment_date": app_date,
        "appointment_time": time_fmt,
        "reason_for_visit": reason_for_visit or "General Consultation",
        "status": "Confirmed",
        "consultation_fee": fee,
        "phone_number": patient_phone,
        "confirmation_sent": 1 if settings.get("enable_reminders") else 0
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc
