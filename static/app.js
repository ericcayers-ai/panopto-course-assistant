// Panopto Course Assistant — minimal vanilla-JS frontend.
"use strict";

let lastLectures = [];     // lectures from the most recent feed load
let jobsTimer = null;

// ---- helpers --------------------------------------------------------------

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* non-JSON */ }
  if (!res.ok) {
    const msg = (data && data.detail) ? data.detail : res.statusText;
    throw new Error(msg);
  }
  return data;
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

// ---- tabs -----------------------------------------------------------------

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "transcripts") loadTranscripts();
    if (btn.dataset.tab === "jobs") loadJobs();
  });
});

// ---- environment status ---------------------------------------------------

async function loadStatus() {
  const bar = document.getElementById("status-bar");
  try {
    const s = await api("/api/status");
    const engines = Object.entries(s.engines).filter(([, v]) => v).map(([k]) => k);
    const parts = [];
    parts.push(engines.length ? `engines: ${engines.join(", ")}` : "no transcription engine installed");
    parts.push(s.cuda ? "CUDA: yes" : "CUDA: no (CPU)");
    if (s.markitdown) parts.push("markitdown: yes");
    parts.push(`output: ${s.output_dir}`);
    bar.textContent = parts.join("  •  ");
    bar.className = "status-bar " + (s.any_engine ? "ok" : "warn");

    const sel = document.getElementById("t-engine");
    clear(sel);
    if (engines.length) {
      engines.forEach((e) => sel.appendChild(el("option", { text: e })));
    } else {
      sel.appendChild(el("option", { text: "(none installed)" }));
    }
    if (s.default_engine) sel.value = s.default_engine;
  } catch (e) {
    bar.textContent = "could not reach backend: " + e.message;
    bar.className = "status-bar warn";
  }
}

// ---- lectures -------------------------------------------------------------

function renderLectures(lectures) {
  lastLectures = lectures;
  const list = document.getElementById("lectures-list");
  clear(list);
  document.getElementById("transcribe-defaults").classList.toggle("hidden", lectures.length === 0);
  if (!lectures.length) { list.appendChild(el("p", { text: "No lectures found." })); return; }

  list.appendChild(el("p", { class: "hint", text: `${lectures.length} lecture(s):` }));
  lectures.forEach((lec, i) => {
    const meta = [
      lec.week != null ? `Week ${lec.week}` : null,
      lec.date || null,
      lec.duration_human !== "?" ? lec.duration_human : null,
      lec.size_human !== "?" ? lec.size_human : null,
    ].filter(Boolean).join(" · ");
    const card = el("div", { class: "card lecture" }, [
      el("div", { class: "lecture-main" }, [
        el("strong", { text: lec.title }),
        el("div", { class: "hint", text: meta }),
      ]),
      el("button", { text: "Transcribe", onclick: () => startTranscribe(i) }),
    ]);
    list.appendChild(card);
  });
}

async function startTranscribe(index) {
  const lec = lastLectures[index];
  const body = {
    lecture: lec,
    engine: document.getElementById("t-engine").value,
    model: document.getElementById("t-model").value,
    language: document.getElementById("t-language").value || "en",
    device: document.getElementById("t-device").value,
    organize: document.getElementById("t-organize").value,
    keep_media: document.getElementById("t-keep").checked,
  };
  try {
    await api("/api/transcribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    alert(`Queued: ${lec.title}\nWatch progress in the Jobs tab.`);
  } catch (e) {
    alert("Could not start transcription: " + e.message);
  }
}

document.getElementById("feed-load").addEventListener("click", async () => {
  const source = document.getElementById("feed-source").value.trim();
  if (!source) return;
  const list = document.getElementById("lectures-list");
  list.textContent = "Loading…";
  try {
    const data = await api("/api/feed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
    renderLectures(data.lectures);
  } catch (e) {
    list.textContent = "Error: " + e.message;
  }
});

document.getElementById("feed-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const list = document.getElementById("lectures-list");
  list.textContent = "Parsing…";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const data = await api("/api/feed/upload", { method: "POST", body: fd });
    renderLectures(data.lectures);
  } catch (e) {
    list.textContent = "Error: " + e.message;
  }
});

// ---- transcripts ----------------------------------------------------------

async function loadTranscripts() {
  const list = document.getElementById("transcripts-list");
  list.textContent = "Loading…";
  try {
    const data = await api("/api/transcripts");
    clear(list);
    if (!data.items.length) { list.textContent = "No transcripts yet."; return; }
    data.items.forEach((it) => {
      const formats = Object.keys(it.formats);
      const label = (it.folder ? it.folder + "/" : "") + it.stem;
      const row = el("div", { class: "list-item" }, [
        el("div", { text: label }),
        el("div", { class: "formats" }, formats.map((f) =>
          el("button", { class: "tag", text: f, onclick: () => viewTranscript(it.formats[f]) })
        )),
      ]);
      list.appendChild(row);
    });
  } catch (e) {
    list.textContent = "Error: " + e.message;
  }
}

async function viewTranscript(relPath) {
  const view = document.getElementById("transcript-view");
  view.textContent = "Loading…";
  try {
    const data = await api("/api/transcript?path=" + encodeURIComponent(relPath));
    view.textContent = data.content;
  } catch (e) {
    view.textContent = "Error: " + e.message;
  }
}

document.getElementById("transcripts-refresh").addEventListener("click", loadTranscripts);

// ---- NotebookLM export ----------------------------------------------------

document.getElementById("nlm-export").addEventListener("click", async () => {
  const out = document.getElementById("nlm-results");
  out.textContent = "Exporting…";
  try {
    const data = await api("/api/export/notebooklm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        course: document.getElementById("nlm-course").value.trim(),
        combined: document.getElementById("nlm-combined").checked,
      }),
    });
    clear(out);
    out.appendChild(el("p", { text: `Exported ${data.count} file(s) → ${data.dest}` }));
    if (data.combined) {
      out.appendChild(el("div", {}, [
        el("button", { class: "tag", text: "view course_pack.md", onclick: () => viewTranscript(data.combined) }),
      ]));
    }
    data.files.forEach((f) => {
      out.appendChild(el("div", { class: "list-item" }, [
        el("span", { text: f }),
        el("button", { class: "tag", text: "view", onclick: () => viewTranscript(f) }),
      ]));
    });
  } catch (e) {
    out.textContent = "Error: " + e.message;
  }
});

// ---- search ---------------------------------------------------------------

async function doSearch() {
  const q = document.getElementById("search-q").value.trim();
  const out = document.getElementById("search-results");
  if (!q) return;
  out.textContent = "Searching…";
  try {
    const data = await api("/api/search?q=" + encodeURIComponent(q));
    clear(out);
    if (!data.results.length) { out.textContent = "No matches."; return; }
    data.results.forEach((r) => {
      const card = el("div", { class: "card" }, [
        el("strong", { text: `${r.file} (${r.count} hit${r.count === 1 ? "" : "s"})` }),
      ]);
      r.snippets.forEach((s) => card.appendChild(el("div", { class: "snippet", text: "… " + s + " …" })));
      out.appendChild(card);
    });
  } catch (e) {
    out.textContent = "Error: " + e.message;
  }
}
document.getElementById("search-go").addEventListener("click", doSearch);
document.getElementById("search-q").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

// ---- pdf ------------------------------------------------------------------

document.getElementById("pdf-go").addEventListener("click", async () => {
  const out = document.getElementById("pdf-results");
  const input_path = document.getElementById("pdf-path").value.trim();
  if (!input_path) return;
  out.textContent = "Converting… (this can take a moment)";
  try {
    const data = await api("/api/pdf/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input_path,
        include_subfolders: document.getElementById("pdf-recursive").checked,
        overwrite: document.getElementById("pdf-overwrite").checked,
      }),
    });
    clear(out);
    out.appendChild(el("p", { text: `Converted ${data.count} PDF(s) → ${data.output_root}` }));
    data.files.forEach((f) => out.appendChild(el("div", { class: "snippet", text: f.md })));
  } catch (e) {
    out.textContent = "Error: " + e.message;
  }
});

// ---- jobs -----------------------------------------------------------------

async function loadJobs() {
  const out = document.getElementById("jobs-list");
  try {
    const data = await api("/api/jobs");
    clear(out);
    if (!data.jobs.length) { out.textContent = "No jobs yet."; stopJobsPolling(); return; }
    let anyActive = false;
    data.jobs.forEach((j) => {
      if (j.status === "queued" || j.status === "running") anyActive = true;
      const pct = Math.round(j.progress * 100);
      const card = el("div", { class: "card job " + j.status }, [
        el("div", {}, [el("strong", { text: j.title }), el("span", { class: "badge", text: j.status })]),
        el("div", { class: "progress" }, [el("div", { class: "bar", style: `width:${pct}%` })]),
        el("div", { class: "hint", text: `${j.stage || ""} ${pct}%` }),
      ]);
      if (j.status === "error") card.appendChild(el("pre", { class: "error", text: j.error }));
      if (j.status === "done" && j.result && j.result.outputs) {
        card.appendChild(el("div", { class: "hint", text: "wrote: " + Object.keys(j.result.outputs).join(", ") }));
      }
      out.appendChild(card);
    });
    if (anyActive) startJobsPolling(); else stopJobsPolling();
  } catch (e) {
    out.textContent = "Error: " + e.message;
  }
}

function startJobsPolling() {
  if (jobsTimer) return;
  jobsTimer = setInterval(loadJobs, 2000);
}
function stopJobsPolling() {
  if (jobsTimer) { clearInterval(jobsTimer); jobsTimer = null; }
}
document.getElementById("jobs-refresh").addEventListener("click", loadJobs);

// ---- materials ------------------------------------------------------------

document.getElementById("materials-go").addEventListener("click", async () => {
  const out = document.getElementById("materials-results");
  const path = document.getElementById("materials-path").value.trim();
  if (!path) return;
  out.textContent = "Listing…";
  try {
    const data = await api("/api/materials?path=" + encodeURIComponent(path));
    clear(out);
    out.appendChild(el("p", { class: "hint", text: data.path }));
    data.entries.forEach((e) => {
      const row = el("div", { class: "list-item" }, [
        el("span", { text: (e.is_dir ? "📁 " : "📄 ") + e.name }),
        el("span", { class: "hint", text: e.size_human }),
      ]);
      if (e.is_dir) {
        row.style.cursor = "pointer";
        row.addEventListener("click", () => {
          document.getElementById("materials-path").value = e.path;
          document.getElementById("materials-go").click();
        });
      }
      out.appendChild(row);
    });
  } catch (e) {
    out.textContent = "Error: " + e.message;
  }
});

// ---- init -----------------------------------------------------------------

loadStatus();
