// The wizard's Downloads step adds up what's ticked, and a choice that needs another (Bulbapedia
// descriptions need the Pokémon data) is only open while that one is ticked.
(function () {
  var form = document.querySelector("[data-wz-total]");
  if (!form) return;
  var sum = form.querySelector("[data-wz-sum]");
  function mb(n) { return n >= 1000 ? (n / 1000).toFixed(1) + " GB" : n + " MB"; }
  function update() {
    var down = 0, kept = 0;
    form.querySelectorAll(".wz-dl").forEach(function (row) {
      var box = row.querySelector("input");
      var needs = box.getAttribute("data-needs");
      if (needs) {
        var other = form.querySelector('input[name="' + needs + '"]');
        box.disabled = !!other && !other.checked;
      }
      if (row.hasAttribute("data-here") || box.disabled && !row.hasAttribute("data-always")) return;
      if (row.hasAttribute("data-always") || box.checked) {
        down += +row.getAttribute("data-down");
        kept += +row.getAttribute("data-kept");
      }
    });
    sum.textContent = "about " + mb(down) + " downloaded, " + mb(kept) + " kept";
  }
  form.addEventListener("change", update);
  update();
})();
