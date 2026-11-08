  document.querySelectorAll(".sbox").forEach(box => {  // the round ✕ clears the search box
    const input = box.querySelector("input"), x = box.querySelector("[data-sbox-x]");
    const set = () => x.hidden = !input.value;
    set(); input.addEventListener("input", set);
    x.addEventListener("click", e => { e.preventDefault(); input.value = ""; set(); input.focus(); });
  });
  (() => {
    const big = document.getElementById("big"), tiles = [...document.querySelectorAll("[data-card]")];
    let at = -1;
    async function show(i) {
      if (i < 0 || i >= tiles.length) return;
      at = i;
      const resp = await fetch(tiles[i].dataset.card, {credentials: "same-origin"});
      big.innerHTML = resp.ok ? await resp.text() : "<p style='padding:20px'>Couldn't open that card.</p>";
      big.querySelector("[data-prev]")?.toggleAttribute("disabled", i === 0);
      big.querySelector("[data-next]")?.toggleAttribute("disabled", i === tiles.length - 1);
      if (!big.open) big.showModal();
    }
    tiles.forEach((t, i) => t.addEventListener("click", () => show(i)));
    big.addEventListener("click", e => {
      if (e.target === big || e.target.closest("[data-close]")) big.close();
      else if (e.target.closest("[data-prev]")) show(at - 1);
      else if (e.target.closest("[data-next]")) show(at + 1);
    });
    document.addEventListener("keydown", e => {
      if (!big.open) return;
      if (e.key === "ArrowLeft") show(at - 1);
      if (e.key === "ArrowRight") show(at + 1);
    });
    let x0 = null;
    big.addEventListener("touchstart", e => { x0 = e.touches[0].clientX; }, {passive: true});
    big.addEventListener("touchend", e => {
      if (x0 === null) return;
      const dx = e.changedTouches[0].clientX - x0; x0 = null;
      if (Math.abs(dx) > 60) show(at + (dx < 0 ? 1 : -1));
    });
    document.querySelector("#pf input[type=search]").addEventListener("search", e => e.target.form.submit());
  })();
