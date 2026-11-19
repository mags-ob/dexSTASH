// Edit arts, on the grid: a tap ticks a tile, the bar merges the ticked arts (with Undo), and the pencil opens
// the art's sheet (its cards, each with Not this art). Without Edit arts on, a tap opens the card.
// Edit mode is edit=1 in the address, so sorts and filters keep it; the ticks live in session storage.
// A reload turns edit mode off and clears the ticks.
(() => {
  const grid = document.getElementById("arts");
  const sheet = document.getElementById("art-sheet");
  if (!grid || !sheet) return;
  const body = sheet.querySelector("[data-art-body]");
  const me = document.currentScript;
  const store = (() => { try { return window.sessionStorage; } catch { return null; } })();
  const TICKS = `arts-ticks:${location.pathname}`, UNDO = `arts-undo:${location.pathname}`;
  const read = key => { try { return JSON.parse(store?.getItem(key) || "null"); } catch { return null; } };
  const write = (key, value) => { try { value == null ? store?.removeItem(key) : store?.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } };

  async function open(card) {
    body.textContent = "Opening…";
    if (!sheet.open) sheet.showModal();
    const r = await fetch(`/pokemon/${grid.dataset.dex}/art?` + new URLSearchParams({card}));
    body.innerHTML = r.ok ? await r.text() : "Couldn't open this art. Refresh and try again.";
    body.querySelector("[data-close]")?.addEventListener("click", () => sheet.close());
  }
  sheet.addEventListener("click", e => { if (e.target === sheet) sheet.close(); });  // a tap on the backdrop

  // A reload starts over: no edit mode, no ticks, no Undo.
  const here = new URL(location.href);
  if (performance.getEntriesByType?.("navigation")[0]?.type === "reload") {
    write(TICKS, null);
    write(UNDO, null);
    here.searchParams.delete("edit");
  }

  // Ticks: the cover card of each ticked art, kept through sorts and filters.
  let ticks = new Set(read(TICKS) || []);
  const bar = document.querySelector("[data-merge]");
  const shown = () => new Set([...grid.querySelectorAll(".ptile")].map(t => t.dataset.card));
  const paint = () => {
    grid.querySelectorAll(".ptile").forEach(t => t.classList.toggle("picked", ticks.has(t.dataset.card)));
    if (!bar) return;
    const on = grid.classList.contains("editing");
    const hidden = [...ticks].filter(c => !shown().has(c)).length;
    bar.hidden = !on || !ticks.size;
    bar.querySelector("[data-count]").textContent = `${ticks.size} art${ticks.size === 1 ? "" : "s"} selected`;
    const note = bar.querySelector("[data-hidden-count]");
    note.hidden = !hidden;
    note.textContent = `${hidden} hidden by filters`;
    bar.querySelector("[data-go]").disabled = ticks.size < 2;
  };
  const tick = card => {
    ticks.has(card) ? ticks.delete(card) : ticks.add(card);
    write(TICKS, [...ticks]);
    paint();
  };
  bar?.querySelector("[data-clear]").addEventListener("click", () => { ticks = new Set(); write(TICKS, null); paint(); });
  bar?.addEventListener("submit", () => {  // every ticked art goes, hidden ones too; then the ticks are spent
    ticks.forEach(card => bar.append(Object.assign(document.createElement("input"), {type: "hidden", name: "card", value: card})));
    ticks = new Set();
    write(TICKS, null);
  });

  // Edit mode: edit=1 rides on the sort links, the active filter chips, Clear filters and the Filters form.
  const modify = document.querySelector("[data-modify]"), hint = document.querySelector("[data-modify-hint]");
  const idle = modify ? modify.innerHTML : "";  // the button as drawn: pencil, Edit arts
  const undoForm = document.querySelector("[data-undo]");
  const links = () => document.querySelectorAll(".pbar .set-sort a, .pfilters a, #filters h2 a");
  const filterForm = document.querySelector("#filters form");
  const editing = on => {
    grid.classList.toggle("editing", on);
    links().forEach(a => {
      const u = new URL(a.href, location.href);
      on ? u.searchParams.set("edit", "1") : u.searchParams.delete("edit");
      a.href = u.pathname + u.search;
    });
    let flag = filterForm?.querySelector('input[name="edit"]');
    if (on && filterForm && !flag) filterForm.append(flag = Object.assign(document.createElement("input"), {type: "hidden", name: "edit", value: "1"}));
    if (!on) flag?.remove();
    on ? here.searchParams.set("edit", "1") : here.searchParams.delete("edit");
    if (!on) { ticks = new Set(); write(TICKS, null); write(UNDO, null); }
    if (undoForm) undoForm.hidden = !on || !read(UNDO);
    if (modify) {
      modify.setAttribute("aria-pressed", String(on));
      modify.innerHTML = on ? "Done" : idle;
      modify.classList.toggle("quiet", !on);
    }
    if (hint) hint.hidden = !on;
    paint();
  };
  modify?.addEventListener("click", () => {
    editing(!grid.classList.contains("editing"));
    history.replaceState(history.state, "", here.pathname + here.search + here.hash);
  });
  // In edit mode a sort or filter replaces this page, so Back still leaves in one step.
  document.addEventListener("click", e => {
    const a = e.target.closest?.(".pbar .set-sort a, .pfilters a, #filters h2 a");
    if (!a || !grid.classList.contains("editing") || e.defaultPrevented) return;
    e.preventDefault();
    location.replace(a.href);
  });
  filterForm?.addEventListener("submit", e => {
    if (!grid.classList.contains("editing")) return;
    e.preventDefault();
    location.replace(filterForm.action + "?" + new URLSearchParams(new FormData(filterForm)));
  });
  grid.querySelectorAll(".ptile > a").forEach(a => a.addEventListener("click", e => {
    if (!grid.classList.contains("editing")) return;  // a tap opens the card
    e.preventDefault();
    if (e.target.closest("[data-pen]")) open(a.parentNode.dataset.card);
    else tick(a.parentNode.dataset.card);
  }));

  // A fix (Not this art, Merge, Undo, Re-check) is sent from the page, and the page it leads to takes the
  // current one's place, so a fix adds no step to Back. If sending fails, the form is posted as before.
  const FIX = /^\/pokemon\/-?\d+\/(fix\/|recheck$)/;
  document.addEventListener("submit", async e => {
    const form = e.target;
    if (e.defaultPrevented || form.dataset.sent || !FIX.test(new URL(form.action, location.href).pathname)) return;
    e.preventDefault();
    const data = new FormData(form, e.submitter);
    for (const k of ["show", "sort", "lang"]) if (here.searchParams.get(k) && !data.has(k)) data.set(k, here.searchParams.get(k));
    if (form === undoForm) write(UNDO, null);
    try {
      const r = await fetch(form.action, {method: "POST", body: data});
      if (!r.ok) throw new Error(r.status);
      location.replace(r.url);
    } catch {
      form.dataset.sent = "1";
      form.submit();
    }
  });

  // Back from a fix: edit mode, and the same art's sheet when there is one. After a merge, "Merged N arts · Undo"
  // shows for a few seconds; Undo merge stays beside Done until you leave edit mode or reload.
  const opened = me.dataset.openArt || here.searchParams.get("open");
  const token = here.searchParams.get("undo"), merged = here.searchParams.get("merged");
  if (token) write(UNDO, token);
  if (undoForm) undoForm.querySelector('input[name="token"]').value = read(UNDO) || "";
  editing(here.searchParams.get("edit") === "1" || Boolean(opened));
  if (opened) {
    const tile = grid.querySelector(`.ptile[data-art="${me.dataset.openArt}"]`);
    open(me.dataset.open || tile?.dataset.card);
  }
  const toast = document.querySelector("[data-toast]");
  if (token && merged && toast) {
    toast.querySelector("span").textContent = `Merged ${merged} arts`;
    toast.hidden = false;
    toast.querySelector("[data-toast-undo]").addEventListener("click", () => undoForm?.requestSubmit());
    setTimeout(() => { toast.hidden = true; }, 6000);
  }
  // open, message, undo and merged leave the address once read, so a reload or Back shows the page as it was.
  for (const k of ["open", "message", "undo", "merged"]) here.searchParams.delete(k);
  history.replaceState(history.state, "", here.pathname + here.search + here.hash);
})();
