  // Photos left on this phone by a batch you left mid-send go up now.
  // If some won't send, "Later" puts them aside for this visit (a note here can send or delete them),
  // so the page is never stuck behind the card.
  (async () => {
    if (!window.indexedDB) return;
    const later = document.getElementById("photos-later");
    async function waiting() { try { return await dsPhotos.list(); } catch (e) { return []; } }
    async function note() {
      const recs = await waiting();
      later.hidden = !recs.length;
      later.querySelector("[data-n]").textContent = `${recs.length} photo${recs.length > 1 ? "s" : ""} saved on this phone haven't sent yet.`;
    }
    async function run() {
      const recs = await waiting();
      if (!recs.length) return;
      sessionStorage.removeItem("dsPhotosLater");
      const over = dsOverlay(`Adding photos 1 of ${recs.length}`, "Photos saved on this phone are finishing. Please stay on this page.");
      let n = 0, stuck = 0;
      for (const r of recs) {
        over.progress(n, recs.length, `Adding photos ${n + 1} of ${recs.length}`);
        try {
          const resp = await dsPhotos.upload(r.batch, r);
          if (resp.ok || resp.status === 400 || resp.status === 404) await dsPhotos.remove(r.id);  // in, not a photo, or the batch is gone
          else stuck++;
        } catch (e) { stuck++; }
        n++;
      }
      if (!stuck) return location.reload();
      over.spinning(false);
      over.progress(n, recs.length, `${stuck} photo${stuck > 1 ? "s" : ""} didn't send`);
      over.note("They stay on this phone. Try again now, or later from this page.");
      over.action("Try again", () => { over.hide(); run(); });
      over.second("Later", () => { sessionStorage.setItem("dsPhotosLater", "1"); over.hide(); note(); });
    }
    later.querySelector("[data-send]").addEventListener("click", () => { later.hidden = true; run(); });
    later.querySelector("[data-drop]").addEventListener("click", async () => {
      if (!confirm("Delete the photos saved on this phone that haven't sent? You can take them again.")) return;
      for (const r of await waiting()) await dsPhotos.remove(r.id).catch(() => {});
      sessionStorage.removeItem("dsPhotosLater");
      later.hidden = true;
    });
    if (sessionStorage.getItem("dsPhotosLater")) note(); else run();
  })();
