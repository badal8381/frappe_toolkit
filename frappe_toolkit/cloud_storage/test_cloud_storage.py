# Copyright (c) 2026, Badal and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_toolkit.cloud_storage import get_file_url
from frappe_toolkit.cloud_storage import providers as P


class TestCloudStorageHelpers(FrappeTestCase):
	def test_get_file_url_private_uses_serve_route(self):
		url = get_file_url(
			"private/Attachments/a.pdf", "a.pdf", is_private=True,
			public_base_url="https://cdn.example.com", full_key="site/private/Attachments/a.pdf",
		)
		self.assertIn("/api/method/frappe_toolkit.cloud_storage.api.serve_file", url)
		self.assertIn("key=private/Attachments/a.pdf", url)

	def test_get_file_url_public_with_domain_is_direct(self):
		url = get_file_url(
			"public/logo.png", "logo.png", is_private=False,
			public_base_url="https://cdn.example.com", full_key="site/public/logo.png",
		)
		self.assertEqual(url, "https://cdn.example.com/site/public/logo.png")

	def test_get_file_url_public_without_domain_falls_back(self):
		url = get_file_url("public/logo.png", "logo.png", is_private=False, public_base_url=None, full_key="site/public/logo.png")
		self.assertIn("serve_file", url)

	def test_provider_endpoints(self):
		self.assertEqual(P.get_endpoint("Cloudflare R2", account_id="acc"), "https://acc.r2.cloudflarestorage.com")
		self.assertEqual(P.get_endpoint("Cloudflare R2"), "")  # no account id yet
		self.assertEqual(P.get_endpoint("Wasabi", region="us-east-1"), "https://s3.us-east-1.wasabisys.com")
		self.assertEqual(P.get_endpoint("AWS S3"), "")  # boto3 default
		self.assertEqual(P.get_default_region("Cloudflare R2"), "auto")


class TestCloudStorageSettings(FrappeTestCase):
	def _new_settings(self, **kwargs):
		doc = frappe.new_doc("Cloud Storage Settings")
		doc.update(kwargs)
		return doc

	def test_r2_normalizes_region_and_endpoint(self):
		doc = self._new_settings(provider="Cloudflare R2", r2_account_id="myacc")
		doc._normalize_provider_config()
		self.assertEqual(doc.region, "auto")
		self.assertEqual(doc.endpoint_url, "https://myacc.r2.cloudflarestorage.com")

	def test_public_base_url_trailing_slash_stripped(self):
		doc = self._new_settings(provider="AWS S3", public_base_url="https://cdn.example.com/")
		doc._normalize_provider_config()
		self.assertEqual(doc.public_base_url, "https://cdn.example.com")

	def test_enabled_requires_credentials(self):
		doc = self._new_settings(provider="AWS S3", enabled=1)
		doc._normalize_provider_config()
		with self.assertRaises(frappe.ValidationError):
			doc._validate_storage()

	def test_backups_require_storage_enabled(self):
		doc = self._new_settings(provider="AWS S3", enabled=0, enable_backups=1)
		with self.assertRaises(frappe.ValidationError):
			doc._validate_backups()
