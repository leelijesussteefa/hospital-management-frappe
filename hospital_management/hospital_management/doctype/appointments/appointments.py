# Copyright (c) 2026, leeli and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Appointments(Document):
    def validate(self):
        self.validate_no_double_booking()

    def validate_no_double_booking(self):
        if not self.doctor or not self.appointment_date or not self.appointment_time:
            return

        if self.status == "Cancelled":
            return

        time_str = str(self.appointment_time).strip()
        time_short = time_str[:5]

        # Check existing active appointment for this doctor on same date and time
        existing = frappe.db.sql("""
            SELECT name, status, appointment_time
            FROM `tabAppointments`
            WHERE doctor = %s
              AND appointment_date = %s
              AND (
                  appointment_time = %s
                  OR TIME_FORMAT(appointment_time, '%%H:%%i:%%s') = %s
                  OR TIME_FORMAT(appointment_time, '%%H:%%i') = %s
              )
              AND status != 'Cancelled'
              AND name != %s
        """, (
            self.doctor,
            str(self.appointment_date),
            time_str,
            time_str,
            time_short,
            self.name or ""
        ), as_dict=True)

        if existing:
            frappe.throw(
                _("This time slot has just been booked by another patient. Please choose another slot."),
                title=_("Slot Conflict")
            )
