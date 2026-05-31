# S3-compatible provider registry.
# Maps provider -> endpoint template + region list. endpoint_template is None
# when the SDK uses the default (AWS S3) or when the endpoint is derived from an
# account id (Cloudflare R2) or supplied by the user (MinIO / Other).

STORAGE_PROVIDERS = {
	"AWS S3": {
		"endpoint_template": None,  # boto3 default endpoint
		"default_region": "us-east-1",
		"regions": {},
	},
	"Wasabi": {
		"endpoint_template": "https://s3.{region}.wasabisys.com",
		"regions": {
			"us-east-1": "US East 1 (N. Virginia)",
			"us-east-2": "US East 2 (N. Virginia)",
			"us-central-1": "US Central 1 (Texas)",
			"us-west-1": "US West 1 (Oregon)",
			"eu-central-1": "EU Central 1 (Amsterdam)",
			"eu-central-2": "EU Central 2 (Frankfurt)",
			"eu-west-1": "EU West 1 (London)",
			"eu-west-2": "EU West 2 (Paris)",
			"ap-northeast-1": "AP Northeast 1 (Tokyo)",
			"ap-northeast-2": "AP Northeast 2 (Osaka)",
			"ap-southeast-1": "AP Southeast 1 (Singapore)",
			"ap-southeast-2": "AP Southeast 2 (Sydney)",
		},
	},
	"Backblaze B2": {
		"endpoint_template": "https://s3.{region}.backblazeb2.com",
		"regions": {
			"us-west-001": "US West (Sacramento)",
			"us-west-002": "US West (Phoenix)",
			"us-west-004": "US West (Oregon)",
			"us-east-005": "US East (New York)",
			"eu-central-003": "EU Central (Amsterdam)",
		},
	},
	"DigitalOcean Spaces": {
		"endpoint_template": "https://{region}.digitaloceanspaces.com",
		"regions": {
			"nyc3": "New York 3",
			"sfo2": "San Francisco 2",
			"sfo3": "San Francisco 3",
			"ams3": "Amsterdam 3",
			"sgp1": "Singapore 1",
			"lon1": "London 1",
			"fra1": "Frankfurt 1",
			"tor1": "Toronto 1",
			"blr1": "Bangalore 1",
			"syd1": "Sydney 1",
		},
	},
	"Cloudflare R2": {
		# Endpoint requires the account id: https://<account_id>.r2.cloudflarestorage.com
		# Region is always "auto" for R2.
		"endpoint_template": None,
		"default_region": "auto",
		"regions": {},
	},
	"Vultr Object Storage": {
		"endpoint_template": "https://{region}.vultrobjects.com",
		"regions": {
			"ewr1": "New Jersey",
			"sjc1": "Silicon Valley",
			"ams1": "Amsterdam",
			"sgp1": "Singapore",
			"blr1": "Bangalore",
			"del1": "New Delhi",
		},
	},
	"Linode Object Storage": {
		"endpoint_template": "https://{region}.linodeobjects.com",
		"regions": {
			"us-east-1": "US East (Newark)",
			"us-southeast-1": "US Southeast (Atlanta)",
			"us-ord-1": "US Central (Chicago)",
			"eu-central-1": "EU Central (Frankfurt)",
			"nl-ams-1": "EU West (Amsterdam)",
			"ap-south-1": "AP South (Mumbai)",
			"in-maa-1": "AP South (Chennai)",
		},
	},
	"Scaleway": {
		"endpoint_template": "https://s3.{region}.scw.cloud",
		"regions": {
			"fr-par": "Paris (France)",
			"nl-ams": "Amsterdam (Netherlands)",
			"pl-waw": "Warsaw (Poland)",
		},
	},
	"IDrive e2": {
		"endpoint_template": "https://s3.{region}.idrivee2.com",
		"regions": {
			"us-west-1": "Oregon",
			"us-west-2": "Los Angeles",
			"us-east-1": "Virginia",
			"eu-west-1": "Ireland",
			"eu-central-1": "Frankfurt 2",
			"ap-southeast-1": "Singapore",
		},
	},
	"MinIO / Other": {
		"endpoint_template": None,  # user-provided
		"regions": {},
	},
}


def get_provider_regions(provider):
	"""Return the region list for a provider for the client-side dropdown.

	Returns:
		list[dict]: [{"region": "us-east-1", "label": "us-east-1 - US East", "endpoint": "https://..."}]
	"""
	info = STORAGE_PROVIDERS.get(provider)
	if not info:
		return []

	template = info.get("endpoint_template")
	result = []
	for region_id, label in info["regions"].items():
		endpoint = template.format(region=region_id) if template else ""
		result.append({
			"region": region_id,
			"label": f"{region_id} - {label}",
			"endpoint": endpoint,
		})
	return result


def get_endpoint(provider, region=None, account_id=None):
	"""Resolve the S3 endpoint URL for a provider.

	Returns an empty string when boto3 should use its default endpoint
	(AWS S3) or when the user must supply one (MinIO / Other with no value).
	"""
	if provider == "Cloudflare R2":
		if account_id:
			return f"https://{account_id}.r2.cloudflarestorage.com"
		return ""

	info = STORAGE_PROVIDERS.get(provider) or {}
	template = info.get("endpoint_template")
	if template and region:
		return template.format(region=region)
	return ""


def get_default_region(provider):
	"""Return the conventional default region for a provider, if any."""
	info = STORAGE_PROVIDERS.get(provider) or {}
	return info.get("default_region", "")
