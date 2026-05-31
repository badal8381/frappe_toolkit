import mimetypes
from urllib.parse import unquote

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce, Count, Sum


@frappe.whitelist(allow_guest=True)
def serve_file(key=None, file_name=None):
	"""Main file-serving endpoint: permission-check then redirect to a presigned URL.

	Private files (and public files when no public domain is configured) point
	their file_url here:
	  /api/method/frappe_toolkit.cloud_storage.api.serve_file?key=...&file_name=...

	Public files with a configured Public Base URL are served directly from the
	CDN and never reach this endpoint.
	"""
	if not key:
		frappe.throw(_("File key is required"), frappe.ValidationError)

	key = unquote(key)

	file_info = frappe.db.get_value(
		"File",
		{"storage_key": key},
		["name", "is_private", "attached_to_doctype", "attached_to_name", "file_name", "owner"],
		as_dict=True,
	)
	if not file_info:
		frappe.throw(_("File not found"), frappe.DoesNotExistError)

	if file_info.is_private:
		if frappe.session.user == "Guest":
			raise frappe.PermissionError(_("Please login to access this file"))

		if file_info.attached_to_doctype and file_info.attached_to_name:
			if not frappe.has_permission(file_info.attached_to_doctype, "read", file_info.attached_to_name):
				raise frappe.PermissionError(_("You don't have permission to access this file"))
		else:
			if file_info.owner != frappe.session.user and "System Manager" not in frappe.get_roles():
				raise frappe.PermissionError(_("You don't have permission to access this file"))

	from frappe_toolkit.cloud_storage.client import StorageClient

	client = StorageClient()
	display_name = file_name or file_info.file_name
	presigned_url = client.generate_presigned_url(key, file_name=display_name)

	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = presigned_url


@frappe.whitelist()
def get_provider_regions(provider):
	"""Return the region list for a provider (for the settings UI)."""
	from frappe_toolkit.cloud_storage.providers import get_provider_regions as _regions

	return _regions(provider)


@frappe.whitelist()
def test_connection():
	"""Test bucket connectivity. System Manager only."""
	frappe.only_for("System Manager")

	from frappe_toolkit.cloud_storage.client import StorageClient

	try:
		return StorageClient().test_connection()
	except Exception as e:
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def migrate_files():
	"""Enqueue bulk migration of all pending local files. System Manager only."""
	frappe.only_for("System Manager")

	settings = frappe.get_cached_doc("Cloud Storage Settings")
	if not settings.enabled:
		frappe.throw(_("Cloud Storage is not enabled"))

	exempt = list({d.exempt_doctype for d in settings.exempt_doctypes})
	filters = {"is_folder": 0, "file_url": ("like", "/%"), "is_on_cloud": 0, "upload_skipped": 0}
	if exempt:
		filters["attached_to_doctype"] = ("not in", exempt)

	count = frappe.db.count("File", filters)
	if count == 0:
		return _("No local files to migrate. All files are already on cloud storage.")

	frappe.enqueue("frappe_toolkit.cloud_storage.scheduler.bulk_migrate_files", queue="long", timeout=3600)
	return _("{0} files queued for migration. This will run in the background.").format(count)


@frappe.whitelist()
def cleanup_local_files():
	"""Enqueue deletion of local copies of files already on cloud. System Manager only."""
	frappe.only_for("System Manager")

	settings = frappe.get_cached_doc("Cloud Storage Settings")
	if not settings.enabled:
		frappe.throw(_("Cloud Storage is not enabled"))

	count = frappe.db.count("File", {"is_folder": 0, "is_on_cloud": 1, "local_deleted": 0})
	if count == 0:
		return _("No files to clean up.")

	frappe.enqueue("frappe_toolkit.cloud_storage.scheduler.cleanup_local_files", queue="long", timeout=3600)
	return _("{0} files on cloud will be checked. Local copies are deleted in the background.").format(count)


@frappe.whitelist()
def upload_single_file(file_name):
	"""Queue a single File for cloud upload. System Manager only."""
	frappe.only_for("System Manager")

	file_doc = frappe.get_doc("File", file_name)
	if file_doc.is_on_cloud:
		return {"success": False, "message": _("File is already on cloud storage")}
	if not file_doc.file_url or not file_doc.file_url.startswith("/"):
		return {"success": False, "message": _("File is not a local file")}

	settings = frappe.get_cached_doc("Cloud Storage Settings")
	if not settings.enabled:
		return {"success": False, "message": _("Cloud Storage is not enabled")}

	frappe.enqueue(
		"frappe_toolkit.cloud_storage.handlers._upload_single_file", queue="short", timeout=300, file_name=file_name
	)
	return {"success": True, "message": _("File queued for cloud upload")}


@frappe.whitelist()
def delete_local_file(file_name):
	"""Delete the local copy of a file already on cloud. System Manager only."""
	frappe.only_for("System Manager")

	import os

	file_doc = frappe.get_doc("File", file_name)
	if not file_doc.is_on_cloud or not file_doc.storage_key:
		return {"success": False, "message": _("File is not on cloud storage")}
	if file_doc.local_deleted:
		return {"success": False, "message": _("Local file already deleted")}

	settings = frappe.get_cached_doc("Cloud Storage Settings")
	from frappe_toolkit.cloud_storage.handlers import _build_file_url, _update_parent_attach_field

	file_path = file_doc.get_full_path()
	old_url = file_doc.file_url
	if os.path.exists(file_path):
		file_doc._delete_file_on_disk()

	if not os.path.exists(file_path):
		new_url = _build_file_url(file_doc, file_doc.storage_key, settings)
		frappe.db.set_value("File", file_doc.name, {"local_deleted": 1, "file_url": new_url}, update_modified=False)
		_update_parent_attach_field(file_doc, old_url, new_url)
		frappe.db.commit()
		return {"success": True, "message": _("Local file deleted")}

	return {"success": False, "message": _("Could not delete local file (it may be shared)")}


@frappe.whitelist()
def get_file_preview(file_name=None, file_url=None):
	"""Return a presigned URL + metadata for previewing a cloud file."""
	if not file_name and not file_url:
		frappe.throw(_("Either file_name or file_url is required"))

	file_doc = frappe.get_doc("File", file_name) if file_name else frappe.get_doc("File", {"file_url": file_url})

	if not file_doc.is_on_cloud or not file_doc.storage_key:
		frappe.throw(_("This file is not stored on cloud storage"))

	if file_doc.is_private and file_doc.attached_to_doctype and file_doc.attached_to_name:
		if not frappe.has_permission(file_doc.attached_to_doctype, "read", file_doc.attached_to_name):
			raise frappe.PermissionError

	from frappe_toolkit.cloud_storage.client import StorageClient

	presigned_url = StorageClient().generate_presigned_url(file_doc.storage_key, file_name=file_doc.file_name)
	content_type = mimetypes.guess_type(file_doc.file_name)[0] or "application/octet-stream"
	return {"url": presigned_url, "content_type": content_type, "file_name": file_doc.file_name}


@frappe.whitelist()
def get_status():
	"""Return cloud-storage statistics for the dashboard. System Manager only."""
	frappe.only_for("System Manager")

	settings = frappe.get_cached_doc("Cloud Storage Settings")
	exempt = [d.exempt_doctype for d in settings.exempt_doctypes]

	File = frappe.qb.DocType("File")
	on_cloud = frappe.db.count("File", {"is_folder": 0, "is_on_cloud": 1})
	cloud_size = (
		frappe.qb.from_(File)
		.select(Coalesce(Sum(File.file_size), 0))
		.where(File.is_folder == 0)
		.where(File.is_on_cloud == 1)
	).run()[0][0]

	pending_base = (
		frappe.qb.from_(File)
		.where(File.is_folder == 0)
		.where(File.file_url.like("/%"))
		.where(File.is_on_cloud == 0)
		.where(File.upload_skipped == 0)
	)
	if exempt:
		pending_base = pending_base.where(
			(File.attached_to_doctype.isnull()) | (File.attached_to_doctype.notin(exempt))
		)
	result = pending_base.select(Count("*"), Coalesce(Sum(File.file_size), 0)).run()
	pending, pending_size = result[0][0], result[0][1]

	exempt_count = 0
	if exempt:
		exempt_count = frappe.db.count(
			"File",
			{"is_folder": 0, "file_url": ("like", "/%"), "is_on_cloud": 0, "attached_to_doctype": ("in", exempt)},
		)

	total_files = frappe.db.count("File", {"is_folder": 0})
	last_uploaded_at = frappe.db.sql("SELECT MAX(cloud_uploaded_at) FROM `tabFile` WHERE is_on_cloud=1")[0][0]
	recent_skipped = frappe.db.count("File", {"is_folder": 0, "upload_skipped": 1})

	return {
		"on_cloud": on_cloud,
		"pending": pending,
		"exempt": exempt_count,
		"total_files": total_files,
		"cloud_size": int(cloud_size),
		"pending_size": int(pending_size),
		"last_uploaded_at": str(last_uploaded_at) if last_uploaded_at else None,
		"recent_skipped": recent_skipped,
	}
