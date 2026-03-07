import frappe
from frappe.model.document import Document


class SidebarSettings(Document):
	def validate(self):
		for item in self.items:
			if item.type in ("URL", "Section"):
				item.link_doctype = ""
				item.link_to = ""
			else:
				item.link_doctype = item.type
