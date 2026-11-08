// Find's "Checking price…" tiles (round 27): their sets are being read in the background. Every few seconds,
// ask /search/prices for the ones still waiting and put the prices in; stop after about a minute or when none wait.
(function () {
  var tries = 0, timer = null;
  function waiting() { return Array.prototype.slice.call(document.querySelectorAll("[data-price-pending]")); }
  function ask() {
    timer = null;
    var tiles = waiting();
    if (!tiles.length || tries++ >= 15) return;
    var q = tiles.slice(0, 200).map(function (t) { return "k=" + encodeURIComponent(t.dataset.pricePending); }).join("&");
    fetch("/search/prices?" + q, { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (found) {
        tiles.forEach(function (t) {
          var key = t.dataset.pricePending;
          if (!(key in found)) return;
          var p = t.querySelector(".p");
          if (p) {
            if (found[key]) { p.textContent = found[key]; }
            else { p.innerHTML = ""; var m = document.createElement("span"); m.className = "muted"; m.textContent = "No price"; p.appendChild(m); }
          }
          delete t.dataset.pricePending;
        });
      }).catch(function () {}).then(later);
  }
  function later() { if (!timer && waiting().length) timer = setTimeout(ask, 4000); }
  document.addEventListener("htmx:afterSwap", function () { tries = Math.min(tries, 5); later(); });
  later();
})();
