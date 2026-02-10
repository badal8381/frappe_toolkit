// Configurable Navigation Sidebar for Frappe Toolkit
// Fetches navigation items from Sidebar Settings DocType

frappe.provide('frappe.ui');

frappe.ui.AwesomeSidebar = class AwesomeSidebar {
	constructor() {
		this.collapsed = localStorage.getItem('awesome_sidebar_collapsed') === '1';
		this.items = [];
		this.init_when_ready();
	}

	init_when_ready() {
		if (window.frappe && frappe.boot) {
			this.fetch_items();
		} else {
			setTimeout(() => this.init_when_ready(), 100);
		}
	}

	fetch_items() {
		frappe.call({
			method: 'frappe_toolkit.api.get_sidebar_items',
			async: true,
			callback: (r) => {
				if (r && r.message) {
					this.items = r.message;
				}
				if (this.items.length > 0) {
					this.setup();
				}
			}
		});
	}

	setup() {
		if ($('#awesome-sidebar').length > 0) return;
		this.inject_sidebar();
		this.bind_events();
	}

	is_dark() {
		let mode = document.documentElement.getAttribute('data-theme-mode') || 'light';
		if (mode === 'dark') return true;
		if (mode === 'automatic') return window.matchMedia('(prefers-color-scheme: dark)').matches;
		return false;
	}

	toggle_theme() {
		let next = this.is_dark() ? 'light' : 'dark';
		document.documentElement.setAttribute('data-theme-mode', next);
		frappe.ui.set_theme();
		frappe.xcall('frappe.core.doctype.user.user.switch_theme', { theme: frappe.utils.to_title_case(next) });
		this.update_theme_toggle();
	}

	update_theme_toggle() {
		$('#awesome-sidebar .awesome-theme-toggle').toggleClass('is-dark', this.is_dark());
	}

	inject_sidebar() {
		let user_fullname = frappe.session.user_fullname || frappe.session.user;
		let user_abbr = frappe.get_abbr ? frappe.get_abbr(user_fullname) : user_fullname.charAt(0).toUpperCase();
		let user_image = frappe.user.image(frappe.session.user);

		let avatar_html = user_image
			? `<div class="awesome-user-avatar"><img src="${user_image}" alt="${user_abbr}"></div>`
			: `<div class="awesome-user-avatar awesome-user-abbr">${user_abbr}</div>`;

		let is_dark = this.is_dark();

		let $sidebar = $(`
			<div id="awesome-sidebar" class="${this.collapsed ? 'collapsed' : ''}">
				<div class="awesome-scroll-section"></div>
				<div class="awesome-bottom-section">
					<div class="awesome-theme-row">
						<div class="awesome-theme-toggle ${is_dark ? 'is-dark' : ''}" title="Toggle theme">
							<div class="awesome-toggle-icon awesome-toggle-sun">${frappe.utils.icon('sun', 'sm')}</div>
							<div class="awesome-toggle-icon awesome-toggle-moon">${frappe.utils.icon('moon', 'sm')}</div>
							<div class="awesome-toggle-knob"></div>
						</div>
					</div>
					<div class="awesome-btn-row awesome-user-btn" title="${user_fullname}">
						${avatar_html}
						<span class="awesome-btn-label">${user_fullname}</span>
					</div>
					<div class="awesome-btn-row awesome-collapse-btn" title="Collapse">
						<div class="awesome-btn-icon awesome-collapse-icon">${frappe.utils.icon('left', 'md')}</div>
						<span class="awesome-btn-label">Collapse</span>
					</div>
				</div>
			</div>
		`).prependTo('body');

		$('body').addClass('awesome-sidebar-active');
		if (this.collapsed) $('body').addClass('awesome-sidebar-collapsed');

		$sidebar.find('.awesome-theme-toggle').on('click', () => this.toggle_theme());
		$sidebar.find('.awesome-user-btn').on('click', () => {
			frappe.set_route('user', frappe.session.user);
		});
		$sidebar.find('.awesome-collapse-btn').on('click', () => this.toggle_collapse());

		this.render_items();
	}

	toggle_collapse() {
		this.collapsed = !this.collapsed;
		localStorage.setItem('awesome_sidebar_collapsed', this.collapsed ? '1' : '0');
		$('#awesome-sidebar').toggleClass('collapsed', this.collapsed);
		$('body').toggleClass('awesome-sidebar-collapsed', this.collapsed);
	}

	get_route_for_item(item) {
		if (item.type === 'URL' || item.type === 'section') return null;
		if (!item.type) return null;

		let route = frappe.utils.generate_route({
			type: item.type,
			name: item.link_to,
			doctype: item.type === 'DocType' ? item.link_to : undefined,
			doc_view: item.doc_view || undefined,
		});

		return route;
	}

	render_items() {
		const $container = $('#awesome-sidebar .awesome-scroll-section');
		$container.empty();

		this.items.forEach(item => {
			if (item.type === 'section') {
				$container.append(`
					<div class="awesome-section-header">
						<span class="awesome-section-label">${item.label}</span>
						<div class="awesome-section-divider"></div>
					</div>
				`);
			} else {
				let icon_html = frappe.utils.icon(item.icon, 'md');
				let route = this.get_route_for_item(item);
				let $el = $(`
					<div class="awesome-nav-item" data-route="${route || ''}" title="${item.label}">
						<div class="awesome-nav-icon">${icon_html}</div>
						<span class="awesome-nav-label">${item.label}</span>
					</div>
				`);

				$el.on('click', (function (nav_item) {
					return function () {
						$('#awesome-sidebar .awesome-nav-item').removeClass('active');
						$(this).addClass('active');

						if (nav_item.type === 'URL') {
							window.open(nav_item.url, '_blank');
							return;
						}

						let r = frappe.utils.generate_route({
							type: nav_item.type,
							name: nav_item.link_to,
							doctype: nav_item.type === 'DocType' ? nav_item.link_to : undefined,
							doc_view: nav_item.doc_view || undefined,
						});

						if (r) {
							frappe.set_route(r);
						}
					};
				})(item));

				$el.appendTo($container);
			}
		});

		this.highlight_active();
	}

	bind_events() {
		frappe.router.on('change', () => {
			if ($('#awesome-sidebar').length === 0) this.setup();
			setTimeout(() => this.highlight_active(), 200);
		});

		$('body').tooltip({
			selector: '#awesome-sidebar.collapsed [title]',
			trigger: 'hover',
			placement: 'right',
			boundary: 'window',
			container: 'body',
			delay: { show: 400, hide: 100 }
		});
	}

	highlight_active(retry_count = 0) {
		if ($('#awesome-sidebar').length === 0) return;

		const normalize = (r) => {
			if (!r) return '';
			let s = r.toLowerCase();
			if (s.startsWith('/app/')) s = s.substring(5);
			return s;
		};

		let route_str = frappe.get_route_str ? frappe.get_route_str() : null;
		if (!route_str) return;
		let target = normalize(route_str);
		let best_match = null;
		let max_len = 0;

		$('#awesome-sidebar .awesome-nav-item').each(function () {
			let btn = normalize($(this).attr('data-route') || '');
			if (btn && target.startsWith(btn)) {
				if (btn.length > max_len) {
					max_len = btn.length;
					best_match = $(this);
				}
			}
		});

		if (best_match) {
			$('#awesome-sidebar .awesome-nav-item').removeClass('active');
			best_match.addClass('active');
		} else if (retry_count < 2) {
			setTimeout(() => this.highlight_active(retry_count + 1), 250);
		}
	}
};

$(document).ready(function () {
	new frappe.ui.AwesomeSidebar();
});
