import frappe
from frappe import _
from frappe.utils import flt, nowdate, getdate

def get_billing_settings():
    try:
        return frappe.get_single("Hospital Settings")
    except Exception:
        return frappe._dict({
            "default_company": "Bosco Soft",
            "default_currency": "INR",
            "auto_generate_invoice": 1
        })

def create_hospital_invoice(patient, doctor=None, appointment=None, prescription=None, items=None, discount_amount=0, tax_percentage=0, remarks=None):
    """Creates a validated database Invoice linked to clinical records."""
    if not patient or not frappe.db.exists("Patient", patient):
        resolved_patient = frappe.db.get_value("Patient", {"patient_name": patient}, "name") if patient else None
        if resolved_patient:
            patient = resolved_patient
        elif patient:
            try:
                pdoc = frappe.get_doc({
                    "doctype": "Patient",
                    "patient_name": str(patient),
                    "gender": "Other"
                })
                pdoc.flags.ignore_permissions = True
                pdoc.insert(ignore_permissions=True)
                patient = pdoc.name
            except Exception:
                frappe.throw(_("Invalid or missing Patient."))
        else:
            frappe.throw(_("Invalid or missing Patient."))
        
    settings = get_billing_settings()
    invoice_date = nowdate()
    
    invoice_items = []
    consultation_charges = 0.0
    medicine_charges = 0.0
    
    # 1. If appointment provided, add consultation fee
    if appointment and frappe.db.exists("Appointments", appointment):
        appt = frappe.get_doc("Appointments", appointment)
        fee = flt(appt.consultation_fee)
        if fee > 0:
            consultation_charges += fee
            invoice_items.append({
                "item_type": "Consultation",
                "item_name": f"Consultation - Dr. {appt.doctor or 'Physician'}",
                "quantity": 1.0,
                "unit_price": fee,
                "amount": fee
            })
            
    # 2. If prescription provided, add medicine items
    if prescription and frappe.db.exists("Prescription", prescription):
        rx = frappe.get_doc("Prescription", prescription)
        rx_items = rx.get("items") or []
        if not rx_items and rx.medicine:
            price = flt(frappe.db.get_value("Medicine", rx.medicine, "price") or 0.0)
            med_name = frappe.db.get_value("Medicine", rx.medicine, "medicine_name") or rx.medicine
            amt = price * 1.0
            medicine_charges += amt
            invoice_items.append({
                "item_type": "Medicine",
                "item_name": med_name,
                "medicine": rx.medicine,
                "quantity": 1.0,
                "unit_price": price,
                "amount": amt
            })
        for item in rx_items:
            price = flt(frappe.db.get_value("Medicine", item.medicine, "price") or 0.0)
            qty = flt(item.quantity) if flt(item.quantity) > 0 else 1.0
            amt = price * qty
            medicine_charges += amt
            invoice_items.append({
                "item_type": "Medicine",
                "item_name": item.medicine_name or item.medicine,
                "medicine": item.medicine,
                "quantity": qty,
                "unit_price": price,
                "amount": amt
            })
            
    # 3. Explicit items
    if items:
        for it in items:
            itype = it.get("item_type") or "Other"
            iname = it.get("item_name") or "Medical Service"
            uprice = flt(it.get("unit_price") or 0.0)
            iqty = flt(it.get("quantity") or 1.0)
            iamt = uprice * iqty
            if itype == "Medicine":
                medicine_charges += iamt
            elif itype == "Consultation":
                consultation_charges += iamt
            invoice_items.append({
                "item_type": itype,
                "item_name": iname,
                "medicine": it.get("medicine"),
                "quantity": iqty,
                "unit_price": uprice,
                "amount": iamt
            })
            
    subtotal = consultation_charges + medicine_charges
    for it in invoice_items:
        if it["item_type"] not in ["Consultation", "Medicine"]:
            subtotal += flt(it["amount"])
            
    disc = flt(discount_amount)
    tax_pct = flt(tax_percentage)
    tax_amt = round((subtotal - disc) * (tax_pct / 100.0), 2)
    grand_total = max(0.0, round(subtotal - disc + tax_amt, 2))
    
    inv = frappe.get_doc({
        "doctype": "Invoice",
        "patient": patient,
        "doctor": doctor,
        "appointment": appointment,
        "prescription": prescription,
        "invoice_date": invoice_date,
        "items": invoice_items,
        "consultation_charges": consultation_charges,
        "medicine_charges": medicine_charges,
        "subtotal": subtotal,
        "discount_amount": disc,
        "tax_percentage": tax_pct,
        "tax_amount": tax_amt,
        "grand_total": grand_total,
        "total_amount": grand_total,
        "paid_amount": 0.0,
        "outstanding_amount": grand_total,
        "payment_status": "Unpaid",
        "remarks": remarks or "Automated hospital charge bill."
    })
    inv.flags.ignore_permissions = True
    inv.insert(ignore_permissions=True)
    
    # Link back to prescription if created from one
    if prescription and frappe.db.exists("Prescription", prescription):
        frappe.db.set_value("Prescription", prescription, "invoice_reference", inv.name)
        
    frappe.db.commit()
    return inv

def record_invoice_payment(invoice_id, amount_paid, payment_method="Cash", transaction_reference=None, notes=None):
    """Processes a payment against an invoice, validating amounts and updating balances."""
    if not frappe.db.exists("Invoice", invoice_id):
        frappe.throw(_("Invoice '{0}' does not exist.").format(invoice_id))
        
    inv = frappe.get_doc("Invoice", invoice_id)
    if inv.payment_status == "Cancelled":
        frappe.throw(_("Cannot record payment for a cancelled invoice."))
        
    amt = flt(amount_paid)
    if amt <= 0:
        frappe.throw(_("Payment amount must be greater than zero."))
        
    if amt > flt(inv.outstanding_amount) + 0.01:
        frappe.throw(_("Payment amount ({0}) exceeds outstanding balance ({1}).").format(amt, inv.outstanding_amount))
        
    # Create Payment record
    pay = frappe.get_doc({
        "doctype": "Payment",
        "invoice": invoice_id,
        "patient": inv.patient,
        "payment_date": nowdate(),
        "amount_paid": amt,
        "payment_method": payment_method or "Cash",
        "payment_status": "Paid",
        "transaction_reference": transaction_reference or "TXN-DIRECT",
        "notes": notes or f"Payment recorded for {invoice_id}"
    })
    pay.insert(ignore_permissions=True)
    
    # Update Invoice balances
    new_paid = flt(inv.paid_amount) + amt
    new_outstanding = max(0.0, round(flt(inv.grand_total) - new_paid, 2))
    
    status = "Paid" if new_outstanding <= 0.01 else "Partially Paid"
    
    frappe.db.set_value("Invoice", invoice_id, {
        "paid_amount": new_paid,
        "outstanding_amount": new_outstanding,
        "payment_status": status,
        "payment_date": nowdate(),
        "payment_method": payment_method
    })
    
    frappe.db.commit()
    return {
        "status": "success",
        "payment_id": pay.name,
        "invoice_id": invoice_id,
        "paid_amount": new_paid,
        "outstanding_amount": new_outstanding,
        "payment_status": status
    }
