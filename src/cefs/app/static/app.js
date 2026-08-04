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
};

let connected = false;
let capturing = false;
let toastTimer = null;

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
  }

  els.preview.hidden = !connected;
  els.placeholder.hidden = connected;
  els.loupeHint.hidden = !connected || !els.loupe.checked;

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

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, textarea")) return;
  if (event.code === "Space") { event.preventDefault(); doCapture(); return; }
  const key = event.key.toLowerCase();
  if (key === "i") { els.invert.checked = !els.invert.checked; setView({ invert: els.invert.checked }); }
  if (key === "l") { els.loupe.checked = !els.loupe.checked; els.loupe.dispatchEvent(new Event("change")); }
});

els.preview.addEventListener("error", () => {
  if (connected) toast("Live-view stream dropped.", true);
});

refresh();
setInterval(() => { if (!capturing) refresh(); }, 5000);
