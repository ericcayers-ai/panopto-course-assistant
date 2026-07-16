"""routers/pages.py - the two HTML pages the app serves (§17).

`/` hands over the single-page frontend; `/docs` renders a self-contained API
reference from /openapi.json (the stock Swagger UI pulls its JS+CSS from a CDN,
so it is blank offline - this app is offline-first).
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

from .. import context

router = APIRouter()


@router.get("/")
def index() -> FileResponse:
    # no-cache so a freshly updated index.html (and the assets it references) is
    # always picked up instead of a stale browser-cached copy.
    return FileResponse(context.STATIC_DIR / "index.html",
                        headers={"Cache-Control": "no-cache"})


@router.get("/docs", include_in_schema=False)
def docs() -> HTMLResponse:
    """Self-contained API docs (no CDN), rendered from /openapi.json.

    Works fully offline, unlike the default Swagger UI which fetches its
    JavaScript and CSS from a public CDN.
    """
    return HTMLResponse(_DOCS_HTML)


_DOCS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Course Assistant - API</title>
<style>
  :root { color-scheme: light dark; --bg:#eeefea; --surface:#fff; --ink:#16181a;
    --muted:#53584f; --border:#cbcfc3; --brand:#a8530a; }
  @media (prefers-color-scheme: dark) { :root { --bg:#17191a; --surface:#1f2224;
    --ink:#eaebe6; --muted:#a2a99d; --border:#363b37; --brand:#e08a2a; } }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.55 system-ui,"Segoe UI",Roboto,Arial,sans-serif;
    color:var(--ink); background:var(--bg); padding:28px 22px; }
  .wrap { max-width:920px; margin:0 auto; }
  h1 { font-family:Bahnschrift,"DIN Alternate","Arial Narrow",sans-serif; font-size:24px; margin:0 0 2px; }
  a.back { color:var(--brand); text-decoration:none; font-size:14px; }
  .sub { color:var(--muted); margin:4px 0 22px; }
  .ep { background:var(--surface); border:1px solid var(--border); border-radius:10px;
    padding:12px 14px; margin:10px 0; box-shadow:0 1px 3px rgba(20,20,16,.06); }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .m { font-weight:700; font-size:12px; padding:3px 9px; border-radius:6px; color:#fff;
    letter-spacing:.04em; }
  .m.get{background:#3e6e2e;} .m.post{background:#a8530a;} .m.put{background:#7a5f0c;}
  .m.delete{background:#a32f22;} .m.patch{background:#6b4a92;}
  .path { font-family:"Cascadia Code",Consolas,ui-monospace,Menlo,monospace; font-size:14px; }
  .summary { color:var(--muted); font-size:13.5px; margin-left:auto; }
  .body { margin:8px 0 0; padding-left:2px; font-size:13px; color:var(--muted); }
  code { background:rgba(127,127,127,.14); border-radius:5px; padding:1px 5px;
    font-size:12.5px; }
  .params { margin:6px 0 0; font-size:13px; }
  .params li { margin:2px 0; }
  .err { color:#a32f22; }
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="/">← back to the app</a>
  <h1 id="title">API</h1>
  <p class="sub" id="sub">Loading the OpenAPI schema…</p>
  <div id="eps"></div>
</div>
<script>
const ORDER = { get:0, post:1, put:2, patch:3, delete:4 };
function esc(s){ return String(s).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function refName(schema){
  if(!schema) return null;
  if(schema.$ref) return schema.$ref.split('/').pop();
  if(schema.items && schema.items.$ref) return schema.items.$ref.split('/').pop()+'[]';
  return null;
}
(async () => {
  try {
    const spec = await (await fetch('/openapi.json')).json();
    document.getElementById('title').textContent =
      (spec.info && spec.info.title || 'API') + ' - v' + (spec.info && spec.info.version || '');
    const rows = [];
    for (const [path, methods] of Object.entries(spec.paths)) {
      for (const [method, op] of Object.entries(methods)) {
        rows.push({ path, method, op });
      }
    }
    rows.sort((a,b) => a.path.localeCompare(b.path) || (ORDER[a.method]-ORDER[b.method]));
    document.getElementById('sub').textContent =
      rows.length + ' endpoint' + (rows.length===1?'':'s') + ' - this page is generated locally, no internet required.';
    const host = document.getElementById('eps');
    for (const { path, method, op } of rows) {
      const div = document.createElement('div');
      div.className = 'ep';
      let html = '<div class="row"><span class="m '+method+'">'+method.toUpperCase()+'</span>'+
        '<span class="path">'+esc(path)+'</span>'+
        (op.summary ? '<span class="summary">'+esc(op.summary)+'</span>' : '')+'</div>';
      const params = (op.parameters||[]).map(p =>
        '<li><code>'+esc(p.name)+'</code> <span style="opacity:.7">('+esc(p.in)+
        (p.required?', required':'')+')</span></li>').join('');
      if (params) html += '<ul class="params">'+params+'</ul>';
      const rb = op.requestBody && op.requestBody.content && op.requestBody.content['application/json'];
      const bodyRef = rb && refName(rb.schema);
      if (bodyRef) html += '<div class="body">body: <code>'+esc(bodyRef)+'</code> (JSON)</div>';
      div.innerHTML = html;
      host.appendChild(div);
    }
  } catch (e) {
    document.getElementById('sub').className = 'sub err';
    document.getElementById('sub').textContent = 'Could not load /openapi.json: ' + e.message;
  }
})();
</script>
</body>
</html>"""
