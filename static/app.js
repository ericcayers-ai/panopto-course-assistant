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

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.detail) ? data.detail : res.statusText);
  return data;
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

// ---- lightweight modal prompt (replaces window.prompt/alert) ---------------

function promptModal(label, placeholder = "") {
  return new Promise((resolve) => {
    const overlay = el("div", { class: "modal-overlay", onclick: (e) => { if (e.target === overlay) { overlay.remove(); resolve(""); } } });
    const inp = el("input", { type: "text", placeholder, class: "modal-input", autocomplete: "off" });
    const commit = () => { overlay.remove(); resolve(inp.value.trim()); };
    const cancel = () => { overlay.remove(); resolve(""); };
    const box = el("div", { class: "modal-box" }, [
      el("p", { class: "modal-label", text: label }),
      inp,
      el("div", { class: "modal-actions" }, [
        el("button", { text: "Create", onclick: commit }),
        el("button", { class: "ghost", text: "Cancel", onclick: cancel }),
      ]),
    ]);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    inp.focus();
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commit();
      else if (e.key === "Escape") cancel();
    });
  });
}

// Export-destination chooser. Always shown before any export so the user
// confirms where files land. Resolves to the chosen path (""=default folder),
// or null if cancelled.
function askExportDest(opts = {}) {
  const { title = "Where should this be saved?",
          hint = "Leave blank to use the default folder inside your library.",
          placeholder = "C:\\Users\\…\\Course exports",
          defaultValue = "",
          confirmText = "Export here" } = opts;
  return new Promise((resolve) => {
    const overlay = el("div", { class: "modal-overlay",
      onclick: (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } } });
    const inp = el("input", { type: "text", placeholder, class: "modal-input",
      autocomplete: "off", value: defaultValue });
    const commit = () => { overlay.remove(); resolve(inp.value.trim()); };
    const cancel = () => { overlay.remove(); resolve(null); };
    const box = el("div", { class: "modal-box" }, [
      el("p", { class: "modal-label", text: title }),
      el("p", { class: "hint", text: hint }),
      inp,
      el("div", { class: "modal-actions" }, [
        el("button", { text: confirmText, onclick: commit }),
        el("button", { class: "ghost", text: "Cancel", onclick: cancel }),
      ]),
    ]);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    inp.focus(); inp.select();
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commit();
      else if (e.key === "Escape") cancel();
    });
  });
}

let toastTimer = null;
function toast(msg, kind = "info") {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 4000);
}

function remember(key, val) { try { localStorage.setItem(key, val); } catch (_) {} }
function recall(key, def = "") { try { return localStorage.getItem(key) ?? def; } catch (_) { return def; } }

// ---- tabs -----------------------------------------------------------------

function showTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === name));
  document.querySelector(".app").classList.remove("menu-open");  // close mobile drawer
  if (name === "home") loadDashboard();
  if (name === "library") loadTranscripts();
  if (name === "jobs") loadJobs();
}
document.querySelectorAll(".tab").forEach((btn) =>
  btn.addEventListener("click", () => showTab(btn.dataset.tab))
);
// dashboard tiles + any [data-goto] element jump to a tab
document.querySelectorAll("[data-goto]").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.goto))
);

// ---- import sub-switch (lectures / documents / notion / browse) -----------

function showImport(name) {
  document.querySelectorAll(".seg").forEach((b) => b.classList.toggle("active", b.dataset.import === name));
  document.querySelectorAll(".import-pane").forEach((p) =>
    p.classList.toggle("active", p.id === "import-" + name));
}
document.querySelectorAll(".seg").forEach((btn) =>
  btn.addEventListener("click", () => showImport(btn.dataset.import))
);

// ---- theme + mobile menu --------------------------------------------------

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  remember("theme", theme);
  const btn = $("theme-toggle");
  if (btn) btn.textContent = theme === "dark" ? "☀️ Theme" : "🌙 Theme";
}
// Apply the saved theme immediately (before the async init chain) so it sticks
// on refresh with no flash, even if later startup code errors out.
applyTheme(recall("theme") ||
  (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
$("theme-toggle")?.addEventListener("click", () =>
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"));
$("menu-toggle").addEventListener("click", () =>
  document.querySelector(".app").classList.toggle("menu-open"));

// ---- dashboard ------------------------------------------------------------

async function loadDashboard() {
  const env = $("dash-env");
  const stats = $("dash-stats");
  const s = State.status;
  if (s && env) {
    clear(env);
    const pill = (label, state) => el("span", { class: "env-pill" }, [
      el("span", { class: "dot " + state }), label]);
    const engines = Object.entries(s.engines).filter(([, v]) => v).map(([k]) => k);
    env.appendChild(pill(engines.length ? `Transcription: ${engines.join(", ")}` : "Transcription: not installed",
      engines.length ? "on" : "off"));
    env.appendChild(pill(s.cuda ? "GPU: CUDA" : "GPU: CPU only", s.cuda ? "on" : "warn"));
    env.appendChild(pill(s.markitdown ? "Documents: ready" : "Documents: install markitdown",
      s.markitdown ? "on" : "off"));
  }
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
      stats.appendChild(tile(State.lectures.length, "lectures loaded"));
    } catch (_) { /* leave empty */ }
  }
}

// ---- environment status ---------------------------------------------------

async function loadStatus() {
  const bar = $("status-bar");
  try {
    const s = await api("/api/status");
    State.status = s;
    const engines = Object.entries(s.engines).filter(([, v]) => v).map(([k]) => k);
    const parts = [
      engines.length ? `engines: ${engines.join(", ")}` : "⚠ no transcription engine installed",
      s.cuda ? "GPU: CUDA" : "GPU: none (CPU)",
      s.markitdown ? "PDF→MD: ready" : "PDF→MD: install markitdown",
      `output → ${s.output_dir}`,
    ];
    bar.textContent = parts.join("   •   ");
    bar.className = "status-bar " + (s.any_engine ? "ok" : "warn");

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

    // LLM availability - show/hide flashcard sections
    const llmReady = s.llm_ready === true;
    const fcMissing = $("fc-llm-missing");
    const fcCatMissing = $("fc-cat-llm-missing");
    const fcBtn = $("fc-generate");
    const fcCatBtn = $("fc-categorize");
    if (fcMissing) fcMissing.classList.toggle("hidden", llmReady);
    if (fcCatMissing) fcCatMissing.classList.toggle("hidden", llmReady);
    if (fcBtn) fcBtn.disabled = !llmReady;
    if (fcCatBtn) fcCatBtn.disabled = !llmReady;
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

function gatherSettings() {
  // Output formats / organisation are no longer chosen here - transcription
  // writes a sensible canonical set and the Export step owns the rest.
  return {
    engine: $("opt-engine").value,
    model: $("opt-model").value,
    language: $("opt-language").value.trim() || "en",
    device: $("opt-device").value,
    audio_only: $("opt-audio").checked,
    skip_existing: $("opt-skip").checked,
    cookies: $("opt-cookies").value.trim(),
    course: currentCourse(),
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
}

// ---- multi-course switcher (§1) -------------------------------------------
// The persisted courses live in the DB now; the switcher picks the *active*
// one and keeps the legacy free-text tag in sync so imports/exports still tag
// correctly. With no courses yet, the switcher hides and the free-text field
// works exactly as before.
const Courses = { list: [], active: null };

async function loadCourses() {
  const sel = $("course-switcher");
  if (!sel) return;
  let data;
  try { data = await api("/api/courses"); } catch (_) { return; }
  Courses.list = data.courses || [];
  Courses.active = data.active_course;
  clear(sel);
  if (!Courses.list.length) {
    sel.classList.add("hidden");
    return;
  }
  sel.classList.remove("hidden");
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
  } catch (e) { toast(e.message, "err"); }
}

async function createCourse() {
  const name = await promptModal("New course name:", "e.g. COMPX234 - Networks");
  if (!name) return;
  try {
    const c = await postJSON("/api/courses", { name });
    await loadCourses();
    await activateCourse(c.id);
  } catch (e) { toast(e.message, "err"); }
}

if ($("course-switcher")) {
  $("course-switcher").addEventListener("change", (e) => {
    if (e.target.value) activateCourse(Number(e.target.value));
  });
}
if ($("course-new")) $("course-new").addEventListener("click", createCourse);

// keep the top-bar field and the Course panel field in sync + persisted
$("course-input")?.addEventListener("input", () => remember("course", $("course-input").value.trim()));
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
  clear(list);
  const has = State.lectures.length > 0;
  ["lectures-heading", "lectures-toolbar"].forEach((id) =>
    $(id)?.classList.toggle("hidden", !has));
  $("settings")?.classList.toggle("hidden", !has);
  if (!has) { list.appendChild(el("p", { class: "empty", text: "No lectures loaded yet." })); return; }

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
          done ? el("span", { class: "badge done", text: "✓ transcribed" })
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
    } catch (e) { toast(`Failed to queue "${State.lectures[i].title}": ${e.message}`, "warn"); }
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
  } catch (e) { toast("Error: " + e.message, "warn"); }
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
  } catch (e) { toast("Error: " + e.message, "warn"); }
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
      list.appendChild(el("p", { class: "empty", text: "Nothing imported yet. Add lectures, documents or a Notion export in step 2." }));
      return;
    }

    // Transcripts (grouped per lecture, with format chips)
    librarySection(list, "🎙️ Transcripts", cats.transcripts.length);
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
    librarySection(list, "📑 Documents", docFiles.length);
    docFiles.forEach((f) => list.appendChild(fileRow(f)));
    if (imgFiles.length) {
      const imgRow = el("div", { class: "list-item muted small" }, [
        el("span", { class: "li-label", text: `📷 ${imgFiles.length} embedded image(s) (from document conversion)` }),
      ]);
      list.appendChild(imgRow);
    }
    librarySection(list, "🗒️ Notion pages", cats.notion.length);
    cats.notion.forEach((f) => list.appendChild(fileRow(f)));
    librarySection(list, "📂 Other sources", cats.others.length);
    cats.others.forEach((f) => list.appendChild(fileRow(f)));
    librarySection(list, "📤 Generated exports", cats.exports.length);
    cats.exports.forEach((f) => list.appendChild(fileRow(f)));
  } catch (e) { list.textContent = "Error: " + e.message; }
}

async function viewTranscript(relPath) {
  const view = $("transcript-view");
  view.textContent = "Loading…";
  try {
    const data = await api("/api/transcript?path=" + encodeURIComponent(relPath));
    view.textContent = data.content;
    view.scrollTop = 0;
  } catch (e) { view.textContent = "Error: " + e.message; }
}

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
    librarySection(list, "🔎 Filtered (" + data.count + ")", data.count);
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
  } catch (e) { list.textContent = "Error: " + e.message; }
}
$("lib-apply").addEventListener("click", applyLibraryFilters);
$("lib-tag").addEventListener("keydown", (e) => { if (e.key === "Enter") applyLibraryFilters(); });
$("lib-clear").addEventListener("click", () => {
  $("lib-type").value = ""; $("lib-week").value = ""; $("lib-tag").value = ""; $("lib-sort").value = "date";
  loadTranscripts();
});

// Export everything (transcripts + documents + Notion) for NotebookLM / any AI
$("export-all").addEventListener("click", async () => {
  const out = $("export-all-results");
  const btn = $("export-all");
  const dest = await askExportDest({ title: "Export everything for AI - where to?",
    defaultValue: $("export-all-dest")?.value.trim() || "" });
  if (dest === null) return;                      // cancelled
  if ($("export-all-dest")) $("export-all-dest").value = dest;
  btn.disabled = true; out.textContent = "Gathering every source…";
  try {
    const data = await api("/api/export/all", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course: currentCourse(), combined: $("all-combined").checked, output_dir: dest || undefined }),
    });
    clear(out);
    out.appendChild(el("p", { class: "ok-text",
      text: `✓ ${data.count} source(s) - ${data.transcripts} transcript(s), ${data.documents} document(s), ${data.notion} Notion page(s)` }));
    if (data.combined) out.appendChild(el("div", {}, [
      el("button", { class: "tag", text: "view everything_pack.md", onclick: () => { viewTranscript(data.combined); showTab("library"); } }),
    ]));
    out.appendChild(el("p", { class: "hint", text: `Per-lecture files written under ${data.dest}` }));
    toast(`Exported ${data.count} source(s) for AI.`, "ok");
  } catch (e) { out.textContent = "Error: " + e.message; toast(e.message, "warn"); }
  finally { btn.disabled = false; }
});

// NotebookLM export
$("nlm-export").addEventListener("click", async () => {
  const out = $("nlm-results");
  const btn = $("nlm-export");
  const dest = await askExportDest({ title: "Export NotebookLM sources - where to?",
    defaultValue: $("nlm-dest")?.value.trim() || "" });
  if (dest === null) return;
  if ($("nlm-dest")) $("nlm-dest").value = dest;
  btn.disabled = true; out.textContent = "Exporting…";
  try {
    const data = await api("/api/export/notebooklm", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course: currentCourse(), combined: $("nlm-combined").checked, output_dir: dest || undefined }),
    });
    clear(out);
    out.appendChild(el("p", { class: "ok-text", text: `✓ Exported ${data.count} file(s) → ${data.dest}` }));
    if (data.combined) out.appendChild(el("div", {}, [
      el("button", { class: "tag", text: "view course_pack.md", onclick: () => viewTranscript(data.combined) }),
    ]));
    data.files.forEach((f) => out.appendChild(el("div", { class: "list-item" }, [
      el("span", { class: "li-label", text: f }),
      el("button", { class: "tag", text: "view", onclick: () => viewTranscript(f) }),
    ])));
    toast(`Exported ${data.count} NotebookLM file(s).`, "ok");
  } catch (e) { out.textContent = "Error: " + e.message; toast(e.message, "warn"); }
  finally { btn.disabled = false; }
});

// Notion study-database CSV export (runs as a background LLM job)
$("studycsv-go").addEventListener("click", async () => {
  const out = $("studycsv-results");
  const btn = $("studycsv-go");
  const dest = await askExportDest({ title: "Export Notion study CSV - where to?",
    defaultValue: $("studycsv-dest")?.value.trim() || "" });
  if (dest === null) return;
  if ($("studycsv-dest")) $("studycsv-dest").value = dest;
  btn.disabled = true; out.textContent = "Queuing export…";
  try {
    const data = await api("/api/export/notion-csv", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course: currentCourse(), output_dir: dest || undefined }),
    });
    clear(out);
    if (data.id) {
      out.appendChild(el("p", { class: "ok-text", text: `✓ Job queued - check Jobs panel for progress.` }));
      out.appendChild(el("button", { class: "tag", text: "Go to Jobs", onclick: () => showTab("jobs") }));
      toast("Study CSV job started.", "ok");
      startJobsPolling();
    } else {
      out.appendChild(el("p", { class: "ok-text", text: `✓ ${data.count} lecture(s) → ${data.csv}` }));
      out.appendChild(el("div", {}, [
        el("button", { class: "tag", text: "view CSV", onclick: () => viewTranscript(data.csv) }),
      ]));
      out.appendChild(el("p", { class: "hint", text: "Columns: " + (data.columns || []).join(", ") }));
      toast(`Exported ${data.count} rows to a Notion CSV.`, "ok");
    }
  } catch (e) { out.textContent = "Error: " + e.message; toast(e.message, "warn"); }
  finally { btn.disabled = false; }
});

// Lecture SRT export - writes SRT files to a user-chosen folder alongside videos
$("srt-export").addEventListener("click", async () => {
  const out = $("srt-results");
  const dest = await askExportDest({
    title: "Export SRT + recordings - choose a folder",
    hint: "Subtitles and each lecture's video are saved here together so players auto-load them.",
    placeholder: "C:\\Videos\\Lectures",
    defaultValue: $("srt-dest").value.trim(),
    confirmText: "Export here" });
  if (dest === null) return;
  if (!dest) { toast("Choose a folder to save the SRT files and recordings.", "warn"); return; }
  $("srt-dest").value = dest;
  const btn = $("srt-export");
  btn.disabled = true; out.textContent = "Exporting SRT files…";
  try {
    const data = await api("/api/export/srt", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_dir: dest }),
    });
    clear(out);
    out.appendChild(el("p", { class: "ok-text",
      text: `✓ ${data.count} SRT file(s) written to ${data.dest || data.output_dir}` }));
    const rec = data.recordings;
    if (rec) {
      const have = (rec.copied?.length || 0) + (rec.downloaded?.length || 0);
      out.appendChild(el("p", { class: have ? "ok-text" : "hint",
        text: `🎬 ${have} lecture recording(s) placed alongside the SRT files`
          + (rec.downloaded?.length ? ` (${rec.downloaded.length} downloaded)` : "") + "." }));
      if (rec.missing?.length) {
        out.appendChild(el("p", { class: "hint",
          text: `${rec.missing.length} recording(s) couldn't be retrieved (not kept locally and `
            + `the source needs sign-in). Re-transcribe those lectures to keep their video.` }));
      }
    }
    out.appendChild(el("p", { class: "hint",
      text: "The .srt files share each video's name, so players load them automatically when both are in this folder." }));
    toast(`Exported ${data.count} SRT file(s).`, "ok");
  } catch (e) { out.textContent = "Error: " + e.message; toast(e.message, "warn"); }
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
  } catch (e) { out.textContent = "Error: " + e.message; }
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
    if (!data.results.length) { out.appendChild(el("p", { class: "empty", text: "No matches." })); return; }
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
  } catch (e) { out.textContent = "Error: " + e.message; }
}
$("search-go").addEventListener("click", doSearch);
$("search-q").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

// ---- flashcards -----------------------------------------------------------

function renderDeckResult(out, data, label) {
  clear(out);
  out.appendChild(el("p", { class: "ok-text", text: `✓ ${data.count} card(s) - ${label}` }));
  out.appendChild(el("div", { class: "row" }, [
    el("button", { class: "tag", text: "view Anki .txt", onclick: () => { viewTranscript(data.anki_tsv); showTab("library"); } }),
    el("button", { class: "tag", text: "view .csv", onclick: () => { viewTranscript(data.csv); showTab("library"); } }),
  ]));
  out.appendChild(el("p", { class: "hint", text: "In Anki: File → Import → pick the .txt (tags map to column 3)." }));
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
  const dest = await askExportDest({ title: "Generate flashcards - where to save the deck?",
    defaultValue: $("fc-output-dir")?.value.trim() || "" });
  if (dest === null) return;
  if ($("fc-output-dir")) $("fc-output-dir").value = dest;
  btn.disabled = true; out.textContent = "Queuing flashcard job…";
  try {
    const data = await api("/api/flashcards/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        deck: $("fc-deck").value.trim() || "flashcards",
        course: currentCourse(),
        max_cards: parseInt($("fc-max").value, 10) || 50,
        output_dir: dest || undefined,
      }),
    });
    clear(out);
    if (data.id) {
      out.appendChild(el("p", { class: "ok-text", text: "✓ Flashcard job queued - check Jobs panel for progress." }));
      out.appendChild(el("button", { class: "tag", text: "Go to Jobs", onclick: () => showTab("jobs") }));
      toast("Flashcard generation started.", "ok");
      startJobsPolling();
    } else {
      renderDeckResult(out, data, "generated with LLM");
      toast(`Generated ${data.count} flashcard(s).`, "ok");
    }
  } catch (e) { out.textContent = "Error: " + e.message; toast(e.message, "warn"); }
  finally { btn.disabled = false; }
});

$("fc-categorize").addEventListener("click", async () => {
  const out = $("fc-cat-results");
  const btn = $("fc-categorize");
  const text = $("fc-cat-text").value.trim();
  const path = $("fc-cat-path").value.trim();
  if (!text && !path) { toast("Paste a deck or give a file path.", "warn"); return; }
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
      out.appendChild(el("p", { class: "ok-text", text: "✓ Categorization job queued - check Jobs panel for progress." }));
      out.appendChild(el("button", { class: "tag", text: "Go to Jobs", onclick: () => showTab("jobs") }));
      toast("Categorization job started.", "ok");
      startJobsPolling();
    } else {
      renderDeckResult(out, data, "tagged");
      toast(`Categorized ${data.count} card(s).`, "ok");
    }
  } catch (e) { out.textContent = "Error: " + e.message; toast(e.message, "warn"); }
  finally { btn.disabled = false; }
});

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
    out.appendChild(el("p", { class: "ok-text", text: `✓ Converted ${data.count} document(s)${imgNote} → ${data.output_root}` }));
    if (data.combined) out.appendChild(el("div", {}, [
      el("button", { class: "tag", text: "view documents_pack.md", onclick: () => { viewTranscript(data.combined); showTab("library"); } }),
    ]));
    data.files.forEach((f) => {
      const row = el("div", { class: "list-item" }, [el("span", { class: "li-label", text: f.error ? `⚠ ${f.src}: ${f.error}` : f.md })]);
      if (!f.error && target === "ai") row.appendChild(el("button", { class: "tag", text: "view", onclick: () => { viewTranscript(f.md); showTab("library"); } }));
      out.appendChild(row);
    });
    toast(`Converted ${data.count} document(s).`, "ok");
  } catch (e) { out.textContent = "Error: " + e.message; toast(e.message, "warn"); }
  finally { btn.disabled = false; }
});

// ---- jobs -----------------------------------------------------------------

// Friendly, plain-language labels for a job's internal stage.
function stageLabel(stage) {
  return ({
    downloading: "Downloading",
    waiting: "Waiting for a free transcription slot",
    transcribing: "Transcribing",
    writing: "Saving files",
    done: "Done",
  })[stage] || (stage ? stage.charAt(0).toUpperCase() + stage.slice(1) : "");
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

    if (!data.jobs.length) { out.appendChild(el("p", { class: "empty", text: "No jobs yet." })); stopJobsPolling(); return; }
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
      const card = el("div", { class: "card job " + j.status }, [
        el("div", { class: "job-head" }, [
          el("strong", { text: j.title }),
          el("span", { class: "badge " + j.status, text: j.status }),
        ]),
        el("div", { class: "progress" }, [el("div", { class: "bar", style: `width:${pct}%` })]),
        el("div", { class: "hint", text: stageText ? `${stageText} · ${pct}%` : `${pct}%` }),
      ]);
      if (j.status === "done" && j.result) {
        if (j.result.status === "skipped") {
          card.appendChild(el("div", { class: "hint", text: "skipped - outputs already exist" }));
        } else if (j.result.outputs) {
          card.appendChild(el("div", { class: "hint", text: "wrote: " + Object.keys(j.result.outputs).join(", ") }));
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
  } catch (e) { out.textContent = "Error: " + e.message; }
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
  } catch (e) { toast(e.message, "err"); }
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
  } catch (e) { toast(e.message, "err"); }
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
        el("span", { class: "li-label", text: (e.is_dir ? "📁 " : "📄 ") + e.name }),
        el("span", { class: "muted", text: e.size_human }),
      ]);
      if (e.is_dir) row.addEventListener("click", () => browse(e.path));
      out.appendChild(row);
    });
  } catch (e) { out.textContent = "Error: " + e.message; }
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
    out.appendChild(el("p", { class: "ok-text", text: `✓ ${d.title || d.code || "Course"}` }));
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
  } catch (e) { out.textContent = "Error: " + e.message; toast(e.message, "warn"); }
});
async function saveMoodleOutline(path) {
  try {
    const d = await api("/api/moodle/parse", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, save_outline: true }),
    });
    toast("Saved outline → " + (d.saved_as || "output folder"), "ok");
  } catch (e) { toast(e.message, "warn"); }
}

// Notion export - render a conversion result into #notion-results
function renderNotionResult(d) {
  const out = $("notion-results");
  clear(out);
  out.appendChild(el("p", { class: "ok-text", text: `✓ Converted ${d.count} page(s) → ${d.dest}` }));
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
  } catch (e) { out.textContent = "Error: " + e.message; toast(e.message, "warn"); }
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
  } catch (e) { out.textContent = "Error: " + e.message; toast(e.message, "warn"); }
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

  if ($("feed-source")) $("feed-source").value = recall("feed");
  $("pdf-path").value = recall("pdfpath");
  $("materials-path").value = recall("matpath");
  $("moodle-path").value = recall("moodlepath");
  $("notion-path").value = recall("notionpath");
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
  loadDashboard();
  initMoodleQuick();
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
    toast("Enter a valid Moodle URL first (e.g. https://elearn.waikato.ac.nz).", "warn");
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

async function _moodleConnect(url, token) {
  if (!url) { toast("Enter your Moodle site link first.", "warn"); return; }
  if (!token) { toast("No token received - try signing in again.", "warn"); return; }
  setConnectStatus("warn", "Connecting to Moodle…");
  try {
    const d = await postJSON("/api/moodle/connect", { url, token });
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
  if (c.code || c.fullname) setCourse(c.code || c.fullname);
  const counts = data.counts || {};
  const res = data.resources || {};
  const conv = data.converted || {};
  const imgs = (conv.files || []).reduce((n, f) => n + (f.images || 0), 0);
  const feeds = data.panopto_feeds || [];

  const bits = [`Imported <strong>${c.fullname || c.code || "course"}</strong> - ${counts.sections || 0} section(s)`];
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
        + "detected for this course. You can paste a Panopto feed URL in the Full workspace to "
        + "transcribe them." }));

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
  toast("Course imported.", "ok");
}

$("mq-import")?.addEventListener("click", async () => {
  if (!_mqConnected) { toast("Connect to Moodle first.", "warn"); return; }
  const sel = $("mq-course-select");
  const courseId = sel && sel.value ? parseInt(sel.value, 10) : 0;
  if (!courseId) { toast("Pick a course to import.", "warn"); return; }
  // "Lectures & transcripts" is one toggle: lectures are only ever pulled as part
  // of the course (with transcription), never as a standalone import.
  const grabDocs = $("mq-grab-docs")?.checked ?? true;
  const grabTranscripts = $("mq-grab-transcripts")?.checked ?? true;
  const grabLectures = grabTranscripts;
  const keepImages = $("mq-images")?.checked ?? true;
  if (!grabDocs && !grabTranscripts) {
    toast("Pick at least one thing to include - documents or lectures.", "warn"); return;
  }
  const btn = $("mq-import"); btn.disabled = true; btn.textContent = "Importing…";
  const out = $("mq-import-result"); clear(out);
  out.appendChild(el("p", { class: "muted", text: "Reading the course from Moodle…" }));
  try {
    const data = await postJSON("/api/moodle/api-import", {
      url: _mqBaseUrl, course_id: courseId,
      grab_lectures: grabLectures || grabTranscripts,
      grab_docs: grabDocs, convert: true, keep_images: keepImages,
    });
    renderMqImport(data, { grabLectures, grabTranscripts, grabDocs });
  } catch (e) {
    clear(out);
    out.appendChild(el("div", { class: "warn-box", text: "Import failed: " + e.message }));
  } finally { btn.disabled = false; btn.textContent = "Import course"; }
});

function renderMqFeeds() {
  const box = $("mq-feeds"); if (!box) return; clear(box);
  const recs = State.mqRecordings || [];
  const feeds = State.mqFeeds || [];
  if (recs.length) {
    box.appendChild(el("p", { class: "ok-text",
      text: `🎬 ${recs.length} recording(s) loaded from the Panopto feed:` }));
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
    toast(e.message, "warn");
    renderMqFeeds();
  } finally { if (btn) { btn.disabled = false; btn.textContent = "Load recordings"; } }
}
$("mq-panopto-load")?.addEventListener("click", loadPanoptoRecordings);

async function autoTranscribeMq() {
  let recs = State.mqRecordings || [];
  // If nothing loaded yet but a URL is pasted, load it first.
  if (!recs.length && $("mq-panopto-url")?.value.trim()) {
    await loadPanoptoRecordings();
    recs = State.mqRecordings || [];
  }
  if (!recs.length) { toast("Paste the Panopto RSS link and load the recordings first.", "warn"); return; }

  const makeTranscript = $("mq-make-transcript")?.checked !== false;

  // "Recording without transcript" → just download the videos to a chosen folder.
  if (!makeTranscript) {
    const dest = await askExportDest({ title: "Download recordings - choose a folder",
      hint: "Each lecture's video is saved here.", placeholder: "C:\\Videos\\Lectures" });
    if (dest === null) return;
    if (!dest) { toast("Choose a folder for the recordings.", "warn"); return; }
    try {
      const d = await postJSON("/api/panopto/download", { lectures: recs, output_dir: dest });
      toast(`Downloaded ${d.downloaded} recording(s) to ${dest}.`, "ok");
    } catch (e) { toast(e.message, "warn"); }
    return;
  }

  // "Recording with transcript" → transcribe from audio (small download).
  if (!mqRecommend || !mqRecommend.ready) { toast(mqRecommend?.reason || "No transcription engine.", "warn"); return; }
  const settings = { engine: mqRecommend.engine, model: mqRecommend.model,
    device: mqRecommend.device, language: mqRecommend.language, interval: mqRecommend.interval,
    audio_only: true };   // transcribe from audio; the video is fetched on SRT export
  let queued = 0;
  for (const lec of recs) {
    try {
      await postJSON("/api/transcribe", { ...settings, lecture: lec });
      queued++;
    } catch (e) { toast("Transcribe error: " + e.message, "warn"); }
  }
  if (queued) {
    toast(`Queued ${queued} recording(s). Transcription takes a few minutes each and runs in the background; track it in Jobs.`, "ok");
    showTab("jobs"); startJobsPolling();
  }
}
$("mq-autotranscribe")?.addEventListener("click", autoTranscribeMq);

$("mq-export-nblm")?.addEventListener("click", () => mqExport("notebooklm"));
$("mq-export-ai")?.addEventListener("click", () => mqExport("all"));
async function mqExport(kind) {
  const out = $("mq-export-result");
  const dest = await askExportDest({
    title: `Export for ${kind === "notebooklm" ? "NotebookLM" : "a general AI"} - where to?` });
  if (dest === null) return;
  clear(out);
  try {
    const path = kind === "notebooklm" ? "/api/export/notebooklm" : "/api/export/all";
    const body = { combined: true, course: currentCourse(), output_dir: dest || undefined };
    const data = await postJSON(path, body);
    const exportDest = data.combined || data.dest || data.path || "the library";
    out.appendChild(el("div", { class: "ok-box", html:
      `Exported for <strong>${kind === "notebooklm" ? "NotebookLM" : "general AI"}</strong> → <code>${exportDest}</code>` }));
    toast("Export ready.", "ok");
  } catch (e) { out.appendChild(el("div", { class: "warn-box", text: "Export failed: " + e.message })); }
}
