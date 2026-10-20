# Copyright (c) 2026, leeli and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt, today


class Invoice(Document):
	def validate(self):
		self.calculate_totals()
		self.update_payment_status()

	def calculate_totals(self):
		consultation_total = 0.0
		medicine_total = 0.0
		items_subtotal = 0.0

		for item in self.get("items", []):
			qty = flt(item.quantity) or 1.0
			rate = flt(item.unit_price) or 0.0
			item.amount = round(qty * rate, 2)
			items_subtotal += item.amount

			if item.item_type == "Consultation":
				consultation_total += item.amount
			elif item.item_type == "Medicine":
				medicine_total += item.amount

		if self.get("items"):
			self.consultation_charges = consultation_total
			self.medicine_charges = medicine_total
			self.subtotal = items_subtotal
		else:
			self.consultation_charges = flt(self.consultation_charges) or 0.0
			self.medicine_charges = flt(self.medicine_charges) or 0.0
			self.subtotal = self.consultation_charges + self.medicine_charges

		discount = flt(self.discount_amount) or 0.0
		tax_pct = flt(self.tax_percentage) or 0.0
		taxable = max(0.0, self.subtotal - discount)
		self.tax_amount = round(taxable * (tax_pct / 100.0), 2)
		self.grand_total = round(taxable + self.tax_amount, 2)
		self.total_amount = self.grand_total

		paid = flt(self.paid_amount) or 0.0
		self.outstanding_amount = max(0.0, round(self.grand_total - paid, 2))

	def update_payment_status(self):
		if self.payment_status == "Cancelled":
			return

		paid = flt(self.paid_amount) or 0.0
		grand = flt(self.grand_total) or 0.0

		if grand > 0 and paid >= grand:
			self.payment_status = "Paid"
			if not self.payment_date:
				self.payment_date = today()
		elif paid > 0 and paid < grand:
			self.payment_status = "Partially Paid"
		else:
			self.payment_status = "Unpaid"
