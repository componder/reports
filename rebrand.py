"""Replace the partner lab's header/footer on a report PDF with Componder branding.

The report body (patient details, results, interpretation, signatures, QR) is
never re-rendered: we only redact the header and footer bands, then draw our
own branding into the cleared space. After conversion an integrity check
compares every word, image and drawing in the body against the original and
refuses the output if anything moved or disappeared.

CLI:  python rebrand.py input.pdf [output.pdf]
"""

import sys
from pathlib import Path

import pymupdf

ASSETS = Path(__file__).parent / "assets"
LOGO = ASSETS / "logo.png"

BRAND = {
    "band_text": "LABORATORY TEST REPORT",
    "company": "COMPONDER HEALTH CARE PRIVATE LIMITED",
    "address": "1-7-749/A/B, Brahmamagari Khaman, Mohan Nagar, Ramnagar, "
               "Hyderabad - 500020, Mobile Number : 96424 96494",
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
FOOTER_RULE_Y = 790.0
NOTE_BASELINES = (797.2, 803.7)
COMPANY_BASELINE = 815.5
ADDRESS_BASELINE = 827.5

# Footer lines from the partner lab that are their branding, not clinical notes.
DROP_FOOTER_LINES = ("Processed at", "Page ")


class LayoutError(Exception):
    """The PDF doesn't look like a report layout we know how to rebrand."""


def _lines(page):
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                yield pymupdf.Rect(line["bbox"]), text


def find_zones(page):
    """Return (header_rect, footer_rect) for the bands to clean on this page.

    Header: everything above the "Patient Name" line.
    Footer: everything below the horizontal rule that separates the
    signatures from the lab's address block.
    """
    w, h = page.rect.width, page.rect.height

    header_bottom = None
    for rect, text in _lines(page):
        if text.startswith("Patient Name"):
            header_bottom = rect.y0 - 1
            break
    if header_bottom is None:
        raise LayoutError(f"page {page.number + 1}: 'Patient Name' line not found")

    footer_top = None
    for d in page.get_drawings():
        r = d["rect"]
        if r.y0 > h * 0.85 and r.height < 3 and r.width > w * 0.6:
            footer_top = r.y0 - 3
            break
    if footer_top is None:
        for rect, text in _lines(page):
            if rect.y0 > h * 0.85 and ("electronically authenticated" in text
                                       or text.startswith("Processed at")):
                footer_top = min(footer_top or rect.y0, rect.y0) - 4
    if footer_top is None:
        raise LayoutError(f"page {page.number + 1}: footer block not found")

    if header_bottom < BAND_Y[1] + 2:
        raise LayoutError(f"page {page.number + 1}: not enough room for the header")
    if footer_top > FOOTER_RULE_Y - 1:
        raise LayoutError(f"page {page.number + 1}: not enough room for the footer")

    return pymupdf.Rect(0, 0, w, header_bottom), pymupdf.Rect(0, footer_top, w, h)


def footer_notes(page, footer):
    """Clinical notes in the lab's footer (e.g. the authentication / NOTE lines),
    carried over verbatim. Address lines and anything naming the lab are dropped."""
    notes = [
        text for rect, text in _lines(page)
        if footer.contains(rect)
        and not text.startswith(DROP_FOOTER_LINES)
        and "clinova" not in text.lower()
    ]
    if len(notes) > len(NOTE_BASELINES):
        raise LayoutError(f"page {page.number + 1}: unexpected footer text {notes}")
    return notes


def _straddles(r, zone):
    return r.intersects(zone) and not zone.contains(r)


def _body_snapshot(page, zones):
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
        if outside(pymupdf.Rect(i["bbox"]))
    )
    drawings = sorted(
        tuple(round(v, 1) for v in d["rect"])
        for d in page.get_drawings()
        if outside(d["rect"])
    )
    return words, images, drawings


def _check_no_straddlers(page, zones):
    """Refuse to cut through body content that crosses into a zone."""
    items = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]
    items += [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    for r in items:
        for z in zones:
            if _straddles(r, z):
                raise LayoutError(
                    f"page {page.number + 1}: content at {tuple(round(v) for v in r)} "
                    "crosses the header/footer boundary"
                )


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


def draw_header(page):
    page.insert_image(pymupdf.Rect(LOGO_RECT), filename=str(LOGO), keep_proportion=True)
    band = pymupdf.Rect(0, BAND_Y[0], page.rect.width, BAND_Y[1])
    page.draw_rect(band, color=None, fill=TEAL, overlay=True)
    _spaced_text(page, BRAND["band_text"], page.rect.width / 2, BAND_Y[1] - 3.6,
                 BAND_TEXT_WIDTH, 9.2, BAND_TEXT)


def draw_footer(page, page_no, page_count, notes):
    w = page.rect.width
    page.draw_line((0, FOOTER_RULE_Y), (w, FOOTER_RULE_Y), color=GREY_LINE, width=1)
    size = 4.95
    for text, baseline in zip(notes, NOTE_BASELINES):
        page.insert_text((26.5, baseline), text, fontname="helv", fontsize=size, color=TEXT)
    label = f"Page {page_no} of {page_count}"
    lw = pymupdf.get_text_length(label, fontname="helv", fontsize=size)
    page.insert_text((w - 24 - lw, NOTE_BASELINES[0]), label, fontname="helv",
                     fontsize=size, color=TEXT)
    _centered(page, BRAND["company"], COMPANY_BASELINE, "hebo", 9.6, TEXT, w - 60)
    _centered(page, BRAND["address"], ADDRESS_BASELINE, "helv", 9.2, TEXT, w - 70)


def rebrand(pdf_bytes):
    """Return (output_bytes, report) or raise LayoutError."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    if doc.is_encrypted:
        raise LayoutError("PDF is password protected")
    if doc.page_count == 0:
        raise LayoutError("PDF has no pages")

    plans = []
    for page in doc:
        if abs(page.rect.width - 595) > 3 or abs(page.rect.height - 842) > 3:
            raise LayoutError(f"page {page.number + 1}: not an A4 page")
        zones = find_zones(page)
        _check_no_straddlers(page, zones)
        plans.append((zones, _body_snapshot(page, zones), footer_notes(page, zones[1])))

    for page, (zones, *_) in zip(doc, plans):
        # Remove whole images only if they sit fully inside a zone; anything
        # crossing a boundary was already rejected above.
        for z in zones:
            page.add_redact_annot(z, fill=(1, 1, 1))
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_REMOVE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE,
        )
        for z in zones:
            if page.get_text("text", clip=z).strip():
                raise LayoutError(f"page {page.number + 1}: old header/footer text remains")
            if any(pymupdf.Rect(i["bbox"]).intersects(z) for i in page.get_image_info()):
                raise LayoutError(f"page {page.number + 1}: old header/footer image remains")

    for i, (page, (_, _, notes)) in enumerate(zip(doc, plans)):
        draw_header(page)
        draw_footer(page, i + 1, doc.page_count, notes)

    doc.set_metadata({
        "title": "Laboratory Test Report",
        "author": "Componder Health Care Private Limited",
        "creator": "Componder",
        "producer": "Componder",
    })
    out = doc.tobytes(garbage=4, deflate=True)

    # Integrity check on the saved bytes: body must be identical.
    check = pymupdf.open(stream=out, filetype="pdf")
    report = {"pages": check.page_count, "words": 0, "images": 0}
    for page, (zones, before, notes) in zip(check, plans):
        after = _body_snapshot(page, zones)
        for label, b, a in zip(("text", "images", "drawings"), before, after):
            if b != a:
                raise LayoutError(
                    f"integrity check failed on page {page.number + 1} ({label} changed); "
                    "output discarded"
                )
        footer_text = " ".join(page.get_text("text", clip=zones[1]).split())
        for note in notes:
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
    print(f"OK {src.name} -> {dst}  ({report['pages']} pages, "
          f"{report['words']} body words + {report['images']} images verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
