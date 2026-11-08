function openDescribe(word) {
  const d = document.getElementById("describe"), w = d.querySelector("[data-words]");
  if (word !== undefined) { w.value = word; d.querySelector("[data-words-x]").hidden = !word; }
  if (!d.open) d.showModal();
  htmx.trigger(d.querySelector("form"), "submit");  // starts the one-time download if it isn't here yet
}
document.querySelector("[data-describe-open]").addEventListener("click", e => { e.preventDefault(); openDescribe(); });
// A Pokémon's tag opens Describe with that word; so does /search?describe=word.
document.addEventListener("click", e => {
  const tag = e.target.closest("[data-describe-word]");
  if (tag) { e.preventDefault(); openDescribe(tag.dataset.describeWord); }
  const chip = e.target.closest("[data-describe-words]");  // ✕ on a word Describe used as a filter
  if (chip) { e.preventDefault(); openDescribe(chip.dataset.describeWords); }
});
// A word added to or taken off a Pokémon: Describe's results reload with the change.
document.body.addEventListener("words-changed", () => {
  const d = document.getElementById("describe");
  if (d.open || d.querySelector("#describe-results [href], #describe-results li")) htmx.trigger(d.querySelector("form"), "submit");
});
const word = document.currentScript.dataset.word;  // /search?describe=word
if (word) addEventListener("load", () => openDescribe(word));
(() => {  // Types: up to two; a third tap lets go of the one picked first
  const box = document.querySelector("[data-two-kinds]"), order = [];
  box?.addEventListener("change", e => {
    const i = e.target;
    if (i.checked) order.push(i); else order.splice(order.indexOf(i), 1);
    while (order.length > 2) order.shift().checked = false;
  });
})();
(() => {  // ✕ clears the words and the results (colour and type stay)
  const w = document.querySelector("[data-words]"), x = document.querySelector("[data-words-x]");
  const results = document.getElementById("describe-results"), empty = results.innerHTML;
  w.addEventListener("input", () => x.hidden = !w.value);
  x.addEventListener("click", () => {
    w.value = ""; x.hidden = true; w.focus();
    const form = w.closest("form");
    if (form.querySelector("[name=colour]:checked")?.value || form.querySelector("[name=kind]:checked")?.value) htmx.trigger(form, "submit");
    else results.innerHTML = empty;
  });
})();
