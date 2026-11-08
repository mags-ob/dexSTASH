// Coming back to Find: load as many pages as were showing, then the page scrolls to where you were.
const perPage = Number(document.currentScript.dataset.page);
window.dsLoadPages = async n => {
  for (let i = 1; i < n; i++) {
    const more = document.querySelector("[data-more]");
    if (!more) return;
    const spot = document.createElement("div");
    more.replaceWith(spot);
    await htmx.ajax("GET", more.getAttribute("hx-get"), {target: spot, swap: "outerHTML"});
  }
};
Object.defineProperty(window, "dsPagesLoaded", {
  get: () => Math.ceil(document.querySelectorAll("#find-grid > :not([data-more])").length / perPage) || 1});
