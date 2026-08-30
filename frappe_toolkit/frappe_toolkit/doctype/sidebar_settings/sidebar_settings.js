frappe.ui.form.on("Sidebar Settings", {
	refresh(frm) {
		(frm.doc.items || []).forEach((row) => {
			if (row.type && row.type !== "URL" && row.type !== "Section") {
				row.link_doctype = row.type;
			}
		});
		frm.refresh_fields();
	},
});

frappe.ui.form.on("Toolkit Sidebar Item", {
	type(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.type === "URL" || row.type === "Section") {
			frappe.model.set_value(cdt, cdn, "link_doctype", "");
			frappe.model.set_value(cdt, cdn, "link_to", "");
		} else {
			frappe.model.set_value(cdt, cdn, "link_doctype", row.type);
		}
	},
});
