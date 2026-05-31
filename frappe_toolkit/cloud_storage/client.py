import mimetypes
import os

import boto3
import frappe
from botocore.config import Config
from botocore.exceptions import ClientError
from frappe import _


class StorageClient:
	"""Wrapper around boto3's S3 client driven by Cloud Storage Settings.

	Works with any S3-compatible provider (AWS S3, Cloudflare R2, Wasabi,
	Backblaze B2, MinIO, etc.) via the configured endpoint URL.
	"""

	def __init__(self):
		self.settings = frappe.get_cached_doc("Cloud Storage Settings")
		if not self.settings.enabled:
			frappe.throw(_("Cloud Storage is not enabled in Cloud Storage Settings"))

		access_key, secret, region, endpoint_url = self.settings.get_storage_credentials()
		client_kwargs = {
			"region_name": region,
			"aws_access_key_id": access_key,
			"aws_secret_access_key": secret,
		}
		if endpoint_url:
			client_kwargs["endpoint_url"] = endpoint_url

		# Path-style addressing: required for Cloudflare R2 (its wildcard cert
		# *.r2.cloudflarestorage.com doesn't cover virtual-host bucket subdomains)
		# and safe for every other S3-compatible provider.
		client_kwargs["config"] = Config(s3={"addressing_style": "path"})

		self.client = boto3.client("s3", **client_kwargs)
		self.bucket = self.settings.bucket_name
		self.prefix = self.settings.folder_prefix or frappe.local.site

	def get_storage_key(self, file_doc):
		"""Generate an object key mirroring Frappe's folder structure.

		Uses the plain filename by default. Only prepends a content-hash prefix
		when a different file with the same name already exists on cloud
		(collision). Strips the 'Home/' root prefix.

		Format: {private|public}/{folder}/{file_name}
		On collision: {private|public}/{folder}/{hash}_{file_name}
		"""
		visibility = "private" if file_doc.is_private else "public"

		folder_path = file_doc.folder or "Home"
		if folder_path == "Home":
			folder_path = ""
		elif folder_path.startswith("Home/"):
			folder_path = folder_path[5:]

		file_name = file_doc.file_name or os.path.basename(file_doc.file_url or "unknown")

		if folder_path:
			key = f"{visibility}/{folder_path}/{file_name}"
		else:
			key = f"{visibility}/{file_name}"

		# Collision check: a different file with the same name/folder/visibility
		# already on cloud — prefix with content hash so we don't overwrite it.
		existing_hash = frappe.db.get_value(
			"File",
			{
				"file_name": file_doc.file_name,
				"folder": file_doc.folder or "Home",
				"is_private": file_doc.is_private,
				"is_on_cloud": 1,
				"name": ["!=", file_doc.name],
			},
			"content_hash",
		)
		if existing_hash and existing_hash != file_doc.content_hash:
			hash_prefix = (file_doc.content_hash or frappe.generate_hash(file_doc.name, 6))[:6]
			if folder_path:
				key = f"{visibility}/{folder_path}/{hash_prefix}_{file_name}"
			else:
				key = f"{visibility}/{hash_prefix}_{file_name}"

		return key

	def get_full_key(self, key):
		"""Prepend the configured folder prefix to a relative key."""
		return f"{self.prefix}/{key}" if self.prefix else key

	def upload_file(self, file_doc):
		"""Upload a File document's local file to cloud storage.

		Uses multipart upload for files larger than 5 MB. Returns the relative
		object key (without prefix) for storage on the File document.
		"""
		file_path = file_doc.get_full_path()
		if not os.path.exists(file_path):
			frappe.throw(_("Local file not found: {0}").format(file_path))

		key = self.get_storage_key(file_doc)
		full_key = self.get_full_key(key)

		content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
		extra_args = {"ContentType": content_type}

		file_size = os.path.getsize(file_path)
		if file_size > 5 * 1024 * 1024:
			config = boto3.s3.transfer.TransferConfig(
				multipart_threshold=5 * 1024 * 1024,
				multipart_chunksize=5 * 1024 * 1024,
			)
			self.client.upload_file(
				file_path, self.bucket, full_key, ExtraArgs=extra_args, Config=config
			)
		else:
			self.client.upload_file(file_path, self.bucket, full_key, ExtraArgs=extra_args)

		return key

	def generate_presigned_url(self, key, expiry=None, file_name=None):
		"""Generate a presigned GET URL for temporary access to an object."""
		if expiry is None:
			expiry = self.settings.presigned_url_expiry or 900

		full_key = self.get_full_key(key)
		params = {"Bucket": self.bucket, "Key": full_key}

		if file_name:
			from urllib.parse import quote

			ascii_name = file_name.encode("ascii", "replace").decode().replace('"', "'")
			encoded_name = quote(file_name, safe="")
			params["ResponseContentDisposition"] = (
				f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
			)

		return self.client.generate_presigned_url(
			"get_object", Params=params, ExpiresIn=expiry
		)

	def download_file(self, key):
		"""Return the raw bytes of an object."""
		full_key = self.get_full_key(key)
		response = self.client.get_object(Bucket=self.bucket, Key=full_key)
		return response["Body"].read()

	def delete_file(self, key):
		"""Delete an object from the bucket."""
		full_key = self.get_full_key(key)
		self.client.delete_object(Bucket=self.bucket, Key=full_key)

	def file_exists(self, key):
		"""Return True if an object exists in the bucket."""
		full_key = self.get_full_key(key)
		try:
			self.client.head_object(Bucket=self.bucket, Key=full_key)
			return True
		except ClientError:
			return False

	def test_connection(self):
		"""Test connectivity via head_bucket. Returns {success, message}."""
		try:
			self.client.head_bucket(Bucket=self.bucket)
			return {
				"success": True,
				"message": _("Successfully connected to bucket: {0}").format(self.bucket),
			}
		except ClientError as e:
			error_code = e.response["Error"]["Code"]
			if error_code == "404":
				return {"success": False, "message": _("Bucket '{0}' does not exist").format(self.bucket)}
			elif error_code == "403":
				return {
					"success": False,
					"message": _("Access denied to bucket '{0}'. Check your credentials.").format(self.bucket),
				}
			return {"success": False, "message": _("Error connecting to storage: {0}").format(str(e))}
		except Exception as e:
			return {"success": False, "message": _("Error connecting to storage: {0}").format(str(e))}
