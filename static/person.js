// One person across every app (person.html?email=...). Administrators only (the server refuses anyone else).
Global.mountUserBadge();

const email = (new URLSearchParams(location.search).get("email") || "").trim().toLowerCase();
const whoEl = document.getElementById("who");
const overallEl = document.getElementById("overall");
const appsEl = document.getElementById("apps");

function appCard(app) {
  const title = Global.el("h3", { class: "section-subhead", text: app.name });
  if (app.state !== "ok") return Global.el("div", { class: "stat-app is-problem" }, [title, Global.el("p", { class: "field-hint is-error", text: app.why })]);
  const account = app.account;
  if (!account) {
    const asked = { pending: "They have asked for it; it is waiting for you.", declined: "They asked for it and you declined." }[app.request] || "";
    return Global.el("div", { class: "stat-app" }, [title, Global.el("p", { class: "field-hint", text: `No account.${asked ? " " + asked : ""}` })]);
  }
  const counts = Object.entries(account.counts).filter(([, n]) => n > 0).map(([label, n]) => `${n.toLocaleString()} ${label}`);
  return Global.el("div", { class: "stat-app" }, [
    title,
    Global.el("div", { class: "stat-grid" }, [
      Usage.card(Usage.ago(account.last_activity_at), "last active"),
      Usage.card(account.active_days, account.active_days === 1 ? "day of use recorded" : "days of use recorded"),
      Usage.card(Usage.ago(account.created_at), "account made"),
    ]),
    Global.el("p", { class: "field-hint", text: `${account.status === "active" ? "" : "Access removed. "}${counts.length ? "Has: " + counts.join(", ") + "." : "Nothing made here yet."}` }),
  ]);
}

async function load() {
  if (!email) {
    overallEl.replaceChildren(Global.el("p", { class: "field-hint is-error", text: "No person was chosen. Pick someone on the Users page." }));
    return;
  }
  let data;
  try {
    data = await Global.withLoading(overallEl, Global.fetchJSON(`/api/stats/person?email=${encodeURIComponent(email)}`));
  } catch (err) {
    overallEl.replaceChildren(Global.el("p", { class: "field-hint is-error", text: err.message }));
    return;
  }
  const name = [data.first_name, data.last_name].filter(Boolean).join(" ");
  whoEl.textContent = name || data.email;
  document.title = `${name || data.email} - Brassworks`;
  overallEl.replaceChildren(
    ...(name ? [Global.el("p", { class: "field-hint", text: data.email + (data.is_admin ? " (an administrator)" : "") })] : data.is_admin ? [Global.el("p", { class: "field-hint", text: "An administrator" })] : []),
    Global.el("div", { class: "stat-grid" }, [
      Usage.card(Usage.ago(data.last_activity_at), "last active anywhere"),
      Usage.card(data.apps_with_an_account, data.apps_with_an_account === 1 ? "app they can use" : "apps they can use"),
      Usage.card(data.active_days, data.active_days === 1 ? "day of use recorded" : "days of use recorded"),
    ])
  );
  appsEl.replaceChildren(...data.apps.map(appCard));
}

load();
