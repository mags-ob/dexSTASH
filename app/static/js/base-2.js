    // Every tap reacts. A sent form's button shows a spinner (and "Saving…" for Save) and can't send twice;
    // forms and buttons marked data-heavy dim the page with a card that says what's happening.
    (() => {
      const WORDS = {Save: "Saving…", Add: "Adding…", Move: "Moving…", Delete: "Deleting…", Remove: "Removing…", Update: "Updating…",
                     Import: "Importing…", Send: "Sending…", Rename: "Renaming…", Restore: "Restoring…", Create: "Creating…", "Log in": "Signing in…"};
      const over = document.getElementById("ds-over"), text = over.querySelector("#ds-over-text"),
            note = over.querySelector("[data-over-note]"), bar = over.querySelector("[data-over-bar]"),
            act = over.querySelector("[data-over-act]"), act2 = over.querySelector("[data-over-act2]");
      window.dsOverlay = (what, more, opts = {}) => {
        text.textContent = what;
        note.textContent = more || "Please stay on this page.";
        bar.classList.add("loose"); bar.firstElementChild.style.width = "";
        over.classList.toggle("see-through", !!opts.seeThrough);
        over.hidden = false;
        act.hidden = true;
        act2.hidden = true;
        over.querySelector(".ds-spin").hidden = false;
        return {
          action(label, fn) { act.textContent = label; act.hidden = false; act.onclick = fn; },
          // A second way out ("Later"), so a card that can't finish never traps the page.
          second(label, fn) { act2.textContent = label; act2.hidden = false; act2.onclick = fn; },
          spinning(on) { over.querySelector(".ds-spin").hidden = !on; },
          progress(n, total, line) {
            bar.classList.remove("loose"); bar.firstElementChild.style.width = (total ? Math.round(100 * n / total) : 0) + "%";
            if (line) text.textContent = line;
          },
          note(t) { note.textContent = t; },
          hide() { over.hidden = true; },
        };
      };
      window.dsIdle = (form, btn) => {
        if (form) delete form.dataset.sending;
        if (btn) { btn.classList.remove("ds-working"); if (btn.dataset.label) { btn.textContent = btn.dataset.label; delete btn.dataset.label; } }
      };
      window.dsBusy = (form, btn) => {
        form.dataset.sending = "1";
        const heavy = btn?.dataset.heavy || form.dataset.heavy;
        if (heavy) dsOverlay(heavy, btn?.dataset.heavyNote || form.dataset.heavyNote);
        if (btn && btn.tagName === "BUTTON") {
          btn.classList.add("ds-working");
          if (!btn.textContent.trim() && btn.querySelector("svg")) btn.classList.add("ds-icon-only");
          const t = btn.textContent.trim();
          if (btn.children.length === 0 && WORDS[t]) { btn.dataset.label = btn.textContent; btn.textContent = WORDS[t]; }
        }
        if (!heavy) setTimeout(() => dsIdle(form, btn), 20000);  // a download, or the page stayed: let it be used again
        if (!form.hasAttribute("target")) document.querySelector(".ds-load").classList.add("go");
      };
      document.addEventListener("submit", e => {
        const form = e.target;
        if (form.dataset.sending) { e.preventDefault(); e.stopImmediatePropagation(); return; }  // a second tap
        if (e.defaultPrevented || form.hasAttribute("data-no-busy")) return;
        dsBusy(form, e.submitter);
      });
      document.addEventListener("htmx:beforeRequest", e => {
        const el = e.detail.elt;
        // An htmx button, or the one icon button of an htmx form (Find's ★).
        const btns = el.matches?.("button") ? [el] : el.matches?.("form") ? [...el.querySelectorAll("button")] : [];
        const b = btns.length === 1 ? btns[0] : null;
        if (b && (b === el || (!b.textContent.trim() && b.querySelector("svg")))) {
          b.classList.add("ds-working"); if (!b.textContent.trim() && b.querySelector("svg")) b.classList.add("ds-icon-only");
        }
      });
      document.addEventListener("htmx:afterRequest", e => {
        const el = e.detail.elt;
        [el, ...(el.querySelectorAll?.(".ds-working") || [])].forEach(x => x.classList?.remove("ds-working"));
      });
      // Page loads: the thin bar along the top.
      document.addEventListener("click", e => {
        const a = e.target.closest("a[href]");
        if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || a.target || a.hasAttribute("download")) return;
        const u = new URL(a.href, location.href);
        if (u.origin !== location.origin || (u.pathname === location.pathname && u.search === location.search && u.hash)) return;
        if (a.getAttribute("href").startsWith("#") || a.getAttribute("href").startsWith("javascript:")) return;
        document.querySelector(".ds-load").classList.add("go");
      });
      // The back button: one page back, always. With nothing of ours to go back to, Home.
      let seen = false;
      try { seen = sessionStorage.getItem("ds-seen") === "1"; sessionStorage.setItem("ds-seen", "1"); } catch (err) {}
      let fromHere = false;
      try { fromHere = !!document.referrer && new URL(document.referrer).origin === location.origin; } catch (err) {}
      document.addEventListener("click", e => {
        const b = e.target.closest("a.back-fab, a[data-go-back]");
        if (!b || e.metaKey || e.ctrlKey || e.shiftKey) return;
        e.preventDefault();
        if ((fromHere || seen) && history.length > 1) history.back();
        else location.href = "/";
      });
    })();
  
