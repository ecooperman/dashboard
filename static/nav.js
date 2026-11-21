// The menu on every dashboard page (Global.buildNav, shared-assets/theme.js): a sidebar on a wide screen, the bottom bar on a phone.
// The apps themselves stay as tiles on the home page. Everything else lives here: your profile, and for an administrator Users
// (with a count of requests waiting) and Usage. The server enforces who may open the admin pages too (they send anyone else home), so
// hiding the links is only tidiness.
const path = window.location.pathname;
(async () => {
  let me = { is_admin: false };
  try {
    me = await Global.fetchJSON("/api/me");
  } catch (err) {
    /* the page's own requests will say what is wrong */
  }
  window.DASHBOARD_ME = me;
  const items = [
    { href: "/", icon: "home", label: "Apps", active: path === "/" },
    { href: "/profile.html", icon: "person", label: "Your profile", active: path === "/profile.html" },
  ];
  if (me.is_admin) {
    items.push({
      href: "/users.html", icon: "users", label: "Users", group: "Admin", active: path === "/users.html" || path === "/person.html",
      badge: { id: "nav-waiting-count", count: me.waiting_requests || 0 }, // requests for access waiting for a decision
    });
    items.push({ href: "/stats.html", icon: "chart", label: "Usage", group: "Admin", active: path === "/stats.html" });
  }
  items.push({ icon: "refresh", label: "Refresh", onclick: () => location.reload() });
  Global.buildNav(items);
  document.dispatchEvent(new Event("dashboard-me-ready"));
})();
