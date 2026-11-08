(() => {
  // Five tabs, switched without a reload. The address keeps the tab (#prices), so Back and a refresh stay on it;
  // a link to a section (#cardtrader, #history, #currency…) opens the tab it's in. After a button on a tab is
  // pressed, the page comes back on the same tab.
  const buttons = [...document.querySelectorAll("[data-ds-tab]")];
  if (buttons.length) {
    const panes = [...document.querySelectorAll("[data-ds-pane]")];
    const names = buttons.map(b => b.dataset.dsTab);
    const remember = name => { try { sessionStorage.setItem("ds-tab", name); } catch (e) { /* private window */ } };
    const show = (name, address) => {
      buttons.forEach(b => { const on = b.dataset.dsTab === name; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); });
      panes.forEach(p => { p.hidden = p.dataset.dsPane !== name; });
      remember(name);
      if (address) history.replaceState(history.state, "", location.pathname + location.search + "#" + name);
    };
    buttons.forEach(b => b.addEventListener("click", () => show(b.dataset.dsTab, true)));
    const follow = () => {  // the tab a #name in the address points to; true when there was one
      const hash = decodeURIComponent(location.hash.slice(1));
      const target = hash && document.getElementById(hash);
      if (names.includes(hash)) { show(hash); return true; }
      if (target && target.closest("[data-ds-pane]")) {
        show(target.closest("[data-ds-pane]").dataset.dsPane);
        target.scrollIntoView({block: "start"});
        return true;
      }
      return false;
    };
    let saved = null;
    try { saved = sessionStorage.getItem("ds-tab"); } catch (e) { /* private window */ }
    if (!follow() && saved && names.includes(saved) && document.referrer.includes("/account/data")) show(saved, true);
    addEventListener("hashchange", follow);
    // Tabs that don't fit on a phone scroll; the chosen one is kept in view.
    document.querySelector(".ds-tabs .on")?.scrollIntoView({block: "nearest", inline: "nearest"});
  }

  // Keeping less price history asks first.
  document.querySelectorAll("form[data-keep]").forEach(f => f.addEventListener("submit", e => {
    const old = f.dataset.keep.split(" ").map(Number);
    const now = ["daily", "weekly", "monthly"].map(n => Number(f.elements[n].value) || 999999);
    if (now.some((v, i) => v < old[i]) &&
        !confirm("Keep less price history? Older prices are turned into weekly and monthly averages (or deleted) now, and that can't be undone.")) {
      e.preventDefault(); e.stopImmediatePropagation();
    }
  }, true));
  // Clearing the price history needs CLEAR typed.
  document.querySelectorAll("form[data-type-clear]").forEach(f => f.addEventListener("submit", e => {
    if (f.elements.confirm.value.trim() !== "CLEAR") {
      alert("Type CLEAR to clear the price history.");
      e.preventDefault(); e.stopImmediatePropagation();
    }
  }, true));
})();
