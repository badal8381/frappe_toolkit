frappe.ui.form.on("Cloud Storage Settings", {
	refresh: function (frm) {
		frm.trigger("render_status");

		if (frm.doc.enabled) {
			frm.add_custom_button(__("Test Connection"), () => frm.trigger("test_connection"), __("Cloud Files"));
			frm.add_custom_button(__("Migrate All Files"), () => frm.trigger("migrate_files"), __("Cloud Files"));
			frm.add_custom_button(__("Storage Status"), () => frm.trigger("render_status"), __("Cloud Files"));
			frm.add_custom_button(__("Clean Up Local Files"), () => frm.trigger("cleanup_local"), __("Cloud Files"));
		}

		if (frm.doc.enable_backups) {
			frm.add_custom_button(__("Backup Now"), () => frm.trigger("backup_now"), __("Backups"));
			frm.add_custom_button(__("Upload Local Backups"), () => frm.trigger("upload_local_backups"), __("Backups"));
			frm.add_custom_button(__("View Backup Logs"), () => frappe.set_route("List", "Cloud Storage Backup Log"), __("Backups"));
		}

		// Live progress for bulk migration / cleanup
		frappe.realtime.off("cloud_migration_progress");
		frappe.realtime.on("cloud_migration_progress", (d) =>
			frm.dashboard.show_progress(__("Migrating Files"), (d.uploaded / d.total) * 100, `${d.uploaded}/${d.total} uploaded`)
		);
		frappe.realtime.off("cloud_migration_complete");
		frappe.realtime.on("cloud_migration_complete", (d) => {
			frm.dashboard.hide_progress();
			frappe.show_alert({ message: __("Migration complete: {0} uploaded, {1} failed", [d.uploaded, d.failed]), indicator: "green" }, 7);
			frm.trigger("render_status");
		});
		frappe.realtime.off("cloud_cleanup_complete");
		frappe.realtime.on("cloud_cleanup_complete", (d) =>
			frappe.show_alert({ message: __("Cleanup complete: {0} deleted, {1} skipped", [d.deleted, d.skipped]), indicator: "green" }, 7)
		);
	},

	provider: function (frm) {
		const p = frm.doc.provider;
		if (p === "Cloudflare R2") {
			frm.set_value("region", "auto");
			frm.trigger("rebuild_r2_endpoint");
		} else {
			frappe.call({
				method: "frappe_toolkit.cloud_storage.api.get_provider_regions",
				args: { provider: p },
				callback: (r) => {
					const regions = r.message || [];
					if (regions.length && !frm.doc.region) {
						frm.set_value("region", regions[0].region);
						if (regions[0].endpoint) frm.set_value("endpoint_url", regions[0].endpoint);
					}
				},
			});
		}
	},

	r2_account_id: function (frm) {
		frm.trigger("rebuild_r2_endpoint");
	},

	rebuild_r2_endpoint: function (frm) {
		if (frm.doc.provider === "Cloudflare R2" && frm.doc.r2_account_id) {
			frm.set_value("endpoint_url", `https://${frm.doc.r2_account_id}.r2.cloudflarestorage.com`);
		}
	},

	test_connection: function (frm) {
		frappe.call({
			method: "frappe_toolkit.cloud_storage.api.test_connection",
			freeze: true,
			freeze_message: __("Testing connection..."),
			callback: (r) => {
				const res = r.message || {};
				frappe.msgprint({
					title: res.success ? __("Success") : __("Connection Failed"),
					indicator: res.success ? "green" : "red",
					message: res.message,
				});
			},
		});
	},

	migrate_files: function (frm) {
		frappe.confirm(__("Upload all pending local files to cloud storage?"), () => {
			frappe.call({
				method: "frappe_toolkit.cloud_storage.api.migrate_files",
				freeze: true,
				callback: (r) => frappe.show_alert({ message: r.message, indicator: "blue" }, 7),
			});
		});
	},

	cleanup_local: function (frm) {
		frappe.confirm(__("Delete local copies of files already on cloud storage?"), () => {
			frappe.call({
				method: "frappe_toolkit.cloud_storage.api.cleanup_local_files",
				freeze: true,
				callback: (r) => frappe.show_alert({ message: r.message, indicator: "blue" }, 7),
			});
		});
	},

	backup_now: function (frm) {
		frappe.confirm(__("Start a site backup to cloud storage now?"), () => {
			frappe.call({
				method: "frappe_toolkit.cloud_storage.backup.take_backup",
				freeze: true,
				callback: (r) => {
					if (r.message && r.message.log_name) {
						frappe.show_alert({ message: __("Backup queued: {0}", [r.message.log_name]), indicator: "blue" }, 7);
						frappe.set_route("Form", "Cloud Storage Backup Log", r.message.log_name);
					}
				},
			});
		});
	},

	upload_local_backups: function (frm) {
		frappe.confirm(__("Scan and upload pre-existing local backups to cloud storage?"), () => {
			frappe.call({
				method: "frappe_toolkit.cloud_storage.backup.upload_local_backups",
				freeze: true,
				callback: (r) => {
					if (r.message) {
						frappe.show_alert({ message: __("{0} backup(s) queued for upload", [r.message.count]), indicator: "blue" }, 7);
					}
				},
			});
		});
	},

	render_status: function (frm) {
		if (!frm.doc.enabled) return;
		frappe.call({
			method: "frappe_toolkit.cloud_storage.api.get_status",
			callback: (r) => {
				const s = r.message;
				if (!s) return;
				const fmt = (b) => frappe.form.formatters.FileSize(b);
				const html = `
					<div class="row text-center" style="gap:0;">
						<div class="col-sm-3"><div class="text-muted small">${__("On Cloud")}</div><div style="font-size:1.5rem;font-weight:600;">${s.on_cloud}</div><div class="text-muted small">${fmt(s.cloud_size)}</div></div>
						<div class="col-sm-3"><div class="text-muted small">${__("Pending")}</div><div style="font-size:1.5rem;font-weight:600;">${s.pending}</div><div class="text-muted small">${fmt(s.pending_size)}</div></div>
						<div class="col-sm-3"><div class="text-muted small">${__("Exempt")}</div><div style="font-size:1.5rem;font-weight:600;">${s.exempt}</div></div>
						<div class="col-sm-3"><div class="text-muted small">${__("Total Files")}</div><div style="font-size:1.5rem;font-weight:600;">${s.total_files}</div></div>
					</div>
					<div class="text-muted small text-center" style="margin-top:8px;">
						${s.last_uploaded_at ? __("Last upload: {0}", [frappe.datetime.str_to_user(s.last_uploaded_at)]) : __("No uploads yet")}
						${s.recent_skipped ? " · " + __("{0} skipped (file missing)", [s.recent_skipped]) : ""}
					</div>`;
				frm.get_field("status_html").$wrapper.html(html);
			},
		});
	},
});
