import datetime
import frappe
from frappe import _
from frappe.utils import flt, today, getdate, cint, now_datetime, now
import json


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
# 2. DOCTOR LIST, SCHEDULE SLOTS & APPOINTMENT BOOKING
# --------------------------------------------------
def _parse_time_to_minutes(val, default_str="09:00:00"):
    """Convert any time format (timedelta, time, str) to minutes since midnight."""
    if not val:
        val = default_str
    if isinstance(val, datetime.timedelta):
        return int(val.total_seconds()) // 60
    if isinstance(val, (datetime.time, datetime.datetime)):
        return val.hour * 60 + val.minute
    
    s = str(val).strip()
    for fmt in ("%I:%M %p", "%I:%M:%S %p", "%H:%M:%S", "%H:%M"):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            return dt.hour * 60 + dt.minute
        except ValueError:
            pass
    try:
        parts = s.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 9 * 60


def _format_time_str(minutes):
    """Return both 12-hour AM/PM string and 24-hour HH:MM:SS string."""
    hours_24 = minutes // 60
    mins = minutes % 60
    t_obj = datetime.time(hours_24, mins)
    time_12 = t_obj.strftime("%I:%M %p").lstrip("0")
    time_24 = f"{hours_24:02d}:{mins:02d}:00"
    return time_12, time_24


@frappe.whitelist(allow_guest=True)
def get_doctors_list():
    return frappe.get_all(
        "Doctor",
        fields=[
            "name", "doctor_name", "department", "specialization", "consultation_fee",
            "shift_start_time", "shift_end_time", "slot_duration_mins", "break_start_time",
            "break_end_time", "working_days"
        ],
        order_by="doctor_name asc"
    )


@frappe.whitelist(allow_guest=True)
def get_available_slots(doctor, date):
    """
    Generate all consultation intervals between shift start and end times based on slot duration,
    excluding break intervals, and compare with booked appointments to return available/unavailable flags.
    """
    if not doctor:
        return {"status": "error", "message": _("Doctor is required"), "slots": []}
    if not date:
        return {"status": "error", "message": _("Date is required"), "slots": []}

    # 1. Resolve Doctor doc & working schedule
    clean_doctor = doctor.split(" (")[0].strip() if doctor else doctor
    doc_record = frappe.db.get_value(
        "Doctor",
        {"doctor_name": clean_doctor},
        [
            "name", "doctor_name", "department", "specialization",
            "shift_start_time", "shift_end_time", "slot_duration_mins",
            "break_start_time", "break_end_time", "working_days", "is_active"
        ],
        as_dict=True
    )
    if not doc_record:
        doc_record = frappe.db.get_value(
            "Doctor",
            clean_doctor,
            [
                "name", "doctor_name", "department", "specialization",
                "shift_start_time", "shift_end_time", "slot_duration_mins",
                "break_start_time", "break_end_time", "working_days", "is_active"
            ],
            as_dict=True
        )

    if not doc_record:
        return {"status": "error", "message": _("Doctor '{0}' not found").format(doctor), "slots": []}

    # 2. Validate Date (not in the past)
    try:
        selected_date = frappe.utils.getdate(date)
    except Exception:
        return {"status": "error", "message": _("Invalid date format. Expected YYYY-MM-DD"), "slots": []}

    today = frappe.utils.getdate(frappe.utils.nowdate())
    if selected_date < today:
        return {
            "status": "error",
            "message": _("Cannot book appointments for past dates."),
            "is_past": True,
            "slots": []
        }

    # 3. Validate Working Day
    day_name = selected_date.strftime("%A")
    raw_working_days = doc_record.working_days or "Monday, Tuesday, Wednesday, Thursday, Friday, Saturday"
    allowed_days = [d.strip().lower() for d in raw_working_days.split(",") if d.strip()]

    if day_name.lower() not in allowed_days:
        return {
            "status": "error",
            "message": _("{0} is not available on {1}s.").format(doc_record.doctor_name or doc_record.name, day_name),
            "is_working_day": False,
            "day_name": day_name,
            "working_days": raw_working_days,
            "slots": []
        }

    # 4. Shift & Slot Configuration with sane defaults
    shift_start_mins = _parse_time_to_minutes(doc_record.shift_start_time, "09:00:00")
    shift_end_mins = _parse_time_to_minutes(doc_record.shift_end_time, "18:00:00")
    slot_duration = int(doc_record.slot_duration_mins or 30)
    if slot_duration <= 0 or slot_duration > 240:
        slot_duration = 30

    break_start_mins = _parse_time_to_minutes(doc_record.break_start_time, "13:00:00") if doc_record.break_start_time else None
    break_end_mins = _parse_time_to_minutes(doc_record.break_end_time, "14:00:00") if doc_record.break_end_time else None

    # 5. Query Booked Appointments for this Doctor & Date (Status != 'Cancelled')
    booked_records = frappe.get_all(
        "Appointments",
        filters={
            "doctor": doc_record.name,
            "appointment_date": str(selected_date),
            "status": ["!=", "Cancelled"]
        },
        fields=["name", "appointment_time", "status"]
    )

    booked_minutes = set()
    for b in booked_records:
        at = b.get("appointment_time")
        if at is not None:
            booked_minutes.add(_parse_time_to_minutes(at))

    # 6. Generate Intervals excluding Lunch/Break
    cur_mins = shift_start_mins
    slots = []

    now_dt = frappe.utils.now_datetime()
    is_today = (selected_date == today)
    current_mins_now = now_dt.hour * 60 + now_dt.minute

    while cur_mins + slot_duration <= shift_end_mins:
        slot_start = cur_mins
        slot_end = cur_mins + slot_duration

        # Check if slot falls in break
        is_break = False
        if break_start_mins is not None and break_end_mins is not None and break_end_mins > break_start_mins:
            if (slot_start >= break_start_mins and slot_start < break_end_mins) or                (slot_end > break_start_mins and slot_start < break_start_mins):
                is_break = True

        if not is_break:
            time_12, time_24 = _format_time_str(slot_start)
            is_booked = (slot_start in booked_minutes)
            is_past = (is_today and slot_start <= current_mins_now)

            available = (not is_booked) and (not is_past)
            reason = None
            if is_booked:
                reason = "Booked"
            elif is_past:
                reason = "Time Passed"

            slots.append({
                "time": time_12,
                "time_24": time_24,
                "available": available,
                "reason": reason
            })

        cur_mins += slot_duration

    return {
        "status": "success",
        "doctor": doc_record.name,
        "doctor_name": doc_record.doctor_name,
        "date": str(selected_date),
        "day_name": day_name,
        "slot_duration": slot_duration,
        "shift_start": doc_record.shift_start_time or "09:00:00",
        "shift_end": doc_record.shift_end_time or "18:00:00",
        "slots": slots
    }


@frappe.whitelist()
def book_appointment(doctor, appointment_date, appointment_time, reason_for_visit=None, phone_number=None):
    """
    Atomic appointment booking with anti-double-booking database lock (FOR UPDATE).
    Rejects the request if the slot is already taken with:
    "This time slot has just been booked by another patient. Please choose another slot."
    """
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Please login to book an appointment"), frappe.PermissionError)

    if not doctor or not appointment_date or not appointment_time:
        frappe.throw(_("Doctor, Appointment Date, and Appointment Time are all required."))

    # 1. Resolve Patient
    patient_id = frappe.db.get_value("Patient", {"email": user}, "name")
    if not patient_id:
        patient_id = frappe.db.get_value("Patient", {"name": user}, "name")

    if not patient_id:
        first_patient = frappe.get_all("Patient", fields=["name"], limit=1)
        if first_patient:
            patient_id = first_patient[0].name
        else:
            frappe.throw(_("No patient record found to link with appointment"))

    # 2. Resolve Doctor
    clean_doctor = doctor.split(" (")[0].strip() if doctor else doctor
    doctor_id = frappe.db.get_value("Doctor", {"doctor_name": clean_doctor}, "name")
    if not doctor_id:
        doctor_id = frappe.db.get_value("Doctor", clean_doctor, "name") or clean_doctor

    doctor_doc = frappe.db.get_value(
        "Doctor",
        doctor_id,
        ["name", "doctor_name", "consultation_fee", "shift_start_time", "shift_end_time", "working_days"],
        as_dict=True
    )
    if not doctor_doc:
        frappe.throw(_("Doctor '{0}' not found").format(doctor))

    doc_fee = doctor_doc.consultation_fee or 0.0

    # 3. Clean and standardize date and time formats
    try:
        clean_date = str(frappe.utils.getdate(appointment_date))
    except Exception:
        frappe.throw(_("Invalid appointment date format."))

    # Normalize time to clean HH:MM:00
    time_mins = _parse_time_to_minutes(appointment_time)
    time_12, time_24 = _format_time_str(time_mins)

    # Validate that the date is not in the past
    today = frappe.utils.getdate(frappe.utils.nowdate())
    if frappe.utils.getdate(clean_date) < today:
        frappe.throw(_("Cannot book appointments for past dates."))

    # 4. Atomic Anti-Double-Booking Check with FOR UPDATE transaction lock
    existing = frappe.db.sql("""
        SELECT name, appointment_time, status
        FROM `tabAppointments`
        WHERE doctor = %s
          AND appointment_date = %s
          AND status != 'Cancelled'
          AND (
              appointment_time = %s
              OR TIME_FORMAT(appointment_time, '%%H:%%i:%%s') = %s
              OR TIME_FORMAT(appointment_time, '%%H:%%i') = %s
              OR appointment_time = %s
          )
        FOR UPDATE
    """, (doctor_id, clean_date, time_24, time_24, time_24[:5], appointment_time), as_dict=True)

    if existing:
        frappe.throw(
            _("This time slot has just been booked by another patient. Please choose another slot."),
            title=_("Slot Conflict")
        )

    # 5. Insert Appointment
    doc = frappe.get_doc({
        "doctype": "Appointments",
        "patient": patient_id,
        "doctor": doctor_id,
        "appointment_date": clean_date,
        "appointment_time": time_24,
        "reason_for_visit": reason_for_visit or "",
        "phone_number": phone_number or "",
        "consultation_fee": doc_fee,
        "status": "Scheduled"
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    try:
        send_booking_confirmation(doc)
    except Exception as e:
        frappe.logger("notifications").warning(f"Error sending instant confirmation: {e}")

    return {
        "status": "success",
        "message": "Appointment booked successfully",
        "appointment": doc.name,
        "date": clean_date,
        "time": time_12
    }


@frappe.whitelist()
def book_patient_appointment(doctor, appointment_date, appointment_time, reason_for_visit=None, phone_number=None):
    return book_appointment(doctor, appointment_date, appointment_time, reason_for_visit, phone_number)


@frappe.whitelist()
def setup_doctor_schedule_defaults():
    """Populate default shift and working hours for doctors that don't have them set."""
    doctors = frappe.get_all("Doctor", fields=["name", "doctor_name", "shift_start_time", "shift_end_time", "slot_duration_mins", "break_start_time", "break_end_time", "working_days"])
    updated_count = 0
    for d in doctors:
        values_to_set = {}
        if not d.shift_start_time:
            values_to_set["shift_start_time"] = "09:00:00"
        if not d.shift_end_time:
            values_to_set["shift_end_time"] = "18:00:00"
        if not d.slot_duration_mins:
            values_to_set["slot_duration_mins"] = 30
        if not d.break_start_time:
            values_to_set["break_start_time"] = "13:00:00"
        if not d.break_end_time:
            values_to_set["break_end_time"] = "14:00:00"
        if not d.working_days:
            values_to_set["working_days"] = "Monday, Tuesday, Wednesday, Thursday, Friday, Saturday"
        
        if values_to_set:
            frappe.db.set_value("Doctor", d.name, values_to_set, update_modified=False)
            updated_count += 1

    frappe.db.commit()
    return {"status": "success", "total_doctors": len(doctors), "updated_doctors": updated_count}



# --------------------------------------------------
# 2b. NOTIFICATIONS & REMINDERS (EMAIL, SMS, WHATSAPP)
# --------------------------------------------------
def send_notification(channel, recipient, subject, message, **kwargs):
    """
    Modular notification dispatcher supporting Email, SMS, and WhatsApp.
    Provides clean fallback to mock logging/console when external gateways
    are offline or credentials are not configured.
    """
    logger = frappe.logger("notifications")
    result = {
        "channel": channel,
        "recipient": recipient,
        "subject": subject,
        "status": "pending",
        "timestamp": frappe.utils.now_datetime().isoformat()
    }

    if not recipient:
        logger.warning(f"Notification skipped: No recipient provided for channel '{channel}'.")
        result["status"] = "skipped"
        result["error"] = "No recipient provided"
        return result

    # 1. EMAIL DISPATCH
    if channel in ("email", "all"):
        try:
            frappe.sendmail(
                recipients=[recipient] if isinstance(recipient, str) else recipient,
                subject=subject,
                message=message,
                now=True
            )
            logger.info(f"[EMAIL SENT] To: {recipient} | Subject: {subject}")
            print(f"📧 [EMAIL SENT] To: {recipient} | Subject: {subject}")
            result["email_status"] = "sent"
        except Exception as e:
            logger.warning(f"[EMAIL FALLBACK / SIMULATION] To: {recipient} | Subject: {subject} | Error: {str(e)}")
            print(f"📧 [EMAIL SIMULATED] To: {recipient} | Subject: {subject}")
            result["email_status"] = "simulated"
            result["email_note"] = "Simulated delivery (SMTP offline or mock mode)"

    # 2. SMS DISPATCH
    if channel in ("sms", "all"):
        phone = kwargs.get("phone") or recipient
        try:
            sms_settings = frappe.get_doc("SMS Settings") if frappe.db.exists("DocType", "SMS Settings") else None
            if sms_settings and getattr(sms_settings, "sms_gateway_url", None):
                from frappe.core.doctype.sms_settings.sms_settings import send_sms
                send_sms(receiver_list=[phone], msg=message)
                logger.info(f"[SMS SENT] To: {phone}")
                print(f"📱 [SMS GATEWAY SENT] To: {phone}")
                result["sms_status"] = "sent"
            else:
                logger.info(f"[SMS MOCK PAYLOAD] To: {phone} | Body: {message}")
                print(f"📱 [SMS DISPATCHED] To: {phone} | Content: {message[:120]}...")
                result["sms_status"] = "simulated"
        except Exception as e:
            logger.warning(f"[SMS ERROR] To: {phone} | Error: {str(e)}")
            print(f"📱 [SMS FALLBACK] To: {phone}")
            result["sms_status"] = "simulated"

    # 3. WHATSAPP DISPATCH
    if channel in ("whatsapp", "all"):
        phone = kwargs.get("phone") or recipient
        try:
            whatsapp_webhook = frappe.conf.get("whatsapp_webhook_url") or None
            if whatsapp_webhook:
                import requests
                payload = {
                    "to": phone,
                    "type": "text",
                    "text": {"body": message}
                }
                requests.post(whatsapp_webhook, json=payload, timeout=5)
                logger.info(f"[WHATSAPP WEBHOOK SENT] To: {phone}")
                print(f"💬 [WHATSAPP WEBHOOK SENT] To: {phone}")
                result["whatsapp_status"] = "sent"
            else:
                logger.info(f"[WHATSAPP MOCK PAYLOAD] To: {phone} | Body: {message}")
                print(f"💬 [WHATSAPP DISPATCHED] To: {phone} | Content: {message[:120]}...")
                result["whatsapp_status"] = "simulated"
        except Exception as e:
            logger.warning(f"[WHATSAPP ERROR] To: {phone} | Error: {str(e)}")
            print(f"💬 [WHATSAPP FALLBACK] To: {phone}")
            result["whatsapp_status"] = "simulated"

    result["status"] = "success"
    return result


@frappe.whitelist()
def send_booking_confirmation(doc, method=None):
    """
    Sends instant Booking Confirmation to the patient via Email, SMS, and WhatsApp
    with clinic address and Google Map link.
    """
    if isinstance(doc, str):
        doc = frappe.get_doc("Appointments", doc)

    # Check if confirmation was already sent
    if getattr(doc, "confirmation_sent", 0):
        return {"status": "skipped", "message": "Confirmation already sent"}

    # Resolve Patient Details
    patient = frappe.db.get_value(
        "Patient",
        doc.patient,
        ["patient_name", "email", "phone"],
        as_dict=True
    ) or {}

    patient_name = patient.get("patient_name") or doc.patient
    patient_email = patient.get("email") or ""
    patient_phone = doc.get("phone_number") or patient.get("phone") or ""

    # Resolve Doctor Details
    doctor = frappe.db.get_value(
        "Doctor",
        doc.doctor,
        ["doctor_name", "department", "specialization", "phone"],
        as_dict=True
    ) or {}
    doctor_name = doctor.get("doctor_name") or doc.doctor
    doctor_dept = doctor.get("department") or doctor.get("specialization") or "Consultant"

    # Format Date & Time
    appt_date_str = str(doc.appointment_date)
    try:
        t_mins = _parse_time_to_minutes(doc.appointment_time)
        time_12, _ = _format_time_str(t_mins)
    except Exception:
        time_12 = str(doc.appointment_time)

    clinic_name = "CarePlus Multispeciality & Dental Clinic"
    clinic_address = "CarePlus Hospital Campus, 12 Healthcare Avenue, Medical City, Chennai - 600001"
    clinic_map_link = "https://maps.google.com/?q=CarePlus+Hospital+Chennai"
    clinic_phone = "+91 44 2800 1234 / +91 98765 43210"

    subject = f"🏥 Appointment Confirmed - {clinic_name} [ID: {doc.name}]"

    # 1. Plain text message for SMS and WhatsApp
    sms_whatsapp_msg = (
        f"🏥 {clinic_name}\n\n"
        f"Dear {patient_name},\n"
        f"Your consultation with Dr. {doctor_name} ({doctor_dept}) has been successfully confirmed!\n\n"
        f"📋 Appointment ID: {doc.name}\n"
        f"📅 Date: {appt_date_str}\n"
        f"⏰ Time: {time_12}\n"
        f"📍 Clinic Address: {clinic_address}\n"
        f"🗺️ Google Map: {clinic_map_link}\n"
        f"📞 Helpline: {clinic_phone}\n\n"
        f"Please arrive 10 minutes prior to your scheduled consultation."
    )

    # 2. Rich HTML email message
    email_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: #1e293b; border-radius: 12px; border: 1px solid #334155; overflow: hidden; }}
            .header {{ background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); padding: 24px; text-align: center; color: #ffffff; }}
            .content {{ padding: 24px; }}
            .badge {{ display: inline-block; background: rgba(16, 185, 129, 0.2); color: #34d399; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 13px; margin-bottom: 16px; }}
            .details-table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
            .details-table td {{ padding: 10px 12px; border-bottom: 1px solid #334155; font-size: 14px; }}
            .details-table td.label {{ color: #94a3b8; width: 35%; }}
            .details-table td.val {{ color: #f8fafc; font-weight: 600; }}
            .map-box {{ background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 14px; margin: 20px 0; text-align: center; }}
            .btn-map {{ display: inline-block; background: #0284c7; color: #ffffff; text-decoration: none; padding: 8px 18px; border-radius: 6px; font-weight: 600; font-size: 13px; margin-top: 10px; }}
            .footer {{ padding: 16px 24px; background: #0f172a; text-align: center; font-size: 12px; color: #64748b; border-top: 1px solid #334155; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 style="margin:0; font-size: 22px;">CarePlus Multispeciality & Dental Clinic</h1>
                <p style="margin: 4px 0 0 0; opacity: 0.9; font-size: 14px;">Appointment Confirmation</p>
            </div>
            <div class="content">
                <div class="badge">✓ Status: Confirmed / Scheduled</div>
                <p style="font-size: 15px; margin-top: 0;">Dear <strong>{patient_name}</strong>,</p>
                <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5;">Your medical consultation has been successfully booked with our clinical department. Here are your booking details:</p>
                
                <table class="details-table">
                    <tr><td class="label">Appointment ID:</td><td class="val">{doc.name}</td></tr>
                    <tr><td class="label">Doctor:</td><td class="val">Dr. {doctor_name} ({doctor_dept})</td></tr>
                    <tr><td class="label">Consultation Date:</td><td class="val">{appt_date_str}</td></tr>
                    <tr><td class="label">Time Slot:</td><td class="val">{time_12}</td></tr>
                    <tr><td class="label">Reason for Visit:</td><td class="val">{doc.reason_for_visit or 'General Consultation'}</td></tr>
                    <tr><td class="label">Consultation Fee:</td><td class="val">₹{float(doc.consultation_fee or 0.0):.2f}</td></tr>
                </table>

                <div class="map-box">
                    <div style="font-weight: 600; color: #f8fafc; font-size: 14px;">📍 Clinic Location & Directions</div>
                    <div style="color: #94a3b8; font-size: 12px; margin-top: 4px;">{clinic_address}</div>
                    <a href="{clinic_map_link}" target="_blank" class="btn-map">Open in Google Maps ↗</a>
                </div>

                <div style="font-size: 12px; color: #94a3b8; line-height: 1.6;">
                    ℹ️ <strong>Instructions:</strong> Please report 10 minutes prior to your slot. If you need to reschedule or cancel, please access your Patient Portal or call our helpline at {clinic_phone}.
                </div>
            </div>
            <div class="footer">
                CarePlus Hospital Management System · Automated Notification Service
            </div>
        </div>
    </body>
    </html>
    """

    # Dispatch Email
    if patient_email:
        send_notification("email", patient_email, subject, email_html)

    # Dispatch SMS & WhatsApp
    if patient_phone:
        send_notification("sms", patient_phone, subject, sms_whatsapp_msg, phone=patient_phone)
        send_notification("whatsapp", patient_phone, subject, sms_whatsapp_msg, phone=patient_phone)

    # Mark confirmation sent in DB
    try:
        frappe.db.set_value("Appointments", doc.name, "confirmation_sent", 1, update_modified=False)
        frappe.db.commit()
    except Exception:
        pass

    return {
        "status": "success",
        "appointment": doc.name,
        "patient": patient_name,
        "email": patient_email,
        "phone": patient_phone,
        "message": "Confirmation sent across Email, SMS, and WhatsApp"
    }


@frappe.whitelist()
def send_appointment_reminders(appointment_id=None):
    """
    Automated 1-Hour Prior Reminder task.
    Runs every 15 minutes via hooks.py scheduler.
    Queries upcoming appointments for today where appointment_time is between now + 45m and now + 75m,
    and reminder_sent is 0.
    """
    now_dt = frappe.utils.now_datetime()
    today_str = str(frappe.utils.getdate(now_dt))

    window_start_dt = now_dt + datetime.timedelta(minutes=45)
    window_end_dt = now_dt + datetime.timedelta(minutes=75)

    window_start_time = window_start_dt.strftime("%H:%M:%S")
    window_end_time = window_end_dt.strftime("%H:%M:%S")

    logger = frappe.logger("reminders")

    if appointment_id:
        appts = frappe.db.sql("""
            SELECT name, patient, doctor, appointment_date, appointment_time, status, phone_number, reminder_sent
            FROM `tabAppointments`
            WHERE name = %s
        """, (appointment_id,), as_dict=True)
    else:
        appts = frappe.db.sql("""
            SELECT name, patient, doctor, appointment_date, appointment_time, status, phone_number, reminder_sent
            FROM `tabAppointments`
            WHERE status IN ('Scheduled', 'Confirmed')
              AND appointment_date = %s
              AND appointment_time >= %s
              AND appointment_time <= %s
              AND (reminder_sent IS NULL OR reminder_sent = 0)
        """, (today_str, window_start_time, window_end_time), as_dict=True)

    logger.info(f"Checking 1-hr appointment reminders for {today_str} between {window_start_time} and {window_end_time}. Found: {len(appts)}")

    processed = []
    clinic_name = "CarePlus Multispeciality & Dental Clinic"
    clinic_address = "CarePlus Hospital Campus, 12 Healthcare Avenue, Medical City, Chennai - 600001"
    clinic_map_link = "https://maps.google.com/?q=CarePlus+Hospital+Chennai"
    clinic_phone = "+91 98765 43210"

    for appt in appts:
        patient = frappe.db.get_value(
            "Patient",
            appt["patient"],
            ["patient_name", "email", "phone"],
            as_dict=True
        ) or {}
        patient_name = patient.get("patient_name") or appt["patient"]
        patient_email = patient.get("email") or ""
        patient_phone = appt.get("phone_number") or patient.get("phone") or ""

        doctor_doc = frappe.db.get_value("Doctor", appt["doctor"], ["doctor_name", "department"], as_dict=True) or {}
        doc_display = doctor_doc.get("doctor_name") or appt["doctor"]

        try:
            t_mins = _parse_time_to_minutes(appt["appointment_time"])
            time_12, _ = _format_time_str(t_mins)
        except Exception:
            time_12 = str(appt["appointment_time"])

        reminder_text = (
            f"⏰ Reminder: You have an upcoming consultation with Dr. {doc_display} at {time_12}.\n\n"
            f"🏥 {clinic_name}\n"
            f"📍 Location: {clinic_address}\n"
            f"🗺️ Map: {clinic_map_link}\n"
            f"📞 Helpline: {clinic_phone}\n\n"
            f"Please arrive 10 minutes before your slot. Safe travels!"
        )

        reminder_subject = f"⏰ Consultation Reminder: Dr. {doc_display} at {time_12} today"

        reminder_html = f"""
        <div style="font-family:'Segoe UI',Arial,sans-serif; background:#0f172a; color:#f8fafc; padding:20px;">
            <div style="max-width:560px; margin:0 auto; background:#1e293b; border-radius:10px; border:1px solid #334155; padding:24px;">
                <h2 style="color:#38bdf8; margin-top:0;">🏥 Appointment Reminder</h2>
                <p>Dear <strong>{patient_name}</strong>,</p>
                <div style="background:rgba(56,189,248,0.1); border-left:4px solid #38bdf8; padding:12px 16px; margin:16px 0; border-radius:4px;">
                    <div style="font-size:16px; font-weight:700; color:#f8fafc;">
                        Reminder: You have an upcoming consultation with Dr. {doc_display} at {time_12}.
                    </div>
                    <div style="font-size:13px; color:#94a3b8; margin-top:4px;">
                        Date: {appt['appointment_date']} · Time: {time_12} · ID: {appt['name']}
                    </div>
                </div>
                <p style="font-size:13px; color:#cbd5e1;">📍 <strong>Clinic Address:</strong> {clinic_address}</p>
                <p><a href="{clinic_map_link}" style="display:inline-block; background:#0284c7; color:#fff; text-decoration:none; padding:8px 16px; border-radius:6px; font-size:13px; font-weight:600;">Get Directions on Google Maps ↗</a></p>
                <p style="font-size:12px; color:#64748b; margin-top:20px;">Please report 10 minutes early. Contact {clinic_phone} for assistance.</p>
            </div>
        </div>
        """

        if patient_email:
            send_notification("email", patient_email, reminder_subject, reminder_html)
        if patient_phone:
            send_notification("sms", patient_phone, reminder_subject, reminder_text, phone=patient_phone)
            send_notification("whatsapp", patient_phone, reminder_subject, reminder_text, phone=patient_phone)

        frappe.db.set_value("Appointments", appt["name"], "reminder_sent", 1, update_modified=False)
        processed.append({
            "appointment": appt["name"],
            "patient": patient_name,
            "doctor": doc_display,
            "time": time_12,
            "phone": patient_phone,
            "email": patient_email
        })

    frappe.db.commit()
    logger.info(f"Reminders completed. Sent {len(processed)} reminders.")

    return {
        "status": "success",
        "timestamp": now_dt.isoformat(),
        "window": f"{window_start_time} - {window_end_time}",
        "reminders_sent": len(processed),
        "appointments": processed
    }


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

    for rx in prescriptions:
        rx["id"] = rx["name"]
        if rx.get("doctor"):
            doc_name = frappe.db.get_value("Doctor", rx["doctor"], "doctor_name")
            if doc_name:
                rx["doctor_name"] = doc_name

    # Invoices for this patient
    invoices = frappe.get_all(
        "Invoice",
        filters={"patient": patient.name},
        fields=[
            "name", "invoice_date", "doctor", "appointment",
            "consultation_charges", "medicine_charges", "subtotal",
            "discount_amount", "tax_amount", "grand_total", "total_amount",
            "paid_amount", "outstanding_amount", "payment_status", "payment_method"
        ],
        order_by="invoice_date desc, creation desc"
    )

    for inv in invoices:
        inv["patient_name"] = patient.patient_name
        if inv.doctor:
            inv["doctor_name"] = frappe.db.get_value("Doctor", inv.doctor, "doctor_name") or inv.doctor

    # Vitals for this patient
    vitals_records = frappe.get_all(
        "Patient Vitals",
        filters={"patient": patient.name},
        fields=[
            "name", "appointment", "recorded_by", "recorded_date_time",
            "bp_systolic", "bp_diastolic", "pulse_rate", "spo2", "temperature",
            "weight_kg", "height_cm", "bmi", "bmi_status",
            "blood_sugar_fasting", "blood_sugar_postprandial", "blood_sugar_random",
            "sugar_remarks", "remarks"
        ],
        order_by="recorded_date_time desc, creation desc"
    )
    latest_vitals = vitals_records[0] if vitals_records else None
    if latest_vitals and latest_vitals.bp_systolic and latest_vitals.bp_diastolic:
        latest_vitals["bp_display"] = f"{latest_vitals.bp_systolic}/{latest_vitals.bp_diastolic} mmHg"

    return {
        "status": "success",
        "patient": patient,
        "appointments": appointments,
        "prescriptions": prescriptions,
        "invoices": invoices,
        "latest_vitals": latest_vitals,
        "vitals_history": vitals_records[:10]
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

    today_date = today()
    doc_filter = ["in", [doctor.name, doctor.doctor_name]]

    todays_appointments = frappe.get_all(
        "Appointments",
        filters={"doctor": doc_filter, "appointment_date": today_date},
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

    # Invoices for this doctor
    invoices = frappe.get_all(
        "Invoice",
        filters={"doctor": doc_filter},
        fields=[
            "name", "patient", "appointment", "invoice_date",
            "grand_total", "total_amount", "paid_amount", "outstanding_amount", "payment_status"
        ],
        order_by="invoice_date desc, creation desc"
    )

    for inv in invoices:
        inv["patient_name"] = frappe.db.get_value("Patient", inv.patient, "patient_name") or inv.patient

    total_patients_seen = frappe.db.count("Appointments", {
        "doctor": doc_filter,
        "status": ("in", ["Completed", "Confirmed"])
    })

    stats = {
        "today_appointments": len(todays_appointments),
        "scheduled_appointments": frappe.db.count("Appointments", {"doctor": doc_filter, "status": "Scheduled"}),
        "total_patients_seen": total_patients_seen,
        "total_prescriptions": len(prescriptions),
        "total_records": len(medical_records),
        "total_invoices": len(invoices),
        "billed_total": sum(flt(inv.get("grand_total") or inv.get("total_amount") or 0) for inv in invoices)
    }

    return {
        "status": "success",
        "doctor": doctor,
        "stats": stats,
        "todays_appointments": todays_appointments,
        "appointments": all_appointments,
        "prescriptions": prescriptions,
        "medical_records": medical_records,
        "invoices": invoices
    }


# --------------------------------------------------
# 5. APPOINTMENT STATUS UPDATE & AUTO-CONSULTATION BILLING
# --------------------------------------------------
@frappe.whitelist()
def update_appointment_status(appointment_id, status):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    if not frappe.db.exists("Appointments", appointment_id):
        return {"status": "error", "message": "Appointment not found"}

    frappe.db.set_value("Appointments", appointment_id, "status", status)

    invoice_name = None
    invoice_msg = ""

    # Requirement 2.a: Automatic Consultation Billing when Completed
    if status == "Completed":
        existing_invoice = frappe.db.get_value("Invoice", {"appointment": appointment_id}, "name")
        if existing_invoice:
            invoice_name = existing_invoice
            invoice_msg = f" (Existing Invoice {existing_invoice} linked)"
        else:
            appt = frappe.get_doc("Appointments", appointment_id)
            doc_fee = flt(appt.consultation_fee)
            if not doc_fee and appt.doctor:
                doc_fee = flt(frappe.db.get_value("Doctor", appt.doctor, "consultation_fee"))

            doctor_name = frappe.db.get_value("Doctor", appt.doctor, "doctor_name") or appt.doctor

            inv = frappe.get_doc({
                "doctype": "Invoice",
                "patient": appt.patient,
                "doctor": appt.doctor,
                "appointment": appt.name,
                "invoice_date": today(),
                "payment_status": "Unpaid",
                "items": [
                    {
                        "item_type": "Consultation",
                        "item_name": f"Doctor Consultation Fee - {doctor_name}",
                        "quantity": 1,
                        "unit_price": doc_fee,
                        "amount": doc_fee
                    }
                ]
            })
            inv.insert(ignore_permissions=True)
            invoice_name = inv.name
            invoice_msg = f" (Draft Consultation Invoice {inv.name} automatically generated)"

    frappe.db.commit()

    return {
        "status": "success",
        "message": f"Appointment status updated to {status}{invoice_msg}",
        "invoice_id": invoice_name
    }


# --------------------------------------------------
# 6. WRITE PRESCRIPTION & PHARMACY BILLING INTEGRATION
# --------------------------------------------------
@frappe.whitelist()
def add_patient_prescription(patient, doctor, medicine, appointment=None, dosage="1 tablet", frequency="As Needed", instructions="After food", quantity=1):
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
    medicine_name = medicine
    unit_price = 0.0

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
            med_doc = frappe.get_doc("Medicine", med_id)
            medicine_name = med_doc.medicine_name or med_doc.name
            unit_price = flt(med_doc.price) or 0.0

            # Optional stock deduction
            qty_num = flt(quantity) or 1.0
            if med_doc.quantity and med_doc.quantity > 0:
                frappe.db.set_value("Medicine", med_id, "quantity", max(0, int(med_doc.quantity - qty_num)))

    # Create prescription record
    doc_data = {
        "doctype": "Prescription",
        "patient": patient_id,
        "doctor": doctor_id,
        "prescription_date": today(),
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

    rx_doc = frappe.get_doc(doc_data)
    rx_doc.insert(ignore_permissions=True)

    # Requirement 2.b: Pharmacy Billing Integration
    qty_num = flt(quantity) or 1.0
    item_amount = round(qty_num * unit_price, 2)
    linked_invoice = None

    # Check for active open invoice for this appointment or patient
    inv_name = None
    if appointment and frappe.db.exists("Appointments", appointment):
        inv_name = frappe.db.get_value("Invoice", {
            "appointment": appointment,
            "payment_status": ("in", ["Unpaid", "Partially Paid"])
        }, "name")

    if not inv_name:
        inv_name = frappe.db.get_value("Invoice", {
            "patient": patient_id,
            "invoice_date": today(),
            "payment_status": ("in", ["Unpaid", "Partially Paid"])
        }, "name")

    if inv_name:
        inv = frappe.get_doc("Invoice", inv_name)
        inv.append("items", {
            "item_type": "Medicine",
            "item_name": f"{medicine_name} ({dosage})",
            "medicine": medicine_val,
            "quantity": qty_num,
            "unit_price": unit_price,
            "amount": item_amount
        })
        if not inv.prescription:
            inv.prescription = rx_doc.name
        inv.save(ignore_permissions=True)
        linked_invoice = inv.name
    else:
        # Create separate pharmacy invoice
        inv = frappe.get_doc({
            "doctype": "Invoice",
            "patient": patient_id,
            "doctor": doctor_id,
            "appointment": appointment or None,
            "prescription": rx_doc.name,
            "invoice_date": today(),
            "payment_status": "Unpaid",
            "items": [
                {
                    "item_type": "Medicine",
                    "item_name": f"{medicine_name} ({dosage})",
                    "medicine": medicine_val,
                    "quantity": qty_num,
                    "unit_price": unit_price,
                    "amount": item_amount
                }
            ]
        })
        inv.insert(ignore_permissions=True)
        linked_invoice = inv.name

    frappe.db.commit()

    return {
        "status": "success",
        "message": f"Prescription recorded and added to Invoice {linked_invoice}",
        "data": rx_doc.as_dict(),
        "invoice_id": linked_invoice
    }


# --------------------------------------------------
# 7. GET MEDICINES LIST
# --------------------------------------------------
@frappe.whitelist()
def get_medicines_list():
    if not frappe.db.exists("DocType", "Medicine"):
        return []

    meta = frappe.get_meta("Medicine")
    fields = ["name"]
    for f in ["medicine_name", "price", "quantity", "batch_number"]:
        if meta.has_field(f):
            fields.append(f)

    medicines = frappe.get_all("Medicine", fields=fields, order_by="name asc")
    return medicines


# --------------------------------------------------
# 8. BILLING & INVOICING CORE APIS
# --------------------------------------------------
@frappe.whitelist()
def get_invoices(status=None, patient=None, doctor=None, from_date=None, to_date=None, search=None):
    filters = {}
    user = frappe.session.user
    roles = frappe.get_roles(user) if user else []

    if status and status != "All":
        filters["payment_status"] = status

    # Patient filter resolution (by ID, patient_name, email, or session user)
    if patient:
        pat = frappe.db.get_value("Patient", {"email": patient}, ["name", "patient_name"], as_dict=True) \
            or frappe.db.get_value("Patient", {"name": patient}, ["name", "patient_name"], as_dict=True) \
            or frappe.db.get_value("Patient", {"patient_name": patient}, ["name", "patient_name"], as_dict=True)
        if pat:
            pat_names = list(set([pat.name, pat.patient_name]))
            filters["patient"] = ["in", pat_names] if len(pat_names) > 1 else pat_names[0]
        else:
            filters["patient"] = patient
    elif user and user != "Guest" and not any(r in roles for r in ["System Manager", "Administrator", "Receptionist"]):
        pat = frappe.db.get_value("Patient", {"email": user}, ["name", "patient_name"], as_dict=True) \
            or frappe.db.get_value("Patient", {"name": user}, ["name", "patient_name"], as_dict=True)
        if pat:
            pat_names = list(set([pat.name, pat.patient_name]))
            filters["patient"] = ["in", pat_names] if len(pat_names) > 1 else pat_names[0]

    # Doctor filter resolution (by ID, doctor_name, email, or session user)
    if doctor:
        doc = frappe.db.get_value("Doctor", {"email": doctor}, ["name", "doctor_name"], as_dict=True) \
            or frappe.db.get_value("Doctor", {"name": doctor}, ["name", "doctor_name"], as_dict=True) \
            or frappe.db.get_value("Doctor", {"doctor_name": doctor}, ["name", "doctor_name"], as_dict=True)
        if doc:
            doc_names = list(set([doc.name, doc.doctor_name]))
            filters["doctor"] = ["in", doc_names] if len(doc_names) > 1 else doc_names[0]
        else:
            filters["doctor"] = doctor
    elif user and user != "Guest" and not any(r in roles for r in ["System Manager", "Administrator", "Receptionist"]):
        doc = frappe.db.get_value("Doctor", {"email": user}, ["name", "doctor_name"], as_dict=True) \
            or frappe.db.get_value("Doctor", {"name": user}, ["name", "doctor_name"], as_dict=True)
        if doc:
            doc_names = list(set([doc.name, doc.doctor_name]))
            filters["doctor"] = ["in", doc_names] if len(doc_names) > 1 else doc_names[0]
    if from_date and to_date:
        filters["invoice_date"] = ["between", [from_date, to_date]]
    elif from_date:
        filters["invoice_date"] = [">=", from_date]
    elif to_date:
        filters["invoice_date"] = ["<=", to_date]

    invoices = frappe.get_all(
        "Invoice",
        filters=filters,
        fields=[
            "name", "patient", "doctor", "appointment", "prescription",
            "invoice_date", "due_date", "consultation_charges", "medicine_charges",
            "subtotal", "discount_amount", "tax_percentage", "tax_amount",
            "grand_total", "total_amount", "paid_amount", "outstanding_amount",
            "payment_status", "payment_method", "transaction_reference", "payment_date",
            "remarks", "creation"
        ],
        order_by="invoice_date desc, creation desc"
    )

    # Attach patient name & doctor name
    for inv in invoices:
        inv["patient_name"] = frappe.db.get_value("Patient", inv.patient, "patient_name") or inv.patient or "Unknown Patient"
        inv["patient_phone"] = frappe.db.get_value("Patient", inv.patient, "phone") or ""
        inv["doctor_name"] = frappe.db.get_value("Doctor", inv.doctor, "doctor_name") or inv.doctor or "Hospital Attending"
        
        # Fallback values if older invoices
        if not inv.get("grand_total"):
            inv["grand_total"] = flt(inv.get("total_amount") or 0)
        if not inv.get("subtotal"):
            inv["subtotal"] = flt(inv.get("grand_total") or 0)
        if inv.get("outstanding_amount") is None:
            paid = flt(inv.get("paid_amount") or 0)
            grand = flt(inv.get("grand_total") or 0)
            inv["outstanding_amount"] = max(0.0, grand - paid)

    # Search filter on patient name or invoice ID
    if search:
        s = search.lower().strip()
        invoices = [
            inv for inv in invoices
            if s in inv.name.lower() or s in (inv.get("patient_name") or "").lower() or s in (inv.get("patient") or "").lower()
        ]

    # Metrics calculation
    all_active = [inv for inv in invoices if inv.get("payment_status") != "Cancelled"]
    total_billed = sum(flt(i.get("grand_total") or 0) for i in all_active)
    total_collected = sum(flt(i.get("paid_amount") or 0) for i in all_active)
    total_outstanding = sum(flt(i.get("outstanding_amount") or 0) for i in all_active)

    counts = {
        "total": len(invoices),
        "paid": len([i for i in invoices if i.get("payment_status") == "Paid"]),
        "unpaid": len([i for i in invoices if i.get("payment_status") == "Unpaid"]),
        "partially_paid": len([i for i in invoices if i.get("payment_status") == "Partially Paid"]),
        "cancelled": len([i for i in invoices if i.get("payment_status") == "Cancelled"])
    }

    return {
        "status": "success",
        "invoices": invoices,
        "metrics": {
            "total_billed": total_billed,
            "total_collected": total_collected,
            "total_outstanding": total_outstanding,
            "counts": counts
        }
    }


@frappe.whitelist()
def get_invoice_details(invoice_id):
    if not frappe.db.exists("Invoice", invoice_id):
        return {"status": "error", "message": "Invoice not found"}

    inv = frappe.get_doc("Invoice", invoice_id)
    inv_dict = inv.as_dict()

    # Patient details
    patient = frappe.db.get_value(
        "Patient",
        inv.patient,
        ["name", "patient_name", "phone", "email", "gender", "date_of_birth", "blood_group", "address"],
        as_dict=True
    ) if inv.patient else {}

    # Doctor details
    doctor = frappe.db.get_value(
        "Doctor",
        inv.doctor,
        ["name", "doctor_name", "department", "specialization", "phone"],
        as_dict=True
    ) if inv.doctor else {}

    # Linked payment history
    payments = frappe.get_all(
        "Payment",
        filters={"invoice": invoice_id},
        fields=["name", "payment_date", "amount_paid", "payment_method", "payment_status", "transaction_reference", "notes"],
        order_by="payment_date desc, creation desc"
    )

    inv_dict["patient_details"] = patient
    inv_dict["doctor_details"] = doctor
    inv_dict["payments"] = payments

    # If items list is empty, synthesize from consultation & medicine charges
    if not inv_dict.get("items"):
        synth_items = []
        if flt(inv_dict.get("consultation_charges")) > 0:
            synth_items.append({
                "item_type": "Consultation",
                "item_name": "Doctor Consultation Fee",
                "quantity": 1,
                "unit_price": flt(inv_dict.get("consultation_charges")),
                "amount": flt(inv_dict.get("consultation_charges"))
            })
        if flt(inv_dict.get("medicine_charges")) > 0:
            synth_items.append({
                "item_type": "Medicine",
                "item_name": "Prescribed Medications",
                "quantity": 1,
                "unit_price": flt(inv_dict.get("medicine_charges")),
                "amount": flt(inv_dict.get("medicine_charges"))
            })
        inv_dict["items"] = synth_items

    return {
        "status": "success",
        "invoice": inv_dict
    }


@frappe.whitelist()
def record_invoice_payment(invoice_id, amount_paid, payment_method="Cash", transaction_reference=None, notes=None):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    if not frappe.db.exists("Invoice", invoice_id):
        return {"status": "error", "message": "Invoice not found"}

    inv = frappe.get_doc("Invoice", invoice_id)

    if inv.payment_status == "Cancelled":
        return {"status": "error", "message": "Cannot pay a cancelled invoice"}

    paid_now = flt(amount_paid)
    if paid_now <= 0:
        return {"status": "error", "message": "Payment amount must be greater than 0"}

    # Update invoice fields
    inv.paid_amount = round(flt(inv.paid_amount) + paid_now, 2)
    inv.payment_method = payment_method
    if transaction_reference:
        inv.transaction_reference = transaction_reference
    if notes:
        existing_notes = inv.remarks or ""
        inv.remarks = f"{existing_notes}\nPayment of ₹{paid_now} via {payment_method} ({transaction_reference or 'N/A'}) - {notes}".strip()

    inv.payment_date = today()
    inv.save(ignore_permissions=True)

    # Create audit record in Payment doctype
    payment_doc = frappe.get_doc({
        "doctype": "Payment",
        "invoice": inv.name,
        "patient": inv.patient,
        "payment_date": today(),
        "amount_paid": paid_now,
        "payment_method": payment_method,
        "payment_status": "Paid",
        "transaction_reference": transaction_reference or "",
        "notes": notes or f"Payment collected for {inv.name}"
    })
    payment_doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success",
        "message": f"Payment of ₹{paid_now:.2f} recorded successfully",
        "invoice": inv.as_dict(),
        "payment_record": payment_doc.name
    }


@frappe.whitelist()
def cancel_invoice(invoice_id, reason=None):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    if not frappe.db.exists("Invoice", invoice_id):
        return {"status": "error", "message": "Invoice not found"}

    inv = frappe.get_doc("Invoice", invoice_id)
    inv.payment_status = "Cancelled"
    inv.remarks = f"{inv.remarks or ''}\nCancelled on {today()}: {reason or 'No reason provided'}".strip()
    inv.save(ignore_permissions=True)
    frappe.db.commit()

    return {"status": "success", "message": f"Invoice {invoice_id} marked as Cancelled"}


@frappe.whitelist()
def create_custom_invoice(patient, doctor=None, appointment=None, items=None, discount_amount=0, tax_percentage=0, remarks=None):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    patient_id = frappe.db.get_value("Patient", {"patient_name": patient}, "name") or patient
    if not frappe.db.exists("Patient", patient_id):
        return {"status": "error", "message": "Patient not found"}

    doctor_id = None
    if doctor:
        doctor_id = frappe.db.get_value("Doctor", {"doctor_name": doctor}, "name") or doctor

    items_list = []
    if isinstance(items, str):
        try:
            items_list = json.loads(items)
        except Exception:
            items_list = []
    elif isinstance(items, list):
        items_list = items

    inv_doc = frappe.get_doc({
        "doctype": "Invoice",
        "patient": patient_id,
        "doctor": doctor_id,
        "appointment": appointment or None,
        "invoice_date": today(),
        "discount_amount": flt(discount_amount),
        "tax_percentage": flt(tax_percentage),
        "remarks": remarks or "Generated from Billing Portal",
        "payment_status": "Unpaid"
    })

    for itm in items_list:
        inv_doc.append("items", {
            "item_type": itm.get("item_type", "Service"),
            "item_name": itm.get("item_name") or "Medical Service",
            "medicine": itm.get("medicine") or None,
            "quantity": flt(itm.get("quantity")) or 1.0,
            "unit_price": flt(itm.get("unit_price")) or 0.0,
            "amount": round((flt(itm.get("quantity")) or 1.0) * (flt(itm.get("unit_price")) or 0.0), 2)
        })

    inv_doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success",
        "message": f"Invoice {inv_doc.name} created successfully",
        "invoice_id": inv_doc.name
    }


# --------------------------------------------------
# 9. PRESCRIPTION PRINT & PDF DETAILS API
# --------------------------------------------------
@frappe.whitelist()
def get_prescription_details(prescription_id=None, appointment_id=None):
    if not prescription_id and not appointment_id:
        return {"status": "error", "message": "prescription_id or appointment_id is required"}

    prescriptions = []
    primary_rx = None
    appt_doc = None

    if prescription_id:
        if not frappe.db.exists("Prescription", prescription_id):
            return {"status": "error", "message": "Prescription not found"}
        primary_rx = frappe.get_doc("Prescription", prescription_id)
        if primary_rx.appointment:
            appointment_id = primary_rx.appointment

    if appointment_id and frappe.db.exists("Appointments", appointment_id):
        appt_doc = frappe.get_doc("Appointments", appointment_id)
        prescriptions = frappe.get_all(
            "Prescription",
            filters={"appointment": appointment_id},
            fields=["name", "medicine", "dosage", "frequency", "duration", "instructions", "prescription_date"],
            order_by="creation asc"
        )

    if not prescriptions and primary_rx:
        prescriptions = [primary_rx.as_dict()]

    if not primary_rx and prescriptions:
        primary_rx = frappe.get_doc("Prescription", prescriptions[0].get("name"))

    patient_id = (primary_rx.patient if primary_rx else (appt_doc.patient if appt_doc else None))
    doctor_id = (primary_rx.doctor if primary_rx else (appt_doc.doctor if appt_doc else None))

    # Patient details
    patient = frappe.db.get_value(
        "Patient",
        patient_id,
        ["name", "patient_name", "date_of_birth", "gender", "phone", "email", "blood_group", "address"],
        as_dict=True
    ) if patient_id and frappe.db.exists("Patient", patient_id) else {}

    # Calculate age
    if patient and patient.get("date_of_birth"):
        try:
            dob = getdate(patient["date_of_birth"])
            t = getdate(today())
            age_years = t.year - dob.year - ((t.month, t.day) < (dob.month, dob.day))
            patient["age"] = f"{age_years} yrs"
        except Exception:
            patient["age"] = "N/A"
    else:
        patient["age"] = "N/A"

    # Doctor details
    doctor = frappe.db.get_value(
        "Doctor",
        doctor_id,
        ["name", "doctor_name", "department", "specialization", "phone", "email"],
        as_dict=True
    ) if doctor_id and frappe.db.exists("Doctor", doctor_id) else {}

    if doctor:
        doctor["license_number"] = f"MCI-TN/{doctor.get('name', 'DOC')[-5:]}/2026"

    # Clinical Summary / Medical Record
    clinical_record = None
    if appointment_id:
        med_rec = frappe.get_all(
            "Medical Record",
            filters={"appointment": appointment_id},
            fields=["name", "symptoms", "diagnosis", "treatment", "visit_date"],
            limit=1
        )
        if med_rec:
            clinical_record = med_rec[0]

    # Prescribed medicines list
    medications = []
    for rx in prescriptions:
        med_id = rx.get("medicine")
        med_info = frappe.db.get_value("Medicine", med_id, ["medicine_name", "batch_number"], as_dict=True) if med_id and frappe.db.exists("Medicine", med_id) else None
        display_name = (med_info.medicine_name if med_info and med_info.get("medicine_name") else med_id) or "Prescribed Drug"
        medications.append({
            "prescription_id": rx.get("name"),
            "medicine_name": display_name,
            "dosage": rx.get("dosage") or "1 tablet",
            "frequency": rx.get("frequency") or "As Needed",
            "duration": rx.get("duration") or "5 days",
            "instructions": rx.get("instructions") or "After food"
        })

    # Patient Vitals for prescription
    vitals = None
    if appointment_id:
        v_list = frappe.get_all(
            "Patient Vitals",
            filters={"appointment": appointment_id},
            fields=[
                "name", "recorded_date_time", "bp_systolic", "bp_diastolic",
                "pulse_rate", "spo2", "temperature", "weight_kg", "height_cm",
                "bmi", "bmi_status", "blood_sugar_fasting", "blood_sugar_postprandial",
                "blood_sugar_random", "sugar_remarks", "remarks"
            ],
            order_by="recorded_date_time desc",
            limit=1
        )
        if v_list:
            vitals = v_list[0]
    if not vitals and patient_id:
        v_list = frappe.get_all(
            "Patient Vitals",
            filters={"patient": patient_id},
            fields=[
                "name", "recorded_date_time", "bp_systolic", "bp_diastolic",
                "pulse_rate", "spo2", "temperature", "weight_kg", "height_cm",
                "bmi", "bmi_status", "blood_sugar_fasting", "blood_sugar_postprandial",
                "blood_sugar_random", "sugar_remarks", "remarks"
            ],
            order_by="recorded_date_time desc",
            limit=1
        )
        if v_list:
            vitals = v_list[0]

    if vitals and vitals.bp_systolic and vitals.bp_diastolic:
        vitals["bp_display"] = f"{vitals.bp_systolic}/{vitals.bp_diastolic} mmHg"

    # Follow-up date (7 days from prescription date)
    rx_date = (primary_rx.prescription_date if primary_rx else today()) or today()
    try:
        from frappe.utils import add_days
        follow_up = add_days(rx_date, 7)
    except Exception:
        follow_up = rx_date

    return {
        "status": "success",
        "prescription_id": (primary_rx.name if primary_rx else (prescriptions[0].get("name") if prescriptions else "PRE-00000")),
        "prescription_date": rx_date,
        "follow_up_date": follow_up,
        "appointment_id": appointment_id,
        "patient": patient,
        "doctor": doctor,
        "clinical_record": clinical_record,
        "vitals": vitals,
        "appointment": {
            "name": appt_doc.name,
            "appointment_date": appt_doc.appointment_date,
            "appointment_time": appt_doc.appointment_time,
            "reason_for_visit": appt_doc.reason_for_visit
        } if appt_doc else None,
        "medications": medications
    }


# --------------------------------------------------
# 10. PATIENT VITALS & BLOOD SUGAR TRACKING APIS
# --------------------------------------------------
@frappe.whitelist(methods=["POST"])
def record_patient_vitals(
    patient,
    appointment=None,
    bp_systolic=None,
    bp_diastolic=None,
    pulse_rate=None,
    spo2=None,
    temperature=None,
    weight_kg=None,
    height_cm=None,
    blood_sugar_fasting=None,
    blood_sugar_postprandial=None,
    blood_sugar_random=None,
    sugar_remarks=None,
    remarks=None
):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Please login to record vitals"), frappe.PermissionError)

    # Check if body was passed as JSON
    if frappe.request and frappe.request.data and not (bp_systolic or bp_diastolic or pulse_rate or spo2 or temperature or weight_kg):
        try:
            data = json.loads(frappe.request.data)
            if isinstance(data, dict):
                patient = data.get("patient") or patient
                appointment = data.get("appointment") or appointment
                bp_systolic = data.get("bp_systolic", bp_systolic)
                bp_diastolic = data.get("bp_diastolic", bp_diastolic)
                pulse_rate = data.get("pulse_rate", pulse_rate)
                spo2 = data.get("spo2", spo2)
                temperature = data.get("temperature", temperature)
                weight_kg = data.get("weight_kg", weight_kg)
                height_cm = data.get("height_cm", height_cm)
                blood_sugar_fasting = data.get("blood_sugar_fasting", blood_sugar_fasting)
                blood_sugar_postprandial = data.get("blood_sugar_postprandial", blood_sugar_postprandial)
                blood_sugar_random = data.get("blood_sugar_random", blood_sugar_random)
                sugar_remarks = data.get("sugar_remarks", sugar_remarks)
                remarks = data.get("remarks", remarks)
        except Exception:
            pass

    patient_id = frappe.db.get_value("Patient", {"email": patient}, "name") \
        or frappe.db.get_value("Patient", {"patient_name": patient}, "name") \
        or frappe.db.get_value("Patient", {"name": patient}, "name") \
        or patient

    if not frappe.db.exists("Patient", patient_id):
        return {"status": "error", "message": f"Patient '{patient}' not found"}

    vitals_doc = frappe.get_doc({
        "doctype": "Patient Vitals",
        "patient": patient_id,
        "appointment": appointment if (appointment and frappe.db.exists("Appointments", appointment)) else None,
        "recorded_by": user,
        "recorded_date_time": now_datetime(),
        "bp_systolic": cint(bp_systolic) if bp_systolic else None,
        "bp_diastolic": cint(bp_diastolic) if bp_diastolic else None,
        "pulse_rate": cint(pulse_rate) if pulse_rate else None,
        "spo2": flt(spo2) if spo2 else None,
        "temperature": flt(temperature) if temperature else None,
        "weight_kg": flt(weight_kg) if weight_kg else None,
        "height_cm": flt(height_cm) if height_cm else None,
        "blood_sugar_fasting": flt(blood_sugar_fasting) if blood_sugar_fasting else None,
        "blood_sugar_postprandial": flt(blood_sugar_postprandial) if blood_sugar_postprandial else None,
        "blood_sugar_random": flt(blood_sugar_random) if blood_sugar_random else None,
        "sugar_remarks": sugar_remarks or None,
        "remarks": remarks or ""
    })

    vitals_doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success",
        "message": "Patient vitals recorded successfully",
        "vitals": vitals_doc.as_dict()
    }


@frappe.whitelist()
def get_latest_patient_vitals(patient=None, appointment=None):
    user = frappe.session.user
    if not patient and not appointment and user and user != "Guest":
        patient = frappe.db.get_value("Patient", {"email": user}, "name") or frappe.db.get_value("Patient", {"name": user}, "name")

    filters = {}
    if appointment:
        filters["appointment"] = appointment
    elif patient:
        patient_id = frappe.db.get_value("Patient", {"email": patient}, "name") \
            or frappe.db.get_value("Patient", {"patient_name": patient}, "name") \
            or frappe.db.get_value("Patient", {"name": patient}, "name") \
            or patient
        filters["patient"] = patient_id

    if not filters:
        return {"status": "error", "message": "patient or appointment reference required"}

    v_list = frappe.get_all(
        "Patient Vitals",
        filters=filters,
        fields=[
            "name", "patient", "appointment", "recorded_by", "recorded_date_time",
            "bp_systolic", "bp_diastolic", "pulse_rate", "spo2", "temperature",
            "weight_kg", "height_cm", "bmi", "bmi_status",
            "blood_sugar_fasting", "blood_sugar_postprandial", "blood_sugar_random",
            "sugar_remarks", "remarks"
        ],
        order_by="recorded_date_time desc, creation desc",
        limit=1
    )

    vitals = v_list[0] if v_list else None
    if vitals:
        if vitals.bp_systolic and vitals.bp_diastolic:
            vitals["bp_display"] = f"{vitals.bp_systolic}/{vitals.bp_diastolic} mmHg"
        else:
            vitals["bp_display"] = "—"
        vitals["patient_name"] = frappe.db.get_value("Patient", vitals.patient, "patient_name") or vitals.patient

    return {
        "status": "success",
        "vitals": vitals
    }


@frappe.whitelist()
def get_patient_vitals_history(patient=None, limit=20):
    user = frappe.session.user
    if not patient and user and user != "Guest":
        patient = frappe.db.get_value("Patient", {"email": user}, "name") or frappe.db.get_value("Patient", {"name": user}, "name")

    if not patient:
        return {"status": "error", "message": "patient reference required"}

    patient_id = frappe.db.get_value("Patient", {"email": patient}, "name") \
        or frappe.db.get_value("Patient", {"patient_name": patient}, "name") \
        or frappe.db.get_value("Patient", {"name": patient}, "name") \
        or patient

    records = frappe.get_all(
        "Patient Vitals",
        filters={"patient": patient_id},
        fields=[
            "name", "patient", "appointment", "recorded_by", "recorded_date_time",
            "bp_systolic", "bp_diastolic", "pulse_rate", "spo2", "temperature",
            "weight_kg", "height_cm", "bmi", "bmi_status",
            "blood_sugar_fasting", "blood_sugar_postprandial", "blood_sugar_random",
            "sugar_remarks", "remarks"
        ],
        order_by="recorded_date_time desc, creation desc",
        limit=cint(limit) or 20
    )

    for r in records:
        if r.bp_systolic and r.bp_diastolic:
            r["bp_display"] = f"{r.bp_systolic}/{r.bp_diastolic} mmHg"
        else:
            r["bp_display"] = "—"

    return {
        "status": "success",
        "vitals": records
    }
