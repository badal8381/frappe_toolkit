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
			this.disable_native_sidebar();
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
		this.inject_mobile_toggle();
		this.bind_events();
	}

	disable_native_sidebar() {
		// Prevent Frappe's native sidebar from rendering (avoids errors
		// when workspaces are removed but still referenced in sidebar items)
		if (frappe.app && frappe.app.sidebar) {
			frappe.app.sidebar.setup = function () {};
			frappe.app.sidebar.make_sidebar = function () {};
			frappe.app.sidebar.toggle = function () {};
		}

		// Initialize AwesomeBar search (normally done by native sidebar)
		// so that Ctrl+G / Ctrl+K keyboard shortcuts work
		this.setup_awesomebar();
	}

	setup_awesomebar() {
		if (!frappe.boot.desk_settings || !frappe.boot.desk_settings.search_bar) return;
		if (!frappe.search || !frappe.search.AwesomeBar) return;

		// Create a hidden search trigger button if it doesn't exist
		if ($('#navbar-modal-search').length === 0) {
			$('<button id="navbar-modal-search" class="hidden"></button>').appendTo('body');
		}

		let awesome_bar = new frappe.search.AwesomeBar();
		awesome_bar.setup('#navbar-modal-search');

		if (frappe.search.utils && frappe.search.utils.make_function_searchable) {
			if (frappe.utils.generate_tracking_url) {
				frappe.search.utils.make_function_searchable(
					frappe.utils.generate_tracking_url,
					__('Generate Tracking URL')
				);
			}
			if (frappe.model.can_read('RQ Job')) {
				frappe.search.utils.make_function_searchable(function () {
					frappe.set_route('List', 'RQ Job');
				}, __('Background Jobs'));
			}
		}
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

	confirm_logout() {
		this.mobile_close();
		frappe.confirm(
			__('Are you sure you want to logout?'),
			() => {
				frappe.call({
					method: 'logout',
					callback: () => {
						window.location.href = '/login';
					}
				});
			}
		);
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
					<div class="awesome-btn-row awesome-logout-btn" title="Logout">
						<div class="awesome-btn-icon awesome-logout-icon">
							<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
								<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
								<polyline points="16 17 21 12 16 7"/>
								<line x1="21" y1="12" x2="9" y2="12"/>
							</svg>
						</div>
						<span class="awesome-btn-label">Logout</span>
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
		$sidebar.find('.awesome-logout-btn').on('click', () => this.confirm_logout());
		$sidebar.find('.awesome-collapse-btn').on('click', () => this.toggle_collapse());

		this.render_items();
	}

	inject_mobile_toggle() {
		if ($('#awesome-mobile-toggle').length > 0) return;

		// Floating hamburger button
		$(`<button id="awesome-mobile-toggle" aria-label="Open menu">
			<svg viewBox="0 0 24 24"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
		</button>`).appendTo('body');

		// Overlay backdrop
		$('<div id="awesome-sidebar-overlay"></div>').appendTo('body');

		// Events
		$('#awesome-mobile-toggle').on('click', () => this.mobile_open());
		$('#awesome-sidebar-overlay').on('click', () => this.mobile_close());
	}

	mobile_open() {
		$('#awesome-sidebar').addClass('mobile-open');
		$('#awesome-sidebar-overlay').addClass('active');
		$('#awesome-mobile-toggle').hide();
	}

	mobile_close() {
		$('#awesome-sidebar').removeClass('mobile-open');
		$('#awesome-sidebar-overlay').removeClass('active');
		$('#awesome-mobile-toggle').show();
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

		if (item.type === 'Workspace') {
			return item.link_to ? frappe.router.slug(item.link_to) : null;
		}

		if (item.type === 'Report' && item.link_to) {
			return 'query-report/' + item.link_to;
		}

		if (item.type === 'Page' && item.link_to) {
			return item.link_to;
		}

		if (item.type === 'Dashboard' && item.link_to) {
			return 'dashboard-view/' + item.link_to;
		}

		let route = frappe.utils.generate_route({
			type: item.type,
			name: item.link_to,
			doctype: item.type === 'DocType' ? item.link_to : undefined,
			doc_view: item.doc_view || undefined,
		});

		return route;
	}

	render_items() {
		const self = this;
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
							window.open(nav_item.url, nav_item.open_in_new_tab ? '_blank' : '_self');
							return;
						}

						let target_route = null;

						if (nav_item.type === 'Report' && nav_item.link_to) {
							target_route = 'query-report/' + nav_item.link_to;
						} else if (nav_item.type === 'Workspace') {
							target_route = nav_item.link_to ? frappe.router.slug(nav_item.link_to) : null;
						} else if (nav_item.type === 'Page' && nav_item.link_to) {
							target_route = nav_item.link_to;
						} else if (nav_item.type === 'Dashboard' && nav_item.link_to) {
							target_route = 'dashboard-view/' + nav_item.link_to;
						} else {
							target_route = frappe.utils.generate_route({
								type: nav_item.type,
								name: nav_item.link_to,
								doctype: nav_item.type === 'DocType' ? nav_item.link_to : undefined,
								doc_view: nav_item.doc_view || undefined,
							});
						}

						if (target_route) {
							if (nav_item.open_in_new_tab) {
								window.open('/app/' + target_route, '_blank');
							} else {
								frappe.set_route(target_route);
							}
						}
						// Close mobile drawer after navigation
						self.mobile_close();
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

		let route_str;
		try {
			route_str = frappe.get_route_str ? frappe.get_route_str() : null;
		} catch (e) {
			return;
		}
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
