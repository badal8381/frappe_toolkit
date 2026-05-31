# Copyright (c) 2026, Badal and contributors
# For license information, please see license.txt

import secrets

import frappe
from frappe.model.document import Document
from frappe.query_builder import Interval
from frappe.query_builder.functions import Now
from frappe.utils import now_datetime


class CloudStorageBackupLog(Document):

	def autoname(self):
		if self.name:
			return

		base = now_datetime().strftime("BKUP-%Y%m%d-%H%M%S")
		if frappe.db.exists("Cloud Storage Backup Log", base):
			base = f"{base}-{secrets.token_hex(2)}"
		self.name = base

	def on_trash(self):
		"""Delete cloud objects when a backup log is manually deleted."""
		if not self.storage_bucket:
			return

		keys = [
			self.db_file_url,
			self.site_config_url,
			self.files_archive_url,
			self.private_archive_url,
		]
		objects = [{"Key": key} for key in keys if key]
		if not objects:
			return

		try:
			settings = frappe.get_doc("Cloud Storage Settings")
			if not settings.enabled:
				return

			from frappe_toolkit.cloud_storage.backup import _get_boto_client

			client = _get_boto_client(settings)
			client.delete_objects(
				Bucket=self.storage_bucket,
				Delete={"Objects": objects, "Quiet": True},
			)
		except Exception:
			frappe.log_error(
				title=f"Cloud Backup Delete Failed: {self.name}",
				message=frappe.get_traceback(),
			)

	@staticmethod
	def clear_old_logs(days=90):
		table = frappe.qb.DocType("Cloud Storage Backup Log")
		frappe.db.delete(table, filters=(table.modified < (Now() - Interval(days=days))))
