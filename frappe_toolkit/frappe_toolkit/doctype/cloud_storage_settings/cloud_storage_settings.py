# Copyright (c) 2026, Badal and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_toolkit.cloud_storage.providers import get_default_region, get_endpoint


class CloudStorageSettings(Document):

	def validate(self):
		self._normalize_provider_config()
		self._validate_storage()
		self._validate_backups()

	def before_save(self):
		self._warn_on_disable()
		self.handle_backup_scheduler()

	def _normalize_provider_config(self):
		"""Fill in region/endpoint conventions per provider before validation."""
		if self.provider == "Cloudflare R2":
			# R2 always uses the 'auto' region and an account-id endpoint.
			self.region = "auto"
			self.endpoint_url = get_endpoint("Cloudflare R2", account_id=self.r2_account_id)
		else:
			if not self.region:
				self.region = get_default_region(self.provider)
			# Only auto-fill the endpoint when the user left it blank.
			if not self.endpoint_url:
				self.endpoint_url = get_endpoint(self.provider, region=self.region)

		if self.endpoint_url and not self.endpoint_url.startswith(("https://", "http://")):
			self.endpoint_url = f"https://{self.endpoint_url}"

		if self.public_base_url:
			self.public_base_url = self.public_base_url.rstrip("/")

	def _validate_storage(self):
		if not self.enabled:
			return

		if not self.access_key_id:
			frappe.throw(_("Access Key ID is required when Cloud Storage is enabled"))
		if not self.secret_access_key:
			frappe.throw(_("Secret Access Key is required when Cloud Storage is enabled"))
		if not self.bucket_name:
			frappe.throw(_("Bucket Name is required when Cloud Storage is enabled"))

		if self.provider == "Cloudflare R2" and not self.r2_account_id:
			frappe.throw(_("R2 Account ID is required for Cloudflare R2"))

		if self.provider == "MinIO / Other" and not self.endpoint_url:
			frappe.throw(_("Endpoint URL is required for MinIO / Other providers"))

		if self.presigned_url_expiry and (
			self.presigned_url_expiry < 1 or self.presigned_url_expiry > 604800
		):
			frappe.throw(_("Presigned URL Expiry must be between 1 and 604800 seconds (7 days)"))

	def _validate_backups(self):
		if not self.enable_backups:
			return

		if not self.enabled:
			frappe.throw(_("Cloud Storage must be enabled to use Cloud Backups"))

		if self.backup_notify_email:
			from frappe.utils import validate_email_address

			validate_email_address(self.backup_notify_email, throw=True)

		if self.backup_retention_count and self.backup_retention_count < 0:
			frappe.throw(_("Keep Last N Backups must be 0 or greater"))
		if self.backup_retention_days and self.backup_retention_days < 0:
			frappe.throw(_("Delete Backups Older Than (days) must be 0 or greater"))

	def _warn_on_disable(self):
		"""Warn (without blocking) when disabling storage while objects still live on cloud."""
		old = self.get_doc_before_save()
		if old and old.enabled and not self.enabled:
			count = frappe.db.count("File", {"is_on_cloud": 1})
			if count:
				frappe.msgprint(
					_("{0} files are stored on cloud storage. Disabling will make them inaccessible until re-enabled.").format(count),
					indicator="orange",
					title=_("Warning"),
				)

	def get_storage_credentials(self):
		"""Return (access_key, secret, region, endpoint_url) for the storage client."""
		return (
			self.access_key_id,
			self.get_password("secret_access_key"),
			self.bucket_region or self.region,
			self.endpoint_url,
		)

	def test_connection(self):
		"""Test bucket connectivity. Returns {success, message}."""
		from frappe_toolkit.cloud_storage.client import StorageClient

		client = StorageClient()
		return client.test_connection()

	def handle_backup_scheduler(self):
		"""Toggle scheduled jobs when Cloud Backups is enabled/disabled.

		When our backups are enabled, stop Frappe's built-in S3 backup jobs to
		avoid duplicate backups and enable ours; reverse on disable.
		"""
		methods = [
			("frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_daily", not self.enable_backups),
			("frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_weekly", not self.enable_backups),
			("frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_monthly", not self.enable_backups),
			("frappe_toolkit.cloud_storage.backup.take_backups_daily", self.enable_backups),
			("frappe_toolkit.cloud_storage.backup.take_backups_weekly", self.enable_backups),
			("frappe_toolkit.cloud_storage.backup.take_backups_monthly", self.enable_backups),
			("frappe_toolkit.cloud_storage.backup.rotate_old_backups_daily", self.enable_backups),
		]
		for method, enable in methods:
			self._toggle_scheduled_job(method, enable)

	def _toggle_scheduled_job(self, method_name, enable=True):
		"""Enable or disable a Scheduled Job Type by its dotted method path."""
		try:
			job = frappe.get_doc("Scheduled Job Type", {"method": method_name})
			if job.stopped == bool(enable):
				job.stopped = not enable
				job.save(ignore_permissions=True)
		except frappe.DoesNotExistError:
			# Job may not be registered yet (e.g. before migrate). Ignore quietly.
			pass
