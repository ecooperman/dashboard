// Usage (stats.html): every app side by side, and the people across all of them. Administrators only (the server refuses anyone else).
Global.mountUserBadge();

const overallEl = document.getElementById("overall");
const appsEl = document.getElementById("apps");
const trendEl = document.getElementById("trend");
const adminsBox = document.getElementById("include-admins");

function overallCards(data) {
  const p = data.people;
  return Global.el("div", { class: "stat-grid" }, [
    Usage.card(p.with_an_account, p.with_an_account === 1 ? "person with an account" : "people with an account"),
    Usage.card(p.active_7d, "active in the last 7 days"),
    Usage.card(p.active_30d, "active in the last 30 days"),
    Usage.card(p.never_used, "have an account but have never used it"),
    Usage.card(p.in_several_apps, "use more than one app"),
    Usage.card(data.waiting_requests, "waiting for you to approve"),
  ]);
}

function appCard(app) {
  const title = Global.el("h3", { class: "section-subhead", text: app.name });
  if (app.state !== "ok") {
    return Global.el("div", { class: "stat-app is-problem" }, [title, Global.el("p", { class: "field-hint is-error", text: app.why })]);
  }
  const totals = Object.entries(app.totals).filter(([, n]) => n > 0).map(([label, n]) => `${n.toLocaleString()} ${label}`);
  return Global.el("div", { class: "stat-app" }, [
    title,
    Global.el("div", { class: "stat-grid" }, [
      Usage.card(app.accounts, app.accounts === 1 ? "person" : "people"),
      Usage.card(app.active_7d, "active in 7 days"),
      Usage.card(app.active_30d, "active in 30 days"),
      Usage.card(app.never_used, "never used it"),
    ]),
    Global.el("p", { class: "field-hint", text: `Last activity: ${Usage.ago(app.last_activity_at)}${app.active_days ? ` - ${Usage.count(app.active_days, "person-day")} of use recorded so far` : ""}` }),
    Global.el("p", { class: "field-hint", text: totals.length ? `Together they have: ${totals.join(", ")}.` : "Nothing made here yet." }),
  ]);
}

async function load() {
  let data;
  try {
    data = await Global.withLoading(overallEl, Global.fetchJSON(`/api/stats${adminsBox.checked ? "?admins=true" : ""}`));
  } catch (err) {
    overallEl.replaceChildren(Global.el("p", { class: "field-hint is-error", text: err.message }));
    appsEl.replaceChildren();
    trendEl.replaceChildren();
    return;
  }
  const notes = data.not_reporting.length
    ? [Global.el("p", { class: "field-hint is-error", text: `Left out of the numbers: ${data.not_reporting.map((n) => n.why).join("; ")}.` })]
    : [];
  overallEl.replaceChildren(overallCards(data), ...notes);
  appsEl.replaceChildren(...data.apps.map(appCard));
  trendEl.replaceChildren(...Trend.section(data.trend));
}

adminsBox.addEventListener("change", load);
load();
