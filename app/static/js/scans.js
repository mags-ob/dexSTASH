  // While photos are being read, fetch the page now and then and swap in the lists.
  (() => {
    const tick = async () => {
      if (document.hidden) return;
      try {
        const html = await (await fetch(location.pathname, {headers: {"X-Refresh": "1"}})).text();
        const next = new DOMParser().parseFromString(html, "text/html").querySelector("main");
        if (next) document.querySelector("main").replaceWith(next);
        if (!next || !next.querySelector("[data-reading]")) clearInterval(timer);
      } catch (e) { /* offline for a moment */ }
    };
    const timer = setInterval(tick, 5000);
  })();
