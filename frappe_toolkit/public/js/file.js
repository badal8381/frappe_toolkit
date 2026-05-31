frappe.ui.form.on("File", {
	refresh: function (frm) {
		if (frm.doc.is_on_cloud && frm.doc.storage_key) {
			let label = frm.doc.local_deleted
				? __("Stored on Cloud (local deleted)")
				: __("Stored on Cloud");
			frm.dashboard.set_headline(
				`<span class="indicator-pill green">${label}</span>`
			);

			if (!frm.doc.local_deleted) {
				frm.add_custom_button(__("View on Cloud"), function () {
					frappe.call({
						method: "frappe_toolkit.cloud_storage.api.get_file_preview",
						args: { file_name: frm.doc.name },
						callback: function (r) {
							if (r.message && r.message.url) {
								window.open(r.message.url, "_blank");
							}
						},
					});
				});
			}

			if (!frm.doc.local_deleted && frappe.user.has_role("System Manager")) {
				frm.add_custom_button(__("Delete Local File"), function () {
					frappe.confirm(
						__("This permanently deletes the local copy. The file stays on cloud storage. Continue?"),
						function () {
							frappe.call({
								method: "frappe_toolkit.cloud_storage.api.delete_local_file",
								args: { file_name: frm.doc.name },
								freeze: true,
								freeze_message: __("Deleting local file..."),
								callback: function (r) {
									if (r.message && r.message.success) {
										frappe.show_alert(
											{ message: __("Local file deleted successfully."), indicator: "green" },
											5
										);
										frm.reload_doc();
									}
								},
							});
						}
					);
				});
			}
		} else if (
			!frm.doc.is_on_cloud &&
			frm.doc.file_url &&
			frm.doc.file_url.startsWith("/")
		) {
			frm.add_custom_button(__("Upload to Cloud"), function () {
				frappe.confirm(
					__("This will upload the file to cloud storage. Continue?"),
					function () {
						frappe.call({
							method: "frappe_toolkit.cloud_storage.api.upload_single_file",
							args: { file_name: frm.doc.name },
							freeze: true,
							freeze_message: __("Queuing file for cloud upload..."),
							callback: function (r) {
								if (r.message && r.message.success) {
									frappe.show_alert(
										{
											message: __("File queued. The form will refresh when complete."),
											indicator: "blue",
										},
										7
									);
								} else {
									frappe.msgprint({
										title: __("Error"),
										indicator: "red",
										message: r.message
											? r.message.message
											: __("Failed to queue file for cloud upload."),
									});
								}
							},
						});
					}
				);
			});
		}

		frappe.realtime.off("cloud_upload_complete");
		frappe.realtime.on("cloud_upload_complete", function (data) {
			if (data.file_name === frm.doc.name) {
				frm.reload_doc();
			}
		});
	},
});
