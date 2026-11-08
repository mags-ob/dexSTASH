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
