  (() => {
    const body = document.body, headings = document.querySelectorAll("[data-heading]"), picked = document.querySelector("[data-picked]"), toggle = document.querySelector("[data-select-toggle]");
    // Tiles arrive in chunks as you scroll, so boxes are looked up each time. "Select all N"
    // means every card the filters match, loaded or not: the server works them out (all=1).
    const boxes = () => [...document.querySelectorAll(".mc-check input")];
    const all = document.querySelector("[data-all]"), selectAll = document.querySelector("[data-select-all]");
    const count = () => {
      const n = all.value === "1" ? Number(selectAll.dataset.total) : boxes().filter(b => b.checked).length;
      headings.forEach(h => h.textContent = body.classList.contains("selecting") ? `${n} selected` : h.dataset.name);
      picked.textContent = `${n} selected`;
      document.querySelectorAll("[data-heavy-tpl]").forEach(b => b.dataset.heavy = b.dataset.heavyTpl.replace("{n}", n === 1 ? "1 card" : `${n} cards`));
      document.querySelectorAll(".bulk-bar button").forEach(b => b.disabled = n === 0);
      const q = document.querySelector("[data-remove-q]");
      if (q) {  // "Remove 5 cards (8 copies) from your stash?"
        const ticked = boxes().filter(b => b.checked);
        const cards = all.value === "1" ? n : new Set(ticked.map(b => b.dataset.card)).size;
        const copies = all.value === "1" ? null : ticked.reduce((t, b) => t + Number(b.dataset.qty || 1), 0);
        const plural = (k, one, many) => `${k} ${k === 1 ? one : many}`;
        const where = q.dataset.where ?? " from your stash";  // in a binder, the choice below says where from
        q.textContent = all.value === "1" ? `Remove all ${plural(n, "card", "cards")} the filters match${where}?`
          : `Remove ${plural(cards, "card", "cards")}${copies !== cards ? ` (${plural(copies, "copy", "copies")})` : ""}${where}?`;
      }
    };
    toggle.addEventListener("click", () => {
      const on = body.classList.toggle("selecting");
      toggle.textContent = on ? "Done" : "Select";
      if (!on) { boxes().forEach(b => b.checked = false); all.value = ""; }
      count();
    });
    document.querySelector(".cgrid").addEventListener("click", e => {
      const row = e.target.closest(".ctile");
      if (!row || !row.querySelector(".mc-check") || !body.classList.contains("selecting") || e.target.closest(".mc-check, form, button")) return;
      const box = row.querySelector(".mc-check input");
      box.checked = !box.checked;
      if (!box.checked) all.value = "";
      count();
    });
    document.addEventListener("change", e => {
      if (!e.target.closest(".mc-check")) return;
      if (!e.target.checked) all.value = "";
      count();
    });
    document.body.addEventListener("htmx:afterSettle", () => {  // a new chunk: ticked too while all are selected
      if (all.value === "1") boxes().forEach(b => b.checked = true);
    });
    selectAll.addEventListener("click", e => {
      e.preventDefault();
      boxes().forEach(b => b.checked = true);
      all.value = "1";
      count();
    });
    document.querySelectorAll("[data-open]").forEach(b => b.addEventListener("click", () => document.getElementById(b.dataset.open).showModal()));
    document.querySelectorAll("dialog.bulk [data-close]").forEach(b => b.addEventListener("click", () => b.closest("dialog").close()));
    const newName = document.querySelector("[data-new-binder-name]");
    document.querySelectorAll("#bulk-binder input[name=to_binder]").forEach(r => r.addEventListener("change", () => {
      newName.hidden = r.value !== "new" || !r.checked; newName.required = !newName.hidden; if (!newName.hidden) newName.focus();
    }));
    const entry = document.querySelector("[data-entry]"), fresh = document.querySelector("[data-new-entry]");
    entry.addEventListener("change", () => fresh.hidden = entry.value !== "new");
    const help = document.querySelector("[data-paid-help]");
    document.querySelectorAll("#bulk-paid input[name=mode]").forEach(r => r.addEventListener("change", () => {
      help.textContent = r.value === "split" && r.checked
        ? "The total is shared evenly across every selected copy, rounded to the cent."
        : "The same price per copy goes on every selected card. Leave it empty to clear it.";
    }));
  })();
