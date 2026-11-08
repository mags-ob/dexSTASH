(() => {
  // The page you were opening loads again as soon as the server answers: tried every 10 seconds, or on Try again.
  const status = document.querySelector("[data-status]"), retry = document.querySelector("[data-retry]");
  const check = async () => {
    status.textContent = "Trying…";
    try {
      const r = await fetch("/healthz", {cache: "no-store"});
      if (r.ok) { location.reload(); return; }
    } catch (e) { /* still unreachable */ }
    status.textContent = "Still can't reach it. Trying again every 10 seconds.";
  };
  retry.addEventListener("click", check);
  setInterval(check, 10000);
  addEventListener("online", check);
  // Scan photos waiting on this phone (photo-queue.js keeps them until the server has them).
  try {
    const open = indexedDB.open("dexstash-photos", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("photos", {keyPath: "id"}).createIndex("batch", "batch");
    open.onsuccess = () => {
      const count = open.result.transaction("photos").objectStore("photos").count();
      count.onsuccess = () => {
        const n = count.result, out = document.querySelector("[data-photos]");
        if (!n) return;
        out.textContent = `${n} scan photo${n === 1 ? " is" : "s are"} waiting on this phone. They go up when you're back.`;
        out.hidden = false;
      };
    };
  } catch (e) { /* no IndexedDB here */ }
})();
