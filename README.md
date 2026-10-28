# Componder Report Branding

Swaps the partner lab's header and footer on report PDFs for Componder branding.
The report body, signatures and QR code are left untouched.

## Web version (GitHub Pages)

`index.html` + `worker.js` run the same `rebrand.py` inside the browser using
Pyodide 0.28.3 (PyMuPDF 1.26.3). Nothing is uploaded: PDFs are processed on the
user's computer. The first visit downloads ~25 MB, which the browser then caches.
Any static host works. To test locally:

```
python3 -m http.server 8000      # then open http://127.0.0.1:8000
```

Changes to `rebrand.py` or `assets/logo.png` go live on the site as soon as they're pushed.

## Run locally (Flask)

```
.venv/bin/python app.py        # then open http://127.0.0.1:5050
```

First-time setup on a new machine:

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Command line, one file: `.venv/bin/python rebrand.py input.pdf [output.pdf]`

## How it works

1. Finds the header band (everything above the "Patient Name" line) and the
   footer band (everything below the rule under the signatures).
2. Redacts both bands, which really deletes the old text, logos and lines
   instead of hiding them under a white box.
3. Draws the Componder logo, the "LABORATORY TEST REPORT" band and the company footer.
   The lab's footer lines ("electronically authenticated… printed date", "NOTE : Assay
   results…" and "Processed at <lab>, <address>…") are carried over word for word, so
   the report still names the lab that did the test. Only the lab's own page numbers are
   replaced.
4. Integrity check: every word, image and drawing in the body is compared with
   the original. If anything differs, the file is rejected instead of produced.

If a PDF has a layout it doesn't recognise, it refuses the file instead of guessing.

## Supported labs

Each lab is a small class in `rebrand.py` (`Clinova`, `Vijaya`, `Thyrocare`), listed in `LABS`.

- **Clinova**: plain and letterhead versions. Header above "Patient Name" is replaced.
  The footer lines (authentication, NOTE, "Processed at Clinova…") are kept word for word.
- **Vijaya Diagnostic Centre**: the whole letterhead is one full-page picture. It's deleted,
  and the address line and "TEST REPORT" box are replaced. The footer gets the clinical note
  ("* Suggested Clinical Correlation…") and "Processed at Vijaya Diagnostic Centre, <address
  from the report>".
- **Thyrocare**: plain and `_RLS` versions. The lab's "Processed At" block and NABL/CAP logos
  stay. The Thyrocare logo/banner (in `_RLS`) is replaced, and the Thyrocare cover and back
  pages of `_RLS` are dropped. The footer is compact because signatures run close to the bottom.

To add a lab: write a class with `name`, `matches(doc)`, `pages(doc)`, `plan(page)` (and
optionally `prepare(page)`), add it to `LABS`, then run sample reports through
`rebrand.py` on the command line and in the browser.

## Branding

- Logo: `assets/logo.png`. Replace it with a high-res export (same proportions) for
  sharper prints. The current one is cropped from a WhatsApp image.
- Company name / address text: the `BRAND` dict at the top of `rebrand.py`.
