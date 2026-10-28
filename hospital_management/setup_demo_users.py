import frappe
from frappe.utils.password import update_password

def ensure_role(role_name):
    if not frappe.db.exists("Role", role_name):
        role = frappe.get_doc({
            "doctype": "Role",
            "role_name": role_name,
            "desk_access": 1
        })
        role.insert(ignore_permissions=True)
        print(f"Created Role: {role_name}")
    else:
        print(f"Role already exists: {role_name}")

def ensure_user(email, first_name, last_name, password, roles):
    valid_roles = [r for r in roles if frappe.db.exists("Role", r)]
    if not frappe.db.exists("User", email):
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "enabled": 1,
            "send_welcome_email": 0,
            "roles": [{"role": r} for r in valid_roles]
        })
        user.insert(ignore_permissions=True)
        print(f"Created User: {email}")
    else:
        user = frappe.get_doc("User", email)
        user.enabled = 1
        existing_roles = {r.role for r in user.roles}
        for r in valid_roles:
            if r not in existing_roles:
                user.append("roles", {"role": r})
        user.save(ignore_permissions=True)
        print(f"Updated User: {email}")

    update_password(email, password)
    print(f"Updated password for: {email}")

def seed_demo_users():
    """Seed demo accounts for Pharmacy and Billing roles."""
    # Ensure roles exist
    for r in ["Pharmacist", "Pharmacy Staff", "Billing Staff", "Billing"]:
        ensure_role(r)

    # Pharmacist demo account
    ensure_user(
        email="pharmacy@careplus.com",
        first_name="Pharmacy",
        last_name="Desk",
        password="Pharmacy@123",
        roles=["Pharmacist", "Pharmacy Staff", "Desk User"]
    )

    # Billing demo account
    ensure_user(
        email="billing@careplus.com",
        first_name="Billing",
        last_name="Cashier",
        password="Billing@123",
        roles=["Billing Staff", "Billing", "Accounts User", "Desk User"]
    )

    frappe.db.commit()
    print("Demo users successfully seeded!")

if __name__ == "__main__":
    seed_demo_users()
