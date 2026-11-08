  document.addEventListener("click", e => {
    const b = e.target.closest("[data-binder-open]");
    if (!b) return;
    const f = document.getElementById(b.dataset.binderOpen);
    f.hidden = !f.hidden; b.setAttribute("aria-expanded", !f.hidden);
  });
  document.addEventListener("change", e => {
    if (!e.target.matches("[data-binder-pick]")) return;
    const name = e.target.form.querySelector("[name=new_binder]");
    name.hidden = e.target.value !== "new"; name.required = !name.hidden;
  });
