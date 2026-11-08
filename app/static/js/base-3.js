    // Search boxes: the round ✕ clears the box.
    document.querySelectorAll(".sbox").forEach(box => {
      const input = box.querySelector("input"), x = box.querySelector("[data-sbox-x]");
      if (!input || !x) return;
      const set = () => x.hidden = !input.value;
      set(); input.addEventListener("input", set);
      x.addEventListener("click", e => { e.preventDefault(); input.value = ""; set(); input.focus(); });
    });
    // Close the profile menu when tapping anywhere else.
    document.addEventListener("click", e => {
      const menu = document.querySelector(".me[open]");
      if (menu && !menu.contains(e.target)) menu.open = false;
    });
    // The Filter sheet slides up from the bottom.
    (() => {
      const panel = document.getElementById("filters"), toggles = [...document.querySelectorAll("[data-filter-toggle]")];
      if (!panel || !toggles.length) return;
      const scrim = document.querySelector(".filters-scrim");
      let opener = toggles[0];
      const show = open => {
        panel.hidden = scrim.hidden = !open;
        toggles.forEach(t => t.setAttribute("aria-expanded", open));
        document.body.style.overflow = open ? "hidden" : "";
        if (!open) opener.focus({preventScroll: true});
      };
      toggles.forEach(t => t.addEventListener("click", () => { opener = t; show(panel.hidden); }));
      scrim.addEventListener("click", () => show(false));
      panel.querySelectorAll("[data-filter-close]").forEach(b => b.addEventListener("click", () => show(false)));
      document.addEventListener("keydown", e => { if (e.key === "Escape") show(false); });
    })();
  
  // The Home Screen app's offline page (app/pwa.py): a service worker that only steps in when a page can't load.
  if ("serviceWorker" in navigator && isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
  // A tap that couldn't reach the server (add, chase, filters…) says so instead of doing nothing.
  (() => {
    let bar = null;
    const show = () => {
      if (!bar) {
        bar = document.createElement("div");
        bar.className = "ds-offline"; bar.setAttribute("role", "alert");
        bar.innerHTML = "<span>Can't reach the server.</span><button type=\"button\">Try again</button>";
        bar.querySelector("button").addEventListener("click", () => location.reload());
        document.body.append(bar);
      }
      bar.hidden = false;
    };
    document.body.addEventListener("htmx:sendError", show);
    document.body.addEventListener("htmx:afterRequest", e => { if (bar && e.detail.successful) bar.hidden = true; });
    window.dsOffline = show;
  })();
