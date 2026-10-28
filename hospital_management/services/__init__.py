# Domain Services for Hospital Management System
from .security_service import (
    require_authenticated, is_hospital_admin, is_doctor, is_nurse,
    is_patient, is_pharmacy_staff, get_current_patient, get_current_doctor,
    get_current_nurse, validate_patient_access, validate_doctor_access,
    validate_nurse_access, validate_pharmacy_access
)
from .appointment_service import (
    get_hospital_settings, get_available_slots, validate_and_book_appointment
)
from .inventory_service import (
    get_medicine_stock_info, dispense_prescription_inventory,
    get_warehouse_with_stock, get_company_and_account_defaults,
    get_or_create_customer_for_patient
)
from .billing_service import (
    create_hospital_invoice, record_invoice_payment
)
from .clinical_service import (
    record_vitals_entry, record_consultation_entry,
    record_nursing_note, record_medication_administration
)
