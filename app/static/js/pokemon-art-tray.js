(() => {
  const grid = document.getElementById("arts");
  let tray = null, openTile = null;
  function rowEnd(tile) {  // the last tile on the same row, so the tray opens below the row
    if (tile.parentNode.classList.contains("era-strip")) return tile.parentNode;  // Under the era's row
    let last = tile;
    for (let t = tile.nextElementSibling; t && t.classList.contains("ptile") && t.offsetTop === tile.offsetTop; t = t.nextElementSibling) last = t;
    return last;
  }
  async function show(tile, card, edit) {
    if (tray) tray.remove();
    if (openTile) openTile.classList.remove("open");
    if (openTile === tile && !card) { tray = openTile = null; return; }
    openTile = tile; tile.classList.add("open");
    tray = document.createElement("div"); tray.className = "tray"; tray.id = "art"; tray.textContent = "Opening…";
    rowEnd(tile).after(tray);
    const q = new URLSearchParams({card: card || tile.dataset.card, lang: grid.dataset.lang});
    if (edit) q.set("edit", "1");
    const r = await fetch(`/pokemon/${grid.dataset.dex}/art?` + q);
    tray.innerHTML = r.ok ? await r.text() : "Couldn't open this art. Refresh and try again.";
    tray.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () => {
      if (b.dataset.on === "1") show(tile, b.dataset.edit, true);
      else { tray.remove(); tile.classList.remove("open"); tray = openTile = null; }  // the art's Done closes it
    }));
    tray.querySelectorAll("[data-more]").forEach(b => b.addEventListener("click", () => { b.parentNode.querySelectorAll("[hidden]").forEach(x => x.hidden = false); b.remove(); }));
    if (edit) tray.scrollIntoView({block: "start"});
  }
  const me = document.currentScript;
  const modify = document.querySelector("[data-modify]"), hint = document.querySelector("[data-modify-hint]");
  const idle = modify ? modify.innerHTML : "";  // the button as drawn: pencil, Modify set
  const editing = on => {
    grid.classList.toggle("editing", on);
    if (!modify) return;
    modify.setAttribute("aria-pressed", String(on));
    modify.innerHTML = on ? "Done" : idle;
    modify.classList.toggle("quiet", !on);
    hint.hidden = !on;
    if (!on && tray) { tray.remove(); openTile?.classList.remove("open"); tray = openTile = null; }
  };
  modify?.addEventListener("click", () => editing(!grid.classList.contains("editing")));
  grid.querySelectorAll(".ptile > a").forEach(a => a.addEventListener("click", e => {
    if (!grid.classList.contains("editing")) return;  // a tap opens the card
    e.preventDefault();
    show(a.parentNode, null, true);
  }));
  const open = me.dataset.openArt || "";
  if (open) {
    const tile = grid.querySelector(`.ptile[data-art="${open}"]`);
    if (tile) { editing(true); show(tile, me.dataset.open || null, true); }
  }
})();
