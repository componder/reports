"""Replace the partner lab's header/footer on a report PDF with Componder branding.

Supported labs: Clinova, Thyrocare, Vijaya Diagnostic Centre. Each has a small
profile class below that says where its header/footer are and which of its
lines must be kept (the lab's "processed at" / clinical notes carry the
report's authority, so they stay word for word).

The report body (patient details, results, interpretation, signatures, QR) is
never re-rendered: we only redact the header and footer bands, then draw our
own branding into the cleared space. After conversion an integrity check
compares every word, image and drawing in the body against the original and
refuses the output if anything moved or disappeared.

CLI:  python rebrand.py input.pdf [output.pdf]
"""

import re
import sys
from pathlib import Path

import pymupdf

ASSETS = Path(__file__).parent / "assets"
LOGO = ASSETS / "logo.png"

BRAND = {
    "band_text": "LABORATORY TEST REPORT",
    "company": "COMPONDER HEALTH CARE PRIVATE LIMITED",
    "address": "H No. 1-7-949 to 956, 4th Flr, Above Indianbank, Zamistanpur, Musheerabad, "
               "Hyderabad - 500020, Telangana, Mobile Number : 96424 96494",
}

# Colours sampled from the Componder letterhead.
TEAL = (33 / 255, 67 / 255, 76 / 255)
BAND_TEXT = (0.93, 0.95, 0.95)
GREY_LINE = (0.32, 0.32, 0.32)
TEXT = (0.13, 0.13, 0.13)

# Layout in PDF points, measured from the letterhead artwork scaled to A4 width.
LOGO_RECT = (388.3, 22.6, 570.3, 75.8)
BAND_Y = (85.2, 98.4)
BAND_TEXT_WIDTH = 190

# "full" footer: lab notes, dotted separator, company name and address.
FOOTER_RULE_Y = 790.0
NOTE_BASELINES = (797.2, 803.7, 810.8)
NOTES_SEPARATOR_Y = 814.4
COMPANY_BASELINE = 824.0
ADDRESS_BASELINE = 833.2

# "compact" footer for reports whose signatures run nearly to the page bottom.
COMPACT_RULE_Y = 817.0
COMPACT_COMPANY_BASELINE = 825.6
COMPACT_ADDRESS_BASELINE = 833.4

# Lab footer lines we redraw ourselves instead of carrying over.
DROP_FOOTER_LINES = ("Page ", "Page:")


class LayoutError(Exception):
    """The PDF doesn't look like a report layout we know how to rebrand."""


def _lines(page):
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                yield pymupdf.Rect(line["bbox"]), text, line["spans"][0]["size"]


def _first_line(page, test):
    """(rect, text) of the first text line passing test(rect, text), or None."""
    return next(((r, t) for r, t, _ in _lines(page) if test(r, t)), None)


def _header_bottom(page, label, test):
    """Just above the row holding the anchor line (e.g. "Patient Name"); other
    fields on that row can sit a point or two higher than the label."""
    found = _first_line(page, test)
    if found is None:
        raise LayoutError(f"page {page.number + 1}: '{label}' line not found")
    y = found[0].y0
    return min(r.y0 for r, _, _ in _lines(page) if abs(r.y0 - y) < 6) - 1


def _footer_lines(page, footer):
    """Text lines inside the footer zone, top to bottom, as (text, font size),
    minus the lab's own "Page X of Y"."""
    notes = sorted(
        (rect.y0, text, size) for rect, text, size in _lines(page)
        if footer.contains(rect) and not text.startswith(DROP_FOOTER_LINES)
    )
    return [(text, size) for _, text, size in notes]


class Plan:
    """What to do on one page.

    header: zones whose old content is deleted; our logo and band go there.
    footer: zone whose old content is deleted; our footer goes there.
    notes:  lab lines to print in our footer, as (text, font size).
    style:  "full" or "compact" footer.
    """

    def __init__(self, header, footer, notes, style="full"):
        self.header = [pymupdf.Rect(r) for r in header]
        self.footer = pymupdf.Rect(footer)
        self.notes = notes
        self.style = style
        self.skip_images = set()  # digests of letterhead images removed outright

    @property
    def zones(self):
        return self.header + [self.footer]


class Clinova:
    """Clinova Pathlabs: header above "Patient Name"; footer below the grey rule
    under the signatures (authentication note, NOTE, "Processed at Clinova…")."""
    name = "Clinova"

    @staticmethod
    def matches(doc):
        return any("clinova" in page.get_text().lower() for page in doc)

    @staticmethod
    def pages(doc):
        return list(range(doc.page_count))

    @staticmethod
    def plan(page):
        w, h = page.rect.width, page.rect.height
        header_bottom = _header_bottom(page, "Patient Name",
                                       lambda r, t: t.startswith("Patient Name"))
        footer_top = None
        for d in page.get_drawings():
            r = d["rect"]
            if r.y0 > h * 0.85 and r.height < 3 and r.width > w * 0.6:
                footer_top = r.y0 - 3
                break
        if footer_top is None:
            for rect, text, _ in _lines(page):
                if rect.y0 > h * 0.85 and ("electronically authenticated" in text
                                           or text.startswith("Processed at")):
                    footer_top = min(footer_top or rect.y0, rect.y0) - 4
        if footer_top is None:
            raise LayoutError(f"page {page.number + 1}: footer block not found")
        footer = pymupdf.Rect(0, footer_top, w, h)
        return Plan([(0, 0, w, header_bottom)], footer, _footer_lines(page, footer))


class Vijaya:
    """Vijaya Diagnostic Centre: the whole letterhead (logo, name, side stripe,
    "Suggested Clinical Correlation" note, customer-care bar) is one full-page
    picture drawn by its own content stream. We delete that picture, redact the
    address line and "TEST REPORT" box above the "Name" line, and re-print the
    clinical note plus a "Processed at" line built from the lab's address."""
    name = "Vijaya"
    LAB_NAME = "Vijaya Diagnostic Centre"
    NOTE = "* Suggested Clinical Correlation, If Necessary Kindly Discuss with signatory"
    FOOTER_TOP = 786.0
    # The whole content stream is just "draw this one image".
    BG_STREAM = re.compile(rb"\s*(q\s*)+[-\d.\s]+cm\s*/(\w+)\s+Do\s*(Q\s*)+")

    @staticmethod
    def matches(doc):
        text = doc[0].get_text()
        return ("Registration ID" in text and "Regn Centre" in text
                and Vijaya._background(doc[0]) is not None)

    @staticmethod
    def pages(doc):
        return list(range(doc.page_count))

    @staticmethod
    def _background(page):
        """(content xref, image digest) of the full-page letterhead picture."""
        doc = page.parent
        names = {img[7]: img[0] for img in page.get_images(full=True)}
        infos = {i["xref"]: i for i in page.get_image_info(hashes=True, xrefs=True)}
        for cx in page.get_contents():
            m = Vijaya.BG_STREAM.fullmatch(doc.xref_stream(cx) or b"")
            if not m:
                continue
            info = infos.get(names.get(m.group(2).decode()))
            if info and pymupdf.Rect(info["bbox"]).get_area() > 0.9 * page.rect.get_area():
                return cx, info["digest"]
        return None

    @staticmethod
    def plan(page):
        w, h = page.rect.width, page.rect.height
        bg = Vijaya._background(page)
        if bg is None:
            raise LayoutError(f"page {page.number + 1}: Vijaya letterhead image not found")
        header = pymupdf.Rect(0, 0, w, _header_bottom(
            page, "Name", lambda r, t: t == "Name" and r.x0 < 60))
        address = _first_line(page, lambda r, t: header.contains(r) and "Hyderabad" in t)
        if address is None:
            raise LayoutError(f"page {page.number + 1}: lab address line not found")
        footer = pymupdf.Rect(0, Vijaya.FOOTER_TOP, w, h)
        extra = _footer_lines(page, footer)
        if extra:
            raise LayoutError(f"page {page.number + 1}: unexpected footer text {extra}")
        notes = [(Vijaya.NOTE, 4.95),
                 (f"Processed at {Vijaya.LAB_NAME}, {address[1]}", 5.8)]
        plan = Plan([header], footer, notes)
        plan.skip_images.add(bg[1])
        return plan

    @staticmethod
    def prepare(page):
        """Delete the letterhead picture (only its own one-line content stream)."""
        cx, _ = Vijaya._background(page)
        page.parent.update_stream(cx, b"q Q\n")


class Thyrocare:
    """Thyrocare: each page keeps the lab's "Processed At" block and NABL/CAP
    logos (top left). We clear the top-right corner and the strip down to
    "Patient Name" (the "_RLS" variant has a Thyrocare logo and banner there;
    the plain one has nothing), draw our logo and band there, and add a compact
    footer below the signatures. The "_RLS" variant's Thyrocare cover and back
    pages are dropped."""
    name = "Thyrocare"
    LAB_BLOCK_RIGHT = 300.0   # "Processed At" + accreditation logos end before this
    LAB_BLOCK_BOTTOM = 82.0   # ... and above this
    FOOTER_TOP = 815.5

    @staticmethod
    def _is_report_page(page):
        text = page.get_text()
        return "Processed At" in text and "Patient Name" in text

    @staticmethod
    def matches(doc):
        return (any("LabCode:" in page.get_text() for page in doc)
                and any(Thyrocare._is_report_page(page) for page in doc))

    @staticmethod
    def pages(doc):
        keep = [p.number for p in doc if Thyrocare._is_report_page(p)]
        # Only leading/trailing extras (cover, terms) may be dropped.
        if keep != list(range(keep[0], keep[-1] + 1)):
            raise LayoutError("a page in the middle of the report isn't a report page")
        return keep

    @staticmethod
    def plan(page):
        w, h = page.rect.width, page.rect.height
        footer = pymupdf.Rect(0, Thyrocare.FOOTER_TOP, w, h)
        extra = _footer_lines(page, footer)
        if extra:
            raise LayoutError(f"page {page.number + 1}: unexpected footer text {extra}")
        header_bottom = _header_bottom(page, "Patient Name",
                                       lambda r, t: t.startswith("Patient Name"))
        corner = (Thyrocare.LAB_BLOCK_RIGHT, 0, w, Thyrocare.LAB_BLOCK_BOTTOM)
        strip = (0, Thyrocare.LAB_BLOCK_BOTTOM, w, header_bottom)
        return Plan([corner, strip], footer, [], style="compact")


LABS = (Clinova, Vijaya, Thyrocare)


def detect_lab(doc):
    for lab in LABS:
        if lab.matches(doc):
            return lab
    raise LayoutError("not a recognised lab report (supported: "
                      + ", ".join(lab.name for lab in LABS) + ")")


def _straddles(r, zone):
    return r.intersects(zone) and not zone.contains(r)


def _body_snapshot(page, zones, skip_images=()):
    """Everything on the page that lies outside the cleaned zones."""
    # Inflate so zero-height lines (rules, underlines) still count as intersecting.
    outside = lambda r: not any((pymupdf.Rect(r) + (-0.5, -0.5, 0.5, 0.5)).intersects(z)
                                for z in zones)
    words = [
        (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1), t)
        for x0, y0, x1, y1, t, *_ in page.get_text("words")
        if outside(pymupdf.Rect(x0, y0, x1, y1))
    ]
    images = sorted(
        (tuple(round(v, 1) for v in i["bbox"]), i["width"], i["height"], i["digest"])
        for i in page.get_image_info(hashes=True)
        if outside(pymupdf.Rect(i["bbox"])) and i["digest"] not in skip_images
    )
    drawings = sorted(
        tuple(round(v, 1) for v in d["rect"])
        for d in page.get_drawings()
        if outside(d["rect"])
    )
    return words, images, drawings


def _check_page(page, plan):
    """Refuse to cut through body content that crosses into a zone, or to draw
    our header/footer outside the cleared zones."""
    items = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]
    items += [pymupdf.Rect(i["bbox"]) for i in page.get_image_info(hashes=True)
              if i["digest"] not in plan.skip_images]
    for r in items:
        for z in plan.zones:
            if _straddles(r, z):
                raise LayoutError(
                    f"page {page.number + 1}: content at {tuple(round(v) for v in r)} "
                    "crosses the header/footer boundary"
                )
    w = page.rect.width
    for r in (pymupdf.Rect(LOGO_RECT), pymupdf.Rect(0, BAND_Y[0], w, BAND_Y[1])):
        if not any(z.contains(r) for z in plan.header):
            raise LayoutError(f"page {page.number + 1}: not enough room for the header")
    footer_room = FOOTER_RULE_Y if plan.style == "full" else COMPACT_RULE_Y
    if plan.footer.y0 > footer_room - 1:
        raise LayoutError(f"page {page.number + 1}: not enough room for the footer")
    slots = len(NOTE_BASELINES) if plan.style == "full" else 0
    if len(plan.notes) > slots:
        raise LayoutError(f"page {page.number + 1}: unexpected footer text {plan.notes}")


def _spaced_text(page, text, center_x, baseline, width, fontsize, color):
    """Draw letter-spaced text centred on center_x, stretched to `width`."""
    font = pymupdf.Font("helv")
    natural = font.text_length(text, fontsize=fontsize)
    gap = max(0, (width - natural) / (len(text) - 1))
    x = center_x - (natural + gap * (len(text) - 1)) / 2
    for ch in text:
        page.insert_text((x, baseline), ch, fontname="helv", fontsize=fontsize, color=color)
        x += font.text_length(ch, fontsize=fontsize) + gap


def _centered(page, text, baseline, fontname, fontsize, color, max_width):
    font = pymupdf.Font(fontname)
    while font.text_length(text, fontsize=fontsize) > max_width and fontsize > 5:
        fontsize -= 0.1
    width = font.text_length(text, fontsize=fontsize)
    page.insert_text(((page.rect.width - width) / 2, baseline), text,
                     fontname=fontname, fontsize=fontsize, color=color)


def _page_label(page, page_no, page_count, baseline):
    size = 4.95
    label = f"Page {page_no} of {page_count}"
    lw = pymupdf.get_text_length(label, fontname="helv", fontsize=size)
    page.insert_text((page.rect.width - 24 - lw, baseline), label, fontname="helv",
                     fontsize=size, color=TEXT)


def draw_header(page):
    page.insert_image(pymupdf.Rect(LOGO_RECT), filename=str(LOGO), keep_proportion=True)
    band = pymupdf.Rect(0, BAND_Y[0], page.rect.width, BAND_Y[1])
    page.draw_rect(band, color=None, fill=TEAL, overlay=True)
    _spaced_text(page, BRAND["band_text"], page.rect.width / 2, BAND_Y[1] - 3.6,
                 BAND_TEXT_WIDTH, 9.2, BAND_TEXT)


def draw_footer(page, page_no, page_count, notes):
    w = page.rect.width
    page.draw_line((0, FOOTER_RULE_Y), (w, FOOTER_RULE_Y), color=GREY_LINE, width=1)
    for (text, size), baseline in zip(notes, NOTE_BASELINES):
        page.insert_text((26.5, baseline), text, fontname="helv",
                         fontsize=min(size, 5.8), color=TEXT)
    # Dotted rule between the lab's lines and the Componder name/address.
    page.draw_line((26.5, NOTES_SEPARATOR_Y), (w - 24, NOTES_SEPARATOR_Y),
                   color=GREY_LINE, width=0.6, dashes="[1 1.6] 0", lineCap=1)
    _page_label(page, page_no, page_count, NOTE_BASELINES[0])
    _centered(page, BRAND["company"], COMPANY_BASELINE, "hebo", 8.8, TEXT, w - 60)
    _centered(page, BRAND["address"], ADDRESS_BASELINE, "helv", 8.4, TEXT, w - 70)


def draw_compact_footer(page, page_no, page_count):
    w = page.rect.width
    page.draw_line((0, COMPACT_RULE_Y), (w, COMPACT_RULE_Y), color=GREY_LINE, width=0.8)
    _page_label(page, page_no, page_count, COMPACT_COMPANY_BASELINE)
    _centered(page, BRAND["company"], COMPACT_COMPANY_BASELINE, "hebo", 7.8, TEXT, w - 200)
    _centered(page, BRAND["address"], COMPACT_ADDRESS_BASELINE, "helv", 7.2, TEXT, w - 70)


def rebrand(pdf_bytes):
    """Return (output_bytes, report) or raise LayoutError."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    if doc.is_encrypted:
        raise LayoutError("PDF is password protected")
    if doc.page_count == 0:
        raise LayoutError("PDF has no pages")

    lab = detect_lab(doc)
    keep = lab.pages(doc)
    dropped = doc.page_count - len(keep)
    if dropped:
        doc.select(keep)

    plans = []
    for page in doc:
        if abs(page.rect.width - 595) > 3 or abs(page.rect.height - 842) > 3:
            raise LayoutError(f"page {page.number + 1}: not an A4 page")
        plan = lab.plan(page)
        _check_page(page, plan)
        plans.append((plan, _body_snapshot(page, plan.zones, plan.skip_images)))

    for page, (plan, _) in zip(doc, plans):
        if hasattr(lab, "prepare"):
            lab.prepare(page)
            page = doc.reload_page(page)  # drop PyMuPDF's cached image list
        # Remove whole images only if they sit fully inside a zone; anything
        # crossing a boundary was already rejected above.
        for z in plan.zones:
            page.add_redact_annot(z, fill=(1, 1, 1))
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_REMOVE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE,
        )
        for z in plan.zones:
            if page.get_text("text", clip=z).strip():
                raise LayoutError(f"page {page.number + 1}: old header/footer text remains")
            if any(pymupdf.Rect(i["bbox"]).intersects(z) for i in page.get_image_info()):
                raise LayoutError(f"page {page.number + 1}: old header/footer image remains")

    for i, (page, (plan, _)) in enumerate(zip(doc, plans)):
        draw_header(page)
        if plan.style == "full":
            draw_footer(page, i + 1, doc.page_count, plan.notes)
        else:
            draw_compact_footer(page, i + 1, doc.page_count)

    doc.set_metadata({
        "title": "Laboratory Test Report",
        "author": "Componder Health Care Private Limited",
        "creator": "Componder",
        "producer": "Componder",
    })
    out = doc.tobytes(garbage=4, deflate=True)

    # Integrity check on the saved bytes: body must be identical.
    check = pymupdf.open(stream=out, filetype="pdf")
    report = {"lab": lab.name, "pages": check.page_count, "dropped": dropped,
              "words": 0, "images": 0}
    for page, (plan, before) in zip(check, plans):
        after = _body_snapshot(page, plan.zones)
        for label, b, a in zip(("text", "images", "drawings"), before, after):
            if b != a:
                raise LayoutError(
                    f"integrity check failed on page {page.number + 1} ({label} changed); "
                    "output discarded"
                )
        if any(i["digest"] in plan.skip_images for i in page.get_image_info(hashes=True)):
            raise LayoutError(f"page {page.number + 1}: old letterhead image remains")
        footer_text = " ".join(page.get_text("text", clip=plan.footer).split())
        for note, _ in plan.notes:
            if " ".join(note.split()) not in footer_text:
                raise LayoutError(f"page {page.number + 1}: footer note missing in output")
        report["words"] += len(before[0])
        report["images"] += len(before[1])
    return out, report


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    src = Path(argv[1])
    dst = Path(argv[2]) if len(argv) > 2 else src.with_name(f"Componder_{src.name}")
    try:
        out, report = rebrand(src.read_bytes())
    except LayoutError as e:
        print(f"FAILED {src.name}: {e}")
        return 1
    dst.write_bytes(out)
    dropped = f", {report['dropped']} cover/back page(s) dropped" if report["dropped"] else ""
    print(f"OK {src.name} -> {dst}  ({report['lab']}, {report['pages']} pages, "
          f"{report['words']} body words + {report['images']} images verified{dropped})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
