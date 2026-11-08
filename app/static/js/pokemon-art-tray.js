// Edit arts: a pencil on each tile; a tap opens that art's Edit art sheet (its cards, Add a card, Taken out).
// Without Edit arts on, a tap opens the card.
(() => {
  const grid = document.getElementById("arts");
  const sheet = document.getElementById("art-sheet");
  if (!grid || !sheet) return;
  const body = sheet.querySelector("[data-art-body]");
  async function open(card) {
    body.textContent = "Opening…";
    if (!sheet.open) sheet.showModal();
    const r = await fetch(`/pokemon/${grid.dataset.dex}/art?` + new URLSearchParams({card}));
    body.innerHTML = r.ok ? await r.text() : "Couldn't open this art. Refresh and try again.";
    body.querySelector("[data-close]")?.addEventListener("click", () => sheet.close());
    const add = body.querySelector("[data-add]");
    if (add) adding(add);
  }
  // Add a card to this art: this Pokémon's cards (filtered as you type, 30 at a time) or All cards (searched).
  const SHOWN = 30;
  const fold = s => (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  function adding(form) {
    const q = form.querySelector("[data-q]"), go = form.querySelector("[data-go]");
    const mine = form.querySelector('[data-pane="mine"]'), all = form.querySelector('[data-pane="all"]');
    const more = mine.querySelector("[data-more]"), none = mine.querySelector("[data-none]");
    const strip = mine.querySelector(".as-strip"), stripHead = strip?.previousElementSibling;
    let scope = "mine", open = false, timer = null, asked = "";
    const count = () => {
      const n = new Set([...form.querySelectorAll('input[name="card"]:checked')].map(i => i.value)).size;
      go.disabled = !n;
      go.textContent = n ? `Add ${n} to this art` : "Add to this art";
    };
    form.addEventListener("change", e => {  // one card can show twice (Closest pictures and Every card): keep both ticks alike
      if (e.target.name !== "card") return;
      form.querySelectorAll(`input[name="card"][value="${CSS.escape(e.target.value)}"]`).forEach(i => { i.checked = e.target.checked; });
      count();
    });
    const filter = () => {
      const words = fold(q.value).split(/\s+/).filter(Boolean);
      let shown = 0, matched = 0;
      mine.querySelectorAll(".as-every .cand").forEach(c => {
        const hit = words.every(w => fold(c.dataset.find).includes(w));
        matched += hit;
        const show = hit && (open || words.length || shown < SHOWN);
        c.hidden = !show;
        shown += show;
      });
      if (strip) { strip.hidden = words.length > 0; stripHead.hidden = words.length > 0; }
      if (more) more.hidden = open || words.length > 0 || matched <= SHOWN;
      if (none) none.hidden = matched > 0;
    };
    const search = async () => {
      const text = q.value.trim();
      if (text === asked) return;
      asked = text;
      const kept = [...all.querySelectorAll('input[name="card"]:checked')].map(i => i.closest(".cand"));
      const r = await fetch(all.dataset.search + "&" + new URLSearchParams({q: text}));
      if (asked !== text) return;  // a newer search is on its way
      all.innerHTML = r.ok ? await r.text() : '<p class="muted as-none">Couldn\'t search. Try again.</p>';
      if (kept.length) {  // ticked cards stay ticked through a new search
        const grid = all.querySelector(".as-every") || all.appendChild(Object.assign(document.createElement("div"), {className: "pgrid as-every"}));
        kept.forEach(c => { grid.querySelector(`input[value="${CSS.escape(c.querySelector("input").value)}"]`)?.closest(".cand").remove(); grid.prepend(c); });
      }
      count();
    };
    form.querySelectorAll("[data-scope]").forEach(b => b.addEventListener("click", () => {
      scope = b.dataset.scope;
      form.querySelectorAll("[data-scope]").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
      mine.hidden = scope !== "mine";
      all.hidden = scope !== "all";
      if (scope === "all") search(); else filter();
      q.focus();
    }));
    q.addEventListener("input", () => {
      if (scope === "mine") return filter();
      clearTimeout(timer);
      timer = setTimeout(search, 250);
    });
    q.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); if (scope === "all") search(); } });
    more?.addEventListener("click", () => { open = true; filter(); });
    filter();
    count();
  }
  sheet.addEventListener("click", e => { if (e.target === sheet) sheet.close(); });  // a tap on the backdrop
  const me = document.currentScript;
  const modify = document.querySelector("[data-modify]"), hint = document.querySelector("[data-modify-hint]");
  const idle = modify ? modify.innerHTML : "";  // the button as drawn: pencil, Edit arts
  const editing = on => {
    grid.classList.toggle("editing", on);
    if (!modify) return;
    modify.setAttribute("aria-pressed", String(on));
    modify.innerHTML = on ? "Done" : idle;
    modify.classList.toggle("quiet", !on);
    if (hint) hint.hidden = !on;
  };
  modify?.addEventListener("click", () => editing(!grid.classList.contains("editing")));
  grid.querySelectorAll(".ptile > a").forEach(a => a.addEventListener("click", e => {
    if (!grid.classList.contains("editing")) return;  // a tap opens the card
    e.preventDefault();
    open(a.parentNode.dataset.card);
  }));
  // Back from a fix (Not this art, Add, Undo, Re-check): the same art's sheet opens again.
  if (me.dataset.openArt) {
    const tile = grid.querySelector(`.ptile[data-art="${me.dataset.openArt}"]`);
    editing(true);
    open(me.dataset.open || tile?.dataset.card);
  }
})();
