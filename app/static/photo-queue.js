// Photos waiting to go up are kept on this phone (IndexedDB) from the moment Add is tapped until the
// server has them, so leaving the page never loses one. Each carries an id; the server ignores a repeat.
window.dsPhotos = (() => {
  let dbp = null;
  const db = () => dbp || (dbp = new Promise((ok, no) => {
    const r = indexedDB.open("dexstash-photos", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("photos", {keyPath: "id"}).createIndex("batch", "batch");
    r.onsuccess = () => ok(r.result);
    r.onerror = () => no(r.error);
  }));
  const run = async (mode, fn) => {
    const t = (await db()).transaction("photos", mode), req = fn(t.objectStore("photos"));
    return new Promise((ok, no) => { t.oncomplete = () => ok(req.result); t.onerror = () => no(t.error); });
  };
  return {
    newId: () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`),
    put: (batch, id, blob) => run("readwrite", s => s.put({id, batch, blob, at: Date.now()})),
    remove: id => run("readwrite", s => s.delete(id)),
    list: batch => run("readonly", s => (batch == null ? s.getAll() : s.index("batch").getAll(batch))),
    // Sends one photo. Returns the response; throws when the phone is offline.
    upload(batch, rec) {
      const body = new FormData();
      body.append("photos", rec.blob, "photo.jpg");
      body.append("client_id", rec.id);
      return fetch(`/scans/${batch}/photos`, {method: "POST", body, headers: {"X-Scan-Upload": "1", "HX-Request": "true"}});
    },
  };
})();
