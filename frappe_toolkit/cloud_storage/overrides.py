from frappe.core.doctype.file.file import File

from frappe_toolkit.cloud_storage import API_PREFIX


class StorageFile(File):
	"""Override the File DocType so cloud-served URLs skip disk validation.

	Frappe's dedup logic copies file_url from an existing File doc when
	content_hash matches. When that file_url is our serve_file route (or a
	public CDN URL), Frappe's disk-based validators reject it. These overrides
	short-circuit only for cloud-stored files and leave local files untouched.
	"""

	def _is_cloud_url(self):
		return bool(self.file_url and self.file_url.startswith(API_PREFIX)) or bool(
			self.is_on_cloud and self.storage_key
		)

	def exists_on_disk(self):
		if self.is_on_cloud and self.storage_key:
			return True
		if self.file_url and self.file_url.startswith(API_PREFIX):
			return True
		return super().exists_on_disk()

	def validate_file_path(self):
		if self._is_cloud_url():
			return
		super().validate_file_path()

	def validate_file_url(self):
		if self._is_cloud_url():
			return
		super().validate_file_url()

	def validate_file_on_disk(self):
		if self._is_cloud_url():
			return
		super().validate_file_on_disk()
