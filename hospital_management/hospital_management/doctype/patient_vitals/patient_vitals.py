# Copyright (c) 2026, CarePlus and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

class PatientVitals(Document):
    def before_save(self):
        if not self.recorded_by:
            self.recorded_by = frappe.session.user or "Administrator"
        if not self.recorded_date_time:
            self.recorded_date_time = now_datetime()

        # Calculate BMI
        weight = flt(self.weight_kg)
        height = flt(self.height_cm)
        if weight > 0 and height > 0:
            height_m = height / 100.0
            self.bmi = round(weight / (height_m ** 2), 1)
            if self.bmi < 18.5:
                self.bmi_status = "Underweight"
            elif self.bmi < 25.0:
                self.bmi_status = "Normal"
            elif self.bmi < 30.0:
                self.bmi_status = "Overweight"
            else:
                self.bmi_status = "Obese"
        else:
            self.bmi = 0.0
            self.bmi_status = ""

        # Classify Blood Sugar
        fbs = flt(self.blood_sugar_fasting) if self.blood_sugar_fasting else None
        ppbs = flt(self.blood_sugar_postprandial) if self.blood_sugar_postprandial else None
        rbs = flt(self.blood_sugar_random) if self.blood_sugar_random else None

        statuses = []
        if fbs is not None and fbs > 0:
            if fbs >= 126:
                statuses.append("High / Diabetic")
            elif fbs >= 100:
                statuses.append("Pre-diabetic")
            else:
                statuses.append("Normal")

        if ppbs is not None and ppbs > 0:
            if ppbs >= 200:
                statuses.append("High / Diabetic")
            elif ppbs >= 140:
                statuses.append("Pre-diabetic")
            else:
                statuses.append("Normal")

        if rbs is not None and rbs > 0:
            if rbs >= 200:
                statuses.append("High / Diabetic")
            elif rbs >= 140:
                statuses.append("Pre-diabetic")
            else:
                statuses.append("Normal")

        if statuses:
            if "High / Diabetic" in statuses:
                self.sugar_remarks = "High / Diabetic"
            elif "Pre-diabetic" in statuses:
                self.sugar_remarks = "Pre-diabetic"
            else:
                self.sugar_remarks = "Normal"
