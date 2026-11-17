// The central Users page: add someone to several apps at once, tick who can use what, edit names.
Global.mountUserBadge();

const addEl = document.getElementById("add-person");
const listEl = document.getElementById("people");
const requestsEl = document.getElementById("requests");

async function api(path, method, body) {
  return Global.fetchJSON(`/api/people${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
}

function displayName(p) {
  return [p.first_name, p.last_name].filter(Boolean).join(" ");
}

// --- the grid -----------------------------------------------------------------------------------------------------

let data = { apps: [], people: [], problems: {}, requests: [] };

async function load() {
  try {
    data = await Global.withLoading(listEl, api("", "GET"));
  } catch (err) {
    listEl.replaceChildren(Global.el("p", { class: "field-hint is-error", text: err.message }));
    return;
  }
  render();
  renderRequests();
  buildAddForm();
}

// --- requests waiting ------------------------------------------------------------------------------------------------

function renderRequests() {
  if (!data.requests.length) {
    requestsEl.replaceChildren(Global.el("p", { class: "field-hint", text: "Nobody is waiting." }));
    return;
  }
  requestsEl.replaceChildren(...data.requests.map(requestCard));
}

function requestCard(req) {
  const who = displayName(req);
  const boxes = req.apps.map((a) => {
    const box = Global.el("input", { type: "checkbox" });
    box.checked = true;
    const label = Global.el("label", { class: "people-app-choice" }, [
      box,
      document.createTextNode(` ${a.name}${a.previously === "removed" ? " (their access was removed before)" : ""}`),
    ]);
    return { app: a, box, label };
  });
  const approve = Global.el("button", { type: "button", class: "save-btn", text: "Approve" });
  const decline = Global.el("button", { type: "button", class: "cancel-btn", text: "Decline" });
  const sync = () => (approve.disabled = !boxes.some((b) => b.box.checked));
  boxes.forEach((b) => b.box.addEventListener("change", sync));

  async function decide(approved) {
    approve.disabled = decline.disabled = true;
    try {
      const done = await Global.fetchJSON("/api/requests/decide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: req.email, approve: approved }),
      });
      const parts = [];
      if (done.added.length) parts.push(`${who || req.email} can now use ${done.added.join(", ")}.`);
      if (!approved.length) parts.push(`Declined ${who || req.email}.`);
      if (done.failed.length) parts.push(`Problems (left waiting): ${done.failed.join("; ")}.`);
      Global.showMessage(parts.join(" "), done.failed.length ? "error" : "success");
      await load();
    } catch (err) {
      Global.showMessage(err.message, "error");
      approve.disabled = decline.disabled = false;
      sync();
    }
  }
  approve.addEventListener("click", () => decide(boxes.filter((b) => b.box.checked).map((b) => b.app.key)));
  decline.addEventListener("click", () => {
    if (confirm(`Turn down ${who || req.email}? They won't be able to ask again.`)) decide([]);
  });

  const card = Global.el("div", { class: "request-card" }, [
    Global.el("strong", { text: who || req.email }),
    ...(who ? [Global.el("div", { class: "field-hint", text: req.email })] : []),
    ...(req.note ? [Global.el("p", { class: "request-note", text: `"${req.note}"` })] : []),
    Global.el("div", { class: "request-apps" }, [Global.el("span", { class: "field-hint", text: "Asked for:" }), ...boxes.map((b) => b.label)]),
    Global.el("div", { class: "request-actions" }, [approve, decline]),
  ]);
  Global.iconifyButtons && Global.iconifyButtons(card);
  return card;
}

function render() {
  const problems = Object.values(data.problems);
  const notes = problems.length
    ? [Global.el("p", { class: "field-hint is-error", text: `Some apps couldn't be reached, so their boxes are left alone: ${problems.join("; ")}.` })]
    : [];
  if (!data.people.length) {
    listEl.replaceChildren(...notes, Global.el("p", { class: "field-hint", text: "Nobody has an account in any app yet." }));
    return;
  }
  const head = Global.el("div", { class: "people-row people-head" }, [
    Global.el("span", { text: "Person" }),
    ...data.apps.map((a) => Global.el("span", { class: "people-cell", text: a.name })),
  ]);
  listEl.replaceChildren(...notes, head, ...data.people.map(row));
}

function row(person) {
  const nameBox = Global.el("div", { class: "people-who" });
  const showName = () => {
    const name = displayName(person);
    nameBox.replaceChildren(
      Global.el("div", { class: "people-name" }, [
        Global.el("strong", { text: name || person.email }),
        Global.el("button", { type: "button", class: "link-btn", "data-btn-icon": "none", text: name ? "Edit name" : "Add name" }),
      ]),
      ...(name ? [Global.el("div", { class: "field-hint", text: person.email })] : [])
    );
    nameBox.querySelector("button").addEventListener("click", editName);
  };
  function editName() {
    const first = Global.el("input", { type: "text", placeholder: "First name", maxlength: "60", value: person.first_name, "aria-label": `First name of ${person.email}` });
    const last = Global.el("input", { type: "text", placeholder: "Last name", maxlength: "60", value: person.last_name, "aria-label": `Last name of ${person.email}` });
    const save = Global.el("button", { type: "button", class: "save-btn", text: "Save" });
    const cancel = Global.el("button", { type: "button", class: "cancel-btn", text: "Cancel" });
    nameBox.replaceChildren(Global.el("div", { class: "people-name-form" }, [first, last, save, cancel]));
    Global.iconifyButtons && Global.iconifyButtons(nameBox);
    first.focus();
    cancel.addEventListener("click", showName);
    const submit = async () => {
      save.disabled = true;
      try {
        const saved = await api("/name", "PUT", { email: person.email, first_name: first.value, last_name: last.value });
        person.first_name = saved.first_name;
        person.last_name = saved.last_name;
        showName();
      } catch (err) {
        Global.showMessage(err.message, "error");
        save.disabled = false;
      }
    };
    save.addEventListener("click", submit);
    [first, last].forEach((i) => i.addEventListener("keydown", (e) => e.key === "Enter" && (e.preventDefault(), submit())));
  }
  showName();

  const cells = data.apps.map((app) => {
    const state = person.access[app.key];
    const box = Global.el("input", { type: "checkbox", "aria-label": `${displayName(person) || person.email} can use ${app.name}` });
    box.checked = state === "active" || state === "admin";
    box.disabled = state === "admin" || state === "unknown";
    box.title = state === "admin" ? "An administrator always has access" : state === "unknown" ? `${app.name} couldn't be reached` : "";
    box.addEventListener("change", async () => {
      const wanted = box.checked;
      if (!wanted && !confirm(`Take ${displayName(person) || person.email}'s access to ${app.name} away?\n\nNothing of theirs is deleted; ticking the box again restores it.`)) {
        box.checked = true;
        return;
      }
      box.disabled = true;
      try {
        await api("/access", "PUT", { email: person.email, app: app.key, enabled: wanted });
        person.access[app.key] = wanted ? "active" : "removed";
        Global.showMessage(`${displayName(person) || person.email} ${wanted ? "can now use" : "can no longer use"} ${app.name}.`, "success");
      } catch (err) {
        box.checked = !wanted;
        Global.showMessage(err.message, "error");
      }
      box.disabled = false;
    });
    return Global.el("span", { class: "people-cell", "data-label": app.name }, [box]);
  });
  return Global.el("div", { class: "people-row" }, [nameBox, ...cells]);
}

// --- the add form ----------------------------------------------------------------------------------------------------

function buildAddForm() {
  const attrs = { autocomplete: "off", "data-1p-ignore": "", "data-lpignore": "true", "data-bwignore": "true", "data-form-type": "other" };
  const email = Global.el("input", { type: "email", placeholder: "Email address", "aria-label": "Email address of the person to add", ...attrs });
  const first = Global.el("input", { type: "text", placeholder: "First name (optional)", maxlength: "60", "aria-label": "First name", ...attrs });
  const last = Global.el("input", { type: "text", placeholder: "Last name (optional)", maxlength: "60", "aria-label": "Last name", ...attrs });
  // Most people get everything, so every app starts ticked.
  const boxes = data.apps.map((a) => {
    const box = Global.el("input", { type: "checkbox" });
    box.checked = true;
    return { app: a, box, label: Global.el("label", { class: "people-app-choice" }, [box, document.createTextNode(` ${a.name}`)]) };
  });
  const setAll = (value) => boxes.forEach((b) => (b.box.checked = value));
  const all = Global.el("button", { type: "button", class: "link-btn", "data-btn-icon": "none", text: "All" });
  const none = Global.el("button", { type: "button", class: "link-btn", "data-btn-icon": "none", text: "None" });
  all.addEventListener("click", () => setAll(true));
  none.addEventListener("click", () => setAll(false));
  const button = Global.el("button", { type: "button", class: "secondary-btn", text: "Add" });
  const problem = Global.el("p", { class: "field-hint is-error hidden" });
  const show = (text) => {
    problem.textContent = text || "";
    problem.classList.toggle("hidden", !text);
  };

  async function add() {
    const address = email.value.trim();
    if (!address) return;
    show("");
    button.disabled = true;
    try {
      const done = await api("", "POST", {
        email: address,
        first_name: first.value,
        last_name: last.value,
        apps: boxes.filter((b) => b.box.checked).map((b) => b.app.key),
      });
      email.value = first.value = last.value = "";
      const parts = [];
      if (done.added.length) parts.push(`${done.email} can now use ${done.added.join(", ")}.`);
      if (done.already.length) parts.push(`Already had ${done.already.join(", ")}.`);
      if (done.failed.length) parts.push(`Problems: ${done.failed.join("; ")}.`);
      Global.showMessage(parts.join(" "), done.failed.length ? "error" : "success");
      await load();
    } catch (err) {
      show(err.message);
    }
    button.disabled = false;
  }
  button.addEventListener("click", add);
  [email, first, last].forEach((i) => {
    i.addEventListener("keydown", (e) => e.key === "Enter" && (e.preventDefault(), add()));
    i.addEventListener("input", () => show(""));
  });
  addEl.replaceChildren(
    Global.el("div", { class: "people-add" }, [email, first, last]),
    Global.el("div", { class: "people-add-apps" }, [Global.el("span", { class: "field-hint", text: "Let them into:" }), ...boxes.map((b) => b.label), all, none]),
    Global.el("div", { class: "people-add" }, [button]),
    problem
  );
  Global.iconifyButtons && Global.iconifyButtons(addEl);
}

load();
