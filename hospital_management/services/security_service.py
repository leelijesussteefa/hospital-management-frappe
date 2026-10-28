import frappe
from frappe import _
from frappe.exceptions import PermissionError

def get_current_user():
    return frappe.session.user

def is_guest():
    return not frappe.session.user or frappe.session.user == "Guest"

def require_authenticated():
    if is_guest():
        frappe.throw(_("Authentication required. Please log in."), PermissionError)

def get_user_roles(user=None):
    user = user or frappe.session.user
    if not user or user == "Guest":
        return []
    return [r.lower() for r in frappe.get_roles(user)]

def is_hospital_admin(user=None):
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    roles = get_user_roles(user)
    return "hospital administrator" in roles or "system manager" in roles

def is_doctor(user=None):
    user = user or frappe.session.user
    roles = get_user_roles(user)
    if "doctor" in roles or "healthcare practitioner" in roles:
        return True
    return bool(frappe.db.exists("Doctor", {"email": user}) or frappe.db.exists("Doctor", {"user_id": user}))

def is_nurse(user=None):
    user = user or frappe.session.user
    roles = get_user_roles(user)
    if "nurse" in roles or "nursing user" in roles:
        return True
    return bool(frappe.db.exists("Nurse", {"email": user}) or frappe.db.exists("Nurse", {"user_id": user}))

def is_pharmacy_staff(user=None):
    user = user or frappe.session.user
    roles = get_user_roles(user)
    return "pharmacy staff" in roles or "pharmacist" in roles or is_hospital_admin(user) or (user and "pharmacy" in user.lower())

def is_patient(user=None):
    user = user or frappe.session.user
    roles = get_user_roles(user)
    if "patient" in roles:
        return True
    return bool(frappe.db.exists("Patient", {"email": user}) or frappe.db.exists("Patient", {"user_id": user}))

def get_current_patient(user=None):
    """Resolve patient document name linked to the given user account."""
    user = user or frappe.session.user
    if not user or user == "Guest":
        return None
    # 1. By user_id link
    p_name = frappe.db.get_value("Patient", {"user_id": user}, "name")
    if p_name:
        return p_name
    # 2. By email match
    p_name = frappe.db.get_value("Patient", {"email": user}, "name")
    if p_name:
        return p_name
    # 3. By exact name match
    if frappe.db.exists("Patient", user):
        return user
    return None

def get_current_doctor(user=None):
    """Resolve doctor document name linked to the given user account."""
    user = user or frappe.session.user
    if not user or user == "Guest":
        return None
    doc_name = frappe.db.get_value("Doctor", {"user_id": user}, "name")
    if doc_name:
        return doc_name
    doc_name = frappe.db.get_value("Doctor", {"email": user}, "name")
    if doc_name:
        return doc_name
    if frappe.db.exists("Doctor", user):
        return user
    return None

def get_current_nurse(user=None):
    """Resolve nurse document name linked to the given user account."""
    user = user or frappe.session.user
    if not user or user == "Guest":
        return None
    nurse_name = frappe.db.get_value("Nurse", {"user_id": user}, "name")
    if nurse_name:
        return nurse_name
    nurse_name = frappe.db.get_value("Nurse", {"email": user}, "name")
    if nurse_name:
        return nurse_name
    if frappe.db.exists("Nurse", user):
        return user
    return None

def validate_patient_access(patient_name, user=None):
    """Ensures caller has permission to view/modify the given patient's records."""
    require_authenticated()
    user = user or frappe.session.user
    
    # Administrators, Doctors, and Nurses have staff access to patient care
    if is_hospital_admin(user) or is_doctor(user) or is_nurse(user) or is_pharmacy_staff(user):
        return True
        
    # Patient users can ONLY access their own records
    my_patient = get_current_patient(user)
    if not my_patient or my_patient != patient_name:
        frappe.throw(_("Unauthorized: You do not have permission to access patient record '{0}'.").format(patient_name), PermissionError)
    return True

def validate_doctor_access(doctor_name, user=None):
    """Ensures caller has permission to act as the specified doctor."""
    require_authenticated()
    user = user or frappe.session.user
    if is_hospital_admin(user):
        return True
    my_doctor = get_current_doctor(user)
    if not my_doctor or my_doctor != doctor_name:
        frappe.throw(_("Unauthorized: You do not have permission to perform actions for Doctor '{0}'.").format(doctor_name), PermissionError)
    return True

def validate_nurse_access(nurse_name=None, user=None):
    """Ensures caller is an authorized nurse or admin."""
    require_authenticated()
    user = user or frappe.session.user
    if is_hospital_admin(user):
        return True
    if not is_nurse(user):
        frappe.throw(_("Unauthorized: Nursing privileges required."), PermissionError)
    return True

def validate_pharmacy_access(user=None):
    """Ensures caller is authorized pharmacy staff or admin."""
    require_authenticated()
    user = user or frappe.session.user
    if not is_pharmacy_staff(user):
        frappe.throw(_("Unauthorized: Pharmacy dispensing privileges required."), PermissionError)
    return True
