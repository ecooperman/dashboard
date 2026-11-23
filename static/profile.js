// Your profile (profile.html): your own first and last name. Needs Global (shared-assets).
Global.mountUserBadge();

// Their own first and last name (shown to administrators next to their email; not needed to sign in).
(async function mountName() {
  const box = document.getElementById("profile-name");
  if (!box) return;
  let name = { first_name: "", last_name: "" };
  try {
    name = await Global.fetchJSON("/api/profile");
  } catch (err) {
    box.replaceChildren(Global.el("p", { class: "field-hint is-error", text: err.message }));
    return;
  }
  const first = Global.el("input", { type: "text", placeholder: "First name", maxlength: "60", value: name.first_name, "aria-label": "Your first name" });
  const last = Global.el("input", { type: "text", placeholder: "Last name", maxlength: "60", value: name.last_name, "aria-label": "Your last name" });
  const save = Global.el("button", { type: "button", class: "save-btn", text: "Save" });
  async function submit() {
    save.disabled = true;
    try {
      const saved = await Global.fetchJSON("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ first_name: first.value, last_name: last.value }),
      });
      first.value = saved.first_name;
      last.value = saved.last_name;
      Global.showMessage("Saved your name.", "success");
    } catch (err) {
      Global.showMessage(err.message, "error");
    }
    save.disabled = false;
  }
  save.addEventListener("click", submit);
  [first, last].forEach((i) => i.addEventListener("keydown", (e) => e.key === "Enter" && (e.preventDefault(), submit())));
  box.replaceChildren(first, last, save);
  Global.iconifyButtons && Global.iconifyButtons(box);
})();


// What they have sent through Send feedback, newest first, so they can see it went.
(async function mountMessages() {
  const box = document.getElementById("my-feedback");
  if (!box) return;
  let messages = [];
  try {
    messages = (await Global.fetchJSON("/api/feedback/mine")).messages;
  } catch (err) {
    box.replaceChildren(Global.el("p", { class: "field-hint is-error", text: err.message }));
    return;
  }
  if (!messages.length) {
    box.replaceChildren(Global.el("p", { class: "field-hint", text: "Nothing sent yet." }));
    return;
  }
  box.replaceChildren(
    ...messages.map((m) =>
      Global.el("div", { class: "request-card" }, [
        Global.el("div", { class: "request-info" }, [
          Global.el("strong", { text: `${m.app_name} - ${m.kind_label} - ${m.done_at != null ? "Dealt with" : "Waiting"}` }),
          Global.el("div", { class: "field-hint", text: new Date(m.created_at * 1000).toLocaleString() }),
          Global.el("div", { text: m.body, style: "white-space: pre-wrap" }),
        ]),
      ])
    )
  );
})();


// --- your API keys: saved once, used by every app ----------------------------------------------------------------------------
// The server never sends a key back, only whether there is one and its last four characters, so this can save, replace and remove but
// never show one.
(function mountKeys() {
  const keysEl = document.getElementById("api-keys");
  if (!keysEl) return;
  const KEYS_API = "/api/keys";

  function statusLine(k) {
    if (k.has_key && !k.readable) return { text: "Your saved key can't be read any more - please enter it again.", warn: true };
    if (k.has_key) {
      const when = k.added_at ? ` (saved ${new Date(k.added_at).toLocaleDateString()})` : "";
      return { text: `Using your key ending ${k.last4}${when}.`, warn: false };
    }
    return { text: `Not set - ${k.feature} won't work for you until you add one (unless you run the apps).`, warn: false };
  }

  async function act(k, buttons, request, doneText) {
    buttons.forEach((b) => (b.disabled = true));
    try {
      const result = await request();
      Global.showMessage(result && result.note ? result.note : doneText, result && result.note ? "error" : "success");
    } catch (err) {
      Global.showMessage(err.message, "error");
    }
    load();
  }

  function keyCard(k, canSave) {
    const status = statusLine(k);
    const input = Global.el("input", {
      type: "password", autocomplete: "off", spellcheck: "false",
      placeholder: k.has_key ? "Paste a new key to replace it" : "Paste your key", "aria-label": `${k.label} API key`,
    });
    const saveBtn = Global.el("button", { type: "button", class: "save-btn", text: "Save and check" });
    const buttons = [saveBtn];
    const actions = [saveBtn];
    const save = () => {
      const key = input.value.trim();
      if (!key) return;
      act(k, buttons, async () => {
        const result = await Global.fetchJSON(`${KEYS_API}/${k.provider}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key }) });
        input.value = "";
        return result;
      }, `Saved your ${k.label} key.`);
    };
    saveBtn.addEventListener("click", save);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        save();
      }
    });
    if (k.has_key) {
      const removeBtn = Global.el("button", { type: "button", class: "cancel-btn", text: "Remove" });
      removeBtn.addEventListener("click", () => {
        if (!confirm(`Remove your ${k.label} key? ${k.feature} will stop working for you until you add another.`)) return;
        act(k, buttons, () => Global.fetchJSON(`${KEYS_API}/${k.provider}`, { method: "DELETE" }), `Removed your ${k.label} key.`);
      });
      buttons.push(removeBtn);
      actions.push(removeBtn);
    }
    const heading = [Global.el("h3", { text: k.label })];
    if (k.optional) heading.push(Global.el("span", { class: "item-badge item-badge-muted", text: "Optional" }));
    return Global.el("div", { class: "key-card" }, [
      Global.el("div", { class: "key-card-head" }, heading),
      Global.el("p", { class: "field-hint", text: k.description }),
      Global.el("p", { class: "key-status" + (status.warn ? " warn" : k.has_key ? " ok" : ""), text: status.text }),
      canSave
        ? Global.el("div", { class: "key-form" }, [input, ...actions])
        : Global.el("p", { class: "field-hint", text: "Saving keys isn't set up on the server yet - ask the administrator." }),
      Global.el("a", { href: k.get_url, target: "_blank", rel: "noopener noreferrer", class: "field-hint key-get", text: `Where to get a ${k.label} key` }),
    ]);
  }

  async function load() {
    let data;
    try {
      data = await Global.withLoading(keysEl, Global.fetchJSON(KEYS_API));
    } catch (err) {
      keysEl.replaceChildren(Global.el("p", { class: "field-hint", text: err.message }));
      return;
    }
    const cards = data.keys.map((k) => keyCard(k, data.can_save));
    keysEl.replaceChildren(...cards);
    Global.iconifyButtons && Global.iconifyButtons(keysEl);
  }

  load();
})();
