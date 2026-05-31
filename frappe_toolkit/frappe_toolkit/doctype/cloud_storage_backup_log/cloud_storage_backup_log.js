frappe.ui.form.on("Cloud Storage Backup Log", {
	refresh: function (frm) {
		frm.trigger("render_pipeline");
		frm.trigger("add_download_icons");

		// Delete-local button when files were uploaded but not yet cleaned locally
		if (frm.doc.status === "Success" && frm.doc.storage_bucket && !frm.doc.local_cleaned) {
			frm.add_custom_button(__("Delete Local Files"), function () {
				frappe.confirm(__("Delete the local backup files for this entry?"), function () {
					frappe.call({
						method: "frappe_toolkit.cloud_storage.backup.cleanup_backup_local_files",
						args: { log_name: frm.doc.name },
						freeze: true,
						callback: () => {
							frappe.show_alert({ message: __("Local backup files deleted."), indicator: "green" }, 5);
							frm.reload_doc();
						},
					});
				});
			});
		}

		// Live status updates while a backup runs
		frappe.realtime.off("cloud_backup_progress");
		frappe.realtime.on("cloud_backup_progress", function (data) {
			if (data.log_name === frm.doc.name) {
				frm.reload_doc();
			}
		});
	},

	render_pipeline: function (frm) {
		const stages = ["Queued", "Generating", "Uploading", "Success"];
		const current = frm.doc.status === "Failed" ? -1 : stages.indexOf(frm.doc.status);
		const chips = stages
			.map((stage, i) => {
				const done = current >= i && current !== -1;
				const active = current === i;
				const color = done ? "#28a745" : "#d1d8dd";
				const pulse = active ? "animation:pulse 1.2s infinite;" : "";
				const label = stage === "Success" ? __("Done") : __(stage);
				return `<span style="display:inline-flex;align-items:center;">
					<span style="width:12px;height:12px;border-radius:50%;background:${color};${pulse}"></span>
					<span style="margin:0 4px;color:${done ? "#28a745" : "#8d99a6"};font-size:12px;">${label}</span>
					${i < stages.length - 1 ? `<span style="width:24px;height:2px;background:${current > i ? "#28a745" : "#d1d8dd"};margin-right:4px;"></span>` : ""}
				</span>`;
			})
			.join("");
		const failed = frm.doc.status === "Failed"
			? `<div style="color:#e24c4c;margin-top:6px;">${__("Backup Failed")}</div>`
			: "";
		frm.dashboard.set_headline(
			`<style>@keyframes pulse{0%{opacity:1}50%{opacity:.3}100%{opacity:1}}</style>
			<div style="display:flex;align-items:center;flex-wrap:wrap;">${chips}</div>${failed}`
		);
	},

	add_download_icons: function (frm) {
		const fields = {
			db_file_url: "Database",
			site_config_url: "Site Config",
			files_archive_url: "Files Archive",
			private_archive_url: "Private Files Archive",
		};
		Object.keys(fields).forEach((fieldname) => {
			if (!frm.doc[fieldname]) return;
			const field = frm.get_field(fieldname);
			if (!field || field._has_download_btn) return;
			field._has_download_btn = true;
			$(`<button class="btn btn-xs btn-default" style="margin-top:4px;">
				<svg class="icon icon-sm"><use href="#icon-download"></use></svg> ${__("Download")}
			</button>`)
				.appendTo(field.$wrapper)
				.on("click", () => {
					frappe.call({
						method: "frappe_toolkit.cloud_storage.backup.get_backup_download_url",
						args: { log_name: frm.doc.name, file_field: fieldname },
						callback: (r) => {
							if (r.message && r.message.url) window.open(r.message.url, "_blank");
						},
					});
				});
		});
	},
});
