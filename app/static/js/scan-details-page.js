// On its own page, ✕, Cancel, Save on the last card and the header Back all go back to the batch,
// in place of this page (no extra Back step). Back throws away unsaved changes, like Cancel.
(() => {
  const batch = document.currentScript.dataset.batch;
  document.body.addEventListener("scan-picked", () => location.replace(batch));
  document.addEventListener("click", e => {
    if (!e.target.closest("a.back-fab")) return;
    const cancel = document.querySelector(".ts-page [data-cv-cancel]");
    if (!cancel) return;
    e.preventDefault(); e.stopImmediatePropagation();
    cancel.click();
  }, true);
})();
