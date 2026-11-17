// The signed-in person's email and a Log out link in the page header (Global.mountUserBadge reads
// /api/me, which says where Log out goes: the front door's own /auth/logout here).
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

// Ask for an app the person has no account in. The server takes the address from their sign-in, never from this page.
(function mountRequests() {
  const note = document.getElementById("request-note");
  document.querySelectorAll("[data-request-app]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await Global.fetchJSON("/api/requests", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ apps: [button.dataset.requestApp], note: note ? note.value : "" }),
        });
        button.replaceWith(Global.el("span", { class: "app-tile-ask field-hint", text: "Requested. Waiting for an administrator." }));
      } catch (err) {
        Global.showMessage(err.message, "error");
        button.disabled = false;
      }
    });
  });
})();
