const LB_CLOSE = document.currentScript.dataset.closeIcon;  // the lightbox's close icon, from the page
    // Never show a page kept in the phone's memory: coming back always loads it fresh.
    addEventListener("pageshow", e => { if (e.persisted) location.reload(); });
    // Where pages open. Pages marked data-keep-place (Find, My Stash, set, Pokémon and binder pages) open
    // where you left them when you come back to them; from the tab bar, or any other page, open at the top.
    (() => {
      history.scrollRestoration = "manual";
      const key = "place:" + location.pathname + location.search;
      const keep = document.body.hasAttribute("data-keep-place");
      const nav = performance.getEntriesByType("navigation")[0];
      const back = (nav && nav.type === "back_forward") || sessionStorage.getItem("ds-back") === "1";
      sessionStorage.removeItem("ds-back");
      const once = sessionStorage.getItem("ds-once:" + location.pathname);  // a pop-up was just saved here
      sessionStorage.removeItem("ds-once:" + location.pathname);
      let saved = null;
      try { saved = JSON.parse(sessionStorage.getItem(key) || "null"); } catch (e) {}
      const go = async () => {
        if (once !== null) { scrollTo(0, +once); return; }
        if (keep && back && saved) {
          if (saved.pages > 1 && window.dsLoadPages) await window.dsLoadPages(saved.pages);
          scrollTo(0, saved.y);
          return;
        }
        scrollTo(0, 0);
      };
      if (document.readyState === "complete") go(); else addEventListener("load", go, {once: true});
      addEventListener("pagehide", () => {
        if (keep) sessionStorage.setItem(key, JSON.stringify({y: scrollY, pages: window.dsPagesLoaded || 1}));
      });
      // The tab bar starts its page over at the top (filters are kept).
      document.addEventListener("click", e => {
        const a = e.target.closest("nav.tabs a, header.top nav a");
        if (a) { const u = new URL(a.href); sessionStorage.removeItem("place:" + u.pathname + u.search); }
        if (e.target.closest("[data-back-link]")) sessionStorage.setItem("ds-back", "1");
      });
    })();
    // Filter, sort and range chips that stay on the same page (Value over time, My Stash, sets, Find, Spending)
    // replace the page instead of adding one, so one Back leaves the page however many filters were tapped.
    document.addEventListener("click", e => {
      const a = e.target.closest("a.chip, .active-filters a, .seg.set-sort a, a[data-replace]");
      if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      if (a.target || a.hasAttribute("download") || [...a.attributes].some(x => x.name.startsWith("hx-"))) return;
      const to = new URL(a.href, location.href);
      if (to.origin !== location.origin || to.pathname !== location.pathname) return;
      e.preventDefault();
      location.replace(to.href);
    });
    // A picture full screen: scan details' photo and card picture.
    // With {photo, use}: your photo and a possible match side by side, with Use this card (Scan Details).
    window.dsLightbox = (src, compare) => {
      if (!src) return;
      let d = document.getElementById("ds-lightbox");
      if (!d) {
        d = document.createElement("dialog");
        d.id = "ds-lightbox"; d.className = "lightbox";
        d.innerHTML = `<button type="button" class="lb-x" aria-label="Close">${LB_CLOSE}</button><div class="lb-scroll"><img class="lb-photo" alt="Your photo"><img class="lb-main" alt=""></div>`
          + `<div class="lb-acts"><button type="button" class="lb-use">Use this card</button><button type="button" class="quiet lb-close">Close</button></div>`;
        d.querySelector(".lb-x").addEventListener("click", () => d.close());
        d.querySelector(".lb-close").addEventListener("click", () => d.close());
        d.querySelector(".lb-use").addEventListener("click", () => { d.close(); d.onUse?.(); });
        d.addEventListener("cancel", e => { e.stopPropagation(); });
        document.body.append(d);
      }
      const two = Boolean(compare && compare.photo);
      d.classList.toggle("compare", Boolean(compare));
      d.classList.toggle("two", two);
      d.querySelector(".lb-photo").hidden = !two;
      if (two) d.querySelector(".lb-photo").src = compare.photo;
      d.querySelector(".lb-main").src = src;
      d.querySelector(".lb-acts").hidden = !compare;
      d.onUse = compare ? compare.use : null;
      d.showModal();
    };
    // Saving a pop-up, or any form (not only the ones marked data-replace): a form
    // that comes back to the page it was sent from replaces that page instead of adding one, so the phone's back
    // button never lands on it twice or opens the pop-up again, and you keep your place on the page. A form that
    // goes to another page adds it as usual. dsSave(form, button) does the same for a script.
    window.dsSave = async (form, submitter) => {
      const body = new FormData(form);
      if (submitter?.name) body.append(submitter.name, submitter.value);
      try {
        const resp = await fetch(submitter?.getAttribute("formaction") || form.action, {method: "POST", body});
        const type = resp.headers.get("content-type") || "";
        if (type.includes("json") || !type.includes("html") && !resp.redirected) throw new Error(resp.status);
        const to = new URL(resp.url);
        if (!resp.redirected) {  // a page drawn straight from the post (an error too): show it, as a normal send would
          const html = await resp.text();
          document.open(); document.write(html); document.close();
          return;
        }
        // A card you don't have yet (a preview) that a form saves (Chase, Add) becomes its card page in place, so one
        // Back returns to Find, the set or the Pokémon page instead of to the same card.
        const fromPreview = location.pathname.startsWith("/preview/") && to.pathname.startsWith("/cards/");
        if (to.pathname !== location.pathname && !fromPreview) { location.assign(resp.url); return; }
        if (fromPreview) { location.replace(resp.url); return; }
        sessionStorage.setItem("ds-once:" + to.pathname, String(scrollY));
        location.replace(resp.url);
      } catch (err) {
        if (err instanceof TypeError && window.dsOffline) { dsIdle(form, submitter); window.dsOffline(); return; }  // no connection
        form.setAttribute("data-native", "");
        dsIdle(form, submitter);
        form.requestSubmit(submitter || undefined);  // something went wrong: send it the usual way, which shows why
      }
    };
    document.addEventListener("submit", e => {
      const form = e.target;
      if (e.defaultPrevented || form.getAttribute("method")?.toLowerCase() !== "post" ||
          form.enctype === "multipart/form-data" && !form.closest("dialog") ||  // CSV files and photo batches go the usual way
          form.hasAttribute("data-native") || form.hasAttribute("target") || form.matches("[hx-post], [hx-get]") ||
          form.closest("#ts")) return;
      e.preventDefault();
      dsSave(form, e.submitter);
    });
    // Tiles that show only a sprite ask for a picture once the page has loaded (at most 24 a page);
    // a small spinner shows while the scanner looks, and the picture swaps in when it's found (gives up after a minute).
    (() => {
      const asked = new Set();
      const show = (tile, url) => {
        const ph = tile.querySelector(".card-ph");
        if (!ph) return;
        ph.classList.remove("looking");
        if (!url) return;
        let img = ph.parentElement.querySelector(":scope > img");
        if (!img) { img = document.createElement("img"); img.alt = ""; img.style.cssText = "width:100%;display:block;border-radius:4px"; ph.before(img); }
        img.onerror = () => { img.hidden = true; ph.hidden = false; };
        img.src = url; img.hidden = false; ph.hidden = true;
      };
      const poll = (ids, n) => setTimeout(() => {
        fetch("/pictures/found?ids=" + encodeURIComponent(ids.join(","))).then(r => r.json()).then(d => {
          ids.forEach(id => document.querySelectorAll(`[data-pic-id="${CSS.escape(id)}"]`).forEach(t => {
            if (d.found[id]) show(t, d.found[id]); else if (!d.waiting.includes(id) || n >= 14) show(t, null);
          }));
          if (d.waiting.length && n < 14) poll(d.waiting, n + 1);
        }).catch(() => ids.forEach(id => document.querySelectorAll(`[data-pic-id="${CSS.escape(id)}"]`).forEach(t => show(t, null))));
      }, 4000);
      const run = () => {
        const tiles = [...document.querySelectorAll("[data-pic-id]")].filter(t => {
          const ph = t.querySelector(".card-ph");
          return ph && !ph.hidden && !asked.has(t.dataset.picId);
        }).slice(0, 24);
        if (!tiles.length) return;
        const ids = [...new Set(tiles.map(t => t.dataset.picId))];
        ids.forEach(id => asked.add(id));
        fetch("/pictures/ask", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ids})})
          .then(r => r.json()).then(d => {
            tiles.forEach(t => { if (d.waiting.includes(t.dataset.picId)) t.querySelector(".card-ph")?.classList.add("looking"); });
            if (d.waiting.length) poll(d.waiting, 0);
          }).catch(() => {});
      };
      const later = () => setTimeout(run, 1500);  // after the pictures that are known have had their try
      if (document.readyState === "complete") later(); else addEventListener("load", later, {once: true});
      document.addEventListener("htmx:afterSettle", later);
    })();
    // The first tap in a search box selects what's there, so typing replaces it.
    document.addEventListener("focusin", e => {
      const box = e.target.closest?.(".sbox input");
      if (box && box.value) setTimeout(() => { try { box.select(); } catch (err) {} }, 0);
    });
    // Rows of cards on a computer (Safari hides scrollbars): ‹ › buttons and click-drag.
    const fine = window.matchMedia("(pointer: fine)");
    const dsRows = () => {
      if (!fine.matches) return;
      document.querySelectorAll(".ts-row, .sd-others").forEach(row => {
        let wrap = row.parentElement.classList.contains("ds-scroller") ? row.parentElement : null;
        if (!wrap) {
          wrap = document.createElement("div"); wrap.className = "ds-scroller";
          row.before(wrap); wrap.append(row);
          for (const [cls, label, dir] of [["prev", "\u2039", -1], ["next", "\u203a", 1]]) {
            const b = document.createElement("button");
            b.type = "button"; b.className = "ds-arrow " + cls; b.textContent = label; b.setAttribute("aria-label", dir < 0 ? "Scroll left" : "Scroll right");
            b.addEventListener("click", () => row.scrollBy({left: dir * row.clientWidth * 0.8, behavior: "smooth"}));
            wrap.append(b);
          }
          row.addEventListener("scroll", () => arrows(row, wrap), {passive: true});
        }
        arrows(row, wrap);
      });
    };
    const arrows = (row, wrap) => {
      wrap.querySelector(".prev").hidden = row.scrollLeft <= 2;
      wrap.querySelector(".next").hidden = row.scrollLeft + row.clientWidth >= row.scrollWidth - 2;
    };
    window.dsRows = dsRows;
    document.addEventListener("DOMContentLoaded", dsRows);
    document.addEventListener("htmx:afterSettle", () => setTimeout(dsRows, 50));
    window.addEventListener("resize", dsRows);
    let drag = null;
    document.addEventListener("mousedown", e => {
      const row = e.button === 0 && fine.matches && e.target.closest?.(".ts-row, .sd-others");
      if (row && row.scrollWidth > row.clientWidth) drag = {row, x: e.clientX, left: row.scrollLeft, moved: false};
    });
    document.addEventListener("mousemove", e => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      if (Math.abs(dx) > 5) { drag.moved = true; drag.row.classList.add("ds-dragging"); }
      if (drag.moved) drag.row.scrollLeft = drag.left - dx;
    });
    document.addEventListener("mouseup", () => {
      if (drag?.moved) { const row = drag.row; setTimeout(() => row.classList.remove("ds-dragging"), 0);
        const stop = ev => { ev.preventDefault(); ev.stopPropagation(); };  // the drag isn't a tap on a card
        row.addEventListener("click", stop, {capture: true, once: true});
        setTimeout(() => row.removeEventListener("click", stop, {capture: true}), 0); }
      drag = null;
    });
    // Back from a TCGplayer or PriceCharting link in scan review, ask whether it was the card
    // (or ask for the right link when it was a search).
    let lcBox = null;
    document.addEventListener("click", e => {
      const a = e.target.closest?.("a[data-lc-site]");
      if (a) lcBox = document.getElementById("sd-lc-" + a.dataset.lcSite);
    });
    const lcShow = () => {
      if (!lcBox || document.visibilityState !== "visible") return;
      const box = lcBox; lcBox = null;
      if (!document.contains(box)) return;
      setTimeout(() => { box.hidden = false; box.scrollIntoView({block: "nearest"}); }, 250);
    };
    document.addEventListener("visibilitychange", lcShow);
    window.addEventListener("focus", lcShow);
    // + Tag / + word chips: Enter adds (the form posts), tapping away adds what's typed, Esc cancels;
    // after adding, the new chip row keeps the cursor in the box for the next one.
    let typedTag = false;
    document.addEventListener("focusout", e => {
      const box = e.target.closest?.("[data-tag-type]");
      if (box && box.value.trim()) box.form.requestSubmit();
    });
    document.addEventListener("keydown", e => {
      const box = e.target.closest?.("[data-tag-type]");
      if (!box) return;
      if (e.key === "Escape") { box.value = ""; box.blur(); }
      if (e.key === "Enter") typedTag = true;
    });
    document.addEventListener("htmx:afterSwap", e => {
      if (typedTag && (e.detail.target.id === "tags" || e.detail.target.id === "ptags")) {
        typedTag = false;
        document.querySelector(`#${e.detail.target.id} [data-tag-type]`)?.focus();
      }
    });
    // "You got it!" when a card you were chasing just went into your stash (_got.html). The page only
    // says one is waiting; asking /got uses it up, so it shows once.
    const showGot = () => {
      if (document.getElementById("got")) return;
      fetch("/got", {headers: {"HX-Request": "true"}}).then(r => r.ok ? r.text() : "").then(html => {
        if (!html.trim()) return;
        document.body.insertAdjacentHTML("beforeend", html);
        const d = document.getElementById("got");
        if (!d) return;
        d.querySelector("[data-keep]").addEventListener("click", () => d.close());
        d.addEventListener("close", () => d.remove());
        const ask = d.querySelector("form[data-ask]");
        ask?.addEventListener("submit", e => {
          if (!confirm(ask.dataset.ask)) { e.preventDefault(); e.stopImmediatePropagation(); }
        });
        d.showModal();
      }).catch(() => {});
    };
    if (document.querySelector("[data-got]")) showGot();
    document.body.addEventListener("dsGot", showGot);  // a scan added by htmx (HX-Trigger)
    // Sticky bars under the header (Find's search) sit just below it.
    (() => {
      const head = document.querySelector("header.top");
      const set = () => document.documentElement.style.setProperty("--head-h", head.offsetHeight + "px");
      set(); addEventListener("resize", set);
    })();
  
