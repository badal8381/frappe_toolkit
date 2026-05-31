import os
from urllib.parse import parse_qs, urlparse

import frappe
from frappe.utils import now_datetime

from frappe_toolkit.cloud_storage import API_PREFIX, get_file_url


def _full_key(storage_key, settings):
	"""Build the prefixed object key from a relative key + settings."""
	prefix = settings.folder_prefix or frappe.local.site
	return f"{prefix}/{storage_key}" if prefix else storage_key


def _build_file_url(file_doc, storage_key, settings):
	"""Return the file_url to store after offload, honouring hybrid serving."""
	return get_file_url(
		storage_key,
		file_doc.file_name,
		is_private=file_doc.is_private,
		public_base_url=settings.public_base_url,
		full_key=_full_key(storage_key, settings),
	)


def on_file_upload(doc, method):
	"""after_insert hook: offload a freshly attached file to cloud storage.

	Enqueues a short background job when Instant Upload is enabled. Otherwise
	the hourly scheduler sweep picks it up.
	"""
	if getattr(doc.flags, "skip_cloud_upload", False):
		return

	if doc.is_folder or doc.is_on_cloud:
		return

	# Only act on local files
	if not doc.file_url or not doc.file_url.startswith("/"):
		return

	# Dedup-created docs that copied our serve_file URL — mark as on-cloud.
	if doc.file_url.startswith(API_PREFIX):
		_mark_dedup_file_as_cloud(doc)
		return

	# Dedup-created docs that copied a local file_url from a file already on cloud.
	if doc.content_hash:
		existing = frappe.db.get_value(
			"File",
			{"content_hash": doc.content_hash, "is_on_cloud": 1, "name": ["!=", doc.name]},
			["storage_key", "cloud_uploaded_at"],
			as_dict=True,
		)
		if existing:
			_mark_dedup_file_as_cloud(doc, storage_key=existing.storage_key, uploaded_at=existing.cloud_uploaded_at)
			return

	if doc.file_url.startswith("/api/method/"):
		return

	try:
		settings = frappe.get_cached_doc("Cloud Storage Settings")
		if not settings.enabled or not settings.upload_on_save:
			return

		exempt = {d.exempt_doctype for d in settings.exempt_doctypes}
		if doc.attached_to_doctype and doc.attached_to_doctype in exempt:
			return

		frappe.enqueue(
			"frappe_toolkit.cloud_storage.handlers._upload_single_file",
			queue="short",
			timeout=300,
			file_name=doc.name,
		)
	except Exception:
		frappe.log_error(
			title="Cloud Upload Enqueue Failed",
			message=f"Failed to enqueue cloud upload for {doc.name}: {frappe.get_traceback()}",
		)


def _upload_single_file(file_name):
	"""Background job: upload one file to cloud storage.

	Uses a SELECT ... FOR UPDATE row lock so the instant-upload handler and the
	hourly scheduler never upload the same file twice. The metadata commit is
	issued BEFORE the local file is removed so a commit failure can never lose
	the file.
	"""
	from frappe_toolkit.cloud_storage.client import StorageClient

	file_doc = frappe.get_doc("File", file_name)

	if file_doc.is_on_cloud:
		return

	lock_result = frappe.db.sql(
		"SELECT name FROM `tabFile` WHERE name=%s AND is_on_cloud=0 FOR UPDATE",
		file_name,
	)
	if not lock_result:
		return

	if file_doc.file_url and file_doc.file_url.startswith(API_PREFIX):
		_mark_dedup_file_as_cloud(file_doc)
		frappe.db.commit()
		return

	if file_doc.content_hash:
		existing = frappe.db.get_value(
			"File",
			{"content_hash": file_doc.content_hash, "is_on_cloud": 1, "name": ["!=", file_doc.name]},
			["storage_key", "cloud_uploaded_at"],
			as_dict=True,
		)
		if existing:
			_mark_dedup_file_as_cloud(file_doc, storage_key=existing.storage_key, uploaded_at=existing.cloud_uploaded_at)
			frappe.db.commit()
			return

	file_path = file_doc.get_full_path()
	if not os.path.exists(file_path):
		return

	settings = frappe.get_cached_doc("Cloud Storage Settings")
	client = StorageClient()
	storage_key = client.upload_file(file_doc)

	frappe.db.set_value(
		"File",
		file_doc.name,
		{"storage_key": storage_key, "is_on_cloud": 1, "cloud_uploaded_at": now_datetime()},
		update_modified=False,
	)
	frappe.db.commit()

	if settings.delete_local_after_upload and os.path.exists(file_path):
		old_url = file_doc.file_url
		file_doc._delete_file_on_disk()
		if not os.path.exists(file_path):
			new_url = _build_file_url(file_doc, storage_key, settings)
			frappe.db.set_value("File", file_doc.name, {"local_deleted": 1, "file_url": new_url}, update_modified=False)
			_update_parent_attach_field(file_doc, old_url, new_url)
		frappe.db.commit()

	frappe.publish_realtime("cloud_upload_complete", {"file_name": file_doc.name})


def _mark_dedup_file_as_cloud(doc, storage_key=None, uploaded_at=None):
	"""Mark a dedup-created File doc as already on cloud without re-uploading."""
	if not storage_key and doc.file_url and doc.file_url.startswith(API_PREFIX):
		parsed = urlparse(doc.file_url)
		params = parse_qs(parsed.query)
		storage_key = params.get("key", [None])[0]

	if not storage_key:
		return

	if not uploaded_at:
		# Re-validate the source is still on cloud (guards against TOCTOU).
		uploaded_at = frappe.db.get_value(
			"File", {"storage_key": storage_key, "is_on_cloud": 1}, "cloud_uploaded_at"
		)
		if not uploaded_at:
			return

	frappe.db.set_value(
		"File",
		doc.name,
		{"is_on_cloud": 1, "storage_key": storage_key, "cloud_uploaded_at": uploaded_at},
		update_modified=False,
	)


def _update_parent_attach_field(file_doc, old_url, new_url):
	"""Update Attach/Attach Image fields on the parent doc when file_url changes."""
	if not file_doc.attached_to_doctype or not file_doc.attached_to_name:
		return
	if not old_url or not new_url or old_url == new_url:
		return

	try:
		meta = frappe.get_meta(file_doc.attached_to_doctype)
	except Exception:
		return

	attach_fields = [df.fieldname for df in meta.fields if df.fieldtype in ("Attach", "Attach Image")]
	if not attach_fields:
		return

	values = frappe.db.get_value(file_doc.attached_to_doctype, file_doc.attached_to_name, attach_fields, as_dict=True)
	if not values:
		return

	for fieldname in attach_fields:
		if values.get(fieldname) == old_url:
			frappe.db.set_value(
				file_doc.attached_to_doctype, file_doc.attached_to_name, fieldname, new_url, update_modified=False
			)
			frappe.clear_document_cache(file_doc.attached_to_doctype, file_doc.attached_to_name)
			break


def on_file_delete(doc, method):
	"""on_trash hook: delete the cloud object when its File is trashed."""
	if not doc.is_on_cloud or not doc.storage_key:
		return

	try:
		settings = frappe.get_cached_doc("Cloud Storage Settings")
		if not settings.enabled or not settings.delete_on_trash:
			return

		# Don't delete a shared object still referenced by dedup siblings.
		other_refs = frappe.db.count(
			"File", {"storage_key": doc.storage_key, "is_on_cloud": 1, "name": ["!=", doc.name]}
		)
		if other_refs:
			return

		from frappe_toolkit.cloud_storage.client import StorageClient

		StorageClient().delete_file(doc.storage_key)
	except Exception as e:
		frappe.log_error(
			title="Cloud Delete Error",
			message=f"Failed to delete {doc.storage_key} from cloud: {str(e)}\n{frappe.get_traceback()}",
		)
