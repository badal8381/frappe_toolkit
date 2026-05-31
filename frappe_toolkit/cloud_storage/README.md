# Cloud Storage (R2 / S3-compatible)

Offload Frappe **File** attachments to S3-compatible object storage and run automated
**site backups** to the same provider. Built around **Cloudflare R2** (zero egress, public
custom domains) but works with AWS S3, Wasabi, Backblaze B2, DigitalOcean Spaces, Vultr,
Linode, Scaleway, IDrive e2, and MinIO via a custom endpoint.

## Highlights

- **Hybrid serving** — public files are served directly from a CDN / R2 public domain
  (cacheable, no Frappe round-trip); private files use short-lived, permission-checked
  presigned-URL redirects. Falls back to presigned URLs when no public domain is set.
- **Automatic offload** — instant background upload on attach (optional) + an hourly
  scheduler sweep, with a per-DocType exempt list and bulk migration of existing files.
- **Full backups** — scheduled (daily/weekly/monthly) DB + site config + file archives, a
  Backup Log with a status pipeline, retention/rotation, email notifications, on-demand
  "Backup Now", presigned download links, and local cleanup.

## Setup

1. Open **Cloud Storage Settings**, tick **Enable Cloud Storage**.
2. **Connection** tab: pick a **Provider** (default Cloudflare R2). For R2 enter the
   **R2 Account ID** (region + endpoint are auto-filled). For other providers pick a region;
   for MinIO/Other supply the **Endpoint URL**. Enter **Access Key ID**, **Secret Access
   Key**, **Bucket Name**.
3. *(Optional)* Set **Public Base URL** to an R2 public/custom domain (e.g.
   `https://files.example.com`) to serve public files straight from the CDN.
4. **Cloud Files → Test Connection** to verify, then `bench --site <site> migrate` (creates
   the File custom fields) and `bench build --app frappe_toolkit`.
5. *(Optional)* Tick **Enable Cloud Backups** and configure the **Backups** tab.

## How files are served

After upload, a File's `file_url` is rewritten to either:
- `https://<public-base-url>/<full_key>` — public files when a public domain is set, or
- `/api/method/frappe_toolkit.cloud_storage.api.serve_file?key=...&file_name=...` — private
  files (and public files without a public domain), which 302-redirect to a presigned URL
  after a permission check.

## Module layout

```
cloud_storage/
  __init__.py    # API_PREFIX + hybrid get_file_url()
  providers.py   # provider registry + R2/endpoint resolution
  client.py      # StorageClient (boto3 wrapper, multipart, presigned URLs)
  handlers.py    # File after_insert / on_trash
  overrides.py   # StorageFile — skip disk validation for cloud URLs
  scheduler.py   # hourly sweep, bulk migrate, local cleanup, orphan adoption
  backup.py      # backup generate/upload/retry/retention/notify + scheduler
  setup.py       # after_migrate: File custom fields
  api.py         # serve_file + migrate/cleanup/status/preview/test endpoints
```

## Requirements

- `boto3` (declared in `pyproject.toml`).
- Bucket credentials with `PutObject`, `GetObject`, `DeleteObject`, `HeadBucket`,
  `HeadObject` permissions.
- For private files keep the bucket private (access via presigned URLs); for direct public
  serving bind the bucket to an R2 public/custom domain.
