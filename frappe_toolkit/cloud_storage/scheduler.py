import os

import frappe
from frappe.utils import cint, now_datetime

from frappe_toolkit.cloud_storage.handlers import (
	_build_file_url,
	_mark_dedup_file_as_cloud,
	_update_parent_attach_field,
)


def upload_pending_files():
	"""Hourly scheduler: offload local files to cloud storage in batches."""
	settings = frappe.get_cached_doc("Cloud Storage Settings")
	if not settings.enabled:
		return

	batch_size = cint(settings.batch_size) or 50
	exempt = {d.exempt_doctype for d in settings.exempt_doctypes}

	files = frappe.get_all(
		"File",
		filters={"is_folder": 0, "file_url": ("like", "/%"), "is_on_cloud": 0, "upload_skipped": 0},
		fields=["name", "file_name", "file_url", "is_private", "attached_to_doctype", "attached_to_name"],
		limit_page_length=batch_size,
		order_by="creation asc",
	)
	if not files:
		return

	from frappe_toolkit.cloud_storage.client import StorageClient

	try:
		client = StorageClient()
	except Exception:
		frappe.log_error(title="Cloud Upload Error", message=f"Failed to init storage client: {frappe.get_traceback()}")
		return

	uploaded_count = 0
	failed_files = []

	for file_data in files:
		if file_data.attached_to_doctype and file_data.attached_to_doctype in exempt:
			continue
		try:
			file_doc = frappe.get_doc("File", file_data.name)
			if file_doc.is_on_cloud:
				continue
			if file_doc.file_url and file_doc.file_url.startswith("/api/method/"):
				continue

			lock_result = frappe.db.sql(
				"SELECT name FROM `tabFile` WHERE name=%s AND is_on_cloud=0 FOR UPDATE", file_doc.name
			)
			if not lock_result:
				continue

			file_path = file_doc.get_full_path()
			if not os.path.exists(file_path):
				frappe.db.set_value("File", file_data.name, "upload_skipped", 1, update_modified=False)
				frappe.db.commit()
				continue

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
					uploaded_count += 1
					continue

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

			uploaded_count += 1
		except Exception:
			failed_files.append(file_data.name)
			continue

	if failed_files:
		frappe.log_error(
			title="Cloud Upload Errors",
			message=f"{uploaded_count} uploaded, {len(failed_files)} failed:\n" + "\n".join(failed_files),
		)


def bulk_migrate_files():
	"""Coordinator: fan out migration of all pending local files into batch jobs."""
	settings = frappe.get_cached_doc("Cloud Storage Settings")
	if not settings.enabled:
		return

	lock_key = "cloud_bulk_migration_running"
	if frappe.cache.get_value(lock_key):
		return
	frappe.cache.set_value(lock_key, 1, expires_in_sec=3600)

	exempt = list({d.exempt_doctype for d in settings.exempt_doctypes})
	batch_size = cint(settings.batch_size) or 50

	pending = frappe.get_all(
		"File", filters=_get_pending_filters(exempt), fields=["name"], order_by="creation asc", limit_page_length=0
	)
	if not pending:
		frappe.cache.delete_value(lock_key)
		return

	total = len(pending)
	chunks = [[f.name for f in pending[i:i + batch_size]] for i in range(0, total, batch_size)]
	migration_id = frappe.generate_hash(length=10)

	frappe.cache.set_value(
		f"cloud_migration:{migration_id}",
		{"total_batches": len(chunks), "completed_batches": 0, "uploaded": 0, "failed": 0},
		expires_in_sec=3600,
	)

	for chunk in chunks:
		frappe.enqueue(
			_migrate_file_batch, queue="short", timeout=600, file_names=chunk, migration_id=migration_id, total=total
		)


def _migrate_file_batch(file_names, migration_id, total):
	"""Worker: upload a batch of files to cloud storage."""
	settings = frappe.get_cached_doc("Cloud Storage Settings")
	from frappe_toolkit.cloud_storage.client import StorageClient

	try:
		client = StorageClient()
	except Exception as e:
		frappe.log_error(title="Cloud Migration Error", message=f"Failed to init storage client: {e}")
		_update_migration_progress(migration_id, 0, len(file_names), total)
		return

	uploaded = 0
	failed_files = []

	for file_name in file_names:
		try:
			file_doc = frappe.get_doc("File", file_name)

			lock_result = frappe.db.sql(
				"SELECT name FROM `tabFile` WHERE name=%s AND is_on_cloud=0 FOR UPDATE", file_doc.name
			)
			if not lock_result:
				continue

			file_path = file_doc.get_full_path()
			if not os.path.exists(file_path):
				frappe.db.set_value("File", file_name, "upload_skipped", 1, update_modified=False)
				frappe.db.commit()
				failed_files.append(file_name)
				continue

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
					uploaded += 1
					continue

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

			uploaded += 1
		except Exception:
			failed_files.append(file_name)

	if failed_files:
		frappe.log_error(
			title="Cloud Migration Errors",
			message=f"{uploaded} uploaded, {len(failed_files)} failed:\n" + "\n".join(failed_files),
		)

	_update_migration_progress(migration_id, uploaded, len(failed_files), total)


def _update_migration_progress(migration_id, batch_uploaded, batch_failed, total):
	"""Update migration progress under a Redis lock and publish realtime events."""
	cache_key = f"cloud_migration:{migration_id}"
	lock_key = f"cloud_migration_lock:{migration_id}"

	with frappe.cache.lock(lock_key, timeout=5):
		progress = frappe.cache.get_value(cache_key) or {}
		progress["uploaded"] = progress.get("uploaded", 0) + batch_uploaded
		progress["failed"] = progress.get("failed", 0) + batch_failed
		progress["completed_batches"] = progress.get("completed_batches", 0) + 1
		frappe.cache.set_value(cache_key, progress, expires_in_sec=3600)

	frappe.publish_realtime(
		"cloud_migration_progress",
		{"uploaded": progress["uploaded"], "failed": progress["failed"], "total": total},
	)

	if progress["completed_batches"] >= progress.get("total_batches", 0):
		frappe.publish_realtime(
			"cloud_migration_complete", {"uploaded": progress["uploaded"], "failed": progress["failed"]}
		)
		frappe.cache.delete_value(cache_key)
		frappe.cache.delete_value("cloud_bulk_migration_running")


def cleanup_local_files():
	"""Background job: delete local copies of files already on cloud."""
	settings = frappe.get_cached_doc("Cloud Storage Settings")
	if not settings.enabled:
		return

	batch_size = cint(settings.batch_size) or 50
	total_deleted = total_skipped = total_missing = 0
	skipped_files = []

	cleanup_filters = {"is_folder": 0, "is_on_cloud": 1, "local_deleted": 0}
	total_pending = frappe.db.count("File", cleanup_filters)
	if not total_pending:
		return

	max_batches = (total_pending // batch_size) + 2
	for _ in range(max_batches):
		files = frappe.get_all(
			"File",
			filters=cleanup_filters,
			fields=["name", "file_name", "file_url", "is_private", "storage_key"],
			limit_page_length=batch_size,
			order_by="creation asc",
		)
		if not files:
			break

		for file_data in files:
			try:
				file_doc = frappe.get_doc("File", file_data.name)
				file_path = file_doc.get_full_path()
				existed = os.path.exists(file_path)

				if existed:
					file_doc._delete_file_on_disk()

				if existed and os.path.exists(file_path):
					total_skipped += 1
					continue

				if existed:
					total_deleted += 1
				else:
					total_missing += 1

				update_fields = {"local_deleted": 1}
				old_url = file_data.file_url
				new_url = None
				if file_data.storage_key:
					new_url = _build_file_url(file_doc, file_data.storage_key, settings)
					update_fields["file_url"] = new_url
				frappe.db.set_value("File", file_data.name, update_fields, update_modified=False)
				if new_url:
					_update_parent_attach_field(file_doc, old_url, new_url)
			except Exception:
				total_skipped += 1
				skipped_files.append(file_data.name)

		frappe.db.commit()
		frappe.publish_realtime(
			"cloud_cleanup_progress",
			{"deleted": total_deleted, "missing": total_missing, "skipped": total_skipped, "total": total_pending},
		)
		if len(files) < batch_size:
			break

	frappe.publish_realtime(
		"cloud_cleanup_complete", {"deleted": total_deleted, "missing": total_missing, "skipped": total_skipped}
	)

	if skipped_files:
		frappe.log_error(
			title="Cloud Local Cleanup Errors",
			message=f"{total_deleted} deleted, {total_missing} already gone, {len(skipped_files)} failed:\n"
			+ "\n".join(skipped_files),
		)


def adopt_orphaned_files():
	"""Background job: create File docs for orphaned disk files and offload them."""
	lock_key = "cloud_adopt_orphans_running"
	if frappe.cache.get_value(lock_key):
		return
	frappe.cache.set_value(lock_key, 1, expires_in_sec=3600)

	site_path = frappe.get_site_path()
	adopted = errors = 0

	try:
		settings = frappe.get_cached_doc("Cloud Storage Settings")
		from frappe_toolkit.cloud_storage.client import StorageClient

		client = StorageClient()

		for base_dir, is_private in [("public/files", 0), ("private/files", 1)]:
			full_dir = os.path.join(site_path, base_dir)
			real_base = os.path.realpath(full_dir)
			if not os.path.isdir(real_base):
				continue

			batch = {}
			for root, _dirs, files in os.walk(full_dir):
				for fname in files:
					full_path = os.path.join(root, fname)
					if not os.path.realpath(full_path).startswith(real_base + os.sep):
						continue
					rel_path = os.path.relpath(full_path, site_path)
					file_url = "/" + rel_path if is_private else "/" + rel_path.replace("public/", "", 1)
					batch[file_url] = full_path

					if len(batch) >= 1000:
						a, e = _process_orphan_batch(batch, is_private, client, settings)
						adopted += a
						errors += e
						batch.clear()
						frappe.publish_realtime("cloud_orphan_progress", {"adopted": adopted, "errors": errors})

			if batch:
				a, e = _process_orphan_batch(batch, is_private, client, settings)
				adopted += a
				errors += e
	except Exception as e:
		frappe.log_error(title="Orphan Adoption Error", message=f"Failed during orphan adoption: {str(e)}")
	finally:
		frappe.cache.delete_value(lock_key)
		frappe.db.commit()
		frappe.publish_realtime("cloud_orphan_complete", {"adopted": adopted, "errors": errors})


def _process_orphan_batch(batch, is_private, client, settings):
	"""Create File docs for orphaned files and offload them. Returns (adopted, errors)."""
	if not batch:
		return 0, 0

	urls = list(batch.keys())
	results = frappe.db.sql(
		"SELECT file_url FROM `tabFile` WHERE file_url IN ({})".format(", ".join(["%s"] * len(urls))), tuple(urls)
	)
	existing = {r[0] for r in results}

	adopted = 0
	failed_urls = []

	for file_url, full_path in batch.items():
		if file_url in existing:
			continue

		try:
			file_doc = frappe.new_doc("File")
			file_doc.file_name = os.path.basename(full_path)
			file_doc.file_url = file_url
			file_doc.is_private = is_private
			file_doc.file_size = os.path.getsize(full_path)
			file_doc.folder = "Home"
			file_doc.flags.skip_cloud_upload = True
			file_doc.insert(ignore_permissions=True)
			frappe.db.commit()
			adopted += 1
		except Exception:
			failed_urls.append(file_url)
			continue

		try:
			if file_doc.content_hash:
				existing_cloud = frappe.db.get_value(
					"File",
					{"content_hash": file_doc.content_hash, "is_on_cloud": 1, "name": ["!=", file_doc.name]},
					["storage_key", "cloud_uploaded_at"],
					as_dict=True,
				)
				if existing_cloud:
					_mark_dedup_file_as_cloud(file_doc, storage_key=existing_cloud.storage_key, uploaded_at=existing_cloud.cloud_uploaded_at)
					frappe.db.commit()
					continue

			storage_key = client.upload_file(file_doc)
			frappe.db.set_value(
				"File",
				file_doc.name,
				{"storage_key": storage_key, "is_on_cloud": 1, "cloud_uploaded_at": now_datetime()},
				update_modified=False,
			)
			frappe.db.commit()

			if settings.delete_local_after_upload and os.path.exists(full_path):
				old_url = file_doc.file_url
				file_doc._delete_file_on_disk()
				if not os.path.exists(full_path):
					new_url = _build_file_url(file_doc, storage_key, settings)
					frappe.db.set_value("File", file_doc.name, {"local_deleted": 1, "file_url": new_url}, update_modified=False)
					_update_parent_attach_field(file_doc, old_url, new_url)
				frappe.db.commit()
		except Exception:
			pass

	if failed_urls:
		frappe.log_error(
			title="Orphan File Adoption Errors", message=f"{len(failed_urls)} files failed:\n" + "\n".join(failed_urls)
		)

	frappe.db.commit()
	return adopted, len(failed_urls)


def _get_pending_filters(exempt_doctypes):
	"""Build the filter dict for local files not yet on cloud."""
	filters = {"is_folder": 0, "file_url": ("like", "/%"), "is_on_cloud": 0, "upload_skipped": 0}
	if exempt_doctypes:
		filters["attached_to_doctype"] = ("not in", exempt_doctypes)
	return filters
