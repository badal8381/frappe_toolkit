import frappe

OLD = "Sidebar Item"
NEW = "Toolkit Sidebar Item"
APP_MODULE = "Frappe Toolkit"


def execute():
	"""Rename this app's `Sidebar Item` child DocType to `Toolkit Sidebar Item`.

	Frappe v16 introduced its own core `Sidebar Item` (module Desk, child of `Sidebar`).
	Because frappe_toolkit syncs after frappe, its definition overwrote the core one, and
	frappe's `v16_0.convert_sidebars` patch then failed validating `type = "Link"` against
	this app's option list. Runs pre_model_sync so the rename happens before either app
	syncs its DocType.
	"""
	if frappe.db.exists("DocType", OLD) and frappe.db.get_value("DocType", OLD, "module") == APP_MODULE:
		if frappe.db.exists("DocType", NEW):
			# both exist: nothing sane to do automatically
			frappe.throw(f"Both '{OLD}' (module {APP_MODULE}) and '{NEW}' exist; resolve manually")
		frappe.rename_doc("DocType", OLD, NEW, force=True)
		frappe.db.commit()

	restore_core_references()

	# The old name is about to be (re-)created by frappe's own model sync in this same migrate;
	# drop every cached copy of its meta so nothing validates against this app's fields.
	frappe.clear_cache(doctype=OLD)
	frappe.clear_cache(doctype=NEW)
	frappe.clear_cache(doctype="Sidebar Settings")


def restore_core_references():
	"""`rename_doc` rewrites *every* Table/Link field whose options were `Sidebar Item`,
	including frappe's own `Sidebar.items`. Point those back at the core doctype; only
	this app's doctypes may reference `Toolkit Sidebar Item`."""
	if not frappe.db.exists("DocType", NEW):
		return

	rows = frappe.db.sql(
		"""
		select df.name, df.parent
		from `tabDocField` df
		join `tabDocType` dt on dt.name = df.parent
		where df.options = %s and dt.module != %s
		""",
		(NEW, APP_MODULE),
		as_dict=True,
	)
	for row in rows:
		frappe.db.set_value("DocField", row.name, "options", OLD, update_modified=False)
		frappe.clear_cache(doctype=row.parent)
		print(f"frappe_toolkit: restored {row.parent} field to reference '{OLD}'")

	for dt in ("Custom Field", "Property Setter"):
		parent_field = "dt" if dt == "Custom Field" else "doc_type"
		filters = {"options" if dt == "Custom Field" else "value": NEW}
		for row in frappe.get_all(dt, filters=filters, fields=["name", parent_field]):
			if frappe.db.get_value("DocType", row[parent_field], "module") != APP_MODULE:
				field = "options" if dt == "Custom Field" else "value"
				frappe.db.set_value(dt, row.name, field, OLD, update_modified=False)
				frappe.clear_cache(doctype=row[parent_field])

	if rows:
		frappe.db.commit()
