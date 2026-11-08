  document.querySelectorAll('[data-open]').forEach(b => b.addEventListener('click', () => {
    b.closest('details')?.removeAttribute('open');
    document.getElementById(b.dataset.open).showModal();
  }));
  // Opened from a preview's ⋯ menu: show the pop-up that was picked.
  const pick = new URLSearchParams(location.search).get('open');
  if (pick && /^mod-(price|links|picture|pokemon)$/.test(pick)) document.getElementById(pick)?.showModal();
  if (pick === 'add') document.getElementById('cv-add-dialog')?.showModal();  // + on a Find tile
  if (pick) {  // Going back never opens the pop-up again
    const url = new URL(location.href);
    url.searchParams.delete('open');
    history.replaceState(history.state, '', url);
  }
  // The Card and Notes & history tabs switch without a reload; #notes (or #history) opens Notes.
  (() => {
    const buttons = document.querySelectorAll("[data-tab-btn]");
    if (!buttons.length) return;
    const show = (name, keep) => {
      buttons.forEach(b => { const on = b.dataset.tabBtn === name; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); });
      document.querySelectorAll("[data-tab]").forEach(p => { p.hidden = p.dataset.tab !== name; });
      if (!keep) history.replaceState(history.state, "", name === "notes" ? "#notes" : location.pathname + location.search);
    };
    buttons.forEach(b => b.addEventListener("click", () => show(b.dataset.tabBtn)));
    if (["#notes", "#history"].includes(location.hash)) {
      show("notes", true);
      document.getElementById(location.hash.slice(1))?.scrollIntoView({block: "start"});
    }
  })();
  // A copy card's Edit sheet. One copy shares the line's paid price, origin and note, so those are
  // greyed out unless the copy moves to another printing or condition. Remove asks first.
  document.querySelectorAll("[data-copy-form]").forEach(f => {
    const fields = f.querySelector("[data-line-fields]"), same = f.querySelector("[data-same]");
    const pick = name => f.querySelector(`[name=${name}]:checked`)?.value;
    const sync = () => {
      const one = pick("scope") === "one";
      const moved = pick("to_variant") !== f.dataset.variant || pick("to_condition") !== f.dataset.condition;
      fields.disabled = one && !moved;
      same.hidden = !fields.disabled;
    };
    f.addEventListener("change", sync);
    sync();
    f.querySelector("[data-remove]").addEventListener("click", e => {
      const n = pick("scope") === "one" ? 1 : Number(f.dataset.qty);
      if (!confirm(`Remove ${n} ${f.dataset.label} cop${n === 1 ? "y" : "ies"} from your stash?`)) e.preventDefault();
    });
  });
