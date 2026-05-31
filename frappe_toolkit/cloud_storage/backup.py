import json
import os
import re
import traceback

import boto3
import frappe
from frappe import _
from frappe.utils import cint, now_datetime
from frappe.utils.backups import new_backup

BACKUP_LOG = "Cloud Storage Backup Log"

_FILE_NAMES = {
	"db": "database.sql.gz",
	"conf": "site_config.json",
	"files": "files.tar",
	"private": "private-files.tar",
}


# ── Whitelisted APIs ──

@frappe.whitelist()
def take_backup():
	"""Create a backup log and enqueue the backup job. System Manager only."""
	frappe.only_for("System Manager")
	return _enqueue_backup(triggered_by=frappe.session.user)


@frappe.whitelist()
def get_backup_download_url(log_name, file_field):
	"""Return a presigned download URL for one file on a backup log."""
	frappe.only_for("System Manager")

	allowed = {"db_file_url", "site_config_url", "files_archive_url", "private_archive_url"}
	if file_field not in allowed:
		frappe.throw(_("Invalid file field. Must be one of: {0}").format(", ".join(sorted(allowed))))

	log = frappe.get_doc(BACKUP_LOG, log_name)
	storage_key = log.get(file_field)
	if not storage_key:
		frappe.throw(_("No object key found for field '{0}' on {1}").format(file_field, log_name))

	settings = frappe.get_doc("Cloud Storage Settings")
	client = _get_boto_client(settings)
	expiry = settings.presigned_url_expiry or 900

	url = client.generate_presigned_url(
		"get_object", Params={"Bucket": log.storage_bucket, "Key": storage_key}, ExpiresIn=expiry
	)
	return {"url": url}


@frappe.whitelist()
def cleanup_backup_local_files(log_name):
	"""Delete local backup files for a specific backup log. System Manager only."""
	frappe.only_for("System Manager")

	log = frappe.get_doc(BACKUP_LOG, log_name)
	if log.local_cleaned:
		frappe.throw(_("Local files already cleaned for {0}").format(log_name))
	if not log.local_backup_paths:
		frappe.throw(_("No local file paths stored for {0}").format(log_name))

	paths = json.loads(log.local_backup_paths)
	_cleanup_local_backups(log, list(paths.values()))
	return {"message": _("Local backup files deleted successfully.")}


@frappe.whitelist()
def upload_local_backups():
	"""Scan the local backup directory, group by timestamp, upload each set."""
	frappe.only_for("System Manager")

	settings = frappe.get_cached_doc("Cloud Storage Settings")
	if not settings.enable_backups:
		frappe.throw(_("Cloud Backups are not enabled"))

	backup_dir = os.path.join(frappe.get_site_path(), "private", "backups")
	if not os.path.isdir(backup_dir):
		frappe.throw(_("No local backup directory found"))

	groups = {}
	pattern = re.compile(r"^(\d{8}_\d{6})-(.+?)-(database\.sql\.gz|site_config_backup\.json|files\.tar|private-files\.tar)$")
	for fname in os.listdir(backup_dir):
		m = pattern.match(fname)
		if not m:
			continue
		groups.setdefault(m.group(1), {})[m.group(3)] = os.path.join(backup_dir, fname)

	if not groups:
		frappe.throw(_("No backup files found in {0}").format(backup_dir))

	type_key_map = {
		"database.sql.gz": "db",
		"site_config_backup.json": "conf",
		"files.tar": "files",
		"private-files.tar": "private",
	}

	candidate_names = ["BKUP-" + ts.replace("_", "-") for ts in groups]
	existing_names = {
		r.name for r in frappe.get_all(BACKUP_LOG, filters={"name": ("in", candidate_names)}, fields=["name"])
	}
	new_groups = {ts: files for ts, files in groups.items() if "BKUP-" + ts.replace("_", "-") not in existing_names}
	if not new_groups:
		frappe.throw(_("All local backups have already been uploaded"))

	triggered_by = frappe.session.user
	queued = []
	backup_timeout = cint(settings.backup_timeout) or 6000

	for ts in sorted(new_groups):
		backup_paths = {}
		for ftype, path in new_groups[ts].items():
			key = type_key_map.get(ftype)
			if key:
				backup_paths[key] = path

		log_name = "BKUP-" + ts.replace("_", "-")
		log = frappe.get_doc({"doctype": BACKUP_LOG, "status": "Queued"})
		log.name = log_name
		log.flags.name_set = True
		log.local_backup_paths = json.dumps(backup_paths)
		log.insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.enqueue(
			_run_upload_existing, queue="long", timeout=backup_timeout, log_name=log_name, triggered_by=triggered_by
		)
		queued.append(log_name)

	return {"queued": queued, "count": len(queued)}


# ── Background jobs ──

def _enqueue_backup(triggered_by="Administrator"):
	settings = frappe.get_cached_doc("Cloud Storage Settings")
	if not settings.enable_backups:
		frappe.throw(_("Cloud Backups are not enabled"))

	log = frappe.get_doc({"doctype": BACKUP_LOG, "status": "Queued"})
	log.insert(ignore_permissions=True)
	frappe.db.commit()

	backup_timeout = cint(settings.backup_timeout) or 6000
	frappe.enqueue(_run_backup, queue="long", timeout=backup_timeout, log_name=log.name, triggered_by=triggered_by, retry_count=0)
	return {"log_name": log.name}


def _run_backup(log_name, retry_count=0, triggered_by="Administrator"):
	"""Background job: generate backup, upload, update log, rotate."""
	log = frappe.get_doc(BACKUP_LOG, log_name)
	settings = None
	client = None
	try:
		log.status = "Generating"
		log.started_at = now_datetime()
		log.save(ignore_permissions=True)
		frappe.db.commit()
		_publish(triggered_by, "generating", log_name)

		settings = frappe.get_doc("Cloud Storage Settings")
		include_files = settings.backup_files
		bucket = settings.backup_bucket_name or settings.bucket_name
		timestamp = now_datetime().strftime("%Y%m%d_%H%M%S")
		prefix = settings.backup_folder_prefix or settings.folder_prefix
		folder = f"{prefix}/backups/{timestamp}" if prefix else f"backups/{timestamp}"

		odb = new_backup(ignore_files=not include_files, force=True)

		backup_paths = {"db": odb.backup_path_db, "conf": odb.backup_path_conf}
		if include_files:
			backup_paths["files"] = odb.backup_path_files
			backup_paths["private"] = odb.backup_path_private_files

		log.reload()
		log.local_backup_paths = json.dumps({k: v for k, v in backup_paths.items() if v})
		log.status = "Uploading"
		log.save(ignore_permissions=True)
		frappe.db.commit()
		_publish(triggered_by, "uploading", log_name)

		client = _get_boto_client(settings)
		uploaded, total_size, db_size, files_size = _upload_paths(client, bucket, folder, backup_paths)

		log.reload()
		log.status = "Success"
		log.completed_at = now_datetime()
		_set_log_results(log, bucket, folder, uploaded, total_size, db_size, files_size)
		log.save(ignore_permissions=True)
		frappe.db.commit()
		_publish(triggered_by, "success", log_name)

		if settings.delete_local_after_upload:
			_cleanup_local_backups(log, list(backup_paths.values()))

		if settings.backup_notify_email and settings.backup_notify_success:
			_send_notification(settings.backup_notify_email, log, success=True)

	except Exception:
		tb = traceback.format_exc()
		if retry_count < 2:
			retry_timeout = cint(frappe.db.get_single_value("Cloud Storage Settings", "backup_timeout")) or 6000
			frappe.enqueue(
				_run_backup, queue="long", timeout=retry_timeout, log_name=log_name,
				triggered_by=triggered_by, retry_count=retry_count + 1,
			)
			return

		_mark_failed(log, tb)
		_publish(triggered_by, "failed", log_name)

		settings = frappe.get_doc("Cloud Storage Settings")
		if settings.backup_notify_email:
			_send_notification(settings.backup_notify_email, log, success=False)
		return

	# Rotate outside the try/except so rotation errors don't trigger backup retries.
	try:
		_rotate_old_backups(client, settings, exclude_log=log_name)
	except Exception:
		frappe.log_error(title="Cloud Backup Rotation Error", message=frappe.get_traceback())


def _run_upload_existing(log_name, triggered_by="Administrator"):
	"""Background job: upload pre-existing local backup files."""
	log = frappe.get_doc(BACKUP_LOG, log_name)
	try:
		log.status = "Uploading"
		log.started_at = now_datetime()
		log.save(ignore_permissions=True)
		frappe.db.commit()
		_publish(triggered_by, "uploading", log_name)

		backup_paths = json.loads(log.local_backup_paths)
		settings = frappe.get_doc("Cloud Storage Settings")
		bucket = settings.backup_bucket_name or settings.bucket_name
		ts = _extract_timestamp(backup_paths)
		prefix = settings.backup_folder_prefix or settings.folder_prefix
		folder = f"{prefix}/backups/{ts}" if prefix else f"backups/{ts}"

		client = _get_boto_client(settings)
		uploaded, total_size, db_size, files_size = _upload_paths(client, bucket, folder, backup_paths)

		log.reload()
		log.status = "Success"
		log.completed_at = now_datetime()
		_set_log_results(log, bucket, folder, uploaded, total_size, db_size, files_size)
		log.save(ignore_permissions=True)
		frappe.db.commit()

		if settings.delete_local_after_upload:
			_cleanup_local_backups(log, list(backup_paths.values()))

		_publish(triggered_by, "success", log_name)
	except Exception:
		_mark_failed(log, traceback.format_exc())
		_publish(triggered_by, "failed", log_name)


# ── Shared helpers ──

def _get_boto_client(settings):
	"""Create a boto3 S3 client from Cloud Storage Settings credentials."""
	from botocore.config import Config

	access_key, secret, region, endpoint_url = settings.get_storage_credentials()
	kwargs = {"region_name": region, "aws_access_key_id": access_key, "aws_secret_access_key": secret}
	if endpoint_url:
		kwargs["endpoint_url"] = endpoint_url
	# Path-style addressing — required for Cloudflare R2, safe everywhere.
	kwargs["config"] = Config(s3={"addressing_style": "path"})
	return boto3.client("s3", **kwargs)


def _get_backup_dir():
	return os.path.realpath(os.path.join(frappe.get_site_path(), "private", "backups"))


def _safe_backup_path(path):
	"""Ensure a path is inside the site's backup directory."""
	real = os.path.realpath(path)
	backup_dir = _get_backup_dir()
	if not real.startswith(backup_dir + os.sep):
		frappe.throw(_("Invalid backup path: {0}").format(path))
	return real


def _upload_paths(client, bucket, folder, backup_paths):
	"""Upload {key: local_path} to the bucket. Returns (uploaded, total, db_size, files_size)."""
	uploaded = {}
	total_size = db_size = files_size = 0

	for key, path in backup_paths.items():
		if not path:
			continue
		safe_path = _safe_backup_path(path)
		if not os.path.exists(safe_path):
			continue
		file_name = _FILE_NAMES.get(key, os.path.basename(safe_path))
		storage_key = f"{folder}/{file_name}"
		file_size = os.path.getsize(safe_path)

		client.upload_file(safe_path, bucket, storage_key)
		uploaded[key] = storage_key
		total_size += file_size
		if key == "db":
			db_size = file_size
		elif key in ("files", "private"):
			files_size += file_size

	return uploaded, total_size, db_size, files_size


def _set_log_results(log, bucket, folder, uploaded, total_size, db_size, files_size):
	log.storage_bucket = bucket
	log.storage_folder = folder
	log.db_file_url = uploaded.get("db", "")
	log.site_config_url = uploaded.get("conf", "")
	log.files_archive_url = uploaded.get("files", "")
	log.private_archive_url = uploaded.get("private", "")
	log.db_size = db_size
	log.files_size = files_size
	log.total_size = total_size


def _extract_timestamp(backup_paths):
	pattern = re.compile(r"(\d{8}_\d{6})")
	for path in backup_paths.values():
		if path:
			m = pattern.search(os.path.basename(path))
			if m:
				return m.group(1)
	return now_datetime().strftime("%Y%m%d_%H%M%S")


def _cleanup_local_backups(log, paths):
	for path in paths:
		if not path:
			continue
		safe_path = _safe_backup_path(path)
		if os.path.exists(safe_path):
			os.remove(safe_path)
	log.reload()
	log.local_cleaned = 1
	log.save(ignore_permissions=True)
	frappe.db.commit()


def _mark_failed(log, traceback_text):
	log.reload()
	log.status = "Failed"
	log.completed_at = now_datetime()
	log.error = traceback_text
	log.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.log_error(title="Cloud Backup Failed", message=traceback_text)


def _publish(user, status, log_name):
	frappe.publish_realtime("cloud_backup_progress", {"status": status, "log_name": log_name}, user=user)


def _send_notification(email, log, success=True):
	site = frappe.local.site
	if success:
		subject = _("Cloud Backup Successful - {0}").format(site)
		message = _(
			"Cloud backup completed successfully.<br><br>"
			"<b>Log:</b> {log_name}<br><b>Bucket:</b> {bucket}<br>"
			"<b>Database:</b> {db_file}<br><b>Total Size:</b> {total_size}"
		).format(
			log_name=log.name, bucket=log.storage_bucket, db_file=log.db_file_url, total_size=_fmt_size(log.total_size)
		)
	else:
		subject = _("Cloud Backup Failed - {0}").format(site)
		message = _("Cloud backup failed.<br><br><b>Log:</b> {log_name}<br><b>Error:</b> Check the backup log for details.").format(
			log_name=log.name
		)
	try:
		frappe.sendmail(recipients=[email], subject=subject, message=message)
	except Exception:
		frappe.log_error(title="Cloud Backup Email Failed", message=frappe.get_traceback())


# ── Retention / rotation ──

def rotate_old_backups_daily():
	"""Scheduler: rotate old backups based on retention settings."""
	settings = frappe.get_cached_doc("Cloud Storage Settings")
	if not settings.enable_backups or not settings.enabled:
		return
	try:
		_rotate_old_backups(_get_boto_client(settings), settings)
	except Exception:
		frappe.log_error(title="Cloud Backup Rotation Error", message=frappe.get_traceback())


def _rotate_old_backups(client, settings, exclude_log=None):
	"""Delete old backups per retention settings. Always keeps the latest success."""
	retention_count = settings.backup_retention_count or 0
	retention_days = settings.backup_retention_days or 0
	if not retention_count and not retention_days:
		return

	all_logs = frappe.get_all(BACKUP_LOG, filters={"status": "Success"}, fields=["name"], order_by="creation desc")
	if not all_logs:
		return

	latest_log = all_logs[0].name
	to_delete = set()

	if retention_count > 0 and len(all_logs) > retention_count:
		for entry in all_logs[retention_count:]:
			to_delete.add(entry.name)

	if retention_days > 0:
		from frappe.utils import add_days

		cutoff = add_days(now_datetime(), -retention_days)
		old = frappe.get_all(
			BACKUP_LOG, filters={"status": "Success", "completed_at": ["<", cutoff]}, fields=["name"]
		)
		for entry in old:
			to_delete.add(entry.name)

	to_delete.discard(latest_log)
	if exclude_log:
		to_delete.discard(exclude_log)
	if not to_delete:
		return

	for log_name in to_delete:
		try:
			_delete_backup_objects(client, log_name)
		except Exception:
			frappe.log_error(title=f"Cloud Backup Rotation Failed: {log_name}", message=frappe.get_traceback())


def _delete_backup_objects(client, log_name):
	"""Delete all objects for a backup log, then delete the log."""
	log = frappe.get_doc(BACKUP_LOG, log_name)
	bucket = log.storage_bucket
	if not bucket:
		frappe.delete_doc(BACKUP_LOG, log_name, ignore_permissions=True)
		return

	keys = [log.db_file_url, log.site_config_url, log.files_archive_url, log.private_archive_url]
	objects = [{"Key": k} for k in keys if k]
	if objects:
		client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})

	frappe.delete_doc(BACKUP_LOG, log_name, ignore_permissions=True)
	frappe.db.commit()


def _fmt_size(size_bytes):
	if not size_bytes:
		return "0 B"
	units = ["B", "KB", "MB", "GB", "TB"]
	i = 0
	size = float(size_bytes)
	while size >= 1024 and i < len(units) - 1:
		size /= 1024
		i += 1
	return f"{size:.1f} {units[i]}"


# ── Scheduler entry points ──

def take_backups_daily():
	_take_backups_if("Daily")


def take_backups_weekly():
	_take_backups_if("Weekly")


def take_backups_monthly():
	_take_backups_if("Monthly")


def _take_backups_if(freq):
	settings = frappe.get_cached_doc("Cloud Storage Settings")
	if not settings.enable_backups or not settings.enabled:
		return
	if settings.backup_frequency != freq:
		return
	_enqueue_backup()
