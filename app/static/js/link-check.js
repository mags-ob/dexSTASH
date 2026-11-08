(() => {
  const key = "pendingGuess";
  const card = document.currentScript.dataset.card;
  const ask = () => {
    let pending = null;
    try { pending = JSON.parse(sessionStorage.getItem(key) || "null"); } catch (e) {}
    const d = pending && pending.card === card && pending.kind === "search" && document.getElementById("search-" + pending.site);
    if (d && !d.open) d.showModal();
  };
  document.addEventListener("click", e => {
    const s = e.target.closest("[data-search-site]");
    if (s) {  // A search, so ask for the link you found (once, until the next Search tap)
      try { sessionStorage.setItem(key, JSON.stringify({card, site: s.dataset.searchSite, kind: "search", at: Date.now()})); } catch (err) {}
    }
  });
  document.querySelectorAll("dialog.link-check").forEach(d => {
    const forget = () => { try { sessionStorage.removeItem(key); } catch (err) {} };
    d.addEventListener("close", forget);
    d.addEventListener("submit", forget);
  });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") ask(); });
  addEventListener("pageshow", ask);
  document.querySelector("dialog.link-check[data-open]")?.showModal();
})();
