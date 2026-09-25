import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Tuple

import pdfplumber
import pymupdf


__version__ = "1.1.1"


BBox = Tuple[float, float, float, float]


@dataclass
class RedactionCandidate:
    page: int
    box: BBox
    source: str
    recoverable: bool = False
    probable_applied: bool = False
    words_under: int = 0
    chars_under: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RedactionStats:
    redaction_boxes_found: int
    recoverable_redactions: int
    probable_applied_redactions: int
    uncertain_redactions: int
    words_under_redactions: int
    chars_under_redactions: int
    total_words_extracted: int
    total_chars_extracted: int
    recovery_rate: float
    findings: List[RedactionCandidate]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def display(self) -> str:
        if self.redaction_boxes_found == 0:
            return (
                "\nRedaction audit\n"
                "-----------------------------------\n"
                "No redaction candidates detected\n"
                f"Total text extracted: {self.total_words_extracted:,} words "
                f"({self.total_chars_extracted:,} chars)\n"
                "-----------------------------------\n"
            )

        return (
            "\nRedaction audit\n"
            "-----------------------------------\n"
            f"Redactions detected:       {self.redaction_boxes_found:,}\n"
            f"Recoverable:               {self.recoverable_redactions:,}\n"
            f"Probably applied:          {self.probable_applied_redactions:,}\n"
            f"Uncertain:                 {self.uncertain_redactions:,}\n"
            f"Words under redactions:    {self.words_under_redactions:,}\n"
            f"Characters recovered:      {self.chars_under_redactions:,}\n"
            f"Hidden-text rate:          {self.recovery_rate:.1f}%\n"
            "-----------------------------------\n"
            f"Total extracted:           {self.total_words_extracted:,} words\n"
            "\n"
            "Classification note: 'probably applied' is heuristic. It means a dark\n"
            "redaction-like rectangle was found but no underlying text is present\n"
            "in the PDF text layer. It may also be an unrelated graphic element.\n"
        )


def _bbox_key(box: BBox, precision: int = 2) -> Tuple[float, float, float, float]:
    return tuple(round(float(v), precision) for v in box)


def _is_dark_fill(fill) -> bool:
    if fill is None:
        return False
    if isinstance(fill, (list, tuple)):
        if len(fill) >= 3:
            return all(float(c) < 0.1 for c in fill[:3])
        if len(fill) == 1:
            return float(fill[0]) < 0.1
    try:
        return float(fill) < 0.1
    except (TypeError, ValueError):
        return False


def _annotation_is_dark(annot) -> bool:
    colors = annot.colors or {}
    return _is_dark_fill(colors.get("fill")) or _is_dark_fill(colors.get("stroke"))


def _annotation_quads(annot) -> List[BBox]:
    vertices = annot.vertices or []
    boxes: List[BBox] = []
    if len(vertices) >= 4 and len(vertices) % 4 == 0:
        for i in range(0, len(vertices), 4):
            quad = vertices[i : i + 4]
            xs = [float(pt[0]) for pt in quad]
            ys = [float(pt[1]) for pt in quad]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
    if not boxes:
        boxes.append(tuple(float(v) for v in annot.rect))
    return boxes


def _bbox_iou(a: BBox, b: BBox) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix0 >= ix1 or iy0 >= iy1:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _annotation_opacity(annot) -> float:
    opacity = float(annot.opacity)
    return 1.0 if opacity < 0 else opacity


def _is_redaction_markup(annot) -> bool:
    annot_type = annot.type[0]
    if annot_type == pymupdf.PDF_ANNOT_REDACT:
        return True
    if annot_type == pymupdf.PDF_ANNOT_HIGHLIGHT:
        return _annotation_opacity(annot) >= 0.8 and _annotation_is_dark(annot)
    if annot_type == pymupdf.PDF_ANNOT_SQUARE:
        colors = annot.colors or {}
        return _annotation_opacity(annot) >= 0.8 and _is_dark_fill(colors.get("fill"))
    return False


def detect_redaction_candidates(pdf_path: str) -> List[List[RedactionCandidate]]:
    doc = pymupdf.open(pdf_path)
    pages: List[List[RedactionCandidate]] = []

    try:
        for page_index, page in enumerate(doc):
            page_candidates: List[RedactionCandidate] = []
            annotation_boxes: List[BBox] = []

            for annot in page.annots() or []:
                if not _is_redaction_markup(annot):
                    continue
                source_name = (
                    "annotation:redact"
                    if annot.type[0] == pymupdf.PDF_ANNOT_REDACT
                    else f"annotation:{annot.type[1].lower()}"
                )
                boxes = _annotation_quads(annot)
                annotation_boxes.extend(boxes)
                for box in boxes:
                    page_candidates.append(
                        RedactionCandidate(page=page_index + 1, box=box, source=source_name)
                    )

            for drawing in page.get_drawings():
                if not _is_dark_fill(drawing.get("fill")):
                    continue
                rect = drawing.get("rect")
                if rect is None:
                    continue
                box = tuple(float(v) for v in rect)
                width = box[2] - box[0]
                height = box[3] - box[1]
                if width <= 10 or height <= 5:
                    continue
                if any(_bbox_iou(box, abox) >= 0.9 for abox in annotation_boxes):
                    continue
                page_candidates.append(
                    RedactionCandidate(page=page_index + 1, box=box, source="dark_rectangle")
                )

            unique = []
            seen = set()
            for candidate in page_candidates:
                key = (candidate.source, _bbox_key(candidate.box))
                if key not in seen:
                    seen.add(key)
                    unique.append(candidate)
            pages.append(unique)
    finally:
        doc.close()

    return pages


def word_overlaps_box(word_bbox: BBox, box: BBox, overlap_threshold: float = 0.5) -> bool:
    wx0, wy0, wx1, wy1 = word_bbox
    bx0, by0, bx1, by1 = box
    ix0 = max(wx0, bx0)
    iy0 = max(wy0, by0)
    ix1 = min(wx1, bx1)
    iy1 = min(wy1, by1)
    if ix0 >= ix1 or iy0 >= iy1:
        return False
    intersection_area = (ix1 - ix0) * (iy1 - iy0)
    word_area = (wx1 - wx0) * (wy1 - wy0)
    return word_area > 0 and (intersection_area / word_area) >= overlap_threshold


def compute_redaction_stats(pdf_path: str) -> RedactionStats:
    candidates_by_page = detect_redaction_candidates(pdf_path)
    total_words = total_chars = words_under = chars_under = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            page_candidates = candidates_by_page[page_index]
            for word in words:
                text = word.get("text", "")
                if not text.strip():
                    continue
                total_words += 1
                total_chars += len(text)
                word_bbox = (
                    float(word.get("x0", 0)),
                    float(word.get("top", 0)),
                    float(word.get("x1", 0)),
                    float(word.get("bottom", 0)),
                )
                matched = []
                for candidate in page_candidates:
                    if word_overlaps_box(word_bbox, candidate.box):
                        candidate.recoverable = True
                        candidate.words_under += 1
                        candidate.chars_under += len(text)
                        matched.append(candidate)
                if matched:
                    words_under += 1
                    chars_under += len(text)

    findings = [c for page in candidates_by_page for c in page]
    for candidate in findings:
        if candidate.recoverable:
            candidate.probable_applied = False
        elif candidate.source == "dark_rectangle":
            candidate.probable_applied = True

    recoverable_count = sum(c.recoverable for c in findings)
    probable_applied_count = sum(c.probable_applied for c in findings)
    uncertain_count = len(findings) - recoverable_count - probable_applied_count
    recovery_rate = (chars_under / total_chars * 100) if total_chars else 0.0

    return RedactionStats(
        redaction_boxes_found=len(findings),
        recoverable_redactions=recoverable_count,
        probable_applied_redactions=probable_applied_count,
        uncertain_redactions=uncertain_count,
        words_under_redactions=words_under,
        chars_under_redactions=chars_under,
        total_words_extracted=total_words,
        total_chars_extracted=total_chars,
        recovery_rate=recovery_rate,
        findings=findings,
    )


def _remove_annotations(doc, mode: str = "redactions") -> int:
    if mode not in {"redactions", "all-annotations"}:
        raise ValueError(f"Unsupported annotation removal mode: {mode}")

    removed = 0
    for page in doc:
        annots = list(page.annots() or [])
        if mode == "all-annotations":
            to_remove = annots
        else:
            primary = [annot for annot in annots if _is_redaction_markup(annot)]
            primary_boxes = [box for annot in primary for box in _annotation_quads(annot)]
            companions = []
            for annot in annots:
                if annot.type[0] != pymupdf.PDF_ANNOT_STRIKE_OUT:
                    continue
                if any(
                    _bbox_iou(sbox, pbox) >= 0.5
                    for sbox in _annotation_quads(annot)
                    for pbox in primary_boxes
                ):
                    companions.append(annot)
            to_remove = primary + companions

        seen_xrefs = set()
        for annot in to_remove:
            if annot.xref in seen_xrefs:
                continue
            seen_xrefs.add(annot.xref)
            page.delete_annot(annot)
            removed += 1
    return removed


def make_clean_pdf(input_pdf: str, output_pdf: str, remove: str = "redactions") -> None:
    doc = pymupdf.open(input_pdf)
    try:
        _remove_annotations(doc, mode=remove)
        doc.save(output_pdf, garbage=4, deflate=True)
    finally:
        doc.close()


def make_side_by_side(input_pdf: str, output_pdf: str, remove: str = "redactions") -> None:
    src = pymupdf.open(input_pdf)
    clean = pymupdf.open(input_pdf)
    out = pymupdf.open()
    try:
        _remove_annotations(clean, mode=remove)
        for page_number in range(src.page_count):
            src_page = src[page_number]
            rect = src_page.rect
            w, h = rect.width, rect.height
            dst = out.new_page(width=2 * w, height=h)
            original_pix = src_page.get_pixmap(
                matrix=pymupdf.Matrix(2, 2), alpha=False, annots=True
            )
            dst.insert_image(
                pymupdf.Rect(0, 0, w, h),
                pixmap=original_pix,
                keep_proportion=False,
                overlay=True,
            )
            dst.show_pdf_page(
                pymupdf.Rect(w, 0, 2 * w, h),
                clean,
                page_number,
                keep_proportion=False,
                overlay=True,
            )
        out.save(output_pdf, garbage=4, deflate=True)
    finally:
        out.close()
        clean.close()
        src.close()


def ensure_parent_dir(path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="pdf-unredact: local PDF redaction auditing and recovery"
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("input_pdf", nargs="?", help="Path to input PDF")
    ap.add_argument("-o", "--output", default=None, help="Output PDF path")
    ap.add_argument("--mode", choices=["side_by_side", "clean"], default="side_by_side")
    ap.add_argument(
        "--remove",
        choices=["redactions", "all-annotations"],
        default="redactions",
        help="Annotations to remove from the cleaned output",
    )
    ap.add_argument("--stats", action="store_true", help="Display redaction audit statistics")
    ap.add_argument("--stats-json", metavar="FILE", help="Write detailed audit stats to JSON")
    ap.add_argument("--web", action="store_true", help="Start the private local web interface")
    ap.add_argument("--port", type=int, default=8765, help="Preferred local web port (default: 8765; falls back automatically if busy)")
    ap.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    args = ap.parse_args()

    if args.web:
        if not 1 <= args.port <= 65535:
            ap.error("--port must be between 1 and 65535")
        from web_app import run_web

        run_web(port=args.port, open_browser=not args.no_browser)
        return

    if not args.input_pdf:
        ap.error("input_pdf is required unless --web is used")
    if not os.path.exists(args.input_pdf):
        ap.error(f"input PDF does not exist: {args.input_pdf}")

    if args.output is None:
        base_dir = os.path.dirname(args.input_pdf) or "."
        new_folder = os.path.join(base_dir, "pdf-unredact-output")
        pdf_name = Path(args.input_pdf).stem
        suffix = "_side_by_side.pdf" if args.mode == "side_by_side" else "_clean.pdf"
        args.output = os.path.join(new_folder, pdf_name + suffix)

    if os.path.abspath(args.input_pdf) == os.path.abspath(args.output):
        ap.error("output PDF must be different from input PDF")

    ensure_parent_dir(args.output)
    if args.mode == "side_by_side":
        make_side_by_side(args.input_pdf, args.output, remove=args.remove)
    else:
        make_clean_pdf(args.input_pdf, args.output, remove=args.remove)
    print(f"Wrote: {args.output}")

    if args.stats or args.stats_json:
        stats = compute_redaction_stats(args.input_pdf)
        if args.stats:
            print(stats.display())
        if args.stats_json:
            ensure_parent_dir(args.stats_json)
            with open(args.stats_json, "w", encoding="utf-8") as f:
                f.write(stats.to_json())
            print(f"Stats written to: {args.stats_json}")


if __name__ == "__main__":
    main()
