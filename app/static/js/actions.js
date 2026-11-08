// Small actions that pages declare with data- attributes instead of inline handlers, so the content security
// policy can refuse every script that isn't one of the app's own files. Loaded in the head: the listeners sit on
// the document, so they work for anything added later too (htmx swaps, sheets).
(() => {
  const on = (type, selector, fn, capture = false) => document.addEventListener(type, e => {
    const el = e.target instanceof Element ? e.target.closest(selector) : null;
    if (el) fn(el, e);
  }, capture);

  // A form that asks first: <form data-confirm="Delete this?">.
  on("submit", "form[data-confirm]", (form, e) => {
    if (!confirm(form.dataset.confirm)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  // A form that never sends itself (htmx sends it): <form data-no-submit>.
  on("submit", "form[data-no-submit]", (form, e) => e.preventDefault(), true);

  // Opening and closing sheets: data-show="dialog id", data-dialog-close, and a tap on a dialog's backdrop
  // closes one marked data-backdrop-close.
  on("click", "[data-show]", el => {
    const d = document.getElementById(el.dataset.show);
    if (!d || d.open) return;
    d.showModal();
    d.dispatchEvent(new Event("ds-open"));
  });
  on("click", "[data-dialog-close]", el => el.closest("dialog")?.close());
  on("click", "dialog[data-backdrop-close]", (d, e) => { if (e.target === d || d.dataset.backdropClose === "any") d.close(); });
  on("click", "[data-reload]", () => location.reload());
  on("click", "[data-select-text]", el => el.select());
  // "More…" shows the rest of a row of chips.
  on("click", "[data-show-more]", el => {
    el.parentNode.querySelectorAll("[data-more]").forEach(x => { x.hidden = false; });
    el.remove();
  });
  // − and + beside a number box.
  on("click", "[data-step]", el => {
    const box = el.parentNode.querySelector("input"), step = Number(el.dataset.step);
    box.value = Math.min(99, Math.max(1, (Number(box.value) || (step > 0 ? 0 : 1)) + step));
  });
  // Enter in a box only closes the keyboard.
  on("keydown", "[data-enter-blur]", (el, e) => { if (e.key === "Enter") { e.preventDefault(); el.blur(); } });
  // A form whose last edited box is sent along (scan details: which box to search by).
  on("input", "form[data-track-edited]", (form, e) => {
    if (e.target.name && e.target.name !== "edited" && form.elements.edited) form.elements.edited.value = e.target.name;
  });
  on("change", "[data-autosubmit]", el => el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit());
  on("change", "[data-clear-field]", el => { const f = el.form.elements[el.dataset.clearField]; if (f) f.value = ""; });
  // A binder picker with "New binder…": its name box shows only then.
  on("change", "select[data-new-binder-select]", el => {
    const name = el.form.elements.new_binder;
    name.hidden = el.value !== "new"; name.required = el.value === "new";
  });
  // "Show all printings": swaps the short list for the long one.
  on("change", "[data-swap-selects]", el => {
    const [a, b] = el.closest("span").querySelectorAll("select");
    a.disabled = a.hidden = el.checked; b.disabled = b.hidden = !el.checked;
  });
  // A select that opens another address with the form's values: data-go="/cards/new?lang={value}&code={code}".
  on("change", "select[data-go]", el => {
    location.href = el.dataset.go.replace(/\{(\w+)\}/g, (_, k) => encodeURIComponent(k === "value" ? el.value : el.form.elements[k]?.value || ""));
  });

  // Pictures that fail to load: data-fail="remove | hide | retry | next | art | remove-parent | code | swap".
  document.addEventListener("error", e => {
    const img = e.target;
    if (!(img instanceof HTMLImageElement) || !img.dataset.fail) return;
    switch (img.dataset.fail) {
      case "remove": img.remove(); break;
      case "hide": img.style.visibility = "hidden"; break;
      case "retry": window.dsRetry?.(img); break;
      case "next": window.dsPic?.(img); break;
      case "art": window.artPic?.(img); break;
      case "remove-parent": img.parentNode?.remove(); break;
      case "code": { const b = document.createElement("b"); b.textContent = img.dataset.code || ""; img.parentNode.replaceChildren(b); break; }
      case "swap": img.hidden = true; if (img.nextElementSibling) img.nextElementSibling.hidden = false; break;
    }
  }, true);
})();
