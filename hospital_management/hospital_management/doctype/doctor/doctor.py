# Copyright (c) 2026, leeli and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils.password import update_password


class Doctor(Document):

    def after_insert(self):
        """Auto-create a Frappe user with doctor role when a Doctor record is created."""
        if self.email:
            self._create_doctor_user()

    def on_update(self):
        """Ensure user exists when email is added/changed on an existing doctor."""
        if self.email and not frappe.db.exists("User", self.email.strip()):
            self._create_doctor_user()

    def _create_doctor_user(self):
        """Create a new Frappe User linked to this doctor with the doctor role."""
        email = self.email.strip()
        role_to_add = "doctor" if frappe.db.exists("Role", "doctor") else "Doctor"

        if frappe.db.exists("User", email):
            user = frappe.get_doc("User", email)
            self._ensure_doctor_role(user, role_to_add)
            return

        try:
            first_name = self.doctor_name or email.split("@")[0]
            user = frappe.get_doc({
                "doctype": "User",
                "email": email,
                "first_name": first_name,
                "enabled": 1,
                "send_welcome_email": 0,
                "user_type": "Website User",
                "roles": [{"role": role_to_add}],
            })
            user.insert(ignore_permissions=True)

            # Set standard default password so the doctor can log in immediately
            update_password(email, "Doctor@123")

            frappe.db.commit()
            frappe.logger().info(f"Doctor user created: {email} for doctor {self.name}")
        except Exception as e:
            frappe.log_error(
                f"Failed to create user for doctor {self.name}: {str(e)}",
                "Doctor User Creation"
            )

    def _ensure_doctor_role(self, user, role_to_add):
        """Add doctor role to an existing user if not already present."""
        existing_roles = [r.role.lower() for r in user.roles]
        if role_to_add.lower() not in existing_roles:
            user.append("roles", {"role": role_to_add})
            user.save(ignore_permissions=True)
            frappe.db.commit()
