    // A photo that fails to load (the NAS was busy) tries again a few times.
    function dsRetry(img) {
      const n = +(img.dataset.tries || 0);
      if (n >= 4) return;
      img.dataset.tries = n + 1;
      setTimeout(() => { const u = new URL(img.src, location.href); u.searchParams.set("try", n + 1); img.src = u; }, 1500 * (n + 1));
    }
    // A card picture that fails to load moves on to the next one in its list, then to the set tile.
    function dsPic(img) {
      const rest = JSON.parse(img.dataset.pics || "[]"), label = img.nextElementSibling?.classList.contains("pic-label") ? img.nextElementSibling : null;
      if (rest.length) {
        const [url, text] = rest.shift();
        img.dataset.pics = JSON.stringify(rest);
        img.src = url;
        if (label) { label.textContent = text; label.hidden = !text; }
        return;
      }
      img.hidden = true;
      if (label) label.hidden = true;
      const tile = (label || img).nextElementSibling;
      if (tile) tile.hidden = false;
    }
  
