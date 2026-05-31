from urllib.parse import quote

# File-serving endpoint for objects that need a permission check or a presigned URL.
API_PREFIX = "/api/method/frappe_toolkit.cloud_storage.api.serve_file"


def get_file_url(storage_key, file_name, is_private=True, public_base_url=None, full_key=None):
	"""Build the ``file_url`` to store on a File document after offload.

	Hybrid serving:
	  - PUBLIC files with a configured ``public_base_url`` are served directly from
	    the CDN / R2 public domain using the full object key (prefix included).
	    The browser never touches Frappe, so the response is cacheable.
	  - Everything else (private files, or no public domain configured) is served
	    through the permission-checked ``serve_file`` endpoint, which 302-redirects
	    to a short-lived presigned URL.

	Args:
		storage_key: Object key WITHOUT the folder prefix (stored on File.storage_key).
		file_name: Display name for the Content-Disposition header.
		is_private: Whether the file is private.
		public_base_url: Configured public domain (no trailing slash), or falsy.
		full_key: Object key WITH the folder prefix — required for direct public URLs.
	"""
	if not is_private and public_base_url and full_key:
		return f"{public_base_url}/{quote(full_key, safe='/')}"
	return f"{API_PREFIX}?key={quote(storage_key, safe='/')}&file_name={quote(file_name or '')}"
