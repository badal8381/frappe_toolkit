import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_migrate():
	"""Create cloud-storage tracking fields on the File DocType."""
	custom_fields = {
		"File": [
			{
				"fieldname": "cloud_storage_info_section",
				"fieldtype": "Section Break",
				"label": "Cloud Storage Info",
				"insert_after": "preview",
				"collapsible": 1,
			},
			{
				"fieldname": "storage_key",
				"fieldtype": "Small Text",
				"label": "Storage Key",
				"insert_after": "cloud_storage_info_section",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "cloud_status_section",
				"fieldtype": "Section Break",
				"insert_after": "storage_key",
				"hide_border": 1,
			},
			{
				"fieldname": "is_on_cloud",
				"fieldtype": "Check",
				"label": "Uploaded to Cloud",
				"insert_after": "cloud_status_section",
				"read_only": 1,
				"default": "0",
				"no_copy": 1,
			},
			{
				"fieldname": "cloud_uploaded_at",
				"fieldtype": "Datetime",
				"label": "Cloud Upload Date",
				"insert_after": "is_on_cloud",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "cloud_col_break",
				"fieldtype": "Column Break",
				"insert_after": "cloud_uploaded_at",
			},
			{
				"fieldname": "local_deleted",
				"fieldtype": "Check",
				"label": "Local File Deleted",
				"insert_after": "cloud_col_break",
				"read_only": 1,
				"default": "0",
				"no_copy": 1,
			},
			{
				"fieldname": "upload_skipped",
				"fieldtype": "Check",
				"label": "Upload Skipped (File Missing)",
				"insert_after": "local_deleted",
				"read_only": 1,
				"default": "0",
				"no_copy": 1,
			},
		]
	}
	# The File table definition may have changed earlier in migrate; retry once
	# to pick up the fresh schema.
	try:
		create_custom_fields(custom_fields, update=True)
	except Exception:
		frappe.db.rollback()
		create_custom_fields(custom_fields, update=True)
		frappe.db.commit()
