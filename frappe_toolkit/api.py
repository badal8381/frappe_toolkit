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
			if not _has_item_access(row):
				continue
			items.append({
				"type": row.type or "",
				"label": row.label,
				"icon": row.icon or "",
				"link_to": row.link_to or "",
				"doc_view": row.doc_view or "",
				"url": row.url or "",
				"open_in_new_tab": row.open_in_new_tab or 0,
			})

	# Remove trailing sections and sections with no items after them
	items = _clean_empty_sections(items)
	return items


def _has_item_access(row):
	"""Check if the current user has access to the sidebar item."""
	item_type = row.type
	link_to = row.link_to

	if item_type == "URL":
		return True

	if not link_to:
		return True

	try:
		if item_type == "DocType":
			return frappe.has_permission(link_to, ptype="read")

		if item_type == "Report":
			return _has_report_access(link_to)

		if item_type == "Page":
			return _has_page_access(link_to)

		if item_type == "Dashboard":
			return True

		if item_type == "Workspace":
			return _has_workspace_access(link_to)

	except Exception:
		return False

	return True


def _has_report_access(report_name):
	"""Check if user can access a report by checking the report's ref doctype permission."""
	if not frappe.db.exists("Report", report_name):
		return False

	report = frappe.get_doc("Report", report_name)

	# Check if user has access to the reference doctype
	if report.ref_doctype:
		if not frappe.has_permission(report.ref_doctype, ptype="read"):
			return False

	# Check report-level role restrictions
	if report.roles:
		user_roles = frappe.get_roles()
		report_roles = [d.role for d in report.roles]
		if not set(user_roles).intersection(set(report_roles)):
			return False

	return True


def _has_page_access(page_name):
	"""Check if user has role-based access to a page."""
	if not frappe.db.exists("Page", page_name):
		return False

	page = frappe.get_doc("Page", page_name)

	# If page has no role restrictions, allow access
	if not page.roles:
		return True

	user_roles = frappe.get_roles()
	page_roles = [d.role for d in page.roles]
	return bool(set(user_roles).intersection(set(page_roles)))


def _has_workspace_access(workspace_name):
	"""Check if user has role-based access to a workspace."""
	if not frappe.db.exists("Workspace", workspace_name):
		return False

	workspace = frappe.get_doc("Workspace", workspace_name)

	# Check role restrictions
	if workspace.roles:
		user_roles = frappe.get_roles()
		workspace_roles = [d.role for d in workspace.roles]
		if not set(user_roles).intersection(set(workspace_roles)):
			return False

	return True


def _clean_empty_sections(items):
	"""Remove section headers that have no visible items after them."""
	if not items:
		return items

	cleaned = []
	i = 0
	while i < len(items):
		if items[i]["type"] == "section":
			# Check if there's at least one non-section item before the next section or end
			has_children = False
			for j in range(i + 1, len(items)):
				if items[j]["type"] == "section":
					break
				has_children = True
				break
			if has_children:
				cleaned.append(items[i])
		else:
			cleaned.append(items[i])
		i += 1

	return cleaned
