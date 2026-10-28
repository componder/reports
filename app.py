"""Local web UI for rebranding lab reports. Run: .venv/bin/python app.py"""

import io
import uuid
import zipfile
from collections import OrderedDict
from pathlib import Path

from flask import Flask, abort, jsonify, render_template_string, request, send_file

from rebrand import LayoutError, rebrand

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

# Converted files live only in memory and vanish when the server stops.
RESULTS = OrderedDict()
MAX_RESULTS = 300


def _store(name, data):
    rid = uuid.uuid4().hex
    RESULTS[rid] = (name, data)
    while len(RESULTS) > MAX_RESULTS:
        RESULTS.popitem(last=False)
    return rid


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.post("/api/convert")
def convert():
    items = []
    for f in request.files.getlist("files"):
        name = Path(f.filename or "report.pdf").name
        try:
            out, report = rebrand(f.read())
        except LayoutError as e:
            items.append({"name": name, "ok": False, "error": str(e)})
            continue
        except Exception as e:  # corrupt / non-PDF uploads
            items.append({"name": name, "ok": False, "error": f"Could not read PDF ({e})"})
            continue
        out_name = f"Componder_{Path(name).stem}.pdf"
        items.append({"name": name, "ok": True, "id": _store(out_name, out),
                      "out_name": out_name, **report})
    return jsonify(items)


@app.get("/file/<rid>")
def file(rid):
    if rid not in RESULTS:
        abort(404)
    name, data = RESULTS[rid]
    return send_file(io.BytesIO(data), mimetype="application/pdf",
                     as_attachment=request.args.get("dl") == "1", download_name=name)


@app.get("/zip")
def zip_all():
    ids = [i for i in request.args.get("ids", "").split(",") if i in RESULTS]
    if not ids:
        abort(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rid in ids:
            name, data = RESULTS[rid]
            z.writestr(name, data)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="Componder_Reports.zip")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Componder Report Branding</title>
<style>
:root{--teal:#21434c;--mint:#7dd5bd;--bg:#f4f7f7;--card:#fff;--muted:#5d6b6e;--ok:#1f8a5b;--bad:#c0392b}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:#1d2a2d}
header{background:var(--teal);color:#fff;padding:18px 24px;display:flex;align-items:center;gap:12px}
header .mark{width:34px;height:34px;border-radius:8px;background:var(--mint);display:grid;place-items:center;color:var(--teal);font-weight:800;font-size:20px}
header h1{font-size:18px;margin:0;font-weight:600}
main{max-width:860px;margin:28px auto;padding:0 16px}
#drop{border:2px dashed #a9c3c0;border-radius:14px;background:var(--card);padding:44px 20px;text-align:center;cursor:pointer;transition:.15s}
#drop.over{border-color:var(--teal);background:#eaf6f2}
#drop strong{display:block;font-size:17px;margin-bottom:4px}
#drop span{color:var(--muted)}
.note{color:var(--muted);font-size:13px;margin:12px 2px 0}
#results{margin-top:24px;display:flex;flex-direction:column;gap:10px}
.row{background:var(--card);border-radius:10px;padding:14px 16px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;box-shadow:0 1px 2px rgba(0,0,0,.05)}
.row .name{flex:1;min-width:200px;font-weight:600;word-break:break-all}
.row .meta{font-size:13px;color:var(--muted);font-weight:400}
.ok .meta b{color:var(--ok)} .bad .meta b{color:var(--bad)}
.btn{border:0;border-radius:8px;padding:8px 14px;font:inherit;font-size:14px;cursor:pointer;text-decoration:none;display:inline-block}
.btn.primary{background:var(--teal);color:#fff} .btn.ghost{background:#e6eeee;color:var(--teal)}
#bar{display:none;justify-content:space-between;align-items:center;margin-top:24px}
#bar.show{display:flex}
.spin{display:inline-block;width:14px;height:14px;border:2px solid #cfdcdc;border-top-color:var(--teal);border-radius:50%;animation:s 1s linear infinite;vertical-align:-2px;margin-right:6px}
@keyframes s{to{transform:rotate(360deg)}}
</style></head><body>
<header><div class="mark">+</div><h1>Componder · Lab Report Branding</h1></header>
<main>
  <label id="drop">
    <input id="pick" type="file" accept="application/pdf" multiple hidden>
    <strong>Drop partner lab report PDFs here</strong>
    <span>or click to choose · multiple files OK</span>
  </label>
  <p class="note">Runs only on this computer, and nothing is uploaded anywhere.
  Header and footer are swapped for Componder branding. The report body, signatures
  and QR are kept exactly as they are, and every file is checked for that before you can download it.</p>
  <div id="bar"><span id="summary"></span><a id="zip" class="btn primary" href="#">Download all (.zip)</a></div>
  <div id="results"></div>
</main>
<script>
const drop=document.getElementById('drop'),pick=document.getElementById('pick'),
      results=document.getElementById('results'),bar=document.getElementById('bar');
let okIds=[];
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('over')}));
drop.addEventListener('drop',ev=>send(ev.dataTransfer.files));
pick.addEventListener('change',()=>{send(pick.files);pick.value=''});
const esc=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function send(files){
  files=[...files].filter(f=>f.name.toLowerCase().endsWith('.pdf'));
  if(!files.length)return;
  const pending=document.createElement('div');pending.className='row';
  pending.innerHTML='<span class="spin"></span> Converting '+files.length+' file(s)…';
  results.prepend(pending);
  const fd=new FormData();files.forEach(f=>fd.append('files',f));
  let items;
  try{items=await (await fetch('/api/convert',{method:'POST',body:fd})).json()}
  catch(e){pending.innerHTML='Server error: '+esc(String(e));return}
  pending.remove();
  for(const it of items.reverse()){
    const r=document.createElement('div');r.className='row '+(it.ok?'ok':'bad');
    if(it.ok){
      okIds.push(it.id);
      r.innerHTML=`<div class="name">${esc(it.out_name)}<div class="meta"><b>✓ Verified</b> · ${esc(it.lab)} · ${it.pages} page(s) · ${it.words} words &amp; ${it.images} images unchanged</div></div>
        <a class="btn ghost" target="_blank" href="/file/${it.id}">Preview</a>
        <a class="btn primary" href="/file/${it.id}?dl=1">Download</a>`;
    }else{
      r.innerHTML=`<div class="name">${esc(it.name)}<div class="meta"><b>✗ Not converted</b> · ${esc(it.error)}</div></div>`;
    }
    results.prepend(r);
  }
  bar.classList.toggle('show',okIds.length>0);
  document.getElementById('summary').textContent=okIds.length+' report(s) ready';
  document.getElementById('zip').href='/zip?ids='+okIds.join(',');
}
</script></body></html>"""

if __name__ == "__main__":
    print("Open http://127.0.0.1:5050")
    app.run(host="127.0.0.1", port=5050, debug=False)
