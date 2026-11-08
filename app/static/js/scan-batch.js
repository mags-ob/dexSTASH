// This batch's values come from the page (data-page on the script tag).
const PAGE = JSON.parse(document.currentScript.dataset.page);
(() => {
  const panel = document.getElementById("ts");
  // Tapping a tile loads the card view into the panel, then opens it.
  // An open card view is one step in history, so the phone's back (or an iPhone swipe) closes it
  // instead of leaving the batch. ✕, Cancel and Save take that step off again, so Back never needs two tries.
  let stepped = false;
  panel.addEventListener("close", () => { if (stepped) { stepped = false; history.back(); } });
  addEventListener("popstate", () => {
    if (!stepped) return;
    stepped = false;
    const cancel = panel.querySelector("[data-cv-cancel]");
    if (cancel) cancel.click(); else panel.close();
  });
  document.body.addEventListener("htmx:afterSwap", e => {
    if (e.detail.target === panel && !panel.open) { panel.showModal(); if (!stepped) { history.pushState({ts: 1}, ""); stepped = true; } }
    if (e.detail.target === panel) panel.querySelector(".ts-body")?.scrollTo(0, 0);
  });
  // Save, Cancel and Remove on the last card close it; the grid fetches what changed.
  document.body.addEventListener("scan-picked", () => { if (panel.open) panel.close(); changes(); });
  document.body.addEventListener("scan-changed", () => changes());
  panel.addEventListener("cancel", e => {  // Esc or the phone's back: same as Cancel
    e.preventDefault();
    const cancel = panel.querySelector("[data-cv-cancel]");
    if (cancel) cancel.click(); else panel.close();
  });
  const opening = new URLSearchParams(location.search).get("open");
  if (opening && /^\d+$/.test(opening)) {
    const change = new URLSearchParams(location.search).get("change") === "1" ? "&change=1" : "";
    history.replaceState(null, "", location.pathname + location.hash);
    htmx.ajax("GET", `/scans/item/${opening}/details?open=1${change}`, {target: panel, swap: "innerHTML"});
  }

  // Sheets: From · Binder, Set for all.
  document.querySelectorAll("[data-open-sheet]").forEach(b => b.addEventListener("click", () => document.getElementById(b.dataset.openSheet).showModal()));
  document.querySelectorAll("dialog.sheet").forEach(d => d.addEventListener("click", e => { if (e.target === d) d.close(); }));
  const from = document.querySelector(".from-form");
  from.querySelectorAll("input[name=came]").forEach(r => r.addEventListener("change", () => {
    from.querySelectorAll("[data-came]").forEach(d => d.hidden = d.dataset.came !== from.came.value);
  }));
  from.querySelectorAll("input[name=to_binder]").forEach(r => r.addEventListener("change", () => {
    const box = from.querySelector("[data-new-binder]");
    box.hidden = from.querySelector("input[name=to_binder]:checked").value !== "new"; box.required = !box.hidden;
  }));
  const all = document.getElementById("all-sheet");
  all.addEventListener("htmx:afterRequest", e => { if (e.detail.successful) { all.close(); all.querySelector("form").reset(); } });

  // Tiles never move: each sits in the grid in the order taken, or in Added / Deleted scans below.
  const listEl = document.getElementById("scan-list");
  const homes = {grid: document.getElementById("grid"), added: document.getElementById("rows-added"), deleted: document.getElementById("rows-deleted")};
  const idOf = el => +el.id.split("-")[1];
  function place(el) {
    if (!el) return;
    const home = homes[el.dataset.section];
    if (!home) { el.remove(); return; }
    if (el.parentElement === home) return;
    const after = [...home.children].find(c => idOf(c) > idOf(el));
    home.insertBefore(el, after || null);
  }
  let state = {to_add: PAGE.toAdd, unchecked: PAGE.unchecked};
  const addAll = document.querySelector("[data-add-all]");
  function summary(c) {
    if (c) {
      state = c;
      document.querySelector("[data-sum]").textContent = `${c.photos} photo${c.photos !== 1 ? "s" : ""} · ${c.checked} checked`;
      const needs = document.querySelector("[data-needs]");
      needs.hidden = !c.needs_match;
      needs.textContent = `· ${c.needs_match} need${c.needs_match === 1 ? "s" : ""} a match`;
      addAll.textContent = `Add ${c.to_add} to My Stash`;
      addAll.disabled = !c.to_add;
    }
    for (const key of ["added", "deleted"]) {
      const n = homes[key].children.length, fold = document.getElementById(`fold-${key}`);
      fold.hidden = !n; fold.querySelector("[data-n]").textContent = n;
    }
    document.getElementById("scan-empty").hidden = homes.grid.children.length > 0;
  }

  async function changes() {
    if (document.hidden) return;
    try {
      const resp = await fetch(`/scans/${PAGE.batch}/changes?since=${listEl.dataset.rev}`, {headers: {"HX-Request": "true"}});
      if (resp.headers.get("HX-Redirect")) { location = resp.headers.get("HX-Redirect"); return; }
      if (!resp.ok) return;
      const data = await resp.json();
      for (const row of data.rows) {
        const old = document.getElementById(`scan-${row.id}`);
        // A row changes a few times in its first seconds (waiting, reading, matched). Its photo moves into the new
        // row as it is, so a picture still loading isn't thrown away and started again (it showed only its top).
        const kept = old ? [...old.querySelectorAll('img[src^="/scans/photo/"]')] : [];
        if (old) old.outerHTML = row.html;
        else homes.grid.insertAdjacentHTML("beforeend", row.html);
        const el = document.getElementById(`scan-${row.id}`);
        for (const img of el.querySelectorAll('img[src^="/scans/photo/"]')) {
          const same = kept.find(k => k.getAttribute("src") === img.getAttribute("src") && !k.isConnected);
          if (same) { same.className = img.className; img.replaceWith(same); kept.splice(kept.indexOf(same), 1); }
        }
        htmx.process(el);
        place(el);
      }
      listEl.dataset.rev = Math.max(+listEl.dataset.rev, data.rev);
      summary(data);
    } catch (e) { /* offline for a moment: try again next time */ }
  }
  setInterval(changes, 3000);
  document.addEventListener("visibilitychange", changes);
  document.body.addEventListener("htmx:afterSettle", e => {
    const id = String(e.detail.target?.id);
    if (id.startsWith("scan-")) { place(document.getElementById(id)); summary(); changes(); }
  });

  // Add N to My Stash: asks first when some cards haven't been checked.
  const unchecked = document.getElementById("unchecked-sheet");
  addAll.addEventListener("click", e => {
    if (!state.unchecked.length) return;
    e.preventDefault();
    const n = state.unchecked.length;
    unchecked.querySelector("[data-unchecked-text]").textContent =
      `${n} of ${state.to_add} card${state.to_add !== 1 ? "s" : ""} ${n === 1 ? "hasn't" : "haven't"} been checked. They'll be added as they show now. Are they all good to add?`;
    unchecked.querySelector("[data-unchecked-pics]").replaceChildren(...state.unchecked.map(id => {
      const pic = document.querySelector(`#scan-${id} .bt-pic`)?.cloneNode(true) || document.createElement("span");
      pic.querySelectorAll(".bt-name, .bt-price, .bt-ok").forEach(e => e.remove());
      pic.className = "bt-pic";
      return pic;
    }));
    unchecked.querySelector("[data-add-anyway]").textContent = `Yes, add all ${state.to_add}`;
    unchecked.showModal();
  });
  unchecked.querySelector("[data-check-first]").addEventListener("click", () => {
    unchecked.close();
    htmx.ajax("GET", `/scans/item/${state.unchecked[0]}/details?open=1`, {target: panel, swap: "innerHTML"});
  });

  // --- sending photos ---
  const status = document.getElementById("upload-status");
  let queue = Promise.resolve(), pending = 0;

  // Shrink on the phone first: a 1600px JPEG is plenty to read and quick to send.
  // crop, when given, is the part of the picture to keep: {x, y, w, h} in its own pixels.
  async function shrink(source, crop) {
    try {
      const bitmap = source instanceof Blob ? await createImageBitmap(source, {imageOrientation: "from-image"}) : source;
      const full = {x: 0, y: 0, w: bitmap.videoWidth || bitmap.width, h: bitmap.videoHeight || bitmap.height};
      const part = crop || full;
      const scale = Math.min(1, 1600 / Math.max(part.w, part.h));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(part.w * scale);
      canvas.height = Math.round(part.h * scale);
      canvas.getContext("2d").drawImage(bitmap, part.x, part.y, part.w, part.h, 0, 0, canvas.width, canvas.height);
      return await new Promise(ok => canvas.toBlob(b => ok(b || source), "image/jpeg", 0.85));
    } catch (e) {
      return source;  // the server shrinks it instead
    }
  }
  function show() {
    status.textContent = pending ? `Sending ${pending} photo${pending > 1 ? "s" : ""}…` : "Sent. They're read one at a time.";
  }
  // Photos go into this phone's storage the moment Add is tapped, then up one at a time behind a
  // see-through card ("Adding photos 3 of 8"). Leaving asks first; what's left goes up next time this batch opens.
  const BATCH = PAGE.batch;
  let total = 0, sent = 0, stuck = [], over = null;
  function progress() {
    if (!over) over = dsOverlay("", "", {seeThrough: true});
    if (pending) {
      over.spinning(true);
      over.progress(sent, total, `Adding photos ${Math.min(sent + 1, total)} of ${total}`);
      over.note("Please stay on this page until they're all in.");
      return;
    }
    if (stuck.length) {
      over.spinning(false);
      over.progress(sent, total, `${stuck.length} photo${stuck.length > 1 ? "s" : ""} didn't send`);
      over.note("They're saved on this phone. Try again, or leave them for later: they send the next time Scan opens.");
      over.action("Try again", () => { const again = stuck; stuck = []; total = sent = 0; again.forEach(r => send(r.blob, r)); });
      over.second("Later", () => {  // Never stuck behind this card
        const n = stuck.length; stuck = []; total = sent = 0; over.hide(); over = null;
        status.textContent = `${n} photo${n > 1 ? "s are" : " is"} saved on this phone and will send next time.`;
      });
      return;
    }
    over.spinning(false);
    over.progress(total, total, `All ${total} added`);
    over.note("They're read one at a time.");
    const o = over; over = null; total = sent = 0;
    setTimeout(() => { if (!pending && !over) o.hide(); }, 1500);
  }
  function send(blob, rec) {  // rec: a photo kept on the phone from before
    pending++; total++; show(); progress();
    rec = rec || {id: dsPhotos.newId(), blob};
    const kept = rec.batch ? Promise.resolve() : dsPhotos.put(BATCH, rec.id, blob).catch(() => {});  // private windows can't keep it
    queue = queue.then(async () => {
      await kept;
      try {
        const resp = await dsPhotos.upload(BATCH, rec);
        if (resp.headers.get("HX-Redirect")) { leaving = true; location = resp.headers.get("HX-Redirect"); return; }
        if (resp.ok || resp.status === 400) {  // in, or not a photo at all: either way it's done with
          dsPhotos.remove(rec.id).catch(() => {});
          if (!resp.ok) status.innerHTML = await resp.text(); else changes();
        } else stuck.push(rec);
      } catch (e) {
        stuck.push(rec);
      } finally {
        pending--; sent++; show(); progress();
      }
    });
  }
  // Leaving while photos are sending: the browser's own warning on close or reload, and our question on a tap.
  let leaving = false;
  const leave = document.getElementById("leave-dialog");
  addEventListener("beforeunload", e => { if ((pending || stuck.length) && !leaving) { e.preventDefault(); e.returnValue = ""; } });
  document.addEventListener("click", e => {
    const a = e.target.closest("a[href]");
    if (!a || !(pending || stuck.length) || leaving || a.target || a.getAttribute("href").startsWith("#")) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const n = pending + stuck.length;
    leave.querySelector("[data-leave-n]").textContent = `${n} photo${n > 1 ? "s are" : " is"} still sending`;
    leave.dataset.to = a.href;
    leave.showModal();
  }, true);
  leave.querySelector("[data-stay]").addEventListener("click", () => leave.close());
  leave.querySelector("[data-go]").addEventListener("click", () => { leaving = true; location = leave.dataset.to; });
  // Photos kept from last time go up now.
  if (window.indexedDB && !sessionStorage.getItem("dsPhotosLater"))
    dsPhotos.list(BATCH).then(recs => recs.forEach(r => send(r.blob, r))).catch(() => {});

  // --- the camera ---
  const cam = document.getElementById("cam"), video = cam.querySelector("video");
  const shotsEl = cam.querySelector("[data-shots]"), addBtn = cam.querySelector("[data-add]");
  const torchBtn = cam.querySelector("[data-torch]"), offMsg = cam.querySelector("[data-cam-off]");
  const offBox = cam.querySelector("[data-cam-off-box]"), retryBtn = cam.querySelector("[data-cam-retry]");
  const big = cam.querySelector("[data-big]"), ask = cam.querySelector("[data-ask]");
  let stream = null, shots = [], torch = false, bigIndex = -1;

  function drawShots() {
    shotsEl.replaceChildren(...shots.map((s, i) => {
      const box = document.createElement("div"); box.className = "cam-shot";
      const img = document.createElement("img"); img.src = s.url; img.alt = `Photo ${i + 1}`;
      img.addEventListener("click", () => { bigIndex = i; big.querySelector("img").src = s.url; big.hidden = false; });
      const del = document.createElement("button"); del.type = "button"; del.className = "del"; del.innerHTML = PAGE.delIcon;
      del.setAttribute("aria-label", `Delete photo ${i + 1}`);
      del.addEventListener("click", () => drop(i));
      box.append(img, del);
      return box;
    }));
    shotsEl.scrollLeft = shotsEl.scrollWidth;
    cam.querySelector("[data-count]").textContent = shots.length;
    cam.querySelector("[data-sum-hint]").hidden = !shots.length;
    addBtn.disabled = !shots.length;
    addBtn.textContent = shots.length ? `Add ${shots.length} to batch` : "Add to batch";
  }
  function drop(i) { URL.revokeObjectURL(shots[i].url); shots.splice(i, 1); drawShots(); }
  function keep(blob) { shots.push({blob, url: URL.createObjectURL(blob)}); drawShots(); }

  // Why the live camera didn't open, in plain words, and what works instead. After the phone said no,
  // or a second failure, the main button opens the phone's own camera (always works); "Try the live camera
  // again" stays as a smaller button. The outline only shows once the picture is actually playing.
  const phoneBtn = cam.querySelector("[data-cam-phone]"), howTo = cam.querySelector("[data-cam-how]");
  let failures = 0;
  const REASONS = {
    NotAllowedError: "Your phone didn't allow the camera for this app.",
    SecurityError: "Your phone didn't allow the camera for this app.",
    NotReadableError: "The camera is busy: another app or page may be using it.",
    AbortError: "The camera is busy: another app or page may be using it.",
    TrackStartError: "The camera is busy: another app or page may be using it.",
    OverconstrainedError: "The camera couldn't start with these settings.",
    NotFoundError: "No camera was found.",
    BlackVideo: "The camera started, but the picture stayed black.",
  };
  function report(name, detail) {  // to the Activity log: the reason and the browser, never a photo
    fetch("/scans/camera-problem", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name, detail: String(detail || "").slice(0, 200)})}).catch(() => {});
  }
  function noCamera(why, retry = false, name = "") {
    offMsg.textContent = why;
    offBox.hidden = false;
    const fallback = retry && (name === "NotAllowedError" || name === "SecurityError" || failures >= 2);
    phoneBtn.hidden = !fallback;
    retryBtn.hidden = !retry;
    retryBtn.className = fallback ? "quiet" : "pop";
    retryBtn.lastChild.textContent = fallback ? " Try the live camera again" : " Turn on the camera";
    howTo.hidden = !(name === "NotAllowedError" || name === "SecurityError");
    cam.querySelector(".cam-guide").hidden = true;
  }
  phoneBtn.addEventListener("click", () => cam.querySelector("[data-camera-input]").click());
  const playing = () => new Promise(resolve => {  // a picture, within about 3 seconds
    const t0 = Date.now();
    const check = () => video.videoWidth ? resolve(true) : Date.now() - t0 > 3000 ? resolve(false) : setTimeout(check, 150);
    check();
  });
  function release() {  // hand the camera back to the phone
    stream?.getTracks().forEach(t => t.stop());
    stream = null; torch = false;
    video.srcObject = null;
  }
  const askCamera = () => navigator.mediaDevices.getUserMedia(
    {video: {facingMode: {ideal: "environment"}, width: {ideal: 1920}, height: {ideal: 1080}}, audio: false});
  // The camera sometimes didn't open after browsing for a while. Opening a batch starts the camera
  // as the page loads, which isn't a tap, and iPhones may refuse that without asking; a camera still held by
  // a page left with it on can also be "busy". So: the camera is always let go when the page is left or
  // hidden, a busy camera is tried again once, and when it still won't open, a button asks again from a tap.
  async function start() {
    cam.hidden = false;
    document.documentElement.style.overflow = "hidden";
    offBox.hidden = true;
    cam.querySelector(".cam-guide").hidden = true;  // shown once the picture plays (no blinking)
    if (!navigator.mediaDevices?.getUserMedia) {
      noCamera("The live camera can't open here, because it needs the https address. Tap the shutter to use your phone's camera, one photo at a time.");
      return;
    }
    release();
    let error = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        stream = await askCamera();
        error = null;
        break;
      } catch (e) {
        error = e;
        if (!["NotReadableError", "AbortError", "TrackStartError"].includes(e?.name)) break;
        await new Promise(r => setTimeout(r, 600));  // busy: give the last user a moment to let go
      }
    }
    if (!error && stream) {
      video.srcObject = stream;
      await video.play().catch(() => {});
      if (!(await playing())) { release(); error = {name: "BlackVideo"}; }
    }
    if (error || !stream) {
      stream = null;
      failures += 1;
      const name = error?.name || "Unknown";
      report(name, `${error?.message || ""} (try ${failures})`);
      noCamera(`${REASONS[name] || "The live camera didn't start."} You can still take photos one at a time with the shutter.`, true, name);
      return;
    }
    failures = 0;
    cam.querySelector(".cam-guide").hidden = false;
    placeGuide();
    const track = stream.getVideoTracks()[0];
    torchBtn.hidden = !(track.getCapabilities?.().torch);
  }
  function stop() {
    release();
    cam.hidden = true; big.hidden = true; ask.hidden = true;
    document.documentElement.style.overflow = "";
  }
  retryBtn.addEventListener("click", start);  // a tap: the phone asks again
  addEventListener("pagehide", release);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") release();
    else if (!cam.hidden && !stream) start();  // back in the app with the camera screen open
  });
  // The outline: an exact card shape (63×88), as big as fits inside the live picture.
  const view = cam.querySelector(".cam-view"), guide = cam.querySelector(".cam-guide");
  function placeGuide() {
    const w = view.clientWidth, h = view.clientHeight;
    if (!w || !h) return;
    const gh = Math.min(h * 0.84, w * 0.86 * 88 / 63);
    guide.style.height = `${gh}px`;
    guide.style.width = `${gh * 63 / 88}px`;
  }
  addEventListener("resize", placeGuide);
  // What's inside the outline, in the video's own pixels (the picture is shown with object-fit: cover),
  // plus a small margin so the card's edges aren't cut.
  function guideCrop() {
    const vw = video.videoWidth, vh = video.videoHeight, w = view.clientWidth, h = view.clientHeight;
    const scale = Math.max(w / vw, h / vh), offX = (w - vw * scale) / 2, offY = (h - vh * scale) / 2;
    const g = guide.getBoundingClientRect(), v = view.getBoundingClientRect();
    const margin = 0.04;
    let x = (g.left - v.left - offX) / scale, y = (g.top - v.top - offY) / scale;
    let cw = g.width / scale, ch = g.height / scale;
    x -= cw * margin; y -= ch * margin; cw *= 1 + 2 * margin; ch *= 1 + 2 * margin;
    x = Math.max(0, x); y = Math.max(0, y);
    return {x, y, w: Math.min(cw, vw - x), h: Math.min(ch, vh - y)};
  }
  async function snap() {
    if (!stream || !video.videoWidth) { cam.querySelector("[data-camera-input]").click(); return; }
    const flash = document.createElement("div"); flash.className = "cam-flash"; cam.append(flash);
    setTimeout(() => flash.remove(), 250);
    keep(await shrink(video, guideCrop()));
  }
  async function fromFiles(input) {
    for (const file of input.files) keep(await shrink(file));
    input.value = "";  // so the same button works again straight away
  }
  function close() {
    if (!shots.length) { stop(); return; }
    ask.querySelector("[data-ask-text]").textContent = `Discard ${shots.length} photo${shots.length > 1 ? "s" : ""}?`;
    ask.hidden = false;
  }

  document.getElementById("open-cam").addEventListener("click", start);
  cam.querySelector("[data-shutter]").addEventListener("click", snap);
  cam.querySelector("[data-cam-close]").addEventListener("click", close);
  cam.querySelector("[data-library]").addEventListener("click", () => cam.querySelector("[data-library-input]").click());
  cam.querySelector("[data-library-input]").addEventListener("change", e => fromFiles(e.target));
  cam.querySelector("[data-camera-input]").addEventListener("change", e => fromFiles(e.target));
  torchBtn.addEventListener("click", async () => {
    try { torch = !torch; await stream.getVideoTracks()[0].applyConstraints({advanced: [{torch}]}); torchBtn.classList.toggle("on", torch); }
    catch (e) { torchBtn.hidden = true; }
  });
  big.querySelector("[data-big-close]").addEventListener("click", () => { big.hidden = true; });
  big.querySelector("[data-big-delete]").addEventListener("click", () => { big.hidden = true; if (bigIndex >= 0) drop(bigIndex); });
  ask.querySelector("[data-ask-keep]").addEventListener("click", () => { ask.hidden = true; });
  ask.querySelector("[data-ask-discard]").addEventListener("click", () => {
    shots.forEach(s => URL.revokeObjectURL(s.url)); shots = []; drawShots(); stop();
  });
  addBtn.addEventListener("click", () => {
    for (const s of shots) { send(s.blob); URL.revokeObjectURL(s.url); }
    shots = []; drawShots(); stop();
  });
  if (PAGE.camera) { start(); history.replaceState(null, "", location.pathname); }
})();
