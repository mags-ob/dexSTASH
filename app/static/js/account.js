  (() => {
    // Picking shows its colours on this page right away (all five roles); nothing is kept until Save.
    const style = document.getElementById("theme-style");
    const ROLES = {accent: "accent", on: "on", ink: "ink", tint: "tint", soft: "accent-soft", dark: "c-dark", on_dark: "on-dark",
                   second_bg: "c-second", on_second: "on-second", light: "c-light", chip: "c-chip", pop: "c-pop", on_pop: "on-pop", page: "bg"};
    document.getElementById("theme-sheet").addEventListener("pokemon-picked", async e => {
      try {
        const r = await fetch(`/themes/palette/${e.detail.id}`);
        if (!r.ok) return;
        const p = await r.json(), vars = m => Object.entries(ROLES).map(([k, v]) => m[k] ? `--${v}:${m[k]};` : "").join("");
        if (style) style.textContent = `:root{${vars(p.light)}} :root[data-mode=dark]{${vars(p.dark)}}`;
      } catch (_) {}
    });
  })();
