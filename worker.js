// Runs rebrand.py inside the browser (Pyodide + PyMuPDF). PDFs never leave this machine.
importScripts("https://cdn.jsdelivr.net/pyodide/v0.28.3/full/pyodide.js");

const ready = (async () => {
  const py = await loadPyodide();
  await py.loadPackage("pymupdf");
  const [src, logo] = await Promise.all([
    fetch("rebrand.py", { cache: "no-cache" }).then(r => r.text()),
    fetch("assets/logo.png", { cache: "no-cache" }).then(r => r.arrayBuffer()),
  ]);
  py.FS.mkdirTree("/app/assets");
  py.FS.writeFile("/app/rebrand.py", src);
  py.FS.writeFile("/app/assets/logo.png", new Uint8Array(logo));
  py.runPython(`
import json, sys
sys.path.insert(0, "/app")
import rebrand

def convert(data):
    try:
        out, report = rebrand.rebrand(data.to_bytes())
    except rebrand.LayoutError as e:
        return json.dumps({"ok": False, "error": str(e)})
    except Exception as e:  # corrupt / non-PDF uploads
        return json.dumps({"ok": False, "error": f"Could not read PDF ({e})"})
    with open("/out.pdf", "wb") as f:
        f.write(out)
    return json.dumps({"ok": True, **report})
`);
  return py;
})();

ready.then(() => postMessage({ type: "ready" }),
           e => postMessage({ type: "failed", error: String(e) }));

onmessage = async ({ data: { id, buf } }) => {
  const py = await ready;
  const result = JSON.parse(py.globals.get("convert")(new Uint8Array(buf)));
  if (result.ok) {
    const pdf = py.FS.readFile("/out.pdf");
    postMessage({ type: "done", id, result, pdf }, [pdf.buffer]);
  } else {
    postMessage({ type: "done", id, result });
  }
};
