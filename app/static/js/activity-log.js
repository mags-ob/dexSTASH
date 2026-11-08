  document.querySelector("[data-copy-log]").addEventListener("click", async e => {
    const text = document.getElementById("log-text").value, b = e.currentTarget;
    try { await navigator.clipboard.writeText(text); }
    catch (err) {  // plain http: select it so a long-press copies it
      const t = document.getElementById("log-text"); t.hidden = false; t.style.cssText = "width:100%;height:160px"; t.select(); return;
    }
    b.textContent = "Copied"; setTimeout(() => b.textContent = "Copy last hour", 2000);
  });
