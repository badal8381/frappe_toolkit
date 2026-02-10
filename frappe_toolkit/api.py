import frappe


@frappe.whitelist()
def get_sidebar_items():
	settings = frappe.get_single("Sidebar Settings")
	if not settings.enabled:
		return []
	items = []
	for row in settings.items:
		if not row.enabled:
			continue
		if row.type == "Section":
			items.append({"type": "section", "label": row.label})
		else:
			items.append({
				"type": row.type or "",
				"label": row.label,
				"icon": row.icon or "",
				"link_to": row.link_to or "",
				"doc_view": row.doc_view or "",
				"url": row.url or "",
			})
	return items
