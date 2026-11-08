(() => {
  const box = document.currentScript.parentNode, zero = document.currentScript.dataset.zero;
  if (box.dataset.cvWired) return;  // the panel keeps its listeners when the next card is swapped in
  box.dataset.cvWired = "1";
  const post = (url, values, swap) => htmx.ajax("POST", url, {target: box, swap, values});
  box.addEventListener("click", e => {
    const t = e.target.closest(".sd-tab");
    if (t) {
      box.querySelectorAll(".sd-tab").forEach(b => b.setAttribute("aria-selected", b === t));
      box.querySelectorAll(".sd-tabpane").forEach(p => p.hidden = p.dataset.pane !== t.dataset.tab);
      return;
    }
    const item = box.querySelector("#cv-cancel")?.action.replace(/\/cancel$/, "");
    if (e.target.closest("[data-cv-change]")) {
      // Open, the panel replaces Printing, Condition, Qty and Paid; picking a card brings them back.
      const b = e.target.closest("[data-cv-change]"), search = box.querySelector("[data-cv-search]");
      search.hidden = !search.hidden;
      const fields = box.querySelector("#cv-save");
      if (fields) fields.hidden = !search.hidden;
      b.setAttribute("aria-expanded", String(!search.hidden));
      b.querySelector(".cv-change-ic").innerHTML = search.hidden ? "&rsaquo;" : "&#8964;";
      if (!search.hidden) search.scrollIntoView({behavior: "smooth", block: "start"});
    } else if (e.target.closest("[data-compare]")) {
      // A possible match big beside your photo; Use this card picks it, as tapping the tile does.
      const b = e.target.closest("[data-compare]"), form = b.closest(".ts-item").querySelector("form");
      dsLightbox(b.dataset.compare, {photo: b.dataset.photo, use: () => form.requestSubmit()});
    } else if (e.target.closest("[data-lightbox]")) {
      dsLightbox(e.target.closest("[data-lightbox]").dataset.lightbox);
    } else if (e.target.closest("[data-cv-cancel]")) {
      e.preventDefault();
      post(`${item}/cancel`, {}, "none");
    } else if (e.target.closest("[data-cv-save]")) {
      e.preventDefault();
      const form = box.querySelector("#cv-save");
      if (!form) return;
      const values = Object.fromEntries(new FormData(form));
      values.next = e.target.closest("[data-cv-save]").value;
      post(`${item}/save`, values, "innerHTML");
    } else if (e.target.closest("[data-cv-remove]")) {
      const b = e.target.closest("[data-cv-remove]");
      if (!b.dataset.armed) {  // asks once: a second tap removes it
        e.preventDefault();
        b.dataset.armed = "1";
        b.textContent = "Tap again to remove it from the batch";
      }
    }
  });
  // The price follows the printing and condition picked.
  box.addEventListener("change", e => {
    const form = e.target.closest("#cv-save");
    if (!form || !["variant", "condition"].includes(e.target.name)) return;
    const chip = form.querySelector("input[name=variant]:checked"), price = box.querySelector("[data-cv-price]");
    const dmg = form.querySelector("input[name=condition]:checked")?.value === "DMG";
    price.innerHTML = chip?.dataset.price
      ? `<strong>${dmg ? zero : chip.dataset.price}</strong><div class="muted"></div>` : `<span class="muted">No price</span>`;
    if (chip?.dataset.price) price.querySelector("div").textContent = chip.dataset.label;
  });
})();
