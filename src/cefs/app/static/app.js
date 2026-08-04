/* Canon EDSDK Film Scanner -- v0.1 browser UI.
 *
 * Deliberately small: no framework, no build step. The server does the image
 * work and hands over an MJPEG stream; this file only sets view options, fires
 * the shutter, and reports state.
 *
 * If a change here seems not to take effect, hard-refresh. Static files reload
 * from disk on every request, but the browser caches them aggressively -- two
 * "bugs" in the sibling project were a stale cached app.js.
 */

const $ = (id) => document.getElementById(id);

const els = {
  chip: $("status-chip"),
  connect: $("connect"),
  preview: $("preview"),
  placeholder: $("placeholder"),
  viewer: $("viewer"),
  loupeHint: $("loupe-hint"),
  model: $("model"),
  lens: $("lens"),
  invert: $("invert"),
  loupe: $("loupe"),
  zoom: $("zoom"),
  zoomValue: $("zoom-value"),
  capture: $("capture"),
  captureStatus: $("capture-status"),
  captures: $("captures"),
  settle: $("settle"),
  shutterMode: $("shutter-mode"),
  outputDir: $("output-dir"),
  toast: $("toast"),
  peaking: $("peaking"),
  peakingSensitivity: $("peaking-sensitivity"),
  peakingValue: $("peaking-value"),
  focusControls: $("focus-controls"),
  focusUnavailable: $("focus-unavailable"),
  focusReason: $("focus-reason"),
  cameraZoomNote: $("camera-zoom-note"),
  sharpnessOn: $("sharpness-on"),
  sharpnessBlock: $("sharpness-block"),
  sharpnessBar: $("sharpness-bar"),
  sharpnessNow: $("sharpness-now"),
  sharpnessBest: $("sharpness-best"),
  sharpnessReset: $("sharpness-reset"),
  filmMode: $("film-mode"),
  inversionMode: $("inversion-mode"),
  sampleBase: $("sample-base"),
  baseNote: $("base-note"),
  filmExposure: $("film-exposure"),
  filmExposureValue: $("film-exposure-value"),
  filmContrast: $("film-contrast"),
  filmContrastValue: $("film-contrast-value"),
  filmReset: $("film-reset"),
};

function setSegmented(container, value) {
  container.querySelectorAll("button").forEach((b) =>
    b.classList.toggle("active", b.dataset.value === String(value))
  );
}

let connected = false;
let capturing = false;
let toastTimer = null;
let focusBusy = false;
let sharpnessTimer = null;

function toast(message, bad = false) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  els.toast.classList.toggle("bad", bad);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { els.toast.hidden = true; }, 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch { /* non-JSON error body; the status line will do */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function render(status) {
  connected = status.connected;

  els.chip.textContent = connected ? "Connected" : (status.error ? "Error" : "Disconnected");
  els.chip.dataset.state = connected ? "on" : (status.error ? "bad" : "off");
  els.connect.textContent = connected ? "Disconnect" : "Connect";

  els.model.textContent = status.model || "—";
  els.lens.textContent = status.lens || "—";
  els.settle.textContent = `${status.settle_delay_s} s`;
  els.outputDir.textContent = status.output_dir || "—";
  els.capture.disabled = !connected || capturing;

  // Report the shutter honestly. EDSDK exposes no shutter-mode property, so
  // claiming "electronic" here would be asserting something we never set.
  const caps = status.capabilities;
  if (caps && caps.notes && caps.notes.electronic_shutter) {
    els.shutterMode.textContent = "camera menu";
    els.shutterMode.title = caps.notes.electronic_shutter;
  } else {
    els.shutterMode.textContent = "—";
  }

  if (status.view) {
    els.invert.checked = status.view.invert;
    els.loupe.checked = status.view.loupe;
    els.zoom.value = status.view.zoom;
    els.zoomValue.textContent = `${Number(status.view.zoom).toFixed(1)}×`;
    els.peaking.checked = status.view.peaking;
    els.peakingSensitivity.value = status.view.peaking_sensitivity;
    els.peakingValue.textContent = `${Math.round(status.view.peaking_sensitivity * 100)}%`;
  }

  // Focus controls appear only when this lens can actually be driven, and an
  // absent control explains itself rather than silently vanishing.
  const canFocus = connected && caps && caps.focus_drive;
  els.focusControls.hidden = !canFocus;
  els.focusUnavailable.hidden = !connected || canFocus;
  if (connected && caps && !caps.focus_drive) {
    els.focusReason.textContent =
      caps.notes.focus_drive || "This lens cannot be driven from the computer.";
  }

  if (connected && caps && !caps.liveview_zoom && caps.notes.liveview_zoom) {
    els.cameraZoomNote.textContent = caps.notes.liveview_zoom;
    els.cameraZoomNote.hidden = false;
  } else {
    els.cameraZoomNote.hidden = true;
  }

  els.preview.hidden = !connected;
  els.placeholder.hidden = connected;
  els.loupeHint.hidden = !connected || !els.loupe.checked;

  if (status.film) {
    setSegmented(els.filmMode, status.film.mode);
    els.filmExposure.value = status.film.exposure;
    els.filmExposureValue.textContent = `${Number(status.film.exposure).toFixed(2)}×`;
    els.filmContrast.value = status.film.contrast;
    els.filmContrastValue.textContent = Number(status.film.contrast).toFixed(2);
    els.baseNote.dataset.measured = status.film.base ? "yes" : "no";
  }
  if (status.view) setSegmented(els.inversionMode, status.view.inversion);

  renderCaptures(status.captures || []);

  if (connected && !els.preview.src) {
    // Cache-buster: without it a reconnect can rebind to the closed stream.
    els.preview.src = `/api/stream?t=${Date.now()}`;
  } else if (!connected) {
    els.preview.removeAttribute("src");
  }
}

function renderCaptures(items) {
  if (!items.length) {
    els.captures.innerHTML = '<li class="empty">Nothing yet.</li>';
    return;
  }
  els.captures.innerHTML = items
    .map((c) => {
      const mb = (c.bytes / 1e6).toFixed(1);
      return `<li><span class="name">${c.name}</span>
              <span class="meta">${mb} MB &middot; ${c.seconds}s</span></li>`;
    })
    .join("");
}

async function refresh() {
  try {
    render(await api("/api/status"));
  } catch (err) {
    toast(err.message, true);
  }
}

async function setView(changes) {
  try {
    await api("/api/view", { method: "POST", body: JSON.stringify(changes) });
  } catch (err) {
    toast(err.message, true);
  }
}

// --- events -----------------------------------------------------------------

els.connect.addEventListener("click", async () => {
  els.connect.disabled = true;
  try {
    const status = await api(connected ? "/api/disconnect" : "/api/connect", { method: "POST" });
    els.preview.removeAttribute("src");
    render(status);
    if (status.connected) toast(`Connected to ${status.model}`);
  } catch (err) {
    toast(err.message, true);
    await refresh();
  } finally {
    els.connect.disabled = false;
  }
});

els.invert.addEventListener("change", () => setView({ invert: els.invert.checked }));

els.loupe.addEventListener("change", () => {
  els.loupeHint.hidden = !connected || !els.loupe.checked;
  setView({ loupe: els.loupe.checked });
});

els.zoom.addEventListener("input", () => {
  els.zoomValue.textContent = `${Number(els.zoom.value).toFixed(1)}×`;
  setView({ zoom: Number(els.zoom.value) });
});

els.preview.addEventListener("click", (event) => {
  // Map the click to normalised image coordinates, allowing for letterboxing
  // from object-fit: contain -- otherwise the loupe jumps on a wide window.
  const box = els.preview.getBoundingClientRect();
  const natural = els.preview.naturalWidth / els.preview.naturalHeight;
  const shown = box.width / box.height;
  let w = box.width, h = box.height, offX = 0, offY = 0;
  if (natural > shown) {
    h = box.width / natural;
    offY = (box.height - h) / 2;
  } else {
    w = box.height * natural;
    offX = (box.width - w) / 2;
  }
  const x = (event.clientX - box.left - offX) / w;
  const y = (event.clientY - box.top - offY) / h;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  els.zoom.dataset.cx = x;
  els.zoom.dataset.cy = y;
  setView({ center_x: x, center_y: y, loupe: true });
  els.loupe.checked = true;
  els.loupeHint.hidden = false;
});

async function doCapture() {
  if (!connected || capturing) return;
  capturing = true;
  els.capture.disabled = true;
  els.captureStatus.hidden = false;
  els.captureStatus.textContent = "Settling, then firing…";
  try {
    const entry = await api("/api/capture", { method: "POST" });
    els.captureStatus.textContent = `Saved ${entry.name}`;
    toast(`Saved ${entry.name}`);
    await refresh();
  } catch (err) {
    els.captureStatus.textContent = err.message;
    toast(err.message, true);
  } finally {
    capturing = false;
    els.capture.disabled = !connected;
  }
}

els.capture.addEventListener("click", doCapture);

// --- peaking ----------------------------------------------------------------

els.peaking.addEventListener("change", () => setView({ peaking: els.peaking.checked }));

els.peakingSensitivity.addEventListener("input", () => {
  const value = Number(els.peakingSensitivity.value);
  els.peakingValue.textContent = `${Math.round(value * 100)}%`;
  setView({ peaking_sensitivity: value });
});

// --- focus ------------------------------------------------------------------

async function driveFocus(direction, coarseness) {
  // Drop presses while one is in flight. Focus commands are paced on the
  // camera thread, so queueing them up makes the lens keep moving long after
  // you stop pressing.
  if (!connected || focusBusy) return;
  focusBusy = true;
  try {
    await api("/api/focus", {
      method: "POST",
      body: JSON.stringify({ direction, coarseness }),
    });
  } catch (err) {
    toast(err.message, true);
  } finally {
    focusBusy = false;
  }
}

els.focusControls.querySelectorAll("button").forEach((button) => {
  button.addEventListener("click", () =>
    driveFocus(button.dataset.dir, button.dataset.size)
  );
});

// --- sharpness --------------------------------------------------------------

async function pollSharpness() {
  if (!connected || !els.sharpnessOn.checked) return;
  try {
    const body = await api("/api/sharpness");
    els.sharpnessNow.textContent = body.sharpness.toFixed(2);
    els.sharpnessBest.textContent = body.best.toFixed(2);
    els.sharpnessBar.style.width = `${Math.round(body.fraction_of_best * 100)}%`;
  } catch {
    // A frame may not be ready between reconnects; the next tick retries.
  }
}

function setSharpnessPolling(on) {
  els.sharpnessBlock.hidden = !on;
  clearInterval(sharpnessTimer);
  sharpnessTimer = null;
  if (on) {
    // 4 Hz: fast enough to follow a focus ring, slow enough that the ~5 ms
    // measurement never competes with the stream.
    sharpnessTimer = setInterval(pollSharpness, 250);
    pollSharpness();
  }
}

els.sharpnessOn.addEventListener("change", () => setSharpnessPolling(els.sharpnessOn.checked));

els.sharpnessReset.addEventListener("click", async () => {
  await api("/api/sharpness/reset", { method: "POST" });
  els.sharpnessBest.textContent = "—";
  els.sharpnessBar.style.width = "0%";
});

// --- film inversion ---------------------------------------------------------

async function setFilm(changes) {
  try {
    const body = await api("/api/film", { method: "POST", body: JSON.stringify(changes) });
    setSegmented(els.filmMode, body.mode);
    return body;
  } catch (err) {
    toast(err.message, true);
  }
}

els.filmMode.querySelectorAll("button").forEach((button) => {
  button.addEventListener("click", () => setFilm({ mode: button.dataset.value }));
});

els.inversionMode.querySelectorAll("button").forEach((button) => {
  button.addEventListener("click", async () => {
    setSegmented(els.inversionMode, button.dataset.value);
    await setView({ inversion: button.dataset.value, invert: button.dataset.value !== "off" });
    els.invert.checked = button.dataset.value !== "off";
  });
});

els.filmExposure.addEventListener("input", () => {
  const v = Number(els.filmExposure.value);
  els.filmExposureValue.textContent = `${v.toFixed(2)}×`;
  setFilm({ exposure: v });
});

els.filmContrast.addEventListener("input", () => {
  const v = Number(els.filmContrast.value);
  els.filmContrastValue.textContent = v.toFixed(2);
  setFilm({ contrast: v });
});

els.filmReset.addEventListener("click", async () => {
  await setFilm({ exposure: 1.0, contrast: 1.65 });
  await api("/api/film/base", { method: "POST", body: JSON.stringify({}) });
  await refresh();
  toast("Inversion settings reset");
});

els.sampleBase.addEventListener("click", async () => {
  // Sample whatever the loupe is centred on. Its width comes from the zoom, so
  // a tighter loupe gives a tighter sample -- which is what you want when the
  // rebate is a narrow strip.
  const half = 0.5 / Math.max(Number(els.zoom.value), 1);
  const cx = Number(els.zoom.dataset.cx ?? 0.5);
  const cy = Number(els.zoom.dataset.cy ?? 0.5);
  const region = [
    Math.max(0, cx - half), Math.max(0, cy - half),
    Math.min(1, 2 * half), Math.min(1, 2 * half),
  ];
  try {
    const body = await api("/api/film/base", {
      method: "POST",
      body: JSON.stringify({ region }),
    });
    const b = body.base;
    toast(b ? `Film base R=${b[0].toFixed(3)} G=${b[1].toFixed(3)} B=${b[2].toFixed(3)}` : "Base reset");
  } catch (err) {
    toast(err.message, true);
  }
});

// --- keyboard ---------------------------------------------------------------

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, textarea")) return;
  if (event.code === "Space") { event.preventDefault(); doCapture(); return; }

  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    const coarseness = event.altKey ? "coarse" : event.shiftKey ? "medium" : "fine";
    driveFocus(event.key === "ArrowLeft" ? "near" : "far", coarseness);
    return;
  }

  const key = event.key.toLowerCase();
  if (key === "i") { els.invert.checked = !els.invert.checked; setView({ invert: els.invert.checked }); }
  if (key === "l") { els.loupe.checked = !els.loupe.checked; els.loupe.dispatchEvent(new Event("change")); }
  if (key === "p") { els.peaking.checked = !els.peaking.checked; setView({ peaking: els.peaking.checked }); }
  if (key === "s") {
    els.sharpnessOn.checked = !els.sharpnessOn.checked;
    setSharpnessPolling(els.sharpnessOn.checked);
  }
});

els.preview.addEventListener("error", () => {
  if (connected) toast("Live-view stream dropped.", true);
});

refresh();
setInterval(() => { if (!capturing) refresh(); }, 5000);
