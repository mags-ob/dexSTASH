  (() => {
    const q = document.querySelector("[data-find-q]"), x = document.querySelector("[data-find-x]");
    q.addEventListener("input", () => x.hidden = !q.value);
    x.addEventListener("click", () => { q.value = ""; x.hidden = true; q.focus(); });  // filters stay
  })();
