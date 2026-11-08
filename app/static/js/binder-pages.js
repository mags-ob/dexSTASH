    // A binder's pages. Swipe (the pages snap) or Turn page; on a wide screen two pages show side by
    // side and Turn page moves two. The page is kept in the address, so Back comes back to it.
    (() => {
      const openFind = document.querySelector("[data-bp-find-open]"), findForm = document.querySelector("[data-bp-find]");
      openFind?.addEventListener("click", () => {
        findForm.hidden = !findForm.hidden;
        if (!findForm.hidden) findForm.querySelector("input").focus();
      });
      const bp = document.querySelector("[data-bp]");
      if (!bp) return;
      const box = bp.querySelector("[data-bp-pages]"), at = bp.querySelector("[data-bp-at]");
      const prev = bp.querySelector("[data-bp-prev]"), next = bp.querySelector("[data-bp-next]");
      const count = Number(bp.dataset.count), first = box.firstElementChild;
      const width = () => first.getBoundingClientRect().width + parseFloat(getComputedStyle(box).columnGap || 0);
      const perView = () => (box.clientWidth >= 2 * first.getBoundingClientRect().width ? 2 : 1);
      const current = () => Math.min(count, Math.round(box.scrollLeft / width()) + 1);
      const go = (n, smooth) => {
        n = Math.max(1, Math.min(n, count));
        if (perView() === 2 && n % 2 === 0) n -= 1;  // a spread starts on an odd page
        box.scrollTo({left: (n - 1) * width(), behavior: smooth ? "smooth" : "auto"});
      };
      const label = () => {
        const n = current(), two = perView() === 2 && n < count;
        at.textContent = two ? `Pages ${n}–${n + 1} of ${count}` : `Page ${n} of ${count}`;
        prev.disabled = n <= 1;
        next.disabled = n + perView() - 1 >= count;
        const u = new URL(location.href);
        if (u.searchParams.get("page") !== String(n)) {
          u.searchParams.set("page", n);
          history.replaceState(history.state, "", u);
        }
      };
      let settle;
      box.addEventListener("scroll", () => { clearTimeout(settle); settle = setTimeout(label, 90); }, {passive: true});
      prev.addEventListener("click", () => go(current() - perView(), true));
      next.addEventListener("click", () => go(current() + perView(), true));
      box.addEventListener("keydown", e => {
        if (e.key === "ArrowRight") { e.preventDefault(); go(current() + perView(), true); }
        if (e.key === "ArrowLeft") { e.preventDefault(); go(current() - perView(), true); }
      });
      addEventListener("resize", () => go(current(), false));
      go(Number(bp.dataset.start) || 1, false);
      label();
      bp.querySelector(".bp-pocket.hit")?.focus({preventScroll: true});
    })();
