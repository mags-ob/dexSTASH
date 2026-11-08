// A picture being looked up for this card swaps in when it's found.
(() => {
  const id = document.currentScript.dataset.id;
  let tries = 0;
  const poll = () => fetch(`/pictures/lookup/${encodeURIComponent(id)}`).then(r => r.json()).then(d => {
    if (d.pics && d.pics.length) {
      const ph = document.querySelector(".cv-pic .card-ph");
      if (!ph) return;
      if (ph.hidden) {  // a picture is up or still loading: swap only if it misses
        const shown = document.querySelector(".cv-pic > img");
        if (!(shown && shown.complete && shown.naturalWidth) && ++tries < 20) setTimeout(poll, 3000);
        return;
      }
      const img = Object.assign(document.createElement("img"), {src: d.pics[0][0], alt: "", style: "width:100%;border-radius:8px;display:block"});
      ph?.replaceWith(img);
    } else if (d.waiting && ++tries < 20) setTimeout(poll, 3000);
  }).catch(() => {});
  setTimeout(poll, 3000);
})();

