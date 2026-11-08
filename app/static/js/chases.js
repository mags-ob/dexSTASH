  // Each line's tile spans as many columns as it has Pokémon, up to a full row.
  (() => {
    const grid = document.getElementById("mon-grid");
    const fit = () => {
      const cols = getComputedStyle(grid).gridTemplateColumns.split(" ").length;
      grid.querySelectorAll(".mtile").forEach(t => t.style.setProperty("--s", Math.min(+t.dataset.n, cols)));
    };
    fit(); addEventListener("resize", fit);
  })();
