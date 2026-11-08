    (() => {
      const d = document.getElementById("share-sheet");
      if (navigator.share) d.querySelectorAll("[data-share-native]").forEach(b => b.hidden = false);
      d.addEventListener("click", e => {
        const copy = e.target.closest("[data-copy]"), send = e.target.closest("[data-share-native]");
        if (copy) {
          const done = () => { copy.textContent = "Copied"; };
          const fallback = () => {  // older phones, or a page not on https
            const t = Object.assign(document.createElement("textarea"), {value: copy.dataset.copy});
            d.append(t); t.select(); document.execCommand("copy"); t.remove(); done();
          };
          navigator.clipboard ? navigator.clipboard.writeText(copy.dataset.copy).then(done, fallback) : fallback();
        }
        if (send) navigator.share(send.dataset.shareUrl ? {url: send.dataset.shareUrl} : {text: send.dataset.shareText});
      });
      if (d.hasAttribute("data-open")) { d.showModal(); const u = new URL(location.href); u.searchParams.delete("share"); history.replaceState(history.state, "", u); }
    })();
  
