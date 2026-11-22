// Feedback (feedback.html): what people sent from any app, by app. Administrators only (the server refuses anyone else).
Global.mountUserBadge();

const inboxEl = document.getElementById("inbox");
const importBox = document.getElementById("import-jobs");
const importBtn = document.getElementById("import-jobs-btn");
const JSON_HEADERS = { "Content-Type": "application/json" };

let data = { messages: [], can_import_jobs: false };

const when = (seconds) => new Date(seconds * 1000).toLocaleString();

async function act(message, action) {
  try {
    await Global.fetchJSON(`/api/feedback/${message.id}/${action}`, { method: "POST" });
    await load();
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
}

function card(message) {
  const done = message.done_at != null;
  const reply = Global.el("a", {
    href: `mailto:${message.email}?subject=${encodeURIComponent(`Re: your ${message.app_name} ${message.kind_label.toLowerCase()}`)}`,
    text: message.email,
  });
  const toggle = Global.el("button", { type: "button", class: "secondary-btn", text: done ? "Reopen" : "Mark done" });
  toggle.addEventListener("click", () => act(message, done ? "reopen" : "done"));
  const remove = Global.el("button", { type: "button", class: "danger-btn", text: "Delete" });
  remove.addEventListener("click", async () => {
    if (!confirm("Delete this message for good?")) return;
    try {
      await Global.fetchJSON(`/api/feedback/${message.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      Global.showMessage(err.message, "error");
    }
  });
  const where = [message.page, when(message.created_at)].filter(Boolean).join(" - ");
  const node = Global.el("div", { class: "request-card" + (done ? " is-done" : "") }, [
    Global.el("div", { class: "request-info" }, [
      Global.el("strong", { text: `${message.kind_label}${done ? " - dealt with" : ""}` }),
      Global.el("div", { class: "field-hint" }, [reply, document.createTextNode(` - ${where}`)]),
      Global.el("div", { text: message.body, style: "white-space: pre-wrap" }),
    ]),
    Global.el("div", { class: "request-actions" }, [toggle, remove]),
  ]);
  Global.iconifyButtons && Global.iconifyButtons(node);
  return node;
}

function render() {
  importBox.classList.toggle("hidden", !data.can_import_jobs);
  if (!data.messages.length) {
    inboxEl.replaceChildren(Global.el("p", { class: "field-hint", text: "Nothing yet." }));
    return;
  }
  const byApp = new Map();
  for (const message of data.messages) {
    if (!byApp.has(message.app)) byApp.set(message.app, { name: message.app_name, messages: [] });
    byApp.get(message.app).messages.push(message);
  }
  // The apps with something still waiting come first.
  const groups = [...byApp.values()].sort((a, b) => b.messages.filter((m) => m.done_at == null).length - a.messages.filter((m) => m.done_at == null).length || a.name.localeCompare(b.name));
  inboxEl.replaceChildren(
    ...groups.flatMap((group) => {
      const waiting = group.messages.filter((m) => m.done_at == null).length;
      return [
        Global.el("h3", { class: "section-subhead", text: `${group.name} (${waiting ? `${waiting} waiting` : "all dealt with"})` }),
        ...group.messages.map(card),
      ];
    })
  );
}

async function load() {
  try {
    data = await Global.withLoading(inboxEl, Global.fetchJSON("/api/feedback"));
  } catch (err) {
    inboxEl.replaceChildren(Global.el("p", { class: "field-hint is-error", text: err.message }));
    return;
  }
  render();
  const badge = document.getElementById("nav-feedback-count");
  if (badge) {
    const waiting = data.messages.filter((m) => m.done_at == null).length;
    badge.textContent = String(waiting);
    badge.classList.toggle("hidden", !waiting);
  }
}

importBtn.addEventListener("click", async () => {
  importBtn.disabled = true;
  try {
    const result = await Global.fetchJSON("/api/feedback/import-jobs", { method: "POST", headers: JSON_HEADERS, body: "{}" });
    Global.showMessage(`Copied ${Usage.count(result.copied, "message")} from the Jobs app.`, "success");
    await load();
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  importBtn.disabled = false;
});

load();
