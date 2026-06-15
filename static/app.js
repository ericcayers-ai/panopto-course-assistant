// Panopto Course Assistant — vanilla-JS frontend.
"use strict";

const State = {
  lectures: [],          // lectures from the most recent feed load
  transcribedStems: new Set(), // safe_titles that already have transcripts
  status: null,          // /api/status payload
  jobsTimer: null,
};

// ---- tiny DOM + fetch helpers ---------------------------------------------

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.detail) ? data.detail : res.statusText);
  return data;
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
  if (name === "transcripts") loadTranscripts();
  if (name === "jobs") loadJobs();
}
document.querySelectorAll(".tab").forEach((btn) =>
  btn.addEventListener("click", () => showTab(btn.dataset.tab))
);

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

    // engine dropdown
    const sel = $("opt-engine");
    clear(sel);
    if (engines.length) engines.forEach((e) => sel.appendChild(el("option", { text: e })));
    else sel.appendChild(el("option", { text: "(none installed)" }));
    if (s.default_engine) sel.value = s.default_engine;

    // output-format checkboxes
    buildOutputChecks(s.output_choices || ["txt", "srt", "md", "json"]);

    // engine-aware warning
    const warn = $("engine-warning");
    if (!s.any_engine) {
      warn.textContent = "No transcription engine installed — you can still load feeds, browse, "
        + "search and export. To transcribe: pip install -r requirements-transcribe.txt";
      warn.classList.remove("hidden");
    } else {
      warn.classList.add("hidden");
    }
  } catch (e) {
    bar.textContent = "could not reach backend: " + e.message;
    bar.className = "status-bar warn";
  }
}

const DEFAULT_OUTPUTS = new Set(["txt", "srt", "md", "json"]);
function buildOutputChecks(choices) {
  const box = $("opt-outputs");
  clear(box);
  choices.forEach((fmt) => {
    const id = "out-" + fmt;
    box.appendChild(el("label", { class: "chk" }, [
      el("input", { type: "checkbox", id, value: fmt, checked: DEFAULT_OUTPUTS.has(fmt) }),
      " " + fmt,
    ]));
  });
}
function selectedOutputs() {
  return [...document.querySelectorAll("#opt-outputs input:checked")].map((i) => i.value);
}

// ---- settings persistence -------------------------------------------------

function gatherSettings() {
  return {
    engine: $("opt-engine").value,
    model: $("opt-model").value,
    language: $("opt-language").value.trim() || "en",
    device: $("opt-device").value,
    organize: $("opt-organize").value,
    outputs: selectedOutputs(),
    interval: parseInt($("opt-interval").value, 10) || 30,
    audio_only: $("opt-audio").checked,
    keep_media: $("opt-keep").checked,
    skip_existing: $("opt-skip").checked,
    force: $("opt-force").checked,
    cookies: $("opt-cookies").value.trim(),
    course: $("opt-course").value.trim(),
  };
}

function setCourse(name) {
  if (!name) return;
  $("course-name").textContent = name;
  if (!$("opt-course").value) $("opt-course").value = name;
  if (!$("nlm-course").value) $("nlm-course").value = name;
  remember("course", name);
}

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
  ["settings-heading", "lectures-heading", "lectures-toolbar"].forEach((id) =>
    $(id).classList.toggle("hidden", !has));
  $("settings").classList.toggle("hidden", !has);
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
        el("div", { class: "hint", text: meta || "—" }),
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
  if (!settings.outputs.length) { toast("Select at least one output format.", "warn"); return; }
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
    toast(`Queued ${queued} lecture(s). See the Jobs tab.`, "ok");
    showTab("jobs");
    startJobsPolling();
  }
}

function openLectureTranscript(lec) {
  showTab("transcripts");
  // after the list loads, open the best available format for this lecture
  setTimeout(async () => {
    try {
      const data = await api("/api/transcripts");
      const g = data.items.find((it) => it.stem === lec.safe_title);
      if (g) {
        const rel = g.formats.txt || g.formats.md || Object.values(g.formats)[0];
        viewTranscript(rel);
      }
    } catch (_) {}
  }, 100);
}

$("feed-load").addEventListener("click", async () => {
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

$("feed-file").addEventListener("change", async (ev) => {
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

$("opt-course").addEventListener("change", () => setCourse($("opt-course").value.trim()));
$("sel-all").addEventListener("click", () => document.querySelectorAll(".lec-check").forEach((c) => (c.checked = true)));
$("sel-none").addEventListener("click", () => document.querySelectorAll(".lec-check").forEach((c) => (c.checked = false)));
$("transcribe-selected").addEventListener("click", () => transcribeLectures(checkedIndexes()));
$("transcribe-pending").addEventListener("click", () =>
  transcribeLectures(State.lectures.map((l, i) => i).filter((i) => !lectureDone(State.lectures[i]))));

// ---- transcripts ----------------------------------------------------------

const FORMAT_ORDER = ["txt", "md", "notebooklm", "summary", "srt", "vtt", "json"];

async function loadTranscripts() {
  const list = $("transcripts-list");
  list.textContent = "Loading…";
  try {
    const data = await api("/api/transcripts");
    State.transcribedStems = new Set(data.items.map((i) => i.stem));
    clear(list);
    if (!data.items.length) {
      list.appendChild(el("p", { class: "empty", text: "No transcripts yet. Transcribe a lecture first." }));
      return;
    }
    data.items.forEach((it) => {
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

// NotebookLM export
$("nlm-export").addEventListener("click", async () => {
  const out = $("nlm-results");
  const btn = $("nlm-export");
  btn.disabled = true; out.textContent = "Exporting…";
  try {
    const data = await api("/api/export/notebooklm", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course: $("nlm-course").value.trim(), combined: $("nlm-combined").checked }),
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
          el("button", { class: "tag", text: "open", onclick: () => viewTranscript(r.file).then(() => showTab("transcripts")) }),
        ]),
      ]);
      r.snippets.forEach((s) => card.appendChild(el("div", { class: "snippet", text: s })));
      out.appendChild(card);
    });
  } catch (e) { out.textContent = "Error: " + e.message; }
}
$("search-go").addEventListener("click", doSearch);
$("search-q").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

// ---- pdf ------------------------------------------------------------------

$("pdf-go").addEventListener("click", async () => {
  const out = $("pdf-results");
  const input_path = $("pdf-path").value.trim();
  if (!input_path) { toast("Enter a folder path.", "warn"); return; }
  remember("pdfpath", input_path);
  const btn = $("pdf-go");
  btn.disabled = true; out.textContent = "Converting… (this can take a moment)";
  try {
    const data = await api("/api/pdf/convert", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input_path,
        include_subfolders: $("pdf-recursive").checked,
        overwrite: $("pdf-overwrite").checked,
      }),
    });
    clear(out);
    out.appendChild(el("p", { class: "ok-text", text: `✓ Converted ${data.count} PDF(s) → ${data.output_root}` }));
    data.files.forEach((f) => out.appendChild(el("div", { class: "snippet", text: f.md })));
    toast(`Converted ${data.count} PDF(s).`, "ok");
  } catch (e) { out.textContent = "Error: " + e.message; toast(e.message, "warn"); }
  finally { btn.disabled = false; }
});

// ---- jobs -----------------------------------------------------------------

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
    data.jobs.forEach((j) => {
      const pct = Math.round(j.progress * 100);
      const card = el("div", { class: "card job " + j.status }, [
        el("div", { class: "job-head" }, [
          el("strong", { text: j.title }),
          el("span", { class: "badge " + j.status, text: j.status }),
        ]),
        el("div", { class: "progress" }, [el("div", { class: "bar", style: `width:${pct}%` })]),
        el("div", { class: "hint", text: `${j.stage || ""} ${pct}%` }),
      ]);
      if (j.status === "done" && j.result) {
        if (j.result.status === "skipped") {
          card.appendChild(el("div", { class: "hint", text: "skipped — outputs already exist" }));
        } else if (j.result.outputs) {
          card.appendChild(el("div", { class: "hint", text: "wrote: " + Object.keys(j.result.outputs).join(", ") }));
        }
      }
      if (j.status === "error") card.appendChild(el("pre", { class: "error", text: j.error }));
      out.appendChild(card);
    });
    if (active) startJobsPolling(); else { stopJobsPolling(); refreshTranscribedSet().then(renderLectures); }
  } catch (e) { out.textContent = "Error: " + e.message; }
}
function startJobsPolling() { if (!State.jobsTimer) State.jobsTimer = setInterval(loadJobs, 2000); }
function stopJobsPolling() { if (State.jobsTimer) { clearInterval(State.jobsTimer); State.jobsTimer = null; } }
$("jobs-refresh").addEventListener("click", loadJobs);

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
$("materials-go").addEventListener("click", () => browse($("materials-path").value.trim()));
$("materials-up").addEventListener("click", () => {
  const p = $("materials-path").value.trim().replace(/[\\/]+$/, "");
  const parent = p.replace(/[\\/][^\\/]*$/, "");
  if (parent && parent !== p) browse(parent);
});

// ---- init -----------------------------------------------------------------

function restore() {
  $("feed-source").value = recall("feed");
  $("pdf-path").value = recall("pdfpath");
  $("materials-path").value = recall("matpath");
  const course = recall("course");
  if (course) { $("course-name").textContent = course; $("opt-course").value = course; $("nlm-course").value = course; }
  try {
    const s = JSON.parse(recall("settings") || "{}");
    if (s.model) $("opt-model").value = s.model;
    if (s.language) $("opt-language").value = s.language;
    if (s.device) $("opt-device").value = s.device;
    if (s.organize) $("opt-organize").value = s.organize;
    if (s.interval) $("opt-interval").value = s.interval;
    if (typeof s.audio_only === "boolean") $("opt-audio").checked = s.audio_only;
    if (typeof s.keep_media === "boolean") $("opt-keep").checked = s.keep_media;
  } catch (_) {}
}

restore();
loadStatus();
