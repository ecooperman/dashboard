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
