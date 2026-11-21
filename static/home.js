// The signed-in person's email and a Log out link in the page header (Global.mountUserBadge reads
// /api/me, which says where Log out goes: the front door's own /auth/logout here).
Global.mountUserBadge();

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
