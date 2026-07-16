// Panopto Course Assistant - vanilla-JS frontend.
"use strict";

const State = {
  lectures: [],          // lectures from the most recent feed load
  transcribedStems: new Set(), // safe_titles that already have transcripts
  status: null,          // /api/status payload
  jobsTimer: null,
  mqFeeds: [],           // Panopto feeds discovered in the Moodle quick import
  mqRecordings: [],      // recordings loaded from a pasted Panopto podcast RSS feed
};

// ---- tiny DOM + fetch helpers ---------------------------------------------

// Every integration failure arrives in one envelope (see app/errors.py):
//   {"detail": "...", "error": {"message", "category", "detail": {}}}
// so there is a single error path here rather than one per integration.
class ApiError extends Error {
  constructor(message, category = "unknown", detail = {}) {
    super(message);
    this.name = "ApiError";
    this.category = category;
    this.detail = detail;
  }
}

// What a user should try next, per §3 failure category.
const CATEGORY_HINT = {
  network: "Check your connection and try again.",
  authentication: "Sign in again, then retry.",
  dependency: "A required component is not installed.",
  filesystem: "Check the path and available disk space.",
  invalid_source: "That source could not be read.",
};

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* non-JSON */ }
  if (res.ok) return data;
  const env = data && data.error;
  if (env) throw new ApiError(env.message, env.category, env.detail);
  throw new ApiError((data && data.detail) ? data.detail : res.statusText);
}

// One place that turns any thrown error into user-facing prose (§16). Never
// prefix "Error:" by hand at a call site.
function errorText(e) {
  const msg = (e && e.message) || String(e);
  const hint = e instanceof ApiError ? CATEGORY_HINT[e.category] : null;
  return hint ? `${msg} ${hint}` : msg;
}

// POST/PUT a JSON body; returns the parsed response.
function postJSON(path, body, method = "POST") {
  return api(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}
const $ = (id) => document.getElementById(id);
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

// ---- icons (§14) -----------------------------------------------------------
// Builds <svg class="ico"><use href="#i-name"/></svg> against the sprite in
// index.html. Decorative by default: pass a label only when the icon is the
// control's *only* content, otherwise it is announced twice.
const SVG_NS = "http://www.w3.org/2000/svg";
function icon(name, { cls = "", label = "" } = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", ("ico " + cls).trim());
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", "#i-" + name);
  svg.appendChild(use);
  if (label) svg.setAttribute("aria-label", label);
  else svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  return svg;
}
// An icon plus its own text, for status lines where colour alone must not
// carry the meaning (§15).
function iconText(name, text, cls = "") {
  return el("span", { class: "icon-text" }, [icon(name, { cls }), " " + text]);
}

// ---- modal dialogs (replaces window.prompt/alert) --------------------------
// One primitive behind every dialog (§15): it is announced as a dialog, traps
// Tab inside itself, closes on Escape or a backdrop click, and hands focus back
// to whatever opened it. Anything that appends a bare .modal-overlay is a bug.

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function openModal(buildBox, { onDismiss = () => {}, boxClass = "" } = {}) {
  const opener = document.activeElement;
  const overlay = el("div", { class: "modal-overlay" });
  const box = el("div", {
    class: ("modal-box " + boxClass).trim(),
    role: "dialog", "aria-modal": "true",
  });
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    document.removeEventListener("keydown", onKey, true);
    overlay.remove();
    if (opener && typeof opener.focus === "function") opener.focus();
  };
  const dismiss = () => { if (!closed) { close(); onDismiss(); } };

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); dismiss(); return; }
    if (e.key !== "Tab") return;
    const items = [...box.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  overlay.addEventListener("click", (e) => { if (e.target === overlay) dismiss(); });
  document.addEventListener("keydown", onKey, true);

  buildBox(box, close);
  overlay.appendChild(box);
  document.body.appendChild(overlay);

  // Label the dialog by its own heading, so screen readers announce its purpose.
  const heading = box.querySelector(".modal-label");
  if (heading) {
    if (!heading.id) heading.id = "modal-label-" + Math.random().toString(36).slice(2, 8);
    box.setAttribute("aria-labelledby", heading.id);
  }
  const target = box.querySelector(FOCUSABLE);
  if (target) target.focus();
  return close;
}

function promptModal(label, placeholder = "") {
  return new Promise((resolve) => {
    openModal((box, close) => {
      const inp = el("input", { type: "text", placeholder, class: "modal-input", autocomplete: "off" });
      const commit = () => { close(); resolve(inp.value.trim()); };
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); });
      box.append(
        el("p", { class: "modal-label", text: label }),
        inp,
        el("div", { class: "modal-actions" }, [
          el("button", { text: "Create", onclick: commit }),
          el("button", { class: "ghost", text: "Cancel", onclick: () => { close(); resolve(""); } }),
        ]),
      );
    }, { onDismiss: () => resolve("") });
  });
}

// Native OS dialogs. The backend opens a real file-explorer window (this app
// runs locally), so the user picks a destination instead of typing a path.
// Both resolve to the chosen path, or null when the user cancels.

async function pickFolder(title = "Choose a folder") {
  try {
    const d = await postJSON("/api/pick-folder", { title });
    if (d.available === false) return askPathFallback(title);
    return d.path || null;            // null = cancelled
  } catch (_) { return askPathFallback(title); }
}

async function pickSaveFile(title = "Save as", defaultName = "", ext = "") {
  try {
    const d = await postJSON("/api/pick-save", { title, default_name: defaultName, ext });
    if (d.available === false) return askPathFallback(title, defaultName);
    return d.path || null;
  } catch (_) { return askPathFallback(title, defaultName); }
}

async function pickFile(title = "Choose a file", ext = "") {
  try {
    const d = await postJSON("/api/pick-file", { title, ext });
    if (d.available === false) return askPathFallback(title);
    return d.path || null;
  } catch (_) { return askPathFallback(title); }
}

// Fallback for hosts with no desktop dialog (e.g. headless): a typed-path modal.
function askPathFallback(title = "Where should this be saved?", defaultValue = "") {
  return new Promise((resolve) => {
    openModal((box, close) => {
      const inp = el("input", { type: "text", placeholder: "C:\\Users\\…\\Course exports",
        class: "modal-input", autocomplete: "off", value: defaultValue });
      const commit = () => { close(); resolve(inp.value.trim() || null); };
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); });
      box.append(
        el("p", { class: "modal-label", text: title }),
        el("p", { class: "hint", text: "No file dialog is available on this host. Enter a path." }),
        inp,
        el("div", { class: "modal-actions" }, [
          el("button", { text: "Save here", onclick: commit }),
          el("button", { class: "ghost", text: "Cancel", onclick: () => { close(); resolve(null); } }),
        ]),
      );
      setTimeout(() => inp.select(), 0);
    }, { onDismiss: () => resolve(null) });
  });
}

// A simple confirmation dialog. Resolves true (confirmed) or false (cancelled).
function confirmModal(title, message, { confirmText = "Confirm", danger = false } = {}) {
  return new Promise((resolve) => {
    openModal((box, close) => {
      box.append(
        el("p", { class: "modal-label", text: title }),
        el("p", { class: "hint", text: message }),
        el("div", { class: "modal-actions" }, [
          el("button", { class: danger ? "danger" : "", text: confirmText,
            onclick: () => { close(); resolve(true); } }),
          el("button", { class: "ghost", text: "Cancel",
            onclick: () => { close(); resolve(false); } }),
        ]),
      );
    }, { onDismiss: () => resolve(false) });
  });
}

// ---- toast -----------------------------------------------------------------
// The element carries aria-live, so screen-reader users get the same feedback
// sighted users get from a transient toast (§15).
const TOAST_ICON = { ok: "check", warn: "alert", err: "alert", info: "info" };
let toastTimer = null;

function toast(msg, kind = "info") {
  const t = $("toast");
  clear(t);
  t.className = "toast " + kind;
  t.classList.remove("hidden");
  t.setAttribute("aria-live", kind === "err" ? "assertive" : "polite");
  t.append(icon(TOAST_ICON[kind] || "info"), el("span", { text: msg }));
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 4000);
}

// Report a failure. The one place an error reaches the user (§16/§17).
function toastError(e) { toast(errorText(e), "err"); }

// ---- empty states with a clear next step -----------------------------------

function emptyState(msg, actions = []) {
  const wrap = el("div", { class: "empty-state" });
  wrap.appendChild(el("p", { class: "empty", text: msg }));
  if (actions.length) {
    const row = el("div", { class: "empty-actions" });
    for (const a of actions) {
      row.appendChild(el("button", {
        class: (a.primary ? "primary " : "ghost ") + "small",
        text: a.label,
        onclick: a.run,
      }));
    }
    wrap.appendChild(row);
  }
  return wrap;
}

// ---- Simple / Advanced mode (progressive disclosure) -----------------------

function applyLevel(level) {
  const simple = level !== "advanced";
  document.body.dataset.level = simple ? "simple" : "advanced";
  remember("level", simple ? "simple" : "advanced");
  const label = $("level-label");
  const btn = $("level-toggle");
  if (label) label.textContent = simple ? "Advanced mode" : "Simple mode";
  if (btn) btn.setAttribute("aria-pressed", String(!simple));
}

function initLevel() {
  applyLevel(recall("level", "simple"));
  $("level-toggle")?.addEventListener("click", () => {
    const next = document.body.dataset.level === "simple" ? "advanced" : "simple";
    applyLevel(next);
    toast(
      next === "advanced"
        ? "Advanced mode on — Semester, capability matrix, cookies, and STT extras are visible."
        : "Simple mode on — Semester and advanced STT/export options are hidden.",
      "info",
    );
  });
}

// ---- panel context (wayfinding) --------------------------------------------

const TAB_LABELS = {
  home: "Home",
  "moodle-quick": "Moodle import",
  import: "Import materials",
  library: "Library",
  notes: "Notes",
  study: "Study",
  semester: "Semester plan",
  export: "Export",
  tts: "Speech",
  jobs: "Jobs",
};

function updatePanelContext(name) {
  const ctx = $("panel-context");
  if (!ctx) return;
  const section = TAB_LABELS[name] || name;
  ctx.textContent = section;
  const course = currentCourse();
  document.title = course ? `${section} — ${course}` : `${section} — Course Assistant`;
}

// ---- file drop zones -------------------------------------------------------

function wireDropZone(zoneEl, inputEl, { onDrop } = {}) {
  if (!zoneEl || !inputEl) return;
  const highlight = () => zoneEl.classList.add("drag-over");
  const unhighlight = () => zoneEl.classList.remove("drag-over");
  zoneEl.addEventListener("dragover", (e) => { e.preventDefault(); highlight(); });
  zoneEl.addEventListener("dragleave", (e) => {
    if (!zoneEl.contains(e.relatedTarget)) unhighlight();
  });
  zoneEl.addEventListener("drop", (e) => {
    e.preventDefault();
    unhighlight();
    const file = e.dataTransfer?.files?.[0];
    if (!file) return;
    try {
      const dt = new DataTransfer();
      dt.items.add(file);
      inputEl.files = dt.files;
    } catch (_) { /* older browsers: onDrop still runs */ }
    inputEl.dispatchEvent(new Event("change", { bubbles: true }));
    if (onDrop) onDrop(file);
    else toast(`Selected ${file.name}.`, "ok");
  });
}

// ---- keyboard shortcuts ----------------------------------------------------

function showShortcuts() {
  openModal((box, close) => {
    box.append(
      el("p", { class: "modal-label", text: "Keyboard shortcuts" }),
      el("ul", { class: "shortcut-list" }, [
        el("li", {}, [el("kbd", { text: "/" }), " Focus library search"]),
        el("li", {}, [el("kbd", { text: "Ctrl" }), " + ", el("kbd", { text: "K" }), " Command palette"]),
        el("li", {}, [el("kbd", { text: "?" }), " Show this list"]),
        el("li", {}, [el("kbd", { text: "Esc" }), " Close dialogs or the mobile menu"]),
      ]),
      el("div", { class: "modal-actions" }, [
        el("button", { text: "Close", onclick: close }),
      ]),
    );
  });
}

function remember(key, val) { try { localStorage.setItem(key, val); } catch (_) {} }
function recall(key, def = "") { try { return localStorage.getItem(key) ?? def; } catch (_) { return def; } }

// ---- tabs -----------------------------------------------------------------

function showTab(name) {
  document.querySelectorAll(".tab").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    // aria-current marks the section you are in, for assistive tech (§15).
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === name));
  document.querySelector(".app").classList.remove("menu-open");  // close mobile drawer
  $("menu-toggle")?.setAttribute("aria-expanded", "false");
  updatePanelContext(name);
  const heading = $(name)?.querySelector("h1, h2");
  if (heading) {
    heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
  }
  if (name === "home") loadDashboard();
  if (name === "library") loadTranscripts();
  if (name === "jobs") loadJobs();
  if (name === "notes") loadNotesWorkspace();
  if (name === "study") loadStudy();
  if (name === "semester") loadSemester();
  if (name === "export") loadExportHub();
  if (name === "tts") refreshSpeechPanel();
}
document.querySelectorAll(".tab").forEach((btn) =>
  btn.addEventListener("click", () => showTab(btn.dataset.tab))
);
// dashboard tiles + any [data-goto] element jump to a tab
document.querySelectorAll("[data-goto]").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.goto))
);

// ---- import sub-switch (documents / notion / browse) ----------------------
// Scoped to [data-import] so Speech reuse of .seg / .import-switch does not clash.

function showImport(name) {
  document.querySelectorAll(".seg[data-import]").forEach((b) => {
    const on = b.dataset.import === name;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", String(on));
  });
  document.querySelectorAll("#import .import-pane").forEach((p) =>
    p.classList.toggle("active", p.id === "import-" + name));
}
document.querySelectorAll(".seg[data-import]").forEach((btn) =>
  btn.addEventListener("click", () => showImport(btn.dataset.import))
);

// ---- Speech sub-switch (Transcribe | Read aloud) --------------------------

let _ttsInited = false;
let _sttCaps = null;

function showSpeechMode(name) {
  document.querySelectorAll(".seg[data-speech]").forEach((b) => {
    const on = b.dataset.speech === name;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", String(on));
  });
  document.querySelectorAll("#tts > .import-pane").forEach((p) =>
    p.classList.toggle("active", p.id === "speech-" + name));
  if (name === "read-aloud") {
    if (!_ttsInited) { _ttsInited = true; initTts(); }
  } else {
    refreshSpeechPanel();
  }
}
document.querySelectorAll(".seg[data-speech]").forEach((btn) =>
  btn.addEventListener("click", () => showSpeechMode(btn.dataset.speech))
);

// ---- theme + mobile menu --------------------------------------------------

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  remember("theme", theme);
  // The button offers the theme you would switch *to*, and says so out loud.
  const next = theme === "dark" ? "light" : "dark";
  const use = $("theme-icon")?.querySelector("use");
  if (use) use.setAttribute("href", theme === "dark" ? "#i-sun" : "#i-moon");
  const label = $("theme-label");
  if (label) label.textContent = next === "dark" ? "Dark theme" : "Light theme";
  $("theme-toggle")?.setAttribute("aria-label", `Switch to the ${next} theme`);
}
// Apply the saved theme immediately (before the async init chain) so it sticks
// on refresh with no flash, even if later startup code errors out.
applyTheme(recall("theme") ||
  (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
$("theme-toggle")?.addEventListener("click", () =>
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"));
$("menu-toggle").addEventListener("click", (e) => {
  const open = document.querySelector(".app").classList.toggle("menu-open");
  e.currentTarget.setAttribute("aria-expanded", String(open));
  e.currentTarget.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
});
document.querySelector(".app")?.addEventListener("click", (e) => {
  const app = document.querySelector(".app");
  if (!app.classList.contains("menu-open") || e.target.closest("#menu-toggle, .sidebar")) return;
  app.classList.remove("menu-open");
  $("menu-toggle")?.setAttribute("aria-expanded", "false");
  $("menu-toggle")?.setAttribute("aria-label", "Open navigation");
});

// ---- dashboard ------------------------------------------------------------

async function loadDashboard() {
  const env = $("dash-env");
  const stats = $("dash-stats");
  const s = State.status;
  if (s && env) {
    clear(env);
    // State is carried by an icon and by the label text, never by colour alone (§15).
    const STATE_ICON = { on: "check", off: "x", warn: "alert" };
    const pill = (label, state) => el("span", { class: "env-pill" }, [
      icon(STATE_ICON[state] || "info", { cls: "state-ico " + state }), label]);
    const engines = Object.entries(s.engines || {}).filter(([, v]) => v).map(([k]) => k);
    env.appendChild(pill(engines.length ? `Transcription: ${engines.join(", ")}` : "Transcription: not installed",
      engines.length ? "on" : "off"));
    env.appendChild(pill(s.cuda ? "GPU: CUDA" : "GPU: CPU only", s.cuda ? "on" : "warn"));
    env.appendChild(pill(s.markitdown ? "Documents: ready" : "Documents: install markitdown",
      s.markitdown ? "on" : "off"));
  }
  await renderEnvPacks();
  if (stats) {
    try {
      const data = await api("/api/transcripts");
      State.transcribedStems = new Set(data.items.map((i) => i.stem));
      const fmtCount = data.items.reduce((n, it) => n + Object.keys(it.formats).length, 0);
      clear(stats);
      const tile = (num, lbl) => el("div", { class: "stat" }, [
        el("div", { class: "num", text: String(num) }), el("div", { class: "lbl", text: lbl })]);
      stats.appendChild(tile(data.items.length, "transcripts"));
      stats.appendChild(tile(fmtCount, "output files"));
      stats.appendChild(tile(State.lectures.length || (State.mqRecordings || []).length, "lectures loaded"));
      const gs = $("getting-started");
      if (gs) {
        const show = !recall("gs-dismissed") && data.items.length === 0;
        gs.classList.toggle("hidden", !show);
      }
    } catch (_) { /* leave empty */ }
  }
}

const ENV_PACK_DEFS = [
  { id: "transcribe", label: "Transcription", hint: "faster-whisper + markitdown" },
  { id: "tts", label: "Read aloud (TTS)", hint: "Kokoro TTS" },
  { id: "browser", label: "Browser scrape", hint: "Playwright Chromium" },
  { id: "stt-quality", label: "STT quality", hint: "Granite / Qwen engines" },
];

async function renderEnvPacks() {
  const host = $("env-packs");
  if (!host) return;
  clear(host);
  let preflight = null;
  let env = null;
  try { preflight = await api("/api/setup/preflight"); } catch (_) { /* optional */ }
  try { env = await api("/api/environment"); } catch (_) { /* optional */ }
  const s = State.status || {};
  const packs = (preflight && preflight.packs) || {};
  const opt = (env && env.optional) || {};
  const optVal = (substr) => {
    for (const [k, v] of Object.entries(opt)) {
      if (k.toLowerCase().includes(substr)) return !!v;
    }
    return false;
  };
  const readyMap = {
    transcribe: !!s.any_engine || !!packs.base,
    tts: optVal("kokoro") || optVal("tts"),
    browser: optVal("playwright") || optVal("browser"),
    "stt-quality": !!packs.quality,
  };

  for (const def of ENV_PACK_DEFS) {
    const ok = !!readyMap[def.id];
    const row = el("div", { class: "env-pack" }, [
      icon(ok ? "check" : "alert", { cls: "state-ico " + (ok ? "on" : "off") }),
      el("span", { text: def.label + (ok ? " · ready" : " · missing") }),
    ]);
    if (!ok) {
      row.appendChild(el("button", {
        class: "small", type: "button", text: "Install",
        title: def.hint,
        onclick: () => installExtrasPack(def.id),
      }));
    }
    host.appendChild(row);
  }
}

async function installExtrasPack(pack) {
  try {
    const job = await postJSON("/api/setup/install-extras", { pack });
    toast(`Installing ${pack}… track progress in Jobs.`, "ok");
    showTab("jobs");
    startJobsPolling();
    return job;
  } catch (e) {
    toastError(e);
  }
}

$("copy-diagnostics-home")?.addEventListener("click", async () => {
  try {
    const bundle = await postJSON("/api/diagnostics/bundle", {});
    await navigator.clipboard.writeText(bundle.text || JSON.stringify(bundle, null, 2));
    toast("Diagnostics copied", "ok");
  } catch (err) {
    toast("Could not copy diagnostics: " + errorText(err), "err");
  }
});

// ---- environment status ---------------------------------------------------

async function loadStatus() {
  const bar = $("status-bar");
  const barText = $("status-bar-text");
  try {
    const s = await api("/api/status");
    State.status = s;
    const engines = Object.entries(s.engines || {}).filter(([, v]) => v).map(([k]) => k);
    const short = s.any_engine
      ? (engines[0] || "STT ready") + (s.cuda ? " · GPU" : " · CPU")
      : "STT missing";
    if (barText) barText.textContent = short;
    else if (bar) bar.textContent = short;
    if (bar) {
      bar.className = "status-bar " + (s.any_engine ? "ok" : "warn");
      bar.title = [
        engines.length ? `engines: ${engines.join(", ")}` : "no transcription engine",
        s.cuda ? "GPU: CUDA" : "GPU: CPU",
        s.markitdown ? "docs: ready" : "docs: markitdown missing",
        `output → ${s.output_dir}`,
      ].join(" · ");
      if (!bar.querySelector(".status-dot")) {
        bar.insertBefore(el("span", { class: "status-dot", "aria-hidden": "true" }), bar.firstChild);
      }
    }

    // engine dropdown (only present in the legacy manual-transcribe UI, if any)
    const sel = $("opt-engine");
    if (sel) {
      clear(sel);
      if (engines.length) engines.forEach((e) => sel.appendChild(el("option", { text: e })));
      else sel.appendChild(el("option", { text: "(none installed)" }));
      if (s.default_engine) sel.value = s.default_engine;
    }

    // document-type checkboxes (for the Documents → Markdown tab)
    buildDocExtChecks(s.doc_exts || [".pdf"]);

    // engine-aware warning
    const warn = $("engine-warning");
    if (warn) {
      if (!s.any_engine) {
        warn.textContent = "No transcription engine installed - you can still import documents, browse, "
          + "search and export. To transcribe Moodle lectures: pip install -r requirements-transcribe.txt";
        warn.classList.remove("hidden");
      } else {
        warn.classList.add("hidden");
      }
    }

    // LLM availability - flashcards still require a model; cheat sheet / practice exam work extractively
    const llmReady = s.llm_ready === true;
    const fcMissing = $("fc-llm-missing");
    const fcCatMissing = $("fc-cat-llm-missing");
    const csMissing = $("cheatsheet-llm-missing");
    const fcBtn = $("fc-generate");
    const fcCatBtn = $("fc-categorize");
    const csBtn = $("cheatsheet-go");
    if (fcMissing) fcMissing.classList.toggle("hidden", llmReady);
    if (fcCatMissing) fcCatMissing.classList.toggle("hidden", llmReady);
    if (csMissing) csMissing.classList.toggle("hidden", llmReady);
    if (fcBtn) fcBtn.disabled = !llmReady;
    if (fcCatBtn) fcCatBtn.disabled = !llmReady;
    if (csBtn) csBtn.disabled = false;
  } catch (e) {
    bar.textContent = "could not reach backend: " + e.message;
    bar.className = "status-bar warn";
  }
}

function buildDocExtChecks(exts) {
  const box = $("doc-exts");
  if (!box) return;
  clear(box);
  exts.forEach((ext) => {
    box.appendChild(el("label", { class: "chk" }, [
      el("input", { type: "checkbox", value: ext, checked: true }),
      " " + ext.replace(".", ""),
    ]));
  });
}
function selectedDocExts() {
  return [...document.querySelectorAll("#doc-exts input:checked")].map((i) => i.value);
}

// ---- settings persistence -------------------------------------------------

function sttProfile() {
  const active = document.querySelector("#stt-profiles .seg.active");
  return (active && active.dataset.profile) || "auto";
}

function gatherSettings() {
  // Output formats / organisation are no longer chosen here - transcription
  // writes a sensible canonical set and the Export step owns the rest. The
  // legacy opt-* controls may be absent (the guided Moodle flow replaced them),
  // so read each defensively and fall back to sensible defaults. Speech-panel
  // STT controls take precedence when present.
  const val = (id, def = "") => { const n = $(id); return n ? n.value : def; };
  const checked = (id, def = false) => { const n = $(id); return n ? n.checked : def; };
  const lang = (val("stt-language") || val("opt-language")).trim() || "en";
  return {
    engine: val("opt-engine"),
    model: val("opt-model"),
    language: lang === "auto" ? "auto" : lang,
    device: val("opt-device") || "auto",
    audio_only: checked("opt-audio"),
    skip_existing: checked("opt-skip", true),
    cookies: val("opt-cookies").trim(),
    course: currentCourse(),
    profile: sttProfile(),
    diarization: val("stt-diarization", "off") || "off",
    caption_first: checked("stt-caption-first", true),
    use_adaptive: true,
  };
}

// ---- global course context (single source of truth) -----------------------
// The course name in the top bar tags every import and export automatically.

function currentCourse() {
  const inp = $("course-input");
  return inp ? inp.value.trim() : recall("course");
}

function setCourse(name) {
  if (!name) return;
  const top = $("course-input");
  if (top) top.value = name;
  const main = $("course-name-main");
  if (main) main.value = name;
  remember("course", name);
  const active = document.querySelector(".panel.active");
  if (active) updatePanelContext(active.id);
}

// ---- multi-course switcher (§1) -------------------------------------------
// The persisted courses live in the DB now; the switcher picks the *active*
// one and keeps the legacy free-text tag in sync so imports/exports still tag
// correctly. With no courses yet, the switcher hides and the free-text field
// works exactly as before.
const Courses = { list: [], active: null };

async function loadCourses() {
  const sel = $("course-switcher");
  const topbarLeft = document.querySelector(".topbar-left");
  if (!sel) return;
  let data;
  try { data = await api("/api/courses"); } catch (_) { return; }
  Courses.list = data.courses || [];
  Courses.active = data.active_course;
  clear(sel);
  if (!Courses.list.length) {
    sel.classList.add("hidden");
    topbarLeft?.classList.remove("switcher-visible");
    if (!recall("course-first-run-asked")) {
      remember("course-first-run-asked", "1");
      // First-run: offer to create an Active course (name + optional paper code).
      setTimeout(() => createCourse({ firstRun: true }), 400);
    }
    return;
  }
  sel.classList.remove("hidden");
  topbarLeft?.classList.add("switcher-visible");
  for (const c of Courses.list) {
    const label = (c.code ? c.code + " - " : "") + c.name + (c.archived ? " (archived)" : "");
    sel.appendChild(el("option", { value: String(c.id), text: label }));
  }
  if (Courses.active != null) {
    sel.value = String(Courses.active);
    const active = Courses.list.find((c) => c.id === Courses.active);
    if (active) setCourse(active.code || active.name);
  }
}

async function activateCourse(id) {
  try {
    const c = await postJSON("/api/courses/" + id + "/activate", {});
    Courses.active = c.id;
    const sel = $("course-switcher");
    if (sel) sel.value = String(c.id);      // keep the dropdown in sync on programmatic switches
    setCourse(c.code || c.name);
    toast("Switched to “" + c.name + "”.", "ok");
    if (document.querySelector("#library.active")) loadTranscripts();
    if (document.querySelector("#home.active")) loadDashboard();
  } catch (e) { toastError(e); }
}

function createCourseModal({ firstRun = false } = {}) {
  return new Promise((resolve) => {
    openModal((box, close) => {
      const nameInp = el("input", {
        type: "text", class: "modal-input", autocomplete: "off",
        placeholder: "e.g. Networks — Spring 2026",
      });
      const codeInp = el("input", {
        type: "text", class: "modal-input", autocomplete: "off",
        placeholder: "Optional paper code (e.g. COMPX234-26B)",
      });
      const commit = () => {
        const name = nameInp.value.trim();
        if (!name) { toast("Enter a course name.", "warn"); return; }
        close();
        resolve({ name, code: codeInp.value.trim() });
      };
      nameInp.addEventListener("keydown", (e) => { if (e.key === "Enter") codeInp.focus(); });
      codeInp.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); });
      box.append(
        el("p", { class: "modal-label",
          text: firstRun ? "Create your first course" : "New course" }),
        el("p", { class: "hint",
          text: "The Active course tags imports, Speech jobs, and exports. Paper codes feed Semester sync." }),
        nameInp,
        codeInp,
        el("div", { class: "modal-actions" }, [
          el("button", { text: "Create", onclick: commit }),
          el("button", { class: "ghost", text: firstRun ? "Skip for now" : "Cancel",
            onclick: () => { close(); resolve(null); } }),
        ]),
      );
      setTimeout(() => nameInp.focus(), 0);
    }, { onDismiss: () => resolve(null) });
  });
}

async function createCourse(opts = {}) {
  const values = await createCourseModal(opts);
  if (!values) return;
  try {
    const c = await postJSON("/api/courses", { name: values.name, code: values.code || "" });
    if (values.code) {
      applyDetectedPaperCodes([values.code]);
      try {
        const prefs = await api("/api/settings");
        const existing = Array.isArray(prefs?.["semester.paper_codes"])
          ? prefs["semester.paper_codes"] : [];
        const merged = [...new Set([...existing, values.code])];
        await postJSON("/api/settings", { values: { "semester.paper_codes": merged } }, "PUT");
      } catch (_) { /* best-effort */ }
    }
    await loadCourses();
    await activateCourse(c.id);
  } catch (e) { toastError(e); }
}

if ($("course-switcher")) {
  $("course-switcher").addEventListener("change", (e) => {
    if (e.target.value) activateCourse(Number(e.target.value));
  });
}
if ($("course-new")) $("course-new").addEventListener("click", createCourse);

// keep the top-bar field and the Course panel field in sync + persisted
$("course-input")?.addEventListener("input", () => {
  remember("course", $("course-input").value.trim());
  const active = document.querySelector(".panel.active");
  if (active) updatePanelContext(active.id);
});
// Legacy "set course" button - no longer in the markup; guard so a missing
// element can't throw and halt the rest of this script (theme, SSO, handlers).
$("course-name-set")?.addEventListener("click", () => {
  const name = $("course-name-main").value.trim();
  if (!name) { toast("Type a course name first.", "warn"); return; }
  setCourse(name);
  toast("Course set to “" + name + "”.", "ok");
});
$("course-name-main")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("course-name-set")?.click();
});

// ---- lectures -------------------------------------------------------------

async function refreshTranscribedSet() {
  try {
    const data = await api("/api/transcripts");
    State.transcribedStems = new Set(data.items.map((i) => i.stem));
  } catch (_) { State.transcribedStems = new Set(); }
}

function lectureDone(lec) { return State.transcribedStems.has(lec.safe_title); }

function renderLectures() {
  const list = $("lectures-list");
  if (!list) return;   // legacy feed-based transcribe UI is not present in this build
  clear(list);
  const has = State.lectures.length > 0;
  ["lectures-heading", "lectures-toolbar"].forEach((id) =>
    $(id)?.classList.toggle("hidden", !has));
  $("settings")?.classList.toggle("hidden", !has);
  if (!has) { list.appendChild(el("p", { class: "empty", text: "No lectures loaded yet. Paste a Panopto podcast link to load recordings." })); return; }

  const noEngine = !State.status || !State.status.any_engine;
  let doneCount = 0;

  State.lectures.forEach((lec, i) => {
    const done = lectureDone(lec);
    if (done) doneCount++;
    const meta = [
      lec.week != null ? `Week ${lec.week}` : null,
      lec.date || null,
      lec.duration_human !== "?" ? lec.duration_human : null,
      lec.size_human !== "?" ? lec.size_human : null,
    ].filter(Boolean).join("  ·  ");

    const actions = [];
    if (done) {
      actions.push(el("button", { class: "ghost small", text: "view", onclick: () => openLectureTranscript(lec) }));
    }
    actions.push(el("button", {
      class: "small", text: done ? "re-transcribe" : "transcribe",
      disabled: noEngine, title: noEngine ? "No engine installed" : "",
      onclick: () => transcribeLectures([i], done),
    }));

    list.appendChild(el("div", { class: "card lecture" }, [
      el("input", { type: "checkbox", class: "lec-check", "data-i": i }),
      el("div", { class: "lecture-main" }, [
        el("div", {}, [
          el("strong", { text: lec.title }),
          done ? el("span", { class: "badge done", text: "Transcribed" })
               : el("span", { class: "badge pending", text: "pending" }),
        ]),
        el("div", { class: "hint", text: meta || "-" }),
      ]),
      el("div", { class: "lec-actions" }, actions),
    ]));
  });

  $("lectures-summary").textContent =
    `${State.lectures.length} lecture(s) · ${doneCount} transcribed · ${State.lectures.length - doneCount} pending`;
}

function checkedIndexes() {
  return [...document.querySelectorAll(".lec-check:checked")].map((c) => parseInt(c.dataset.i, 10));
}

async function transcribeLectures(indexes, allowReTranscribe = false) {
  if (!State.status || !State.status.any_engine) {
    toast("No transcription engine installed.", "warn"); return;
  }
  if (!indexes.length) { toast("No lectures selected.", "warn"); return; }
  const settings = gatherSettings();
  if (allowReTranscribe) settings.force = true;
  remember("settings", JSON.stringify(settings));

  let queued = 0;
  for (const i of indexes) {
    try {
      await api("/api/transcribe", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...settings, lecture: State.lectures[i] }),
      });
      queued++;
    } catch (e) { toast(`Could not queue "${State.lectures[i].title}": ${errorText(e)}`, "err"); }
  }
  if (queued) {
    toast(`Queued ${queued} lecture(s). Transcription takes a few minutes each; track it in Jobs.`, "ok");
    showTab("jobs");
    startJobsPolling();
  }
}

async function openLectureTranscript(lec) {
  showTab("library");
  try {
    const data = await api("/api/transcripts");
    const g = data.items.find((it) => it.stem === lec.safe_title);
    if (g) {
      const rel = g.formats.txt || g.formats.md || Object.values(g.formats)[0];
      if (rel) viewTranscript(rel);
    }
  } catch (_) {}
}

$("feed-load")?.addEventListener("click", async () => {
  const source = $("feed-source").value.trim();
  if (!source) { toast("Enter a feed URL or path.", "warn"); return; }
  remember("feed", source);
  const btn = $("feed-load");
  btn.disabled = true; btn.textContent = "Loading…";
  try {
    const data = await api("/api/feed", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
    await refreshTranscribedSet();
    State.lectures = data.lectures;
    renderLectures();
    toast(`Loaded ${data.count} lecture(s).`, "ok");
  } catch (e) { toastError(e); }
  finally { btn.disabled = false; btn.textContent = "Load feed"; }
});

$("feed-file")?.addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const data = await api("/api/feed/upload", { method: "POST", body: fd });
    if (data.channel) setCourse(data.channel);
    await refreshTranscribedSet();
    State.lectures = data.lectures;
    renderLectures();
    toast(`Parsed ${data.count} lecture(s) from ${file.name}.`, "ok");
  } catch (e) { toastError(e); }
});

$("sel-all")?.addEventListener("click", () => document.querySelectorAll(".lec-check").forEach((c) => (c.checked = true)));
$("sel-none")?.addEventListener("click", () => document.querySelectorAll(".lec-check").forEach((c) => (c.checked = false)));
$("transcribe-selected")?.addEventListener("click", () => transcribeLectures(checkedIndexes()));
$("transcribe-pending")?.addEventListener("click", () =>
  transcribeLectures(State.lectures.map((l, i) => i).filter((i) => !lectureDone(State.lectures[i]))));

// ---- library (comprehensive: transcripts + documents + notion + exports) --

const FORMAT_ORDER = ["txt", "md", "notebooklm", "summary", "srt", "vtt", "json"];

function librarySection(list, title, count) {
  if (!count) return;
  list.appendChild(el("div", { class: "lib-section", text: `${title} · ${count}` }));
}

function fileRow(f) {
  const row = el("div", { class: "list-item" }, [
    el("span", { class: "li-label", text: f.path },),
    el("span", { class: "lib-meta" }, [
      f.size_human ? el("span", { class: "muted", text: f.size_human }) : null,
      f.viewable !== false ? el("button", { class: "tag", text: "view", onclick: () => viewTranscript(f.path) }) : null,
    ]),
  ]);
  return row;
}

async function loadTranscripts() {  // loads the whole Library
  const list = $("transcripts-list");
  list.textContent = "Loading…";
  try {
    const data = await api("/api/library");
    const cats = data.categories;
    State.transcribedStems = new Set(cats.transcripts.map((i) => i.stem));
    clear(list);
    if (!data.counts.total) {
      list.appendChild(emptyState("Nothing imported yet. Start with a Moodle course, or add documents and Notion exports.", [
        { label: "Import Moodle course", primary: true, run: () => showTab("moodle-quick") },
        { label: "Import documents", run: () => showTab("import") },
      ]));
      return;
    }

    // Transcripts (grouped per lecture, with format chips)
    librarySection(list, "Transcripts", cats.transcripts.length);
    cats.transcripts.forEach((it) => {
      const label = (it.folder ? it.folder + "/" : "") + it.stem;
      const fmts = Object.keys(it.formats).sort(
        (a, b) => FORMAT_ORDER.indexOf(a) - FORMAT_ORDER.indexOf(b));
      list.appendChild(el("div", { class: "list-item" }, [
        el("div", { class: "li-label", text: label }),
        el("div", { class: "formats" }, fmts.map((f) =>
          el("button", { class: "tag", text: f, title: "view " + f, onclick: () => viewTranscript(it.formats[f]) })
        )),
      ]));
    });

    // Documents - separate image assets from primary docs
    const _IMG_EXTS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff"]);
    const isImg = (f) => _IMG_EXTS.has((f.name || f.path || "").toLowerCase().replace(/.*\./, "."));
    const docFiles = cats.documents.filter((f) => !isImg(f));
    const imgFiles = cats.documents.filter(isImg);
    librarySection(list, "Documents", docFiles.length);
    docFiles.forEach((f) => list.appendChild(fileRow(f)));
    if (imgFiles.length) {
      const imgRow = el("div", { class: "list-item muted small" }, [
        el("span", { class: "li-label", text: `${imgFiles.length} embedded image(s) (from document conversion)` }),
      ]);
      list.appendChild(imgRow);
    }
    librarySection(list, "Notion pages", cats.notion.length);
    cats.notion.forEach((f) => list.appendChild(fileRow(f)));
    librarySection(list, "Other sources", cats.others.length);
    cats.others.forEach((f) => list.appendChild(fileRow(f)));
    librarySection(list, "Generated exports", cats.exports.length);
    cats.exports.forEach((f) => list.appendChild(fileRow(f)));
  } catch (e) { list.textContent = errorText(e); }
}

async function viewTranscript(relPath) {
  const view = $("transcript-view");
  view.textContent = "Loading…";
  try {
    const data = await api("/api/transcript?path=" + encodeURIComponent(relPath));
    view.textContent = data.content;
    view.scrollTop = 0;
    openItemMeta(relPath);
  } catch (e) { view.textContent = errorText(e); }
}

// ---- notes, tags & citations for the selected library item ----------------

function openItemMeta(relPath) {
  State.currentPath = relPath;
  const box = $("item-meta");
  box.classList.remove("hidden");
  $("item-meta-name").textContent = relPath.split("/").pop();
  $("item-citations").textContent = "";
  $("note-body").value = "";
  $("note-bookmark").checked = false;
  loadItemTags(relPath);
  loadItemNotes(relPath);
  loadCollection(relPath);
}

// Everything derived from this lecture, in one call (§17). Documents have no
// collection, so a 404 simply hides the strip rather than shouting.
async function loadCollection(relPath) {
  const host = $("item-collection");
  clear(host);
  let data;
  try {
    data = await api("/api/collections?lecture=" + encodeURIComponent(relPath));
  } catch (_) { return; }

  const cells = [
    ["glossary", "glossary terms", () => { showTab("study"); focusCard("glossary-card"); }],
    ["keywords", "keywords", null],
    ["related", "related lectures", null],
    ["notes", "notes", null],
    ["tags", "tags", null],
    ["formats", "file formats", null],
  ];
  const grid = el("div", { class: "collection-grid" });
  for (const [key, label, go] of cells) {
    const n = (data.counts && data.counts[key]) || 0;
    const cell = el(go && n ? "button" : "div", {
      class: "collection-cell" + (n ? "" : " empty-cell"),
      ...(go && n ? { onclick: go, type: "button" } : {}),
    }, [el("span", { class: "n", text: String(n) }), el("span", { class: "k", text: label })]);
    grid.appendChild(cell);
  }
  host.append(
    el("div", { class: "collection-head" }, [
      icon("link"),
      el("span", { class: "muted small", text: "Derived from this lecture" }),
    ]),
    grid,
  );
}

// Move the user to a card and mark it, rather than silently scrolling.
function focusCard(id) {
  const card = $(id);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.setAttribute("tabindex", "-1");
  card.focus({ preventScroll: true });
}
document.querySelectorAll("[data-focus-card]").forEach((b) =>
  b.addEventListener("click", () => focusCard(b.dataset.focusCard)));

async function loadItemTags(relPath) {
  const wrap = $("item-tags");
  clear(wrap);
  try {
    const data = await api("/api/tags?path=" + encodeURIComponent(relPath));
    (data.tags || []).forEach((name) => {
      wrap.appendChild(el("span", { class: "tag removable" }, [
        name,
        el("button", { class: "tag-x", title: "remove tag", text: "×",
          onclick: () => removeItemTag(relPath, name) }),
      ]));
    });
    if (!(data.tags || []).length) wrap.appendChild(el("span", { class: "muted small", text: "none yet" }));
  } catch (_) { /* leave empty */ }
}

async function addItemTag(relPath, name) {
  name = (name || "").trim();
  if (!name) return;
  try {
    await postJSON("/api/tags", { path: relPath, name, course_id: null });
    loadItemTags(relPath);
  } catch (e) { toastError(e); }
}

async function removeItemTag(relPath, name) {
  try {
    await api("/api/tags?path=" + encodeURIComponent(relPath) + "&name=" + encodeURIComponent(name),
      { method: "DELETE" });
    loadItemTags(relPath);
  } catch (e) { toastError(e); }
}

async function loadItemNotes(relPath) {
  const wrap = $("item-notes");
  clear(wrap);
  try {
    const data = await api("/api/notes?path=" + encodeURIComponent(relPath));
    if (!data.notes.length) { wrap.appendChild(el("p", { class: "muted small", text: "No notes yet. Add one above." })); return; }
    data.notes.forEach((n) => {
      wrap.appendChild(el("div", { class: "note-item" }, [
        n.bookmark ? icon("bookmark", { cls: "note-flag", label: "Bookmarked" }) : null,
        n.timestamp_s != null ? el("span", { class: "note-ts", text: fmtTs(n.timestamp_s) }) : null,
        el("span", { class: "note-text", text: n.body }),
        el("button", { class: "tag-x", title: "delete note", text: "×",
          onclick: () => deleteNote(n.id, relPath) }),
      ]));
    });
  } catch (e) { wrap.textContent = errorText(e); }
}

function fmtTs(s) {
  s = Math.max(0, Math.round(s));
  const m = Math.floor(s / 60), sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

async function addNote(relPath) {
  const body = $("note-body").value.trim();
  if (!body) return;
  try {
    await postJSON("/api/notes", { path: relPath, body, bookmark: $("note-bookmark").checked, course_id: null });
    $("note-body").value = ""; $("note-bookmark").checked = false;
    loadItemNotes(relPath);
  } catch (e) { toastError(e); }
}

async function deleteNote(id, relPath) {
  try { await api("/api/notes/" + id, { method: "DELETE" }); loadItemNotes(relPath); }
  catch (e) { toastError(e); }
}

async function showCitations(relPath) {
  const box = $("item-citations");
  box.textContent = "Loading…";
  try {
    const data = await api("/api/citations?path=" + encodeURIComponent(relPath));
    clear(box);
    Object.entries(data.citations).forEach(([style, text]) => {
      box.appendChild(el("div", { class: "cite-row" }, [
        el("span", { class: "cite-style", text: style.toUpperCase() }),
        el("code", { class: "cite-text", text }),
        el("button", { class: "ghost small", text: "Copy", onclick: () => copyText(text) }),
      ]));
    });
  } catch (e) { box.textContent = e.message; }
}

function copyText(text) {
  try { navigator.clipboard.writeText(text); toast("Copied to the clipboard.", "ok"); }
  catch (_) { toast("Could not copy to the clipboard.", "err"); }
}

$("item-tag-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && State.currentPath) { addItemTag(State.currentPath, e.target.value); e.target.value = ""; }
});
$("note-add-go").addEventListener("click", () => { if (State.currentPath) addNote(State.currentPath); });
$("note-body").addEventListener("keydown", (e) => { if (e.key === "Enter" && State.currentPath) addNote(State.currentPath); });
$("item-cite-go").addEventListener("click", () => { if (State.currentPath) showCitations(State.currentPath); });
$("item-open-notes")?.addEventListener("click", () => {
  if (!State.currentPath) return;
  State.notesAttachPath = State.currentPath;
  showTab("notes");
});

// ---- Study tab ------------------------------------------------------------

async function loadStudy() {
  loadStreak();
  loadNextUp();
  loadWorkload();
  loadTracker();
}

async function loadTracker() {
  const box = $("tracker-body");
  if (!box) return;
  try {
    const data = await api("/api/study/tracker");
    clear(box);
    const upcoming = data.upcoming || [];
    if (!upcoming.length && !(data.assessments || []).length) {
      box.appendChild(el("p", { class: "muted small", text: "No assignments yet. Add one above." }));
      return;
    }
    const list = el("div", { class: "tracker-list" });
    (data.assessments || []).forEach((a) => {
      list.appendChild(el("div", { class: "tracker-item" }, [
        el("span", { class: "tracker-kind", text: a.kind || "assignment" }),
        el("strong", { text: a.name }),
        a.week != null ? el("span", { class: "muted small", text: `W${a.week}` }) : null,
        el("span", { class: "muted small", text: a.due_date || "no due date" }),
        el("select", {
          class: "small",
          onchange: async (e) => {
            try {
              await postJSON(`/api/assessments/${a.id}`, { status: e.target.value }, "PATCH");
              toast("Assessment updated.", "ok");
              loadTracker();
            } catch (err) { toastError(err); }
          },
        }, ["not_started", "in_progress", "submitted", "graded"].map((s) =>
          el("option", { value: s, text: s.replace("_", " "), selected: a.status === s || null }))),
      ]));
    });
    box.appendChild(list);
  } catch (e) { box.textContent = errorText(e); }
}

async function addTrackerItem() {
  const name = ($("tracker-name")?.value || "").trim();
  if (!name) { toast("Enter an assignment name.", "warn"); return; }
  const due = $("tracker-due")?.value || "";
  const kind = $("tracker-kind")?.value || "assignment";
  const weekRaw = $("tracker-week")?.value;
  const week = weekRaw ? parseInt(weekRaw, 10) : null;
  try {
    await postJSON("/api/assessments", { name, due_date: due, kind, week });
    $("tracker-name").value = "";
    toast("Assessment added.", "ok");
    loadTracker();
  } catch (e) { toastError(e); }
}

async function loadStreak() {
  const box = $("streak-body");
  try {
    const s = await api("/api/streak");
    clear(box);
    box.appendChild(el("div", { class: "streak-num" }, [
      s.current_streak > 0
        ? el("span", { class: "streak-flame" }, [icon("flame", { label: "On a streak" })])
        : el("span", { class: "streak-flame muted", text: "·" }),
      el("strong", { text: String(s.current_streak) }),
      el("span", { class: "muted", text: ` day${s.current_streak === 1 ? "" : "s"}` }),
    ]));
    box.appendChild(el("div", { class: "hint", text:
      `Longest: ${s.longest_streak} · Active days: ${s.active_days}` }));
    const pct = Math.min(100, s.goal_pct);
    box.appendChild(el("div", { class: "progress" }, [el("div", { class: "bar", style: `width:${pct}%` })]));
    box.appendChild(el("div", { class: "hint", text:
      `Today: ${s.today_minutes} / ${s.goal_minutes} min` + (s.goal_met ? " - goal met" : "") }));
  } catch (e) { box.textContent = errorText(e); }
}

async function loadNextUp() {
  const box = $("nextup-body");
  try {
    const data = await api("/api/next-up");
    clear(box);
    if (!data.actions.length) { box.appendChild(el("p", { class: "muted small", text: "All caught up. Nothing pressing." })); return; }
    data.actions.forEach((a) => {
      const row = el("div", { class: "nextup-item clickable", onclick: () => a.goto && showTab(a.goto) }, [
        el("span", { class: "nextup-kind " + a.kind, text: a.kind }),
        el("div", {}, [
          el("div", { class: "nextup-title", text: a.title }),
          el("div", { class: "muted small", text: a.detail || "" }),
        ]),
      ]);
      box.appendChild(row);
    });
  } catch (e) { box.textContent = errorText(e); }
}

async function loadWorkload() {
  const box = $("workload-body");
  try {
    const w = await api("/api/workload");
    clear(box);
    if (!w.lectures) {
      box.appendChild(emptyState("No transcripts yet. Import and transcribe lecture recordings to see a workload estimate.", [
        { label: "Import Moodle course", primary: true, run: () => showTab("moodle-quick") },
        { label: "Open library", run: () => showTab("library") },
      ]));
      return;
    }
    box.appendChild(el("div", { class: "hint", text:
      `${w.lectures} lectures · ${w.total_words.toLocaleString()} words · ` +
      `read ~${hm(w.total_read_min)}, review ~${hm(w.total_review_min)}` }));
    const table = el("table", { class: "wl-table" }, [
      el("tr", {}, [el("th", { text: "Week" }), el("th", { text: "Lectures" }),
        el("th", { text: "Read" }), el("th", { text: "Review" })]),
      ...w.by_week.map((b) => el("tr", {}, [
        el("td", { text: String(b.week) }), el("td", { text: String(b.lectures) }),
        el("td", { text: hm(b.read_min) }), el("td", { text: hm(b.review_min) }),
      ])),
    ]);
    box.appendChild(table);
  } catch (e) { box.textContent = errorText(e); }
}

function hm(min) {
  min = Math.round(min);
  if (min < 60) return min + "m";
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

// -- practice quiz ----------------------------------------------------------

async function startPractice() {
  const box = $("practice-body");
  const count = parseInt($("practice-count").value, 10) || 10;
  box.textContent = "Building quiz…";
  try {
    const quiz = await api(`/api/practice-quiz?count=${count}`);
    if (!quiz.count) {
      clear(box);
      box.appendChild(emptyState(quiz.reason || "Not enough review cards yet. Generate flashcards under Export to seed the deck.", [
        { label: "Generate flashcards", primary: true, run: () => { showTab("export"); $("export-more-tools")?.setAttribute("open", ""); } },
        { label: "Open Study", run: () => showTab("study") },
      ]));
      return;
    }
    State.practiceQuiz = quiz;
    State.practiceAnswers = new Array(quiz.questions.length).fill(null);
    renderPractice();
  } catch (e) { box.textContent = errorText(e); }
}

function renderPractice() {
  const box = $("practice-body");
  const quiz = State.practiceQuiz;
  clear(box);
  quiz.questions.forEach((q, qi) => {
    const opts = el("div", { class: "quiz-opts" }, q.options.map((opt, oi) =>
      el("label", { class: "quiz-opt" }, [
        el("input", { type: "radio", name: "q" + qi,
          onchange: () => { State.practiceAnswers[qi] = oi; } }),
        " " + opt,
      ])));
    box.appendChild(el("div", { class: "quiz-q" }, [
      el("div", { class: "quiz-qtext", text: `${qi + 1}. ${q.question}` }), opts,
    ]));
  });
  box.appendChild(el("button", { class: "primary", text: "Submit answers", onclick: submitPractice }));
  box.appendChild(el("div", { id: "practice-score", class: "results" }));
}

async function submitPractice() {
  try {
    const res = await postJSON("/api/practice-quiz/grade", {
      questions: State.practiceQuiz.questions, answers: State.practiceAnswers, record: true });
    const out = $("practice-score");
    clear(out);
    out.appendChild(el("p", { class: res.pct >= 50 ? "banner ok" : "banner warn",
      text: `Score: ${res.score} / ${res.total} (${res.pct}%)` }));
    res.detail.forEach((d, i) => {
      out.appendChild(el("div", { class: "quiz-result " + (d.correct ? "ok" : "bad") }, [
        icon(d.correct ? "check" : "x", { label: d.correct ? "Correct" : "Incorrect" }),
        el("span", { text: d.question }),
        d.correct ? null : el("span", { class: "muted small", text: " — answer: " + d.answer }),
      ]));
    });
  } catch (e) { toastError(e); }
}

// -- glossary & study guide -------------------------------------------------

async function showGlossary() {
  const box = $("glossary-body");
  box.textContent = "Building glossary…";
  try {
    const g = await api("/api/glossary");
    clear(box);
    if (!g.count) {
      box.appendChild(emptyState("No glossary terms yet. Transcribe lectures or import course documents first.", [
        { label: "Import Moodle course", primary: true, run: () => showTab("moodle-quick") },
        { label: "Open library", run: () => showTab("library") },
      ]));
      return;
    }
    box.appendChild(el("div", { class: "hint", text: `${g.count} terms from ${g.lectures_scanned} lectures` }));
    g.terms.slice(0, 60).forEach((t) => {
      box.appendChild(el("div", { class: "gloss-term" }, [
        el("strong", { text: t.term }), el("span", { text: " — " + t.definition }),
      ]));
    });
  } catch (e) { box.textContent = errorText(e); }
}

async function exportGlossary() {
  const dest = await pickFolder("Choose a folder for the glossary");
  if (dest === null) return;
  try {
    const r = await postJSON("/api/export/glossary", { course: currentCourse(), output_dir: dest });
    toast(`Glossary exported: ${r.count} terms.`, "ok");
  } catch (e) { toastError(e); }
}

async function exportGuide() {
  const dest = await pickFolder("Choose a folder for the study guide");
  if (dest === null) return;
  const box = $("guide-body");
  box.textContent = "Building study guide…";
  try {
    const r = await postJSON("/api/export/study-guide", { course: currentCourse(), output_dir: dest });
    box.textContent = "";
    toast(`Study guide built: ${r.lectures} lectures, ${r.glossary_terms} terms.`, "ok");
  } catch (e) { box.textContent = ""; toastError(e); }
}

$("study-refresh").addEventListener("click", loadStudy);
$("practice-start").addEventListener("click", startPractice);
$("glossary-view").addEventListener("click", showGlossary);
$("glossary-export").addEventListener("click", exportGlossary);
$("guide-export").addEventListener("click", exportGuide);
$("tracker-add")?.addEventListener("click", addTrackerItem);
$("recall-start")?.addEventListener("click", startDailyRecall);
$("slideshow-start")?.addEventListener("click", startSlideshow);
$("focus-start")?.addEventListener("click", startFocusMode);
$("focus-stop")?.addEventListener("click", completeFocusMode);
$("essay-grade")?.addEventListener("click", gradeEssay);
$("ai-chat-ask")?.addEventListener("click", askLibrary);
$("ai-chat-q")?.addEventListener("keydown", (e) => { if (e.key === "Enter") askLibrary(); });
$("mode-practice")?.addEventListener("click", () => { focusCard("practice-card"); startPractice(); });
$("mode-recall")?.addEventListener("click", () => { focusCard("recall-card"); startDailyRecall(); });
$("mode-slideshow")?.addEventListener("click", () => { focusCard("slideshow-card"); startSlideshow(); });
$("mode-focus")?.addEventListener("click", () => { focusCard("focus-card"); startFocusMode(); });
document.querySelectorAll("[data-goto-tab]").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.gotoTab)));

// -- daily recall -----------------------------------------------------------

async function startDailyRecall() {
  const box = $("recall-body");
  box.textContent = "Loading due cards…";
  try {
    const data = await api("/api/study/daily-recall?limit=20");
    clear(box);
    if (!data.count) {
      box.appendChild(emptyState("Nothing due today. Generate flashcards from Notes or Export to seed the deck.", [
        { label: "Open Notes", primary: true, run: () => showTab("notes") },
        { label: "Generate flashcards", run: () => { showTab("export"); $("export-more-tools")?.setAttribute("open", ""); } },
      ]));
      return;
    }
    State.recallItems = data.items;
    State.recallIndex = 0;
    renderRecall();
  } catch (e) { box.textContent = errorText(e); }
}

function renderRecall() {
  const box = $("recall-body");
  const items = State.recallItems || [];
  const i = State.recallIndex || 0;
  clear(box);
  if (!items.length) return;
  const item = items[i];
  box.appendChild(el("div", { class: "hint", text: `${i + 1} / ${items.length} · due ${item.due || "today"}` }));
  box.appendChild(el("div", { class: "flip-prompt", text: item.front }));
  const answer = el("div", { class: "flip-answer muted", hidden: true, text: item.back });
  box.appendChild(answer);
  box.appendChild(el("button", { class: "ghost small", text: "Show answer", type: "button",
    onclick: () => answer.removeAttribute("hidden") }));
  const grades = el("div", { class: "row gap wrap", style: "margin-top:10px" });
  [0, 1, 2, 3, 4, 5].forEach((q) => {
    grades.appendChild(el("button", {
      class: "ghost small", type: "button", text: String(q),
      title: "SM-2 quality " + q,
      onclick: () => gradeRecallItem(item.id, q),
    }));
  });
  box.appendChild(el("p", { class: "hint", text: "Grade recall: 0 again · 3 hard · 5 easy" }));
  box.appendChild(grades);
}

async function gradeRecallItem(id, quality) {
  try {
    await postJSON(`/api/reviews/${id}/grade`, { quality });
    State.recallIndex = (State.recallIndex || 0) + 1;
    if (State.recallIndex >= (State.recallItems || []).length) {
      $("recall-body").textContent = "Recall session complete.";
      loadStreak();
      return;
    }
    renderRecall();
  } catch (e) { toastError(e); }
}

// -- slideshow --------------------------------------------------------------

async function startSlideshow() {
  const box = $("slideshow-body");
  box.textContent = "Loading deck…";
  try {
    const data = await api("/api/study/slideshow?limit=40");
    clear(box);
    if (!data.count) {
      box.appendChild(emptyState("No cards yet. Create a flashcard set from a note first.", [
        { label: "Open Notes", primary: true, run: () => showTab("notes") },
      ]));
      return;
    }
    State.slideshow = data.cards;
    State.slideshowIndex = 0;
    State.slideshowFlipped = false;
    renderSlideshow();
  } catch (e) { box.textContent = errorText(e); }
}

function renderSlideshow() {
  const box = $("slideshow-body");
  const cards = State.slideshow || [];
  const i = State.slideshowIndex || 0;
  clear(box);
  if (!cards.length) return;
  const card = cards[i];
  const flipped = !!State.slideshowFlipped;
  box.appendChild(el("div", { class: "hint", text: `${i + 1} / ${cards.length}` }));
  box.appendChild(el("button", {
    type: "button",
    class: "flip-card" + (flipped ? " flipped" : ""),
    text: flipped ? card.back : card.front,
    onclick: () => { State.slideshowFlipped = !flipped; renderSlideshow(); },
  }));
  box.appendChild(el("div", { class: "row gap wrap", style: "margin-top:10px" }, [
    el("button", { class: "ghost small", type: "button", text: "← Prev",
      onclick: () => {
        State.slideshowIndex = (i - 1 + cards.length) % cards.length;
        State.slideshowFlipped = false;
        renderSlideshow();
      } }),
    el("button", { class: "ghost small", type: "button", text: "Next →",
      onclick: () => {
        State.slideshowIndex = (i + 1) % cards.length;
        State.slideshowFlipped = false;
        renderSlideshow();
      } }),
  ]));
}

// -- focus / Lock In --------------------------------------------------------

async function startFocusMode() {
  const minutes = parseInt($("focus-minutes")?.value, 10) || 25;
  try {
    const ticket = await postJSON("/api/study/focus/start", { minutes, activity_type: "focus" });
    State.focusTicket = ticket;
    State.focusEndsAt = Date.now() + minutes * 60 * 1000;
    $("focus-stop").hidden = false;
    $("focus-start").disabled = true;
    tickFocus();
    State.focusTimer = setInterval(tickFocus, 1000);
  } catch (e) { toastError(e); }
}

function tickFocus() {
  const box = $("focus-body");
  if (!State.focusEndsAt) return;
  const left = Math.max(0, State.focusEndsAt - Date.now());
  const m = Math.floor(left / 60000);
  const s = Math.floor((left % 60000) / 1000);
  box.textContent = left === 0
    ? "Time is up. Complete the session to log it."
    : `Focus · ${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")} remaining`;
  if (left === 0 && State.focusTimer) {
    clearInterval(State.focusTimer);
    State.focusTimer = null;
  }
}

async function completeFocusMode() {
  const ticket = State.focusTicket;
  if (!ticket) return;
  const planned = ticket.minutes || 25;
  const elapsed = planned; // credit the planned block when completing
  try {
    await postJSON("/api/study/focus/complete", {
      minutes: elapsed,
      activity_type: "focus",
      started_at: ticket.started_at || "",
    });
    toast(`Focus session logged: ${elapsed} min.`, "ok");
    $("focus-body").textContent = `Logged ${elapsed} minutes.`;
    $("focus-stop").hidden = true;
    $("focus-start").disabled = false;
    if (State.focusTimer) clearInterval(State.focusTimer);
    State.focusTicket = null;
    State.focusEndsAt = null;
    loadStreak();
  } catch (e) { toastError(e); }
}

// -- essay grader -----------------------------------------------------------

async function gradeEssay() {
  const box = $("essay-result");
  box.textContent = "Grading…";
  try {
    const res = await postJSON("/api/essay/grade", {
      title: ($("essay-title")?.value || "").trim(),
      rubric: ($("essay-rubric")?.value || "").trim(),
      essay: ($("essay-body")?.value || "").trim(),
      save: true,
    });
    clear(box);
    box.appendChild(el("div", { class: "essay-scores" }, [
      el("div", {}, [el("strong", { text: `${Math.round(res.score)}%` }), el("span", { class: "muted small", text: " Essay grade" })]),
      el("div", {}, [el("strong", { text: `${Math.round(res.originality)}%` }), el("span", { class: "muted small", text: " Originality" })]),
    ]));
    (res.strengths || []).forEach((s) => {
      const text = String(s);
      box.appendChild(el("p", { class: "essay-ok",
        text: text.startsWith("Did well") ? text : "Did well · " + text }));
    });
    (res.improvements || []).forEach((s) => {
      const text = String(s);
      box.appendChild(el("p", { class: "essay-improve",
        text: text.startsWith("Improve") ? text : "Improve · " + text }));
    });
    if (res.summary) box.appendChild(el("p", { class: "hint", text: res.summary }));
    box.appendChild(el("p", { class: "muted small", text: `Generated: ${res.generated}` }));
  } catch (e) { box.textContent = errorText(e); }
}

// -- AI chat ----------------------------------------------------------------

async function askLibrary() {
  const q = ($("ai-chat-q")?.value || "").trim();
  if (!q) { toast("Ask a question first.", "warn"); return; }
  const box = $("ai-chat-body");
  box.textContent = "Searching the library…";
  try {
    const res = await postJSON("/api/llm/chat", { query: q });
    clear(box);
    box.appendChild(el("p", { text: res.answer || "" }));
    if ((res.citations || []).length) {
      const ul = el("ul", { class: "cite-list" });
      res.citations.forEach((c) => {
        ul.appendChild(el("li", { class: "muted small",
          text: `[${c.n}] ${c.lecture}: ${c.snippet || ""}` }));
      });
      box.appendChild(ul);
    }
    box.appendChild(el("p", { class: "muted small", text: `Generated: ${res.generated} · confidence ${res.confidence}` }));
  } catch (e) { box.textContent = errorText(e); }
}

// -- notes workspace --------------------------------------------------------

async function loadNotesWorkspace() {
  try {
    const data = await api("/api/notes/workspace");
    State.notesWorkspace = data;
    renderNoteFolders(data.folders || []);
    renderFlashcardSets(data.flashcard_sets || []);
    renderNotesList(data.notes || []);
    if (State.notesAttachPath && $("note-attach-path")) {
      $("note-attach-path").value = State.notesAttachPath;
      if ($("note-session-type") && !$("note-session-type").value) {
        $("note-session-type").value = "lecture";
      }
      if (!$("note-title")?.value) {
        const leaf = State.notesAttachPath.split("/").pop() || "";
        $("note-title").value = leaf.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ");
      }
      State.notesAttachPath = null;
      $("note-compose-body")?.focus();
    }
  } catch (e) {
    toastError(e);
  }
}

function renderNoteFolders(folders) {
  const list = $("note-folders-list");
  const sel = $("note-folder-select");
  if (!list || !sel) return;
  clear(list);
  clear(sel);
  sel.appendChild(el("option", { value: "", text: "No folder" }));
  folders.forEach((f) => {
    list.appendChild(el("div", { class: "notes-row" }, [
      el("span", { text: f.name }),
      el("button", { class: "ghost small", type: "button", text: "Delete",
        onclick: async () => {
          try {
            await api(`/api/note-folders/${f.id}`, { method: "DELETE" });
            loadNotesWorkspace();
          } catch (e) { toastError(e); }
        } }),
    ]));
    sel.appendChild(el("option", { value: String(f.id), text: f.name }));
  });
  if (!folders.length) list.appendChild(el("p", { class: "muted small", text: "No folders yet." }));
}

function renderFlashcardSets(sets) {
  const list = $("flashcard-sets-list");
  if (!list) return;
  clear(list);
  if (!sets.length) {
    list.appendChild(el("p", { class: "muted small", text: "No flashcard sets yet." }));
    return;
  }
  sets.forEach((s) => {
    list.appendChild(el("div", { class: "notes-row" }, [
      el("div", {}, [
        el("strong", { text: s.name }),
        el("div", { class: "muted small", text: `${s.card_count} cards` }),
      ]),
      el("button", { class: "ghost small", type: "button", text: "Slideshow",
        onclick: async () => {
          showTab("study");
          const box = $("slideshow-body");
          try {
            const data = await api(`/api/study/slideshow?set_id=${s.id}`);
            State.slideshow = data.cards;
            State.slideshowIndex = 0;
            State.slideshowFlipped = false;
            focusCard("slideshow-card");
            renderSlideshow();
          } catch (e) { box.textContent = errorText(e); }
        } }),
    ]));
  });
}

function renderNotesList(notes) {
  const list = $("notes-library-list");
  if (!list) return;
  clear(list);
  list.appendChild(el("h3", { text: `Library (${notes.length})` }));
  if (!notes.length) {
    list.appendChild(el("p", { class: "muted small", text: "No notes yet. Save one or import a Word/PDF file." }));
    return;
  }
  notes.forEach((n) => {
    const title = n.title || (n.body || "").slice(0, 48) || `Note ${n.id}`;
    list.appendChild(el("div", { class: "notes-row" }, [
      el("div", {}, [
        el("strong", { text: title }),
        el("div", { class: "muted small",
          text: [n.session_type, n.path].filter(Boolean).join(" · ") || "unfiled" }),
      ]),
      el("div", { class: "row gap" }, [
        el("button", { class: "ghost small", type: "button", text: "Open",
          onclick: () => {
            $("note-title").value = n.title || "";
            $("note-compose-body").value = n.body || "";
            $("note-session-type").value = n.session_type || "";
            $("note-attach-path").value = n.path || "";
            $("note-folder-select").value = n.folder_id != null ? String(n.folder_id) : "";
            State.editingNoteId = n.id;
          } }),
        el("button", { class: "ghost small", type: "button", text: "→ Cards",
          onclick: () => noteToCards(n.id) }),
        el("button", { class: "ghost small", type: "button", text: "Delete",
          onclick: async () => {
            try {
              await api(`/api/notes/${n.id}`, { method: "DELETE" });
              if (State.editingNoteId === n.id) State.editingNoteId = null;
              loadNotesWorkspace();
            } catch (e) { toastError(e); }
          } }),
      ]),
    ]));
  });
}

async function saveComposedNote() {
  const body = ($("note-compose-body")?.value || "").trim();
  if (!body) { toast("Write a note body first.", "warn"); return; }
  const title = ($("note-title")?.value || "").trim();
  const session_type = $("note-session-type")?.value || "";
  const path = ($("note-attach-path")?.value || "").trim();
  const folderRaw = $("note-folder-select")?.value;
  const folder_id = folderRaw ? parseInt(folderRaw, 10) : null;
  try {
    if (State.editingNoteId) {
      await postJSON(`/api/notes/${State.editingNoteId}`, {
        body, title, session_type, path, folder_id,
      }, "PATCH");
      toast("Note updated.", "ok");
    } else {
      await postJSON("/api/notes", {
        body, title, session_type, path, folder_id, bookmark: false,
      });
      toast("Note saved.", "ok");
    }
    State.editingNoteId = null;
    $("note-compose-body").value = "";
    $("note-title").value = "";
    loadNotesWorkspace();
  } catch (e) { toastError(e); }
}

async function addNoteFolder() {
  const name = ($("note-folder-name")?.value || "").trim();
  if (!name) { toast("Enter a folder name.", "warn"); return; }
  try {
    await postJSON("/api/note-folders", { name });
    $("note-folder-name").value = "";
    loadNotesWorkspace();
  } catch (e) { toastError(e); }
}

async function importNoteFile() {
  const path = await pickFile("Choose a Word or PDF file", ".pdf;.docx;.doc;.txt;.md");
  if (!path) return;
  const session_type = $("note-session-type")?.value || "";
  const folderRaw = $("note-folder-select")?.value;
  const folder_id = folderRaw ? parseInt(folderRaw, 10) : null;
  const attach_path = ($("note-attach-path")?.value || "").trim();
  try {
    await postJSON("/api/notes/import", {
      path, session_type, folder_id, attach_path,
      title: ($("note-title")?.value || "").trim(),
    });
    toast("Imported into notes.", "ok");
    loadNotesWorkspace();
  } catch (e) { toastError(e); }
}

async function noteToCards(noteId) {
  const id = noteId || State.editingNoteId;
  if (!id) { toast("Open or save a note first.", "warn"); return; }
  try {
    const res = await postJSON("/api/flashcard-sets/from-note", { note_id: id });
    toast(`Created ${res.seeded} cards.`, "ok");
    loadNotesWorkspace();
  } catch (e) { toastError(e); }
}

$("notes-refresh")?.addEventListener("click", loadNotesWorkspace);
$("note-folder-add")?.addEventListener("click", addNoteFolder);
$("note-save")?.addEventListener("click", saveComposedNote);
$("note-import")?.addEventListener("click", importNoteFile);
$("note-to-cards")?.addEventListener("click", () => noteToCards());

// ---- command palette (Ctrl/Cmd+K) -----------------------------------------

const PALETTE_ACTIONS = [
  { label: "Go to Home", run: () => showTab("home") },
  { label: "Go to Moodle import", run: () => showTab("moodle-quick") },
  { label: "Go to Import", run: () => showTab("import") },
  { label: "Go to Library", run: () => showTab("library") },
  { label: "Go to Notes", run: () => showTab("notes") },
  { label: "Go to Study", run: () => showTab("study") },
  { label: "Go to Export", run: () => showTab("export") },
  { label: "Go to Jobs", run: () => showTab("jobs") },
  { label: "Go to Speech", run: () => showTab("tts") },
  { label: "Go to Semester plan", run: () => showTab("semester") },
  { label: "Search the library", run: () => { showTab("library"); const q = $("search-q"); if (q) q.focus(); } },
  { label: "Show keyboard shortcuts", run: () => showShortcuts() },
  { label: "Start a practice quiz", run: () => { showTab("study"); startPractice(); } },
  { label: "Start daily recall", run: () => { showTab("study"); startDailyRecall(); } },
  { label: "Show glossary", run: () => { showTab("study"); showGlossary(); } },
  { label: "Toggle theme", run: () => $("theme-toggle").click() },
];

let paletteOpen = false;
function openPalette() {
  if (paletteOpen) return;
  paletteOpen = true;
  const input = el("input", { type: "text", class: "palette-input", placeholder: "Type a command…",
    autocomplete: "off", role: "combobox", "aria-expanded": "true",
    "aria-controls": "palette-list", "aria-autocomplete": "list" });
  const list = el("div", { id: "palette-list", class: "palette-list", role: "listbox",
    "aria-label": "Commands" });
  let filtered = PALETTE_ACTIONS.slice();
  let active = 0;

  openModal((box, close) => {
    const dismiss = () => { paletteOpen = false; close(); };
    function render() {
      clear(list);
      filtered.forEach((a, i) => {
        const id = "palette-opt-" + i;
        list.appendChild(el("div", {
          id, role: "option", "aria-selected": i === active ? "true" : "false",
          class: "palette-item" + (i === active ? " active" : ""),
          onclick: () => { dismiss(); a.run(); },
        }, [a.label]));
      });
      input.setAttribute("aria-activedescendant", filtered.length ? "palette-opt-" + active : "");
    }
    input.addEventListener("input", () => {
      const q = input.value.toLowerCase();
      filtered = PALETTE_ACTIONS.filter((a) => a.label.toLowerCase().includes(q));
      active = 0; render();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { active = Math.min(active + 1, filtered.length - 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); render(); e.preventDefault(); }
      else if (e.key === "Enter") { const a = filtered[active]; dismiss(); if (a) a.run(); }
      // Escape is handled by openModal, which also restores focus.
    });
    box.append(input, list);
    render();
  }, { boxClass: "palette-box", onDismiss: () => { paletteOpen = false; } });
}

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
  const typing = (() => {
    const a = document.activeElement;
    if (!a) return false;
    const tag = a.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || a.isContentEditable;
  })();
  if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    showTab("library");
    $("search-q")?.focus();
  } else if (e.key === "?" && !typing) {
    e.preventDefault();
    showShortcuts();
  } else if (e.key === "Escape" && document.querySelector(".app.menu-open")) {
    document.querySelector(".app").classList.remove("menu-open");
    $("menu-toggle")?.setAttribute("aria-expanded", "false");
  }
});

$("transcripts-refresh").addEventListener("click", loadTranscripts);

// ---- library filter / sort (§2) ------------------------------------------
async function applyLibraryFilters() {
  const list = $("transcripts-list");
  const params = new URLSearchParams();
  const type = $("lib-type").value;
  const week = $("lib-week").value.trim();
  const tag = $("lib-tag").value.trim();
  const sort = $("lib-sort").value;
  if (type) params.set("type", type);
  if (week) params.set("week", week);
  if (tag) params.set("tag", tag);
  if (sort) params.set("sort", sort);
  list.textContent = "Filtering…";
  try {
    const data = await api("/api/index?" + params.toString());
    clear(list);
    librarySection(list, "Filtered (" + data.count + ")", data.count);
    if (!data.count) { list.appendChild(el("p", { class: "empty", text: "No items match these filters." })); return; }
    data.items.forEach((it) => {
      list.appendChild(el("div", { class: "list-item clickable", onclick: () => viewTranscript(it.path) }, [
        el("div", { class: "li-label", text: (it.folder ? it.folder + "/" : "") + it.title }),
        el("div", { class: "formats" }, [
          el("span", { class: "tag", text: it.type }),
          it.week != null ? el("span", { class: "tag", text: "wk " + it.week }) : null,
        ]),
      ]));
    });
  } catch (e) { list.textContent = errorText(e); }
}
$("lib-apply").addEventListener("click", applyLibraryFilters);
$("lib-tag").addEventListener("keydown", (e) => { if (e.key === "Enter") applyLibraryFilters(); });
$("lib-clear").addEventListener("click", () => {
  $("lib-type").value = ""; $("lib-week").value = ""; $("lib-tag").value = ""; $("lib-sort").value = "date";
  loadTranscripts();
});

// Export hub: preset bundles and planner downloads in one discoverable place.
let selectedExportPreset = "revision";
document.querySelectorAll("[data-export-preset]").forEach((button) => {
  button.addEventListener("click", () => {
    selectedExportPreset = button.dataset.exportPreset;
    document.querySelectorAll("[data-export-preset]").forEach((candidate) => {
      const active = candidate === button;
      candidate.classList.toggle("active", active);
      candidate.setAttribute("aria-pressed", String(active));
    });
    clear($("bundle-results"));
  });
});

function exportArtifactPaths(result) {
  const paths = [];
  for (const value of Object.values(result.results || {})) {
    if (!value || typeof value !== "object") continue;
    for (const key of ["combined", "path", "csv", "anki_tsv"]) {
      if (typeof value[key] === "string" && value[key]) paths.push(value[key]);
    }
    if (Array.isArray(value.files)) paths.push(...value.files);
  }
  return [...new Set(paths)];
}

$("bundle-preview")?.addEventListener("click", async () => {
  const out = $("bundle-results");
  out.textContent = "Checking the library…";
  try {
    const data = await postJSON("/api/export/preview", {
      preset: selectedExportPreset, scope: "course", course: currentCourse(),
    });
    clear(out);
    out.appendChild(el("p", { class: "ok-text",
      text: `${data.lectures_in_scope} lecture(s) will be included.` }));
    const list = el("ul", { class: "artifact-preview" });
    for (const artifact of data.artifacts || []) {
      list.appendChild(el("li", { text: `${artifact.target.replaceAll("_", " ")}: about ${artifact.estimated_items} item(s)` }));
    }
    out.appendChild(list);
  } catch (e) { out.textContent = errorText(e); toastError(e); }
});

$("bundle-run")?.addEventListener("click", async () => {
  const out = $("bundle-results");
  const btn = $("bundle-run");
  btn.disabled = true; out.textContent = "Creating bundle…";
  try {
    const data = await postJSON("/api/export/run", {
      preset: selectedExportPreset, scope: "course", course: currentCourse(),
    });
    clear(out);
    const paths = exportArtifactPaths(data);
    out.appendChild(el("p", { class: "ok-text",
      text: `Created ${data.targets.length} export type(s) in the library.` }));
    for (const path of paths.slice(0, 20)) {
      const canView = /\.(md|txt|csv|srt|vtt)$/i.test(path);
      out.appendChild(el("div", { class: "list-item" }, [
        el("span", { class: "li-label", text: path }),
        canView ? el("button", { class: "tag", text: "View", onclick: () => viewTranscript(path) }) : null,
      ]));
    }
    toast("Export bundle created.", "ok");
    loadTranscripts();
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

function updateExportPlanLinks(planId) {
  // Semester download rows were demoted; suites are the primary path.
  const status = $("calendar-export-status");
  if (status && planId) {
    status.dataset.planId = String(planId);
  }
}

async function loadExportHub() {
  const status = $("calendar-export-status");
  if (!status) return;
  try {
    const data = await api("/api/semester/plans");
    const plans = (data.plans || []).slice().sort((a, b) =>
      String(b.created_at || "").localeCompare(String(a.created_at || "")));
    const latest = plans[0];
    updateExportPlanLinks(latest?.id);
    status.textContent = latest
      ? `Latest plan: ${latest.name} (${latest.task_count} tasks). Use Study suites → Sync above.`
      : "No semester plan yet. The assessment calendar above is still available.";
  } catch (e) {
    updateExportPlanLinks(null);
    status.textContent = "Semester plan status is temporarily unavailable.";
  }
  loadSuiteSettings();
}

$("export-open-semester")?.addEventListener("click", () => showTab("semester"));
$("suite-open-semester")?.addEventListener("click", (e) => { e.preventDefault(); showTab("semester"); });

// ---- Study suites ---------------------------------------------------------
let selectedSuiteFormat = "obsidian";

document.querySelectorAll("[data-suite-format]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("[data-suite-format]").forEach((b) => {
      b.classList.toggle("active", b === btn);
      b.setAttribute("aria-pressed", b === btn ? "true" : "false");
    });
    selectedSuiteFormat = btn.dataset.suiteFormat;
  });
});

function renderSuiteLastSync(last) {
  const host = $("suite-last-sync");
  if (!host) return;
  if (!last || typeof last !== "object") {
    host.textContent = "No suite sync yet. Set destination folders, then Sync suites.";
    return;
  }
  const formats = (last.formats || []).join(", ") || "none";
  const bits = [
    `Last sync: ${formats}`,
    `${last.new_files ?? 0} new`,
    `${last.updated ?? 0} updated`,
  ];
  if (last.at) bits.push(String(last.at).replace("T", " ").slice(0, 19));
  host.textContent = bits.join(" · ");
}

async function loadSuiteSettings() {
  try {
    const data = await api("/api/suites/settings");
    const dest = data.destinations || {};
    if ($("suite-dest-obsidian")) $("suite-dest-obsidian").value = dest.obsidian || "";
    if ($("suite-dest-notion")) $("suite-dest-notion").value = dest.notion || "";
    if ($("suite-dest-onenote")) $("suite-dest-onenote").value = dest.onenote || "";
    const enabled = new Set(data.enabled || ["obsidian"]);
    if ($("suite-enable-obsidian")) $("suite-enable-obsidian").checked = enabled.has("obsidian");
    if ($("suite-enable-notion")) $("suite-enable-notion").checked = enabled.has("notion");
    if ($("suite-enable-onenote")) $("suite-enable-onenote").checked = enabled.has("onenote");
    if ($("suite-auto-sync")) $("suite-auto-sync").checked = !!data.auto_sync;
    renderSuiteLastSync(data.last_sync);
  } catch (_) { /* ignore */ }
}

async function saveSuiteSettings() {
  const destinations = {
    obsidian: ($("suite-dest-obsidian")?.value || "").trim(),
    notion: ($("suite-dest-notion")?.value || "").trim(),
    onenote: ($("suite-dest-onenote")?.value || "").trim(),
  };
  const enabled = [];
  if ($("suite-enable-obsidian")?.checked) enabled.push("obsidian");
  if ($("suite-enable-notion")?.checked) enabled.push("notion");
  if ($("suite-enable-onenote")?.checked) enabled.push("onenote");
  await api("/api/suites/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      destinations,
      enabled,
      auto_sync: !!$("suite-auto-sync")?.checked,
    }),
  });
}

document.querySelectorAll(".suite-pick-dest").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const key = btn.dataset.suite;
    const dest = await pickFolder(`Choose destination folder for ${key}`);
    if (dest === null) return;
    const input = $(`suite-dest-${key}`);
    if (input) input.value = dest;
    await saveSuiteSettings();
    toast(`${key} destination saved.`, "ok");
  });
});

["suite-enable-obsidian", "suite-enable-notion", "suite-enable-onenote", "suite-auto-sync"]
  .forEach((id) => $(id)?.addEventListener("change", () => { saveSuiteSettings().catch(() => {}); }));

["suite-dest-obsidian", "suite-dest-notion", "suite-dest-onenote"]
  .forEach((id) => $(id)?.addEventListener("change", () => { saveSuiteSettings().catch(() => {}); }));

function suiteEnabledFormats() {
  const enabled = [];
  if ($("suite-enable-obsidian")?.checked) enabled.push("obsidian");
  if ($("suite-enable-notion")?.checked) enabled.push("notion");
  if ($("suite-enable-onenote")?.checked) enabled.push("onenote");
  return enabled;
}

function suitePaperCodesFromUi() {
  return getSelectedPaperCodes();
}

$("suite-preview")?.addEventListener("click", async () => {
  const out = $("suite-results");
  out.textContent = "Previewing…";
  try {
    await saveSuiteSettings();
    const data = await postJSON("/api/suites/preview", { format: selectedSuiteFormat });
    clear(out);
    if (!(data.task_count || data.subjects?.length)) {
      out.appendChild(el("p", { class: "hint",
        text: "No semester plan yet. Import a Moodle course or run Update everything on Semester first." }));
    }
    out.appendChild(el("p", { class: "ok-text",
      text: `${data.format} suite ≈ ${data.estimated_files} files · ${data.subjects.length} paper(s) · ${data.task_count} tasks` }));
  } catch (e) { out.textContent = errorText(e); toastError(e); }
});

$("suite-build")?.addEventListener("click", async () => {
  const out = $("suite-results");
  const btn = $("suite-build");
  btn.disabled = true; out.textContent = "Building suite…";
  try {
    await saveSuiteSettings();
    const data = await postJSON("/api/suites/build", {
      format: selectedSuiteFormat, output: "folder",
    });
    clear(out);
    out.appendChild(el("p", { class: "ok-text",
      text: `Built ${data.format} suite (${data.file_count} files) at ${data.root}` }));
    if (data.destination) {
      out.appendChild(el("p", { class: "hint", text: `Also mirrored to ${data.destination}` }));
    } else {
      out.appendChild(el("p", { class: "hint",
        text: "No destination folder set for this format — suite built under the library _suites folder." }));
    }
    toast("Suite built.", "ok");
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

$("suite-sync")?.addEventListener("click", async () => {
  const out = $("suite-results");
  const btn = $("suite-sync");
  btn.disabled = true; out.textContent = "Syncing suites…";
  try {
    await saveSuiteSettings();
    const enabled = suiteEnabledFormats();
    if (!enabled.length) {
      out.textContent = "Enable at least one suite format (Obsidian, Notion, or OneNote).";
      toast("Enable a suite format first.", "warn");
      return;
    }
    const missingDest = enabled.filter((fmt) => !($(`suite-dest-${fmt}`)?.value || "").trim());
    if (missingDest.length === enabled.length) {
      out.textContent = "Set at least one destination folder before Sync, or use Build to write under the library.";
      toast("Set a destination folder first.", "warn");
      return;
    }
    const formats = enabled.filter((fmt) => !missingDest.includes(fmt));
    const paperCodes = suitePaperCodesFromUi();
    const job = await postJSON("/api/suites/sync", {
      push_live: true,
      formats,
      paper_codes: paperCodes.length ? paperCodes : undefined,
      discover_panopto: true,
      use_browser: mqImportMode === "browser",
    });
    clear(out);
    out.appendChild(el("p", { class: "ok-text", text: `Suite sync job #${job.id} started.` }));
    if (missingDest.length) {
      out.appendChild(el("p", { class: "hint",
        text: `Skipped ${missingDest.join(", ")} (no destination folder).` }));
    }
    out.appendChild(el("p", { class: "hint",
      text: "Progress is tracked under Jobs. Reopen Export to refresh last-sync status." }));
    toast("Suite sync started — see Jobs.", "ok");
    showTab("jobs"); startJobsPolling();
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

// Export everything (transcripts + documents + Notion) for NotebookLM / any AI
$("export-all").addEventListener("click", async () => {
  const out = $("export-all-results");
  const btn = $("export-all");
  const dest = await pickFolder("Choose a folder to export all sources into");
  if (dest === null) return;                      // cancelled
  btn.disabled = true; out.textContent = "Gathering every source…";
  try {
    const data = await api("/api/export/all", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course: currentCourse(), combined: $("all-combined").checked, output_dir: dest }),
    });
    clear(out);
    out.appendChild(el("p", { class: "ok-text",
      text: `Exported ${data.count} source(s): ${data.transcripts} transcript(s), ${data.documents} document(s), ${data.notion} Notion page(s).` }));
    if (data.combined) out.appendChild(el("div", {}, [
      el("button", { class: "tag", text: "View everything_pack.md", onclick: () => { viewTranscript(data.combined); showTab("library"); } }),
    ]));
    out.appendChild(el("p", { class: "hint", text: `Files saved to ${data.output_dir || data.dest}` }));
    toast(`Exported ${data.count} source(s).`, "ok");
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

// NotebookLM export
$("nlm-export").addEventListener("click", async () => {
  const out = $("nlm-results");
  const btn = $("nlm-export");
  const dest = await pickFolder("Choose a folder for the NotebookLM sources");
  if (dest === null) return;
  btn.disabled = true; out.textContent = "Exporting…";
  try {
    const data = await api("/api/export/notebooklm", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course: currentCourse(), combined: $("nlm-combined").checked, output_dir: dest }),
    });
    clear(out);
    out.appendChild(el("p", { class: "ok-text", text: `Exported ${data.count} file(s) to ${data.output_dir || data.dest}.` }));
    if (data.combined) out.appendChild(el("div", {}, [
      el("button", { class: "tag", text: "View course_pack.md", onclick: () => viewTranscript(data.combined) }),
    ]));
    data.files.forEach((f) => out.appendChild(el("div", { class: "list-item" }, [
      el("span", { class: "li-label", text: f }),
      el("button", { class: "tag", text: "View", onclick: () => viewTranscript(f) }),
    ])));
    toast(`Exported ${data.count} NotebookLM file(s).`, "ok");
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

// Notion study-database CSV export (runs as a background LLM job)
$("studycsv-go").addEventListener("click", async () => {
  const out = $("studycsv-results");
  const btn = $("studycsv-go");
  const dest = await pickFolder("Choose a folder for the study database CSV");
  if (dest === null) return;
  btn.disabled = true; out.textContent = "Queuing export…";
  try {
    const data = await api("/api/export/notion-csv", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course: currentCourse(), output_dir: dest }),
    });
    clear(out);
    if (data.id) {
      out.appendChild(el("p", { class: "ok-text", text: "Job queued. See the Jobs panel for progress." }));
      out.appendChild(el("button", { class: "tag", text: "Go to Jobs", onclick: () => showTab("jobs") }));
      toast("Study database export started.", "ok");
      startJobsPolling();
    } else {
      out.appendChild(el("p", { class: "ok-text", text: `Exported ${data.count} lecture(s) to ${data.output_dir || data.csv}.` }));
      out.appendChild(el("div", {}, [
        el("button", { class: "tag", text: "View CSV", onclick: () => viewTranscript(data.csv) }),
      ]));
      out.appendChild(el("p", { class: "hint", text: "Columns: " + (data.columns || []).join(", ") }));
      toast(`Exported ${data.count} rows to a CSV.`, "ok");
    }
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

// Lecture SRT export - writes SRT files to a user-chosen folder alongside videos
$("srt-export").addEventListener("click", async () => {
  const out = $("srt-results");
  const dest = await pickFolder("Choose a folder for the subtitles and recordings");
  if (dest === null) return;
  const btn = $("srt-export");
  btn.disabled = true; out.textContent = "Exporting subtitle files…";
  try {
    const data = await api("/api/export/srt", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_dir: dest }),
    });
    clear(out);
    out.appendChild(el("p", { class: "ok-text",
      text: `Exported ${data.count} subtitle file(s) to ${data.dest || data.output_dir}.` }));
    const rec = data.recordings;
    if (rec) {
      const have = (rec.copied?.length || 0) + (rec.downloaded?.length || 0);
      out.appendChild(el("p", { class: have ? "ok-text" : "hint",
        text: `${have} lecture recording(s) placed alongside the subtitle files`
          + (rec.downloaded?.length ? ` (${rec.downloaded.length} downloaded)` : "") + "." }));
      if (rec.missing?.length) {
        out.appendChild(el("p", { class: "hint",
          text: `${rec.missing.length} recording(s) could not be retrieved (not kept locally, and `
            + `the source requires sign-in). Re-transcribe those lectures to retain their video.` }));
      }
    }
    out.appendChild(el("p", { class: "hint",
      text: "The .srt files share each video's name, so players load them automatically when both are in this folder." }));
    toast(`Exported ${data.count} SRT file(s).`, "ok");
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

$("formats-export")?.addEventListener("click", async () => {
  const out = $("formats-results");
  const btn = $("formats-export");
  const formats = [...document.querySelectorAll("#format-checks input:checked")].map((input) => input.value);
  if (!formats.length) { toast("Select at least one format.", "warn"); return; }
  btn.disabled = true; out.textContent = "Generating formats…";
  try {
    const data = await postJSON("/api/export/formats", { formats });
    clear(out);
    out.appendChild(el("p", { class: "ok-text",
      text: `Generated ${data.count} file(s): ${data.formats.join(", ")}.` }));
    for (const path of data.files || []) {
      out.appendChild(el("div", { class: "list-item" }, [
        el("span", { class: "li-label", text: path }),
        el("button", { class: "tag", text: "View", onclick: () => viewTranscript(path) }),
      ]));
    }
    toast(`Generated ${data.count} file(s).`, "ok");
    loadTranscripts();
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

// Reorganize
$("org-go").addEventListener("click", async () => {
  const out = $("org-results");
  out.textContent = "Reorganizing…";
  try {
    const data = await api("/api/organize", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ by: $("org-by").value }),
    });
    out.textContent = `Moved ${data.moved} file(s) (by ${data.by}).`;
    toast(`Reorganized ${data.moved} file(s).`, "ok");
    loadTranscripts();
  } catch (e) { out.textContent = errorText(e); }
});

// ---- search ---------------------------------------------------------------

async function doSearch() {
  const q = $("search-q").value.trim();
  const out = $("search-results");
  if (!q) { toast("Enter a search term.", "warn"); return; }
  out.textContent = "Searching…";
  try {
    const data = await api("/api/search?q=" + encodeURIComponent(q));
    clear(out);
    if (!data.results.length) { out.appendChild(el("p", { class: "empty", text: "No matches. Try a shorter phrase, or clear the filters." })); return; }
    out.appendChild(el("p", { class: "muted", text: `${data.results.length} lecture(s) match.` }));
    data.results.forEach((r) => {
      const label = (r.folder ? r.folder + "/" : "") + r.lecture;
      const card = el("div", { class: "card" }, [
        el("div", { class: "search-head" }, [
          el("strong", { text: label }),
          el("span", { class: "badge", text: `${r.count} hit${r.count === 1 ? "" : "s"}` }),
          el("button", { class: "tag", text: "open", onclick: () => viewTranscript(r.file).then(() => showTab("library")) }),
        ]),
      ]);
      r.snippets.forEach((s) => card.appendChild(el("div", { class: "snippet", text: s })));
      out.appendChild(card);
    });
  } catch (e) { out.textContent = errorText(e); }
}
$("search-go").addEventListener("click", doSearch);
$("search-q").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

// ---- flashcards -----------------------------------------------------------

function renderDeckResult(out, data, label) {
  clear(out);
  out.appendChild(el("p", { class: "ok-text", text: `${data.count} card(s) ${label}.` }));
  out.appendChild(el("div", { class: "row" }, [
    el("button", { class: "tag", text: "View Anki .txt", onclick: () => { viewTranscript(data.anki_tsv); showTab("library"); } }),
    el("button", { class: "tag", text: "View .csv", onclick: () => { viewTranscript(data.csv); showTab("library"); } }),
  ]));
  out.appendChild(el("p", { class: "hint", text: "In Anki: File → Import, then select the .txt file (tags map to column 3)." }));
  (data.preview || []).forEach((c) => {
    out.appendChild(el("div", { class: "card flashcard" }, [
      el("div", {}, [el("strong", { text: "Q: " }), c.front]),
      el("div", {}, [el("span", { class: "muted", text: "A: " }), c.back]),
      el("div", { class: "hint", text: "tags: " + (c.tags || []).join(" ") }),
    ]));
  });
}

$("fc-generate").addEventListener("click", async () => {
  const out = $("fc-gen-results");
  const btn = $("fc-generate");
  const dest = await pickFolder("Choose a folder to save the flashcard deck");
  if (dest === null) return;
  btn.disabled = true; out.textContent = "Queuing flashcard job…";
  try {
    const data = await api("/api/flashcards/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        deck: $("fc-deck").value.trim() || "flashcards",
        course: currentCourse(),
        max_cards: parseInt($("fc-max").value, 10) || 50,
        output_dir: dest,
      }),
    });
    clear(out);
    if (data.id) {
      out.appendChild(el("p", { class: "ok-text", text: "Flashcard job queued. See the Jobs panel for progress." }));
      out.appendChild(el("button", { class: "tag", text: "Go to Jobs", onclick: () => showTab("jobs") }));
      toast("Flashcard generation started.", "ok");
      startJobsPolling();
    } else {
      renderDeckResult(out, data, "generated");
      toast(`Generated ${data.count} flashcard(s).`, "ok");
    }
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

$("fc-categorize").addEventListener("click", async () => {
  const out = $("fc-cat-results");
  const btn = $("fc-categorize");
  const text = $("fc-cat-text").value.trim();
  const path = $("fc-cat-path").value.trim();
  if (!text && !path) { toast("Paste a deck, or give the path to one.", "warn"); return; }
  btn.disabled = true; out.textContent = "Queuing categorization job…";
  try {
    const data = await api("/api/flashcards/categorize", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text, path,
        course: currentCourse(),
        deck: $("fc-cat-deck").value.trim() || "categorized",
      }),
    });
    clear(out);
    if (data.id) {
      out.appendChild(el("p", { class: "ok-text", text: "Categorization job queued. See the Jobs panel for progress." }));
      out.appendChild(el("button", { class: "tag", text: "Go to Jobs", onclick: () => showTab("jobs") }));
      toast("Categorization started.", "ok");
      startJobsPolling();
    } else {
      renderDeckResult(out, data, "tagged");
      toast(`Categorized ${data.count} card(s).`, "ok");
    }
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

// ---- exam cheat sheet (PDF, LLM, A4 page limit) ---------------------------

$("cheatsheet-go")?.addEventListener("click", async () => {
  const out = $("cheatsheet-results");
  const btn = $("cheatsheet-go");
  const pages = Math.max(1, Math.min(parseInt($("cheatsheet-pages").value, 10) || 1, 10));
  const save = await pickSaveFile("Save the exam cheat sheet",
    (currentCourse() || "course").replace(/[^\w.-]+/g, "_") + "_cheatsheet.pdf", ".pdf");
  if (save === null) return;
  btn.disabled = true; out.textContent = "Queuing cheat sheet job…";
  try {
    const data = await api("/api/export/cheatsheet", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course: currentCourse(), max_pages: pages, save_path: save }),
    });
    clear(out);
    if (data.id) {
      out.appendChild(el("p", { class: "ok-text", text: "Cheat sheet job queued. See the Jobs panel for progress." }));
      out.appendChild(el("button", { class: "tag", text: "Go to Jobs", onclick: () => showTab("jobs") }));
      toast("Cheat sheet generation started.", "ok");
      startJobsPolling();
    }
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

function parseTopicWeights(raw) {
  const text = (raw || "").trim();
  if (!text) return null;
  const out = {};
  text.split(/[,;]+/).forEach((part) => {
    const m = part.trim().match(/^([^:]+):?\s*([\d.]+)\s*%?$/);
    if (m) out[m[1].trim()] = parseFloat(m[2]);
  });
  return Object.keys(out).length ? out : null;
}

function selectedPracticeTypes() {
  const types = [];
  if ($("pe-type-mcq")?.checked) types.push("mcq");
  if ($("pe-type-short")?.checked) types.push("short");
  if ($("pe-type-long")?.checked) types.push("long");
  if ($("pe-type-cloze")?.checked) types.push("cloze");
  if ($("pe-type-tf")?.checked) types.push("truefalse");
  return types.length ? types : ["mcq", "short", "long"];
}

function renderPdfJobHint(container, result) {
  if (!result) return;
  const path = result.path || result.pdf_path || result.md_path;
  if (path) {
    container.appendChild(el("p", { class: "hint", text: "Saved: " + path }));
  }
  if (result.truncated) {
    container.appendChild(el("p", { class: "banner warn",
      text: "Page budget full — lower-priority points were dropped." }));
  }
  if (result.note) {
    container.appendChild(el("p", { class: "hint", text: result.note }));
  }
  if (result.rel) {
    container.appendChild(el("button", {
      class: "tag", text: "Open in library",
      onclick: () => { viewTranscript(result.rel); showTab("library"); },
    }));
  }
}

$("practice-exam-go")?.addEventListener("click", async () => {
  const out = $("practice-exam-results");
  const btn = $("practice-exam-go");
  const course = currentCourse();
  const n = Math.max(10, Math.min(parseInt($("pe-count").value, 10) || 100, 150));
  const kind = $("pe-kind-exam")?.checked ? "exam" : "practice";
  const stem = (course || "course").replace(/[^\w.-]+/g, "_")
    + (kind === "exam" ? "_exam" : "_practice") + `_${n}q.pdf`;
  const save = await pickSaveFile(
    kind === "exam" ? "Save the exam paper" : "Save the practice exam", stem, ".pdf");
  if (save === null) return;
  const formats = ["pdf"];
  if ($("pe-format-md")?.checked) formats.push("md");
  btn.disabled = true; out.textContent = "Queuing practice exam job…";
  try {
    const payload = {
      course,
      n,
      types: selectedPracticeTypes(),
      difficulty: $("pe-difficulty")?.value || "medium",
      scope: $("pe-scope")?.value || "course",
      target: $("pe-target")?.value.trim() || "",
      weights: parseTopicWeights($("pe-weights")?.value),
      seed: $("pe-seed")?.value.trim() || null,
      include_answer_key: $("pe-answer-key")?.checked !== false,
      time_minutes: parseInt($("pe-time")?.value, 10) || null,
      total_marks: parseInt($("pe-marks")?.value, 10) || null,
      kind,
      formats,
      save_path: save,
    };
    const data = await api("/api/export/practice-exam", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    clear(out);
    if (data.id) {
      out.appendChild(el("p", { class: "ok-text",
        text: `${kind === "exam" ? "Exam" : "Practice exam"} job queued (${n} questions). See Jobs for progress.` }));
      out.appendChild(el("button", { class: "tag", text: "Go to Jobs", onclick: () => showTab("jobs") }));
      toast("Practice exam generation started.", "ok");
      startJobsPolling();
    }
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

// ---- local Ollama management ----------------------------------------------

function renderOllamaStatus(s) {
  const box = $("ollama-status"); if (!box) return;
  clear(box);
  const dot = (state, label) => el("span", { class: "env-pill" }, [el("span", { class: "dot " + state }), label]);
  box.appendChild(dot(s.installed ? "on" : "off", s.installed ? "Ollama installed" : "Ollama not installed"));
  box.appendChild(dot(s.running ? "on" : "warn", s.running ? "Server running" : "Server stopped"));
  if (s.models && s.models.length) {
    box.appendChild(dot("on", `${s.models.length} model(s) installed`));
  }

  // Populate the model dropdown with the curated list; mark installed ones.
  const sel = $("ollama-model");
  if (sel) {
    const prev = sel.value;
    clear(sel);
    const curated = s.curated_models || [];
    const installed = new Set(s.models || []);
    curated.forEach((m) => {
      const ready = installed.has(m.tag) || [...installed].some((im) => im.startsWith(m.tag.split(":")[0] + ":"));
      const label = m.label + (m.recommended ? " (recommended)" : "") + (ready ? " - installed" : "");
      sel.appendChild(el("option", { value: m.tag, text: label }));
    });
    // Pre-select: previous choice → recommended → first
    if (prev && [...sel.options].some((o) => o.value === prev)) {
      sel.value = prev;
    } else {
      const rec = curated.find((m) => m.recommended);
      if (rec) sel.value = rec.tag;
    }
  }
}

async function refreshOllama() {
  try { renderOllamaStatus(await api("/api/ollama/status")); }
  catch (_) { /* leave as-is */ }
}

$("ollama-refresh")?.addEventListener("click", refreshOllama);

// "Download" button inside the hidden custom-model details.
$("ollama-pull")?.addEventListener("click", async () => {
  const out = $("ollama-results");
  const model = $("ollama-model-custom")?.value.trim();
  if (!model) { toast("Enter a model name to download.", "warn"); return; }
  out.textContent = `Queuing download of ${model}…`;
  try {
    const data = await postJSON("/api/ollama/pull", { model });
    if (data.id) {
      out.textContent = `Downloading ${model}. See the Jobs panel for progress.`;
      toast("Model download started.", "ok");
      showTab("jobs"); startJobsPolling();
    }
  } catch (e) { out.textContent = errorText(e); toastError(e); }
});

// "Initialize model" — one-click: install Ollama if needed, start server, pull model, activate.
$("ollama-init")?.addEventListener("click", async () => {
  const out = $("ollama-results");
  const btn = $("ollama-init");
  const model = $("ollama-model")?.value || "llama3.2:3b";

  btn.disabled = true;
  out.innerHTML = '<span class="mq-sso-spinner"></span> Initializing — this may take a few minutes…';

  try {
    let s = await postJSON("/api/ollama/initialize", { model });

    if (s.installed === false) {
      // Ollama not on PATH — offer automatic Windows install.
      out.textContent = "Ollama is not installed. Installing now (this may take a minute)…";
      let inst;
      try {
        inst = await postJSON("/api/ollama/install", {});
      } catch (ie) {
        out.innerHTML = `Install failed: ${ie.message}. <a href="https://ollama.com/download" target="_blank" rel="noopener">Install manually <svg class="ico" aria-hidden="true"><use href="#i-external"/></svg></a>`;
        toast("Ollama install failed.", "warn"); btn.disabled = false; return;
      }
      if (!inst.ok) {
        out.innerHTML = `Install did not complete. <a href="https://ollama.com/download" target="_blank" rel="noopener">Install manually <svg class="ico" aria-hidden="true"><use href="#i-external"/></svg></a>`;
        toast("Ollama install failed.", "warn"); btn.disabled = false; return;
      }
      out.textContent = "Ollama installed — pulling model now…";
      s = await postJSON("/api/ollama/initialize", { model });
    }

    renderOllamaStatus(s);
    out.textContent = s.message || `Model "${model}" is ready.`;
    toast("Local AI model ready.", "ok");
    loadStatus();  // enable flashcard / cheat-sheet buttons
  } catch (e) {
    out.textContent = errorText(e);
    toastError(e);
  } finally {
    btn.disabled = false;
  }
});

// Refresh Ollama status whenever its panel is opened.
$("ollama-section")?.addEventListener("toggle", (e) => { if (e.target.open) refreshOllama(); });

// ---- pdf ------------------------------------------------------------------

$("pdf-go").addEventListener("click", async () => {
  const out = $("pdf-results");
  const input_path = $("pdf-path").value.trim();
  if (!input_path) { toast("Enter a folder or file path.", "warn"); return; }
  remember("pdfpath", input_path);
  const target = $("doc-target").value;
  const btn = $("pdf-go");
  btn.disabled = true; out.textContent = "Converting… (this can take a moment)";
  try {
    const data = await api("/api/docs/convert", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input_path,
        exts: selectedDocExts(),
        include_subfolders: $("pdf-recursive").checked,
        overwrite: $("pdf-overwrite").checked,
        target,
        combined: $("doc-combined").checked,
        keep_images: $("doc-images").checked,
      }),
    });
    clear(out);
    const withImgs = data.files ? data.files.reduce((n, f) => n + (f.images || 0), 0) : 0;
    const imgNote = withImgs ? ` · ${withImgs} image(s) attached` : "";
    out.appendChild(el("p", { class: "ok-text", text: `Converted ${data.count} document(s)${imgNote} to ${data.output_root}.` }));
    if (data.combined) out.appendChild(el("div", {}, [
      el("button", { class: "tag", text: "view documents_pack.md", onclick: () => { viewTranscript(data.combined); showTab("library"); } }),
    ]));
    data.files.forEach((f) => {
      const row = el("div", { class: "list-item" }, [f.error
        ? el("span", { class: "li-label" }, [icon("alert", { cls: "state-ico warn" }), ` ${f.src}: ${f.error}`])
        : el("span", { class: "li-label", text: f.md })]);
      if (!f.error && target === "ai") row.appendChild(el("button", { class: "tag", text: "view", onclick: () => { viewTranscript(f.md); showTab("library"); } }));
      out.appendChild(row);
    });
    toast(`Converted ${data.count} document(s).`, "ok");
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

// ---- jobs -----------------------------------------------------------------

const STT_STAGE_LABELS = {
  captions: "Checking captions",
  preprocess: "Normalizing audio",
  transcribing: "Transcribing",
  enriching: "Aligning / speakers",
  downloading: "Downloading media",
  waiting: "Waiting for a free transcription slot",
  writing: "Saving files",
  done: "Done",
};

function stageLabel(stage) {
  if (!stage) return "";
  if (STT_STAGE_LABELS[stage]) return STT_STAGE_LABELS[stage];
  return stage.charAt(0).toUpperCase() + stage.slice(1);
}

// Return a human "~Xm remaining" string for a running job, or "" if not enough data.
function jobEta(j) {
  if (j.status !== "running" || !j.started_at || j.progress < 0.05) return "";
  const elapsedS = (Date.now() - new Date(j.started_at).getTime()) / 1000;
  if (elapsedS < 2) return "";
  const remainingS = Math.round((elapsedS / j.progress) * (1 - j.progress));
  if (remainingS <= 0) return "";
  if (remainingS < 60) return `~${remainingS}s remaining`;
  return `~${Math.ceil(remainingS / 60)}m remaining`;
}

async function loadJobs() {
  const out = $("jobs-list");
  try {
    const data = await api("/api/jobs");
    clear(out);
    const active = data.jobs.filter((j) => j.status === "queued" || j.status === "running").length;
    const badge = $("jobs-badge");
    badge.textContent = active;
    badge.classList.toggle("hidden", active === 0);

    if (!data.jobs.length) {
      out.appendChild(emptyState("No jobs yet. Transcription and export jobs appear here while they run.", [
        { label: "Import Moodle course", primary: true, run: () => showTab("moodle-quick") },
        { label: "Go to Export", run: () => showTab("export") },
      ]));
      stopJobsPolling();
      return;
    }
    // A calm heads-up while transcriptions run: they are slow and that is normal.
    const transcribing = data.jobs.some((j) =>
      j.type === "transcribe" && (j.status === "queued" || j.status === "running"));
    if (transcribing) {
      out.appendChild(el("p", { class: "hint",
        text: "Transcription runs in the background and can take a few minutes per lecture. "
          + "You can keep using the app; this list updates on its own." }));
    }
    data.jobs.forEach((j) => {
      const pct = Math.round(j.progress * 100);
      const stageText = stageLabel(j.stage);
      const eta = jobEta(j);
      const hintParts = [stageText ? `${stageText} · ${pct}%` : `${pct}%`, eta].filter(Boolean);
      const card = el("div", { class: "card job " + j.status }, [
        el("div", { class: "job-head" }, [
          el("strong", { text: j.title }),
          el("span", { class: "badge " + j.status, text: j.status }),
        ]),
        el("div", { class: "progress" }, [el("div", { class: "bar", style: `width:${pct}%` })]),
        el("div", { class: "hint", text: hintParts.join(" · ") }),
      ]);
      if (j.status === "done" && j.result) {
        if (j.result.status === "skipped") {
          card.appendChild(el("div", { class: "hint", text: "skipped - outputs already exist" }));
        } else if (j.result.outputs) {
          card.appendChild(el("div", { class: "hint", text: "wrote: " + Object.keys(j.result.outputs).join(", ") }));
        }
        if (j.type === "transcribe" && (j.result.route_reason || j.result.engine)) {
          const bits = [
            j.result.engine && j.result.model ? `${j.result.engine}/${j.result.model}` : j.result.engine,
            j.result.route_reason,
          ].filter(Boolean);
          card.appendChild(el("div", { class: "hint", text: bits.join(" — ") }));
        }
        // Surface whether an AI job actually used the model or fell back to the
        // offline heuristic, so a silent fallback doesn't read as "AI output".
        if (j.result.generated === "ai") {
          card.appendChild(el("div", { class: "hint", text: "Generated by AI" + (j.result.provider ? ` (${j.result.provider})` : "") }));
        } else if (j.result.generated === "extractive") {
          const why = j.result.reason
            || "the AI model was unavailable or its reply couldn't be parsed";
          card.appendChild(el("div", { class: "banner warn",
            text: "Built with offline heuristics - " + why }));
        }
        if (j.type === "cheatsheet" || j.type === "practice_exam") {
          const resBox = el("div", { class: "results compact" });
          renderPdfJobHint(resBox, j.result);
          if (resBox.childNodes.length) card.appendChild(resBox);
        }
      }
      if (j.status === "error") {
        if (j.failure_category) {
          card.appendChild(el("div", { class: "hint", text: "failure type: " + j.failure_category }));
        }
        card.appendChild(el("pre", { class: "error", text: j.error }));
      }
      // §3 controls: cancel a live job; retry a failed/canceled/interrupted one; view logs.
      const actions = el("div", { class: "job-actions" });
      if (j.status === "queued" || j.status === "running") {
        actions.appendChild(el("button", {
          class: "ghost small", text: "Cancel",
          onclick: () => jobAction(j.id, "cancel"),
        }));
      }
      if (j.retryable) {
        actions.appendChild(el("button", {
          class: "ghost small", text: "Retry",
          onclick: () => jobAction(j.id, "retry"),
        }));
      }
      actions.appendChild(el("button", {
        class: "ghost small", text: "Logs",
        onclick(e) { showJobLogs(j.id, e.currentTarget); },
      }));
      card.appendChild(actions);
      out.appendChild(card);
    });
    if (active) startJobsPolling(); else { stopJobsPolling(); refreshTranscribedSet().then(renderLectures); }
  } catch (e) { out.textContent = errorText(e); }
}
function startJobsPolling() { if (!State.jobsTimer) State.jobsTimer = setInterval(loadJobs, 2000); }
function stopJobsPolling() { if (State.jobsTimer) { clearInterval(State.jobsTimer); State.jobsTimer = null; } }
$("jobs-refresh").addEventListener("click", loadJobs);

async function jobAction(id, action) {
  try {
    await postJSON("/api/jobs/" + id + "/" + action, {});
    toast(action === "retry" ? "Retrying job…" : "Job canceled.", "ok");
    if (action === "retry") startJobsPolling();
    loadJobs();
  } catch (e) { toastError(e); }
}

async function showJobLogs(id, btn) {
  const card = btn?.closest(".card");
  const existing = card?.querySelector(".job-logs");
  if (existing) {
    existing.remove();
    if (btn) btn.textContent = "Logs";
    return;
  }
  try {
    const data = await api("/api/jobs/" + id + "/logs");
    const pre = el("pre", { class: "job-logs", text: data.logs || "(no logs yet)" });
    if (card) card.appendChild(pre);
    if (btn) btn.textContent = "Hide logs";
  } catch (e) { toastError(e); }
}

// ---- materials ------------------------------------------------------------

async function browse(path) {
  const out = $("materials-results");
  if (!path) { toast("Enter a folder path.", "warn"); return; }
  $("materials-path").value = path;
  remember("matpath", path);
  out.textContent = "Listing…";
  try {
    const data = await api("/api/materials?path=" + encodeURIComponent(path));
    clear(out);
    out.appendChild(el("p", { class: "muted", text: data.path }));
    if (!data.entries.length) { out.appendChild(el("p", { class: "empty", text: "(empty folder)" })); return; }
    data.entries.forEach((e) => {
      const row = el("div", { class: "list-item" + (e.is_dir ? " clickable" : "") }, [
        el("span", { class: "li-label" }, [icon(e.is_dir ? "folder" : "file"), " " + e.name]),
        el("span", { class: "muted", text: e.size_human }),
      ]);
      if (e.is_dir) row.addEventListener("click", () => browse(e.path));
      out.appendChild(row);
    });
  } catch (e) { out.textContent = errorText(e); }
}
// Moodle course parser
$("moodle-go").addEventListener("click", async () => {
  const out = $("moodle-results");
  const path = $("moodle-path").value.trim();
  if (!path) { toast("Enter the Moodle course folder/file path.", "warn"); return; }
  remember("moodlepath", path);
  out.textContent = "Parsing…";
  try {
    const d = await api("/api/moodle/parse", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    clear(out);
    out.appendChild(el("p", { class: "ok-text", text: `${d.title || d.code || "Course"}` }));
    if (d.code) out.appendChild(el("p", { class: "muted", text: "Code: " + d.code }));
    const actions = el("div", { class: "row" }, [
      el("button", { class: "tag", text: "use as course name",
        onclick: () => { setCourse(d.title || d.code); toast("Course name set.", "ok"); } }),
      el("button", { class: "tag", text: "save outline as source",
        onclick: () => saveMoodleOutline(path) }),
    ]);
    out.appendChild(actions);
    const summary = [
      `${d.section_count} section(s)`,
      d.activity_count ? `${d.activity_count} activity(ies)` : null,
      d.resource_count ? `${d.resource_count} document(s)` : null,
    ].filter(Boolean).join(" · ");
    out.appendChild(el("p", { class: "muted", text: summary }));
    d.sections.forEach((s) => {
      const tag = s.week != null ? `Week ${s.week}` : "";
      out.appendChild(el("div", { class: "list-item" }, [
        el("span", { class: "li-label", text: s.name }),
        el("span", { class: "muted", text: tag }),
      ]));
    });
    if (d.activities && d.activities.length) {
      out.appendChild(el("p", { class: "muted", text: "Activities & resources:" }));
      d.activities.forEach((a) => out.appendChild(el("div", { class: "list-item" }, [
        el("span", { class: "li-label", text: a.name }),
        el("span", { class: "badge", text: a.kind_label }),
      ])));
    }
  } catch (e) { out.textContent = errorText(e); toastError(e); }
});
async function saveMoodleOutline(path) {
  try {
    const d = await api("/api/moodle/parse", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, save_outline: true }),
    });
    toast("Saved outline → " + (d.saved_as || "output folder"), "ok");
  } catch (e) { toastError(e); }
}

// Notion export - render a conversion result into #notion-results
function renderNotionResult(d) {
  const out = $("notion-results");
  clear(out);
  out.appendChild(el("p", { class: "ok-text", text: `Converted ${d.count} page(s) to ${d.dest}.` }));
  if (d.combined) out.appendChild(el("div", {}, [
    el("button", { class: "tag", text: "view notion_pack.md", onclick: () => { viewTranscript(d.combined); showTab("library"); } }),
  ]));
  d.files.forEach((f) => out.appendChild(el("div", { class: "list-item" }, [
    el("span", { class: "li-label", text: f }),
    el("button", { class: "tag", text: "view", onclick: () => { viewTranscript(f); showTab("library"); } }),
  ])));
  toast(`Converted ${d.count} Notion page(s).`, "ok");
}

// Notion export - upload a .zip / .html directly
$("notion-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const out = $("notion-results");
  out.textContent = `Importing ${file.name}…`;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const d = await api("/api/notion/upload?combined=" + ($("notion-combined").checked ? "true" : "false"),
      { method: "POST", body: fd });
    renderNotionResult(d);
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { ev.target.value = ""; }
});

// Notion export converter
$("notion-go").addEventListener("click", async () => {
  const out = $("notion-results");
  const path = $("notion-path").value.trim();
  if (!path) { toast("Enter a Notion .zip, .html file or export folder.", "warn"); return; }
  remember("notionpath", path);
  const btn = $("notion-go");
  btn.disabled = true; out.textContent = "Converting…";
  try {
    const d = await api("/api/notion/convert", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, combined: $("notion-combined").checked }),
    });
    renderNotionResult(d);
  } catch (e) { out.textContent = errorText(e); toastError(e); }
  finally { btn.disabled = false; }
});

$("materials-go").addEventListener("click", () => browse($("materials-path").value.trim()));
$("materials-up").addEventListener("click", () => {
  const p = $("materials-path").value.trim().replace(/[\\/]+$/, "");
  const parent = p.replace(/[\\/][^\\/]*$/, "");
  if (parent && parent !== p) browse(parent);
});

// ---- init -----------------------------------------------------------------

function restore() {
  // theme: saved choice, else follow the OS preference
  const savedTheme = recall("theme") ||
    (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  applyTheme(savedTheme);
  initLevel();

  if ($("feed-source")) $("feed-source").value = recall("feed");
  $("pdf-path").value = recall("pdfpath");
  $("materials-path").value = recall("matpath");
  $("moodle-path").value = recall("moodlepath");
  $("notion-path").value = recall("notionpath");
  if ($("sem-paper-codes")) $("sem-paper-codes").value = recall("sem-paper-codes");
  rememberPaperCodes(recall("sem-paper-codes"));
  renderPaperChips();
  // We no longer restore mqcookies from localStorage; it's ephemeral.
  const course = recall("course");
  if (course) { $("course-input").value = course; $("course-name-main").value = course; }
  try {
    const s = JSON.parse(recall("settings") || "{}");
    if (s.model && $("opt-model")) $("opt-model").value = s.model;
    if (s.language && $("opt-language")) $("opt-language").value = s.language;
    if (s.device && $("opt-device")) $("opt-device").value = s.device;
    if (typeof s.audio_only === "boolean" && $("opt-audio")) $("opt-audio").checked = s.audio_only;
    if (typeof s.skip_existing === "boolean" && $("opt-skip")) $("opt-skip").checked = s.skip_existing;
  } catch (_) {}
}

loadStatus().then(() => {
  restore();                   // now the engine/model selects are populated
  updatePanelContext("home");
  loadDashboard();
  initMoodleQuick();
  refreshOllama();             // pre-populate the local-AI panel
  wireDropZone($("notion-drop"), $("notion-file"));
  wireDropZone($("sem-schedule-drop"), $("sem-schedule-file"));
  $("sem-paper-codes")?.addEventListener("change", (e) => {
    rememberPaperCodes(e.target.value);
    renderPaperChips();
  });
  $("getting-started-dismiss")?.addEventListener("click", () => {
    remember("gs-dismissed", "1");
    $("getting-started")?.classList.add("hidden");
  });
  $("gs-goto-moodle")?.addEventListener("click", () => showTab("moodle-quick"));
  $("shortcuts-help")?.addEventListener("click", showShortcuts);
});
loadCourses();


// ---- Moodle "Simple" guided flow ------------------------------------------
let mqRecommend = null, mqInited = false;
let _mqConnected = false;   // true once we have a stored token for the site

async function initMoodleQuick() {
  if (mqInited) return; mqInited = true;
  try {
    mqRecommend = await api("/api/transcribe/recommend");
    const r = $("mq-recommend");
    if (r) r.textContent = mqRecommend.ready
      ? `Recommended settings: ${mqRecommend.rationale}`
      : `Transcription is unavailable: ${mqRecommend.reason} Documents can still be imported.`;
    // Populate the advanced-settings engine dropdown from what is installed.
    const sel = $("mq-adv-engine");
    if (sel) {
      clear(sel);
      sel.appendChild(el("option", { value: "", text: "Recommended" }));
      const engines = (State.status && State.status.engines)
        ? Object.entries(State.status.engines).filter(([, v]) => v).map(([k]) => k) : [];
      engines.forEach((eng) => sel.appendChild(el("option", { value: eng, text: eng })));
    }
  } catch (_) {}
}

// ---- connect helper (Moodle web-service API) ------------------------------
function setConnectStatus(state, text) {
  const box = $("mq-connect-status"); if (!box) return;
  box.classList.remove("hidden");
  const dot = box.querySelector(".dot");
  if (dot) dot.className = "dot " + state;     // off | warn | on
  const t = $("mq-connect-text");
  if (t) t.textContent = text;
}

// SSO polling - started when the user opens the sign-in page.  Polls
// /api/moodle/sso-poll every 2 s; the OS protocol handler (Windows) calls
// /api/moodle/sso-callback when courseassistant:// lands, and the poll picks it up.
let _ssoPollTimer = null;
let _ssoStartedAt = 0;

function _stopSsoPoll() {
  if (_ssoPollTimer) { clearInterval(_ssoPollTimer); _ssoPollTimer = null; }
  $("mq-sso-waiting")?.classList.add("hidden");
}

function _startSsoPoll() {
  _stopSsoPoll();
  _ssoStartedAt = Date.now();
  $("mq-sso-waiting")?.classList.remove("hidden");
  _ssoPollTimer = setInterval(async () => {
    if (Date.now() - _ssoStartedAt > 5 * 60 * 1000) { _stopSsoPoll(); return; }
    try {
      const d = await api("/api/moodle/sso-poll");
      if (d.token) {
        _stopSsoPoll();
        toast("Signed in - connecting…", "ok");
        await _moodleConnect($("mq-url").value.trim(), d.token);
      }
    } catch (_) { /* ignore poll errors */ }
  }, 2000);
}

$("mq-sso-cancel")?.addEventListener("click", (e) => {
  e.preventDefault();
  _stopSsoPoll();
});

// "Open sign-in page ↗" - Option B: browser SSO launch flow.
// IMPORTANT: window.open must run synchronously inside the click handler or the
// browser treats it as a programmatic popup and blanks/blocks it. So we build the
// launch.php URL on the client (no await before opening the tab).
$("mq-launch-sso")?.addEventListener("click", () => {
  const raw = $("mq-url").value.trim();
  if (!raw) { toast("Enter your Moodle site URL first.", "warn"); return; }
  let base;
  try {
    base = new URL(/^https?:\/\//i.test(raw) ? raw : "https://" + raw);
  } catch (_) {
    toast("Enter a valid Moodle URL first (e.g. https://moodle.example.edu).", "warn");
    return;
  }
  // urlscheme=courseassistant: our OS handler intercepts courseassistant://token=…
  // and POSTs it to /api/moodle/sso-callback so the poll below picks it up.
  const launch = base.origin +
    "/admin/tool/mobile/launch.php?service=moodle_mobile_app" +
    "&passport=courseassistant&urlscheme=courseassistant";
  // No noopener: a brand-new about:blank tab that we immediately navigate is fine,
  // and some browsers blank a noopener tab opened to a cross-origin redirect chain.
  window.open(launch, "_blank");
  _startSsoPoll();
});

async function _waitMoodleJob(jobId, { onTick } = {}) {
  for (let i = 0; i < 300; i++) {
    const job = await api(`/api/jobs/${jobId}`);
    if (onTick) onTick(job);
    if (job.status === "done") return job;
    if (job.status === "error" || job.status === "failed") {
      const err = new Error(job.error || "Moodle job failed");
      err.job = job;
      throw err;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("Moodle job timed out — check the Jobs panel for progress.");
}

function _moodleCookies() {
  return ($("mq-cookies")?.value || $("sem-moodle-cookies")?.value || "").trim();
}

async function _moodleConnect(url, token) {
  if (!url) { toast("Enter your Moodle site link first.", "warn"); return; }
  if (!token) { toast("No token received. Try signing in again.", "warn"); return; }
  setConnectStatus("warn", "Connecting to Moodle…");
  try {
    const queued = await postJSON("/api/moodle/connect", {
      url, token, cookies: _moodleCookies(),
    });
    showTab("jobs");
    startJobsPolling();
    const job = await _waitMoodleJob(queued.id, {
      onTick(j) {
        if (j.stage) setConnectStatus("warn", `Connecting… (${j.stage})`);
      },
    });
    const d = job.result || {};
    const courses = d.courses || [];
    const sel = $("mq-course-select"); clear(sel);
    if (!courses.length) {
      setConnectStatus("warn", `Connected to ${d.sitename || d.host}, but no enrolled courses were found.`);
    } else {
      courses.forEach((c) => sel.appendChild(
        el("option", { value: String(c.id), text: c.fullname || c.shortname || ("Course " + c.id) })));
      const m = url.match(/[?&]id=(\d+)/);
      if (m && courses.some((c) => String(c.id) === m[1])) sel.value = m[1];
      setConnectStatus("on", `Connected to ${d.sitename || d.host} as ${d.fullname || "you"}. ${courses.length} course(s) available.`);
      $("mq-course-pick").classList.remove("hidden");
    }
    _mqConnected = true;
    _mqBaseUrl = d.base_url || url;
    applyDetectedPaperCodes(d.paper_codes || []);
    if (d.calendar_discovered && $("sem-calendar-masked")) {
      $("sem-calendar-masked").textContent = `Calendar URL discovered: ${d.calendar_url}`;
    }
    toast("Connected to Moodle.", "ok");
  } catch (e) {
    const msg = e.message || "";
    const display = msg.startsWith("SSO_REJECTED:") ? msg.slice("SSO_REJECTED:".length).trim() : msg;
    setConnectStatus("off", display);
    toast(display, "err");
  }
}
let _mqBaseUrl = "";

// Render the outcome of an API import: a labelled, unambiguous breakdown so
// lectures, documents, links and activities are never confused with each other.
function renderMqImport(data, { grabLectures = true, grabTranscripts = true, grabDocs = true } = {}) {
  const out = $("mq-import-result"); clear(out);
  const c = data.course || {};
  const local = c.local_course;
  if (local) {
    setCourse(local.code || local.name || c.code || c.fullname);
    loadCourses();
  } else if (c.code || c.fullname) {
    setCourse(c.code || c.fullname);
  }
  const counts = data.counts || {};
  const res = data.resources || {};
  const conv = data.converted || {};
  const imgs = (conv.files || []).reduce((n, f) => n + (f.images || 0), 0);
  const feeds = data.panopto_feeds || [];

  const bits = [`Imported <strong>${c.fullname || c.code || "course"}</strong> - ${counts.sections || 0} section(s)`];
  if (local) bits.push(`Active course set to ${local.code || local.name}`);
  if (grabDocs)
    bits.push(`${res.downloaded || 0} of ${counts.documents || 0} document(s) downloaded, ${conv.count || 0} converted to Markdown`
      + (imgs ? ` (${imgs} image(s) attached)` : ""));
  if (grabLectures || grabTranscripts)
    bits.push(`${counts.lectures || 0} lecture(s), ${feeds.length} transcribable feed(s)`);
  out.appendChild(el("div", { class: "ok-box", html: bits.join(" · ") + "." }));

  // Labelled tallies - each item type is counted distinctly from the typed API.
  out.appendChild(el("div", { class: "row mq-counts" }, [
    el("span", { class: "tag", text: `${counts.lectures || 0} lectures` }),
    el("span", { class: "tag", text: `${counts.documents || 0} documents` }),
    el("span", { class: "tag", text: `${counts.links || 0} links` }),
    el("span", { class: "tag", text: `${counts.activities || 0} activities` }),
  ]));

  if ((res.errors || []).length)
    out.appendChild(el("p", { class: "muted small",
      text: `${res.errors.length} document(s) could not be downloaded.` }));

  if (!feeds.length && (grabLectures || grabTranscripts) && (counts.lectures || 0) > 0)
    out.appendChild(el("p", { class: "muted small",
      text: `${counts.lectures} lecture link(s) were found, but no transcribable Panopto feed was `
        + "detected. Paste the Video podcast RSS link below, then open Speech to transcribe." }));

  State.mqFeeds = feeds;
  State.mqRecordings = [];
  renderMqFeeds();
  // Show the recordings step whenever recordings are wanted, so the user can
  // paste the Panopto RSS link even when the API auto-detected no feeds.
  $("mq-step-transcribe").classList.toggle("hidden", !(grabLectures || grabTranscripts));
  // Prefill the paste field with the best detected feed, if any.
  if (feeds.length && $("mq-panopto-url") && !$("mq-panopto-url").value.trim())
    $("mq-panopto-url").value = feeds[0];
  $("mq-step-export").classList.remove("hidden");
  applyDetectedPaperCodes(data.paper_codes || []);
  if (data.calendar_discovered && $("sem-calendar-masked")) {
    $("sem-calendar-masked").textContent = `Calendar URL discovered: ${data.calendar_url}`;
  }
  if ((data.warnings || []).length) {
    out.appendChild(el("div", { class: "warn-box",
      text: data.warnings.join(" · ") }));
  }
  // Browser mode: try auto Panopto discovery beyond Moodle-linked feeds
  if (mqImportMode === "browser" && !feeds.length) {
    postJSON("/api/panopto/discover", {
      moodle_url: _mqBaseUrl || ($("mq-url")?.value || ""),
      cookies: _moodleCookies(),
      use_playwright: true,
    }).then((d) => {
      if ((d.feeds || []).length && $("mq-panopto-url")) {
        $("mq-panopto-url").value = d.feeds[0];
        State.mqFeeds = d.feeds;
        renderMqFeeds();
        toast(`Discovered ${d.feeds.length} Panopto feed(s).`, "ok");
      }
    }).catch(() => {});
  }
  toast("Course imported.", "ok");
}

$("mq-import")?.addEventListener("click", async () => {
  if (!_mqConnected) { toast("Connect to Moodle first.", "warn"); return; }
  const sel = $("mq-course-select");
  const courseId = sel && sel.value ? parseInt(sel.value, 10) : 0;
  if (!courseId) { toast("Select a course to import.", "warn"); return; }
  // "Lectures & transcripts" is one toggle: lectures are only ever pulled as part
  // of the course (with transcription), never as a standalone import.
  const grabDocs = $("mq-grab-docs")?.checked ?? true;
  const grabTranscripts = $("mq-grab-transcripts")?.checked ?? true;
  const grabLectures = grabTranscripts;
  const keepImages = $("mq-images")?.checked ?? true;
  if (!grabDocs && !grabTranscripts) {
    toast("Select at least one thing to include: documents or lectures.", "warn"); return;
  }
  const btn = $("mq-import"); btn.disabled = true; btn.textContent = "Importing…";
  const out = $("mq-import-result"); clear(out);
  out.appendChild(el("p", { class: "import-loading" }, [
    el("span", { class: "mq-sso-spinner" }),
    " Reading the course from Moodle…",
  ]));
  try {
    const queued = await postJSON("/api/moodle/api-import", {
      url: _mqBaseUrl, course_id: courseId,
      grab_lectures: grabLectures || grabTranscripts,
      grab_docs: grabDocs, convert: true, keep_images: keepImages,
      create_course: true,
      use_browser: mqImportMode === "browser",
      cookies: _moodleCookies(),
    });
    showTab("jobs");
    startJobsPolling();
    const job = await _waitMoodleJob(queued.id, {
      onTick(j) {
        if (j.stage) {
          const stage = j.stage.replace(/_/g, " ");
          out.querySelector(".import-loading")?.replaceWith(
            el("p", { class: "import-loading" }, [
              el("span", { class: "mq-sso-spinner" }),
              ` ${stage}…`,
            ]),
          );
        }
      },
    });
    renderMqImport(job.result || {}, { grabLectures, grabTranscripts, grabDocs });
  } catch (e) {
    clear(out);
    const msg = e.job?.error || e.message || "Import failed";
    out.appendChild(el("div", { class: "warn-box", text: "Import failed: " + msg }));
    toast(msg, "err");
  } finally { btn.disabled = false; btn.textContent = "Import course"; }
});

function renderMqFeeds() {
  const box = $("mq-feeds"); if (!box) return; clear(box);
  const recs = State.mqRecordings || [];
  const feeds = State.mqFeeds || [];
  if (recs.length) {
    box.appendChild(el("p", { class: "ok-text",
      text: `${recs.length} recording(s) loaded from the Panopto feed:` }));
    recs.forEach((r) => box.appendChild(el("div", { class: "list-item" }, [
      el("span", { class: "li-label", text: r.title || r.safe_title || "recording" }),
      el("span", { class: "muted small", text: r.video_url ? "video + audio" : "audio" }),
    ])));
    return;
  }
  if (feeds.length) {
    box.appendChild(el("p", { class: "muted small",
      text: `${feeds.length} lecture feed(s) detected - paste the Panopto RSS link above to load them.` }));
    return;
  }
  box.appendChild(el("p", { class: "muted small", text:
    "No recordings loaded yet - paste the Panopto “Video podcast (RSS)” link above." }));
}

// Load recordings from a pasted Panopto podcast RSS URL.
async function loadPanoptoRecordings() {
  const url = $("mq-panopto-url")?.value.trim();
  if (!url) { toast("Paste the Panopto Video podcast (RSS) link first.", "warn"); return; }
  const btn = $("mq-panopto-load");
  if (btn) { btn.disabled = true; btn.textContent = "Loading…"; }
  try {
    const d = await postJSON("/api/moodle/panopto-feed", { source: url });
    State.mqRecordings = d.lectures || [];
    renderMqFeeds();
    toast(`Loaded ${State.mqRecordings.length} recording(s).`, "ok");
  } catch (e) {
    toastError(e);
    renderMqFeeds();
  } finally { if (btn) { btn.disabled = false; btn.textContent = "Load recordings"; } }
}
$("mq-panopto-load")?.addEventListener("click", loadPanoptoRecordings);

async function ensureMqRecordingsLoaded() {
  let recs = State.mqRecordings || [];
  if (!recs.length && $("mq-panopto-url")?.value.trim()) {
    await loadPanoptoRecordings();
    recs = State.mqRecordings || [];
  }
  return recs;
}

/** Shared STT enqueue used by Speech and Moodle “Queue now”. */
async function enqueueLectureList(lectures, { force = false, resultsEl = null } = {}) {
  if (!State.status || !State.status.any_engine) {
    toast("No transcription engine installed. Use Home → Environment to install Transcription.", "warn");
    return 0;
  }
  if (!lectures.length) {
    toast("No recordings to queue.", "warn");
    return 0;
  }
  const settings = gatherSettings();
  settings.audio_only = true;
  if (force) {
    settings.force = true;
    settings.skip_existing = false;
  }
  remember("settings", JSON.stringify(settings));
  let queued = 0;
  const out = resultsEl || $("stt-results");
  for (const lec of lectures) {
    try {
      const job = await postJSON("/api/transcribe", { ...settings, lecture: lec });
      queued++;
      if (out && job?.id) {
        out.appendChild(el("p", {
          class: "ok-text",
          text: `Queued “${lec.title || lec.safe_title || "recording"}” · job ${String(job.id).slice(0, 8)}…`,
        }));
      }
    } catch (e) {
      toast(`Could not queue "${lec.title || "recording"}": ${errorText(e)}`, "err");
    }
  }
  if (queued) {
    toast(`Queued ${queued} recording(s). Track progress in Jobs.`, "ok");
    showTab("jobs");
    startJobsPolling();
  }
  return queued;
}

async function openMqInSpeech() {
  const recs = await ensureMqRecordingsLoaded();
  if (!recs.length) {
    toast("Paste the Panopto RSS link and load the recordings first.", "warn");
    return;
  }
  // Preload Speech with the Moodle recording list (single STT settings home).
  State.lectures = recs.slice();
  const first = recs[0];
  if ($("stt-media-url") && first) {
    $("stt-media-url").value = first.url || first.video_url || "";
  }
  showTab("tts");
  showSpeechMode("transcribe");
  toast(`${recs.length} recording(s) ready in Speech. Adjust settings, then Transcribe.`, "ok");
}

async function autoTranscribeMq() {
  const recs = await ensureMqRecordingsLoaded();
  if (!recs.length) {
    toast("Paste the Panopto RSS link and load the recordings first.", "warn");
    return;
  }
  State.lectures = recs.slice();
  const overwrite = $("mq-overwrite")?.checked === true;
  await enqueueLectureList(recs, { force: overwrite });
}
$("mq-autotranscribe")?.addEventListener("click", autoTranscribeMq);
$("mq-open-speech")?.addEventListener("click", openMqInSpeech);

$("mq-export-suites")?.addEventListener("click", () => {
  showTab("export");
  $("suite-exports-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
});
$("mq-export-ai")?.addEventListener("click", () => mqExport("all"));
async function mqExport(kind) {
  const out = $("mq-export-result");
  const dest = await pickFolder(`Choose a folder for the ${kind === "notebooklm" ? "NotebookLM" : "general-AI"} export`);
  if (dest === null) return;
  clear(out);
  try {
    const path = kind === "notebooklm" ? "/api/export/notebooklm" : "/api/export/all";
    const body = { combined: true, course: currentCourse(), output_dir: dest };
    const data = await postJSON(path, body);
    const exportDest = data.output_dir || data.combined || data.dest || data.path || "the library";
    out.appendChild(el("div", { class: "ok-box", html:
      `Exported for <strong>${kind === "notebooklm" ? "NotebookLM" : "a general AI assistant"}</strong> to <code>${exportDest}</code>.` }));
    toast("Export complete.", "ok");
  } catch (e) { out.appendChild(el("div", { class: "warn-box", text: "Export failed: " + e.message })); }
}

// ---- Moodle import mode + capability matrix ------------------------------
let mqImportMode = "browser";

async function renderMqCapabilityMatrix(mode) {
  const host = $("mq-capability-matrix");
  if (!host) return;
  try {
    const data = await api(`/api/moodle/capabilities?mode=${encodeURIComponent(mode)}`);
    clear(host);
    const table = el("table", { class: "mq-cap-table", "data-active-mode": mode });
    table.appendChild(el("thead", {}, [
      el("tr", {}, [
        el("th", { text: "Capability" }),
        el("th", { class: mode === "api" ? "mq-cap-active" : "", text: "API" }),
        el("th", { class: mode === "browser" ? "mq-cap-active" : "", text: "Browser" }),
      ]),
    ]));
    const tbody = el("tbody");
    for (const row of (data.matrix || [])) {
      tbody.appendChild(el("tr", {}, [
        el("td", { text: row.capability }),
        el("td", { class: mode === "api" ? "mq-cap-active" : "",
          text: String(row.api === true ? "Yes" : row.api) }),
        el("td", { class: mode === "browser" ? "mq-cap-active" : "",
          text: String(row.browser === true ? "Yes" : row.browser) }),
      ]));
    }
    table.appendChild(tbody);
    host.appendChild(table);
    if (data.playwright_available === false && mode === "browser") {
      host.appendChild(el("p", { class: "hint",
        text: "Playwright is not installed. Browser mode uses cookie/HTML scrape first, "
          + "then falls back to the API. For forums & Panopto-only pages run: "
          + "pip install -r requirements-browser.txt && playwright install chromium" }));
    } else if (mode === "browser") {
      host.appendChild(el("p", { class: "hint",
        text: "Browser mode (recommended) crawls Moodle with your session cookies, "
          + "discovers calendar & Panopto feeds, and falls back to the API when needed." }));
    } else {
      host.appendChild(el("p", { class: "hint",
        text: "API mode uses the Moodle web-service for exact course data when browser scrape is not needed." }));
    }
  } catch (e) {
    host.textContent = "Could not load capability matrix.";
  }
}

function setMqImportMode(mode) {
  mqImportMode = mode === "browser" ? "browser" : "api";
  document.querySelectorAll("[data-mq-mode]").forEach((btn) => {
    const active = btn.dataset.mqMode === mqImportMode;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
  renderMqCapabilityMatrix(mqImportMode);
}

$("mq-mode-api")?.addEventListener("click", () => setMqImportMode("api"));
$("mq-mode-browser")?.addEventListener("click", () => setMqImportMode("browser"));
setMqImportMode("browser");

function applyDetectedPaperCodes(codes) {
  if (!Array.isArray(codes) || !codes.length) return;
  const normalized = codes.map((c) => String(c).split("-")[0].toUpperCase()).filter(Boolean);
  const existing = getSelectedPaperCodes();
  const known = new Set([...(recallJson("sem-paper-known") || []), ...normalized, ...existing]);
  rememberJson("sem-paper-known", [...known]);
  const merged = [...new Set([...existing, ...normalized])];
  setSelectedPaperCodes(merged);
  renderPaperChips();
}

function recallJson(key) {
  try { return JSON.parse(recall(key) || "null"); } catch (_) { return null; }
}
function rememberJson(key, val) {
  try { remember(key, JSON.stringify(val)); } catch (_) { /* ignore */ }
}

function getSelectedPaperCodes() {
  const hidden = $("sem-paper-codes");
  const raw = (hidden?.value || recall("sem-paper-codes") || "").trim();
  return raw.split(/[,\s]+/).map((s) => s.trim().toUpperCase().split("-")[0]).filter(Boolean);
}

function setSelectedPaperCodes(codes) {
  const uniq = [...new Set((codes || []).map((c) => String(c).toUpperCase().split("-")[0]).filter(Boolean))];
  const joined = uniq.join(", ");
  if ($("sem-paper-codes")) $("sem-paper-codes").value = joined;
  rememberPaperCodes(joined);
}

function renderPaperChips() {
  const host = $("sem-paper-chips");
  if (!host) return;
  clear(host);
  const selected = new Set(getSelectedPaperCodes());
  const known = new Set([...(recallJson("sem-paper-known") || []), ...selected]);
  if (!known.size) {
    host.appendChild(el("span", { class: "hint", text: "No paper codes yet — add one below or import from Moodle." }));
    return;
  }
  [...known].sort().forEach((code) => {
    const on = selected.has(code);
    const chip = el("button", {
      type: "button",
      class: "paper-chip",
      "aria-pressed": on ? "true" : "false",
      text: code,
    });
    chip.addEventListener("click", (ev) => {
      if (ev.target.closest(".chip-x")) return;
      const next = new Set(getSelectedPaperCodes());
      if (next.has(code)) next.delete(code); else next.add(code);
      setSelectedPaperCodes([...next]);
      renderPaperChips();
    });
    const x = el("span", { class: "chip-x", text: "×", title: "Remove", role: "button" });
    x.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const nextSel = getSelectedPaperCodes().filter((c) => c !== code);
      setSelectedPaperCodes(nextSel);
      const knownList = (recallJson("sem-paper-known") || []).filter((c) => c !== code);
      rememberJson("sem-paper-known", knownList);
      renderPaperChips();
    });
    chip.appendChild(x);
    host.appendChild(chip);
  });
}

function addPaperCodeFromInput() {
  const raw = ($("sem-paper-add")?.value || "").trim().toUpperCase().split("-")[0];
  if (!raw) return;
  const known = new Set([...(recallJson("sem-paper-known") || []), ...getSelectedPaperCodes(), raw]);
  rememberJson("sem-paper-known", [...known]);
  setSelectedPaperCodes([...getSelectedPaperCodes(), raw]);
  if ($("sem-paper-add")) $("sem-paper-add").value = "";
  renderPaperChips();
}

$("sem-paper-add-btn")?.addEventListener("click", addPaperCodeFromInput);
$("sem-paper-add")?.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") { ev.preventDefault(); addPaperCodeFromInput(); }
});

// ---- remove course files (with confirmation) ------------------------------

$("course-clear")?.addEventListener("click", async () => {
  const ok = await confirmModal(
    "Clear the entire library?",
    "This permanently deletes every transcript, document, Notion page, and generated export in this "
    + "workspace, including files from other courses. The database, saved settings, and backups are kept. "
    + "This cannot be undone.",
    { confirmText: "Remove files", danger: true });
  if (!ok) return;
  const out = $("course-clear-results");
    out.textContent = "Clearing the library…";
  try {
    const d = await postJSON("/api/library/clear", {});
    out.textContent = `Removed ${d.files} file(s) across ${d.folders} folder(s).`;
    toast("Library cleared.", "ok");
    loadTranscripts();
    loadDashboard();
  } catch (e) { out.textContent = errorText(e); toastError(e); }
});

// ---- Speech hub (STT + TTS) ------------------------------------------------

function formatCacheBytes(n) {
  const b = Number(n) || 0;
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 * 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
  return `${(b / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

async function refreshSpeechPanel() {
  if (!$("stt-main")) return;
  await refreshSttCapabilities();
  await refreshSttRoute();
}

async function refreshSttCapabilities() {
  const strip = $("stt-cap-strip");
  if (!strip) return;
  try {
    _sttCaps = await api("/api/stt/capabilities");
    const engines = (_sttCaps.engines || []).filter((e) => e.probe && e.probe.installed);
    const engNames = engines.map((e) => e.display_name || e.name).slice(0, 4);
    const cache = _sttCaps.cache || {};
    const cached = (cache.models || []).length;
    const parts = [
      engNames.length ? `Engines: ${engNames.join(", ")}` : "No STT engines installed",
      `Cache: ${formatCacheBytes(cache.bytes)} · ${cached} model(s)`,
      _sttCaps.privacy || "Local/offline only",
    ];
    strip.textContent = parts.join(" · ");
  } catch (e) {
    strip.textContent = "Could not load STT capabilities: " + errorText(e);
  }
}

async function refreshSttRoute() {
  const textEl = $("stt-route-text");
  const status = $("stt-route-status");
  if (!textEl) return;
  const lang = ($("stt-language")?.value || "auto").trim() || "auto";
  const captionFirst = $("stt-caption-first") ? $("stt-caption-first").checked : true;
  try {
    const data = await postJSON("/api/stt/route", {
      profile: sttProfile(),
      language: lang,
      caption_first: captionFirst,
      has_usable_captions: false,
    });
    const route = data.route || {};
    const est = data.estimate || {};
    const estBit = est.disk_mb != null
      ? ` · ~${est.disk_mb} MB model${est.cached ? " (cached)" : ""}`
      : "";
    textEl.textContent = `${route.reason || "Routed."} → ${route.engine || "?"}/${route.model || "?"}${estBit}`;
    if (status) {
      const dot = status.querySelector(".dot");
      if (dot) { dot.classList.remove("off"); dot.classList.add("on"); }
    }
  } catch (e) {
    textEl.textContent = "Could not route: " + errorText(e);
    if (status) {
      const dot = status.querySelector(".dot");
      if (dot) { dot.classList.add("off"); dot.classList.remove("on"); }
    }
  }
}

document.querySelectorAll("#stt-profiles .seg[data-profile]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#stt-profiles .seg[data-profile]").forEach((b) => {
      const on = b === btn;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", String(on));
    });
    refreshSttRoute();
  });
});
$("stt-language")?.addEventListener("change", () => refreshSttRoute());
$("stt-language")?.addEventListener("blur", () => refreshSttRoute());
$("stt-caption-first")?.addEventListener("change", () => refreshSttRoute());
$("stt-diarization")?.addEventListener("change", () => refreshSttRoute());

async function enqueueSpeechTranscribe() {
  const out = $("stt-results");
  if (out) clear(out);
  const indexes = typeof checkedIndexes === "function" ? checkedIndexes() : [];
  const media = ($("stt-media-url")?.value || "").trim();
  const overwrite = $("mq-overwrite")?.checked === true;

  // Prefer explicit lecture selection, then Moodle-preloaded lectures, then a media URL.
  let lectures = [];
  if (indexes.length && State.lectures.length) {
    lectures = indexes.map((i) => State.lectures[i]).filter(Boolean);
  } else if ((State.mqRecordings || []).length && !media) {
    lectures = State.mqRecordings.slice();
  } else if (State.lectures.length && !media) {
    lectures = State.lectures.slice();
  }

  if (lectures.length) {
    await enqueueLectureList(lectures, { force: overwrite, resultsEl: out });
    return;
  }
  if (media) {
    const title = media.split(/[\\/]/).pop() || "media";
    await enqueueLectureList([{ title, url: media }], { force: overwrite, resultsEl: out });
    return;
  }
  toast("Enter a media URL / path, or load recordings from Moodle → Transcribe in Speech.", "warn");
}

$("stt-transcribe")?.addEventListener("click", () => enqueueSpeechTranscribe());

// ---- Live mic → /ws/stt/live ----------------------------------------------

const LiveStt = {
  ws: null,
  stream: null,
  audioCtx: null,
  processor: null,
  source: null,
  provisional: "",
  finals: [],
};

function setLiveButtons({ start, pause, resume, stop }) {
  if ($("stt-live-start")) $("stt-live-start").disabled = !start;
  if ($("stt-live-pause")) $("stt-live-pause").disabled = !pause;
  if ($("stt-live-resume")) $("stt-live-resume").disabled = !resume;
  if ($("stt-live-stop")) $("stt-live-stop").disabled = !stop;
}

function renderLiveResults() {
  const out = $("stt-results");
  if (!out) return;
  clear(out);
  if (LiveStt.finals.length) {
    out.appendChild(el("p", { class: "ok-text", text: LiveStt.finals.join(" ") }));
  }
  if (LiveStt.provisional) {
    out.appendChild(el("p", { class: "hint", text: "… " + LiveStt.provisional }));
  }
}

function floatTo16BitPCM(float32) {
  const buf = new ArrayBuffer(float32.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

async function startLiveStt() {
  if (!navigator.mediaDevices?.getUserMedia) {
    toast("Microphone capture is not available in this browser.", "warn");
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    toast("Microphone access denied or unavailable. You can still transcribe files.", "warn");
    return;
  }

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${proto}//${location.host}/ws/stt/live`;
  let ws;
  try {
    ws = new WebSocket(wsUrl);
  } catch (e) {
    stream.getTracks().forEach((t) => t.stop());
    toast("Could not open live STT websocket.", "err");
    return;
  }

  LiveStt.stream = stream;
  LiveStt.ws = ws;
  LiveStt.provisional = "";
  LiveStt.finals = [];
  setLiveButtons({ start: false, pause: false, resume: false, stop: false });

  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    const lang = ($("stt-language")?.value || "en").trim() || "en";
    ws.send(JSON.stringify({
      op: "start",
      language: lang === "auto" ? "en" : lang,
    }));
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    const event = msg.event;
    if (event === "ready") {
      setLiveButtons({ start: false, pause: true, resume: false, stop: true });
      const out = $("stt-results");
      if (out) {
        clear(out);
        out.appendChild(el("p", {
          class: "hint",
          text: `Live ready · ${msg.engine || "?"}/${msg.model || "?"} — ${msg.reason || ""}`,
        }));
      }
      _beginPcmCapture();
    } else if (event === "provisional" || (event === "partial")) {
      LiveStt.provisional = msg.text || "";
      renderLiveResults();
    } else if (event === "final" || msg.final === true) {
      if (msg.text) LiveStt.finals.push(msg.text);
      LiveStt.provisional = "";
      renderLiveResults();
    } else if (event === "paused") {
      setLiveButtons({ start: false, pause: false, resume: true, stop: true });
    } else if (event === "resumed") {
      setLiveButtons({ start: false, pause: true, resume: false, stop: true });
    } else if (event === "done") {
      const text = msg.result?.text || LiveStt.finals.join(" ");
      const out = $("stt-results");
      if (out) {
        clear(out);
        out.appendChild(el("p", { class: "ok-text", text: text || "(no speech captured)" }));
      }
      _teardownLiveCapture(false);
      setLiveButtons({ start: true, pause: false, resume: false, stop: false });
    } else if (event === "error") {
      toast("Live STT: " + (msg.error || "unknown error"), "err");
    } else if (event === "backpressure") {
      /* drop — server cleared buffer */
    }
  };
  ws.onerror = () => {
    toast("Live STT connection error.", "err");
  };
  ws.onclose = () => {
    _teardownLiveCapture(false);
    setLiveButtons({ start: true, pause: false, resume: false, stop: false });
  };
}

function _beginPcmCapture() {
  if (!LiveStt.stream) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    const source = ctx.createMediaStreamSource(LiveStt.stream);
    // ScriptProcessor is deprecated but widely available; fine for this pragmatic path.
    const processor = ctx.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (e) => {
      if (!LiveStt.ws || LiveStt.ws.readyState !== WebSocket.OPEN) return;
      const input = e.inputBuffer.getChannelData(0);
      LiveStt.ws.send(floatTo16BitPCM(input));
    };
    source.connect(processor);
    const mute = ctx.createGain();
    mute.gain.value = 0;
    processor.connect(mute);
    mute.connect(ctx.destination);
    LiveStt.audioCtx = ctx;
    LiveStt.source = source;
    LiveStt.processor = processor;
  } catch (e) {
    toast("Could not start audio capture: " + errorText(e), "err");
    stopLiveStt();
  }
}

function _teardownLiveCapture(closeWs) {
  try { LiveStt.processor?.disconnect(); } catch (_) {}
  try { LiveStt.source?.disconnect(); } catch (_) {}
  try { LiveStt.audioCtx?.close(); } catch (_) {}
  LiveStt.processor = null;
  LiveStt.source = null;
  LiveStt.audioCtx = null;
  if (LiveStt.stream) {
    LiveStt.stream.getTracks().forEach((t) => t.stop());
    LiveStt.stream = null;
  }
  if (closeWs && LiveStt.ws) {
    try { LiveStt.ws.close(); } catch (_) {}
  }
  LiveStt.ws = null;
}

function pauseLiveStt() {
  if (LiveStt.ws?.readyState === WebSocket.OPEN) {
    LiveStt.ws.send(JSON.stringify({ op: "pause" }));
  }
}
function resumeLiveStt() {
  if (LiveStt.ws?.readyState === WebSocket.OPEN) {
    LiveStt.ws.send(JSON.stringify({ op: "resume" }));
  }
}
function stopLiveStt() {
  if (LiveStt.ws?.readyState === WebSocket.OPEN) {
    try { LiveStt.ws.send(JSON.stringify({ op: "stop" })); } catch (_) {}
  } else {
    _teardownLiveCapture(true);
    setLiveButtons({ start: true, pause: false, resume: false, stop: false });
  }
}

$("stt-live-start")?.addEventListener("click", () => startLiveStt());
$("stt-live-pause")?.addEventListener("click", () => pauseLiveStt());
$("stt-live-resume")?.addEventListener("click", () => resumeLiveStt());
$("stt-live-stop")?.addEventListener("click", () => stopLiveStt());

async function initTts() {
  const unavailBanner = $("tts-unavail");
  const voiceSel = $("tts-voice");
  const genBtn = $("tts-generate");
  try {
    const data = await api("/api/tts/status");
    const voices = data.voices || [];
    voiceSel.innerHTML = "";
    const opt = (v) => el("option", { value: v.id, text: v.label });
    const byGroup = new Map();
    for (const v of voices) {
      const g = v.group || "Voices";
      if (!byGroup.has(g)) byGroup.set(g, []);
      byGroup.get(g).push(v);
    }
    for (const [label, items] of byGroup) {
      const grp = el("optgroup", { label });
      items.forEach(v => grp.appendChild(opt(v)));
      voiceSel.appendChild(grp);
    }
    if (!data.available) {
      unavailBanner.classList.remove("hidden");
      genBtn.disabled = true;
    } else {
      genBtn.disabled = false;
    }
  } catch (e) {
    unavailBanner.textContent = "Could not reach TTS status endpoint: " + e.message;
    unavailBanner.classList.remove("hidden");
  }
}

// Browse for a .md file
$("tts-pick-file")?.addEventListener("click", async () => {
  try {
    const d = await postJSON("/api/pick-file", { title: "Choose a Markdown file", ext: ".md" });
    if (d.available === false) {
      const p = await promptModal("Enter path to the .md file", "C:\\path\\to\\notes.md");
      if (p) $("tts-md-path").value = p;
    } else if (d.path) {
      $("tts-md-path").value = d.path;
    }
  } catch (e) { toast("Could not open the file picker: " + errorText(e), "err"); }
});

// Generate button
$("tts-generate")?.addEventListener("click", async () => {
  const out = $("tts-results");
  const btn = $("tts-generate");
  const mdPath = $("tts-md-path").value.trim();
  const voice = $("tts-voice").value;
  const modelPath = $("tts-model-path")?.value.trim() || "hexgrad/Kokoro-82M";
  const speedRaw = parseFloat($("tts-speed")?.value || "1");
  const speed = Number.isFinite(speedRaw) ? Math.min(2, Math.max(0.5, speedRaw)) : 1.0;

  if (!mdPath) { toast("Choose a Markdown file first.", "warn"); return; }
  if (!voice)  { toast("Select a voice.", "warn"); return; }

  btn.disabled = true;
  clear(out);
  out.appendChild(el("p", { class: "hint", text: "Queuing TTS job…" }));

  try {
    const data = await postJSON("/api/tts/generate", {
      md_path: mdPath, voice, model_path: modelPath, speed,
    });
    clear(out);
    if (data.id) {
      // Background job queued — show status and poll
      const outputPath = data.output_path || "";
      const statusP = el("p", { class: "ok-text",
        text: `Job queued (${data.id.slice(0, 8)}…). Generating long-form audio — watch chunk progress in Jobs.` });
      out.appendChild(statusP);
      out.appendChild(el("button", { class: "tag", text: "Watch in Jobs",
        onclick: () => { showTab("jobs"); startJobsPolling(); } }));
      startJobsPolling();

      // Poll until done, then show download link
      if (outputPath) _pollTtsJob(data.id, outputPath, out);
    } else {
      _renderTtsResult(data, out);
    }
  } catch (e) {
    clear(out);
    out.appendChild(el("div", { class: "warn-box", text: "TTS error: " + e.message }));
    toast("Speech generation failed: " + errorText(e), "err");
  } finally {
    btn.disabled = false;
  }
});

async function _pollTtsJob(jobId, outputPath, out) {
  for (let i = 0; i < 360; i++) {          // max ~30 min at 5-second intervals
    await new Promise(r => setTimeout(r, 5000));
    try {
      const job = await api(`/api/jobs/${jobId}`);
      if (job.status === "done") {
        _renderTtsResult({ output_path: outputPath, voice: job.payload?.voice }, out);
        return;
      }
      if (job.status === "failed" || job.status === "interrupted") {
        out.appendChild(el("p", { class: "warn-box",
          text: `TTS job ${job.status}. Check the Jobs panel for details.` }));
        return;
      }
    } catch (_) { /* ignore transient fetch errors while polling */ }
  }
}

function _renderTtsResult(data, out) {
  clear(out);
  const path = data.output_path || "";
  const dur = data.duration_s != null ? ` (${data.duration_s}s)` : "";
  out.appendChild(el("p", { class: "ok-text", text: `Audio ready${dur}: ${path}` }));
  if (path) {
    const encodedPath = encodeURIComponent(path);
    out.appendChild(el("a", {
      href: `/api/tts/audio?path=${encodedPath}`,
      download: path.split(/[\\/]/).pop(),
      class: "tag",
      text: "Download WAV",
    }));
    out.appendChild(el("span", { text: " " }));
    // Inline audio player
    const audio = el("audio", { controls: true, style: "display:block;margin-top:8px" });
    audio.src = `/api/tts/audio?path=${encodedPath}`;
    out.appendChild(audio);
  }
  toast("Audio generated.", "ok");
}

// ---- semester planner -----------------------------------------------------

const Semester = { planId: null, scheduleId: null, lastOutline: null };

function renderSyncSteps(result) {
  const host = $("sem-sync-status");
  clear(host);
  if (!result) return;
  for (const s of result.steps || []) {
    const cls = s.status === "ok" ? "ok-text" : s.status === "error" ? "err-text" : "hint";
    host.appendChild(el("p", { class: cls, text: `${s.step}: ${s.detail}` }));
  }
  if (result.errors?.length) {
    for (const e of result.errors) {
      host.appendChild(el("p", { class: "err-text", text: e }));
    }
  }
}

function setExportLinks(planId) {
  const row = $("sem-export-row");
  if (row) row.hidden = false;
  if ($("sem-export-notion")) $("sem-export-notion").href = `/api/semester/plans/${planId}/export/notion.csv`;
  if ($("sem-export-obsidian")) $("sem-export-obsidian").href = `/api/semester/plans/${planId}/export/obsidian.zip`;
  if ($("sem-export-calendar")) $("sem-export-calendar").href = `/api/semester/plans/${planId}/export/calendar.ics`;
  if ($("sem-export-google")) $("sem-export-google").href = `/api/semester/plans/${planId}/export/google-calendar.csv`;
  updateExportPlanLinks(planId);
  const calHint = $("sem-export-calendar-hint");
  if (calHint) calHint.hidden = false;
}

$("sem-open-suites")?.addEventListener("click", () => {
  showTab("export");
  $("suite-exports-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
});

function rememberPaperCodes(raw) {
  const codes = raw.split(",").map((s) => s.trim()).filter(Boolean);
  if (!codes.length) return;
  remember("sem-paper-codes", codes.join(", "));
  let recent = [];
  try { recent = JSON.parse(recall("sem-paper-recent", "[]")); } catch (_) {}
  for (const c of codes) {
    const base = c.split("-")[0].toUpperCase();
    if (!recent.includes(base)) recent.unshift(base);
  }
  recent = recent.slice(0, 12);
  remember("sem-paper-recent", JSON.stringify(recent));
  const dl = $("sem-paper-suggestions");
  if (dl) {
    clear(dl);
    recent.forEach((c) => dl.appendChild(el("option", { value: c })));
  }
}

async function loadSemester() {
  const codes = recall("sem-paper-codes");
  if (codes && $("sem-paper-codes") && !$("sem-paper-codes").value) $("sem-paper-codes").value = codes;
  rememberPaperCodes($("sem-paper-codes")?.value || codes || "");
  renderPaperChips();
  try {
    // Backend stores codes detected during Moodle connect/import
    const prefs = await api("/api/settings");
    const detected = prefs?.["semester.paper_codes"];
    if (Array.isArray(detected) && detected.length) applyDetectedPaperCodes(detected);
  } catch (_) { /* optional */ }
  try {
    const data = await api("/api/semester/plans");
    const plans = (data.plans || []).slice().sort((a, b) =>
      String(b.created_at || "").localeCompare(String(a.created_at || "")));
    if (plans[0]) {
      Semester.planId = plans[0].id;
      setExportLinks(plans[0].id);
      const plan = await api(`/api/semester/plans/${plans[0].id}`);
      renderTimeline(plan);
    } else {
      renderTimeline(null);
    }
  } catch (_) { renderTimeline(null); }
  try {
    const d = await api("/api/semester/moodle/announcements");
    renderAnnouncements(d.announcements || []);
  } catch (_) { /* empty state */ }
  try {
    const cal = await api("/api/semester/moodle/calendar-url");
    const masked = $("sem-calendar-masked");
    if (masked) {
      masked.textContent = cal.configured
        ? `Saved: ${cal.masked_url}`
        : "Paste your Moodle calendar export URL once — stored securely on this machine.";
    }
  } catch (_) { /* optional */ }
}

function renderOutlinePreview(outline) {
  const box = $("sem-outline-preview");
  if (!outline) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const assess = (outline.assessments || [])
    .map((a) => `<tr><td>${esc(a.name)}</td><td>${a.weight ?? ""}%</td><td>${esc(a.due_date || "")}</td></tr>`)
    .join("");
  box.innerHTML = `
    <strong>${esc(outline.title || outline.paper_code)}</strong>
    <p class="hint">${esc(outline.paper_code || "")}</p>
    ${assess ? `<table><thead><tr><th>Assessment</th><th>Weight</th><th>Due</th></tr></thead><tbody>${assess}</tbody></table>` : `<p class="hint">${esc(outline.note || "No assessments parsed yet.")}</p>`}`;
}

function renderTimeline(plan) {
  const host = $("sem-timeline");
  clear(host);
  if (!plan?.timeline?.length) {
    host.appendChild(el("p", { class: "hint", text: "No tasks scheduled yet. Add paper codes and run Update everything." }));
    return;
  }
  for (const week of plan.timeline || []) {
    const block = el("div", { class: "timeline-week" });
    block.appendChild(el("h4", { text: week.week_start === "Unscheduled" ? "Unscheduled" : `Week of ${week.week_start}` }));
    for (const t of week.tasks) {
      const row = el("div", { class: "timeline-task" });
      row.appendChild(el("span", { class: "due", text: t.due_date || "—" }));
      row.appendChild(el("span", { text: `${t.subject ? t.subject + " · " : ""}${t.type}: ${t.name}` }));
      block.appendChild(row);
    }
    host.appendChild(block);
  }
}

function renderAnnouncements(rows) {
  const host = $("sem-announcements");
  clear(host);
  if (!rows.length) {
    host.appendChild(el("p", { class: "hint", text: "No announcements downloaded yet." }));
    return;
  }
  for (const a of rows) {
    const item = el("article", { class: "announcement-item" });
    item.appendChild(el("h4", { text: a.title || "Announcement" }));
    item.appendChild(el("p", { class: "meta", text: [a.author, a.posted_at].filter(Boolean).join(" · ") }));
    item.appendChild(el("p", { text: (a.body || "").slice(0, 500) }));
    host.appendChild(item);
  }
}

function esc(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

$("sem-paper-search")?.addEventListener("click", async () => {
  const q = $("sem-paper-q").value.trim();
  const out = $("sem-paper-results");
  clear(out);
  if (!q) return;
  try {
    const d = await api(`/api/semester/papers/search?q=${encodeURIComponent(q)}`);
    for (const r of d.results || []) {
      const inst = (r.instances || []).map((i) => i.code).join(", ");
      out.appendChild(el("p", { text: `${r.code} — ${r.title}${inst ? " (" + inst + ")" : ""}` }));
    }
    if (!d.results?.length) out.appendChild(el("p", { class: "hint", text: "No papers found." }));
  } catch (e) { toastError(e); }
});

$("sem-paper-fetch")?.addEventListener("click", async () => {
  const code = $("sem-paper-q").value.trim();
  if (!code) return toast("Enter a paper code first.", "warn");
  try {
    const outline = await postJSON("/api/semester/papers/fetch", { paper_code: code });
    Semester.lastOutline = outline;
    renderOutlinePreview(outline);
    const base = (outline.paper_code || code).split("-")[0].toUpperCase();
    applyDetectedPaperCodes([base]);
    toast(`Loaded outline for ${outline.paper_code || code}.`, "ok");
  } catch (e) { toastError(e); }
});

$("sem-schedule-import")?.addEventListener("click", async () => {
  const file = $("sem-schedule-file").files?.[0];
  if (!file) return toast("Choose a zip or CSV file.", "warn");
  const fd = new FormData();
  fd.append("file", file);
  try {
    const d = await api("/api/semester/schedule/import", { method: "POST", body: fd });
    Semester.scheduleId = d.id;
    const out = $("sem-schedule-status");
    clear(out);
    out.appendChild(el("p", { class: "ok-text", text: `Imported ${d.task_count} tasks across ${(d.subjects || []).length} papers.` }));
    toast("Class schedule imported.", "ok");
  } catch (e) { toastError(e); }
});

$("sem-plan-build")?.addEventListener("click", async () => {
  const codes = getSelectedPaperCodes();
  if (!codes.length) return toast("Add at least one paper code.", "warn");
  try {
    const body = { paper_codes: codes, class_schedule_id: Semester.scheduleId || null };
    const plan = await postJSON("/api/semester/plan/build", body);
    Semester.planId = plan.id;
    const full = await api(`/api/semester/plans/${plan.id}`);
    renderTimeline(full);
    const st = $("sem-plan-status");
    clear(st);
    st.appendChild(el("p", { class: "ok-text", text: `${plan.task_count} tasks in ${plan.name}` }));
    setExportLinks(plan.id);
    toast("Task schedule generated.", "ok");
  } catch (e) { toastError(e); }
});

$("sem-calendar-save")?.addEventListener("click", async () => {
  const url = $("sem-calendar-url").value.trim();
  try {
    const d = await api("/api/semester/moodle/calendar-url", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    $("sem-calendar-masked").textContent = d.stored
      ? `Saved: ${d.masked_url}`
      : "Calendar URL cleared.";
    toast(d.stored ? "Calendar URL saved securely." : "Calendar URL cleared.", "ok");
  } catch (e) { toastError(e); }
});

$("sem-sync-all")?.addEventListener("click", async () => {
  const codes = getSelectedPaperCodes();
  if (!codes.length) return toast("Add at least one paper code.", "warn");
  setSelectedPaperCodes(codes);
  const btn = $("sem-sync-all");
  const status = $("sem-sync-status");
  btn.disabled = true;
  const prevLabel = btn.textContent;
  btn.textContent = "Updating…";
  clear(status);
  status.appendChild(el("p", { class: "import-loading", role: "status" }, [
    el("span", { class: "mq-sso-spinner", "aria-hidden": "true" }),
    " Syncing outlines, schedule, calendar, and exports…",
  ]));
  try {
    const body = {
      paper_codes: codes,
      class_schedule_id: Semester.scheduleId || null,
      moodle_announcements_url: $("sem-moodle-url")?.value.trim() || "",
      moodle_cookies: $("sem-moodle-cookies")?.value.trim() || "",
      calendar_url: $("sem-calendar-url")?.value.trim() || "",
    };
    const file = $("sem-schedule-file")?.files?.[0];
    let plan;
    if (file) {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("payload", JSON.stringify(body));
      plan = await api("/api/semester/sync-all", { method: "POST", body: fd });
    } else {
      plan = await postJSON("/api/semester/sync-all", body);
    }
    Semester.planId = plan.plan_id;
    renderTimeline(plan);
    renderSyncSteps(plan);
    setExportLinks(plan.plan_id);
    toast(plan.ok ? "Everything updated." : "Updated with some errors — see status.", plan.ok ? "ok" : "warn");
  } catch (e) { toastError(e); }
  finally { btn.disabled = false; btn.textContent = prevLabel; }
});

$("sem-moodle-fetch")?.addEventListener("click", async () => {
  const url = $("sem-moodle-url").value.trim();
  if (!url) return toast("Paste a Moodle course URL.", "warn");
  try {
    const d = await postJSON("/api/semester/moodle/announcements", {
      url, cookies: $("sem-moodle-cookies").value.trim(),
    });
    renderAnnouncements(d.announcements || []);
    toast(`Stored ${d.stored ?? d.announcement_count ?? 0} announcements.`, "ok");
  } catch (e) { toastError(e); }
});
