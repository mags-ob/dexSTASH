  (() => {
    const open = document.querySelector("[data-save-open]"), form = document.querySelector("[data-save-form]");
    if (open && form) open.addEventListener("click", () => { form.hidden = !form.hidden; if (!form.hidden) form.querySelector("[name=name]").focus(); });
  })();
