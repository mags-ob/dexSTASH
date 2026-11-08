(() => {
  const sheet = document.currentScript.closest("dialog"), data = JSON.parse(sheet.querySelector("[data-ps-data]").textContent);
  const grid = sheet.querySelector("[data-ps-grid]"), q = sheet.querySelector("[data-ps-q]"), none = sheet.querySelector("[data-ps-none]");
  const pick = sheet.querySelector("[data-ps-pick]"), now = sheet.querySelector("[data-ps-now]"), go = sheet.querySelector("[data-ps-go]");
  const GENS = [0, 151, 251, 386, 493, 649, 721, 809, 905, 1025], mine = new Set(data.mine);
  const pad = n => "#" + String(n).padStart(3, "0");
  const names = Object.fromEntries(data.all);
  let gen = "all", drawn = false;
  if (data.sel) now.textContent = `${pad(data.sel)} ${names[data.sel] || ""}`;
  function draw() {  // once, the first time the sheet opens
    drawn = true;
    grid.innerHTML = data.all.map(([id, name]) => {
      const strip = (data.sw[id] || []).map(c => `<i style="background:${c}"></i>`).join("");
      return `<button type="button" class="ps-tile" role="option" data-id="${id}" aria-selected="${id == data.sel}">` +
        (data.sprites ? `<img src="/sprites/${id}.png" alt="" width="64" height="64" loading="lazy">` : "") +
        `<span class="muted">${pad(id)}</span><b></b><span class="ps-strip">${strip}</span></button>`;
    }).join("");
    grid.querySelectorAll(".ps-tile").forEach(t => t.querySelector("b").textContent = names[t.dataset.id]);
  }
  function filter() {
    const t = q.value.trim().toLowerCase().replace(/^#/, ""), n = t.replace(/^0+/, "");
    let shown = 0;
    grid.querySelectorAll(".ps-tile").forEach(tile => {
      const id = +tile.dataset.id, name = names[id].toLowerCase();
      const inGen = gen === "all" || (gen === "mine" ? mine.has(id) : id > GENS[gen - 1] && id <= GENS[gen]);
      const hit = !t || name.includes(t) || (n && String(id).startsWith(n));
      tile.hidden = !(inGen && hit);
      if (!tile.hidden) shown++;
    });
    none.hidden = shown > 0;
  }
  sheet.addEventListener("ds-open", () => { if (!drawn) { draw(); filter(); } grid.querySelector("[aria-selected=true]")?.scrollIntoView({block: "center"}); });
  q.addEventListener("input", filter);
  sheet.querySelector("[data-sbox-x]").addEventListener("click", () => setTimeout(filter));
  sheet.querySelectorAll("[data-ps-gen]").forEach(c => c.addEventListener("click", () => {
    gen = c.dataset.psGen;
    sheet.querySelectorAll("[data-ps-gen]").forEach(x => { x.classList.toggle("on", x === c); x.setAttribute("aria-pressed", x === c); });
    filter();
  }));
  grid.addEventListener("click", e => {
    const tile = e.target.closest(".ps-tile");
    if (!tile) return;
    grid.querySelectorAll("[aria-selected=true]").forEach(x => x.setAttribute("aria-selected", "false"));
    tile.setAttribute("aria-selected", "true");
    pick.value = tile.dataset.id;
    now.textContent = `${pad(tile.dataset.id)} ${names[tile.dataset.id]}`;
    go.disabled = false;
    sheet.dispatchEvent(new CustomEvent("pokemon-picked", {bubbles: true, detail: {id: tile.dataset.id, name: names[tile.dataset.id]}}));
  });
  const modeSwitch = () => {
    const daily = sheet.querySelector("input[name=mode][value=daily]")?.checked;
    sheet.querySelectorAll("[data-ps-fixed]").forEach(x => x.hidden = !!daily);
    sheet.querySelectorAll("[data-ps-daily]").forEach(x => x.hidden = !daily);
    sheet.querySelector("[data-ps-label]").textContent = daily ? "A new Pokémon every day" : "Showing on this page";
    now.hidden = !!daily;
  };
  sheet.querySelectorAll("input[name=mode]").forEach(r => r.addEventListener("change", modeSwitch));
  modeSwitch();
  // Pokémon or Artist. The artist box suggests names as you type; Follow sends what's in it.
  const aq = sheet.querySelector("[data-ps-artist-q]");
  if (aq) {
    const list = sheet.querySelector("[data-ps-artist-list]"), hint = sheet.querySelector("[data-ps-artist-none]");
    const isArtist = () => sheet.querySelector("input[name=kind][value=artist]").checked;
    const kindSwitch = () => {
      const a = isArtist();
      sheet.querySelectorAll("[data-ps-fixed]").forEach(x => x.hidden = a);
      sheet.querySelectorAll("[data-ps-artist]").forEach(x => x.hidden = !a);
      now.textContent = a ? (aq.value.trim() || "Nothing yet") : (pick.value ? `${pad(pick.value)} ${names[pick.value]}` : "Nothing yet");
      go.disabled = a ? !aq.value.trim() : !pick.value;
      if (a) aq.focus();
    };
    let timer = null;
    aq.addEventListener("input", () => {
      now.textContent = aq.value.trim() || "Nothing yet";
      go.disabled = !aq.value.trim();
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const r = await fetch("/artists/suggest?q=" + encodeURIComponent(aq.value.trim()));
        const found = r.ok ? (await r.json()).names : [];
        list.innerHTML = "";
        found.forEach(n => {
          const b = document.createElement("button");
          b.type = "button"; b.className = "chip"; b.textContent = n;
          b.addEventListener("click", () => { aq.value = n; now.textContent = n; go.disabled = false; });
          list.append(b);
        });
        hint.hidden = found.length > 0;
      }, 250);
    });
    sheet.querySelectorAll("input[name=kind]").forEach(r => r.addEventListener("change", kindSwitch));
  }
  sheet.querySelector("[data-psheet-close]").addEventListener("click", () => sheet.close());
})();

