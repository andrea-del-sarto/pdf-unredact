import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Tuple

import pymupdf


__version__ = "1.2.1"


BBox = Tuple[float, float, float, float]
DARKNESS_THRESHOLD = 0.15


@dataclass
class RedactionCandidate:
    page: int
    box: BBox
    source: str
    recoverable: bool = False
    removable: bool = False
    removal_method: str | None = None
    probable_applied: bool = False
    words_under: int = 0
    chars_under: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RedactionStats:
    redaction_boxes_found: int
    recoverable_redactions: int
    removable_redactions: int
    annotation_removals: int
    content_stream_removals: int
    unsupported_recoverable_redactions: int
    probable_applied_redactions: int
    uncertain_redactions: int
    words_under_redactions: int
    chars_under_redactions: int
    total_words_extracted: int
    total_chars_extracted: int
    hidden_text_percentage: float
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
            f"Removable:                 {self.removable_redactions:,}\n"
            f"  Annotation delete:       {self.annotation_removals:,}\n"
            f"  Content-stream rewrite:  {self.content_stream_removals:,}\n"
            f"Recoverable, unsupported:  {self.unsupported_recoverable_redactions:,}\n"
            f"Probably applied:          {self.probable_applied_redactions:,}\n"
            f"Uncertain:                 {self.uncertain_redactions:,}\n"
            f"Words under redactions:    {self.words_under_redactions:,}\n"
            f"Characters under covers:   {self.chars_under_redactions:,}\n"
            f"Hidden text share:         {self.hidden_text_percentage:.1f}%\n"
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
            return all(float(c) <= DARKNESS_THRESHOLD for c in fill[:3])
        if len(fill) == 1:
            return float(fill[0]) <= DARKNESS_THRESHOLD
    try:
        return float(fill) <= DARKNESS_THRESHOLD
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


def _drawing_is_single_cover_shape(drawing) -> bool:
    """Return True only for a drawing that represents one cover-shaped path.

    PyMuPDF may report a compound table/grid drawing with a large bounding box
    and a dark fill even though the drawing consists of many thin rectangles.
    Treating that union bbox as one redaction creates false positives over live
    table text. For automatic redaction detection we therefore require a
    single normalized rectangle item. More complex paths remain conservative /
    unsupported unless they are normalized by PyMuPDF to one rectangle.
    """
    items = drawing.get("items") or []
    return len(items) == 1 and items[0][0] == "re"


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
                if not _drawing_is_single_cover_shape(drawing):
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



@dataclass
class CleanResult:
    annotations_removed: int = 0
    content_stream_rectangles_removed: int = 0
    unsupported_recoverable_rectangles: int = 0
    rewrite_attempts: int = 0
    rewrite_successes: int = 0
    rewrite_failures: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _PdfToken:
    value: str
    start: int
    end: int


def _pdf_tokens(data: bytes) -> List[_PdfToken]:
    """Tokenize enough PDF content syntax to safely locate isolated q...Q blocks."""
    tokens: List[_PdfToken] = []
    n = len(data)
    i = 0
    whitespace = b"\x00\x09\x0a\x0c\x0d\x20"
    delimiters = b"()<>[]{}/%"

    while i < n:
        if data[i] in whitespace:
            i += 1
            continue
        if data[i] == ord('%'):
            while i < n and data[i] not in b"\r\n":
                i += 1
            continue

        start = i
        ch = data[i]

        # Literal strings may contain arbitrary operator-looking bytes.
        if ch == ord('('):
            depth = 1
            i += 1
            while i < n and depth:
                if data[i] == ord('\\'):
                    i += 2
                    continue
                if data[i] == ord('('):
                    depth += 1
                elif data[i] == ord(')'):
                    depth -= 1
                i += 1
            tokens.append(_PdfToken("<string>", start, i))
            continue

        # Hex strings / dictionaries.
        if ch == ord('<'):
            if i + 1 < n and data[i + 1] == ord('<'):
                tokens.append(_PdfToken("<<", i, i + 2))
                i += 2
            else:
                i += 1
                while i < n and data[i] != ord('>'):
                    i += 1
                i = min(n, i + 1)
                tokens.append(_PdfToken("<hex>", start, i))
            continue
        if ch == ord('>') and i + 1 < n and data[i + 1] == ord('>'):
            tokens.append(_PdfToken(">>", i, i + 2))
            i += 2
            continue

        # Names are a single token.
        if ch == ord('/'):
            i += 1
            while i < n and data[i] not in whitespace + delimiters:
                i += 1
            tokens.append(_PdfToken(data[start:i].decode("latin1"), start, i))
            continue

        if ch in b"[]{}":
            tokens.append(_PdfToken(chr(ch), i, i + 1))
            i += 1
            continue

        i += 1
        while i < n and data[i] not in whitespace + delimiters:
            i += 1
        tokens.append(_PdfToken(data[start:i].decode("latin1"), start, i))

    return tokens


def _number(value: str):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_rect(x: float, y: float, w: float, h: float) -> pymupdf.Rect:
    return pymupdf.Rect(min(x, x + w), min(y, y + h), max(x, x + w), max(y, y + h))


def _rect_close(a: BBox, b: BBox, tolerance: float = 1.25) -> bool:
    return all(abs(float(x) - float(y)) <= tolerance for x, y in zip(a, b)) or _bbox_iou(a, b) >= 0.98


def _safe_extgstate_names(doc, page) -> set[str]:
    """Return ExtGState resource names safe for isolated cover removal.

    We intentionally whitelist only simple opacity / normal-blend states. Unknown
    graphics-state entries remain unsupported so the rewriter never removes a
    drawing whose visual semantics it does not understand.
    """
    import re

    try:
        kind, value = doc.xref_get_key(page.xref, "Resources/ExtGState")
    except Exception:
        return set()
    if kind != "dict" or not value:
        return set()

    safe = set()
    # Handles both inline dictionaries (/GS <<...>>) and indirect refs
    # (/GS 12 0 R). This is deliberately a small PDF-dictionary parser rather
    # than a general-purpose parser.
    pattern = re.compile(r"/([^/<>\s]+)\s*(<<.*?>>|\d+\s+\d+\s+R)")
    for match in pattern.finditer(value):
        name = "/" + match.group(1)
        spec = match.group(2)
        if spec.endswith(" R"):
            try:
                xref = int(spec.split()[0])
                spec = doc.xref_object(xref, compressed=False)
            except Exception:
                continue

        # Unknown soft masks or non-normal blend modes are not safe.
        if "/SMask" in spec and not re.search(r"/SMask\s*/None\b", spec):
            continue
        bm = re.search(r"/BM\s*/([^/<>\s]+)", spec)
        if bm and bm.group(1) != "Normal":
            continue

        def _alpha(key: str) -> float:
            m = re.search(rf"/{key}\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+))", spec)
            return float(m.group(1)) if m else 1.0

        # A near-opaque state is acceptable for a dark visual cover. Lower
        # alpha values could be legitimate highlighting / design graphics.
        if _alpha("ca") < 0.95 or _alpha("CA") < 0.95:
            continue

        # Allow only the small set of entries whose effect is understood here.
        keys = set(re.findall(r"/([A-Za-z][A-Za-z0-9]*)\b", spec))
        if not keys.issubset({"ca", "CA", "BM", "SMask", "None", "Normal", "Type", "ExtGState"}):
            continue
        safe.add(name)
    return safe


def _matrix_from_operands(values) -> pymupdf.Matrix:
    return pymupdf.Matrix(*[float(v) for v in values])


def _transform_point(x: float, y: float, ctm: pymupdf.Matrix, page) -> pymupdf.Point:
    return pymupdf.Point(x, y) * ctm * page.transformation_matrix


def _bbox_from_points(points: List[pymupdf.Point]) -> BBox:
    xs = [float(p.x) for p in points]
    ys = [float(p.y) for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _axis_aligned_rectangle_from_path(points: List[pymupdf.Point], tolerance: float = 0.75) -> BBox | None:
    """Recognize a four-corner axis-aligned rectangle after all transforms."""
    if len(points) != 4:
        return None
    box = _bbox_from_points(points)
    x0, y0, x1, y1 = box
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    expected = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    actual = [(float(p.x), float(p.y)) for p in points]
    for ex in expected:
        if not any(abs(ex[0] - ax) <= tolerance and abs(ex[1] - ay) <= tolerance for ax, ay in actual):
            return None
    return box


def _analyze_simple_rectangle_block(tokens: List[_PdfToken], page, doc=None, initial_ctm=None) -> BBox | None:
    """Return the page-space bbox for a safely isolated dark filled rectangle.

    Supported conservative subset:
    - ``re`` rectangles, including negative width/height;
    - rectangular ``m/l/l/l/h`` paths;
    - ``f``, ``f*``, ``B``, ``B*``, ``b`` and ``b*`` painting;
    - simple ``cm`` transformations tracked through a CTM;
    - a whitelisted opaque / normal-blend ExtGState (``gs``);
    - simple stroke width / stroke colour operators used by ``B``.

    Text, images, clipping, curves and unknown graphics-state operations remain
    unsupported by design.
    """
    operands: List[float] = []
    rectangles: List[BBox] = []
    path_points: List[pymupdf.Point] = []
    path_closed = False
    fill_rgb = (0.0, 0.0, 0.0)  # PDF default fill colour is black.
    fills = 0
    ctm = pymupdf.Matrix(initial_ctm) if initial_ctm is not None else pymupdf.Matrix(1, 0, 0, 1, 0, 0)
    safe_gs = _safe_extgstate_names(doc, page) if doc is not None else set()
    pending_name = None

    for token in tokens:
        value = token.value
        number = _number(value)
        if number is not None:
            operands.append(number)
            continue

        if value.startswith("/"):
            # Names are accepted only as an operand immediately consumed by gs.
            if operands or pending_name is not None:
                return None
            pending_name = value
            continue

        if value == "cm":
            if pending_name is not None or len(operands) < 6:
                return None
            vals = operands[-6:]
            operands = operands[:-6]
            # PDF concatenation: the newly supplied matrix is concatenated with
            # the current transformation matrix.
            ctm = _matrix_from_operands(vals) * ctm
            continue

        if value == "gs":
            if operands or pending_name is None or pending_name not in safe_gs:
                return None
            pending_name = None
            continue

        if pending_name is not None:
            return None

        if value == "re":
            if len(operands) < 4:
                return None
            x, y, w, h = operands[-4:]
            operands = operands[:-4]
            raw = [
                (x, y),
                (x + w, y),
                (x + w, y + h),
                (x, y + h),
            ]
            pts = [_transform_point(px, py, ctm, page) for px, py in raw]
            rectangles.append(_bbox_from_points(pts))
            continue

        if value == "m":
            if len(operands) < 2 or path_points:
                return None
            x, y = operands[-2:]
            operands = operands[:-2]
            path_points = [_transform_point(x, y, ctm, page)]
            path_closed = False
            continue
        if value == "l":
            if len(operands) < 2 or not path_points:
                return None
            x, y = operands[-2:]
            operands = operands[:-2]
            path_points.append(_transform_point(x, y, ctm, page))
            continue
        if value == "h":
            if operands or not path_points:
                return None
            path_closed = True
            continue
        if value == "n":
            # End current path without painting. A number of generators emit
            # this before starting the actual rectangle.
            if operands:
                return None
            path_points = []
            path_closed = False
            continue

        if value == "g":
            if len(operands) < 1:
                return None
            gray = operands[-1]
            operands = operands[:-1]
            fill_rgb = (gray, gray, gray)
            continue
        if value == "rg":
            if len(operands) < 3:
                return None
            fill_rgb = tuple(operands[-3:])
            operands = operands[:-3]
            continue
        if value == "k":
            if len(operands) < 4:
                return None
            c, m, y, k = operands[-4:]
            operands = operands[:-4]
            fill_rgb = (1 - min(1.0, c + k), 1 - min(1.0, m + k), 1 - min(1.0, y + k))
            continue

        # Stroke-only graphics state is harmless when the isolated rectangle is
        # deleted as a whole. Support the common subset needed for B/B* blocks.
        if value == "G":
            if len(operands) < 1:
                return None
            operands = operands[:-1]
            continue
        if value == "RG":
            if len(operands) < 3:
                return None
            operands = operands[:-3]
            continue
        if value == "K":
            if len(operands) < 4:
                return None
            operands = operands[:-4]
            continue
        if value in {"w", "J", "j", "M", "i"}:
            if len(operands) < 1:
                return None
            operands = operands[:-1]
            continue

        # f/f* fill; B/B* fill+stroke; b/b* close+fill+stroke.
        if value in {"f", "f*", "B", "B*", "b", "b*"}:
            if operands:
                return None
            if path_points:
                if value in {"b", "b*"}:
                    path_closed = True
                if not path_closed:
                    return None
                path_box = _axis_aligned_rectangle_from_path(path_points)
                if path_box is None:
                    return None
                rectangles.append(path_box)
                path_points = []
                path_closed = False
            fills += 1
            continue

        # q/Q are excluded by the caller. Everything else remains unsupported.
        return None

    if pending_name is not None or operands or path_points or len(rectangles) != 1 or fills != 1 or not _is_dark_fill(fill_rgb):
        return None
    return tuple(float(v) for v in rectangles[0])

def _rewrite_ranges_for_box(doc, page, target_box: BBox):
    """Find safely removable byte ranges for a dark rectangle candidate.

    Content streams are interpreted sequentially and the current transformation
    matrix (CTM) is tracked across ``cm`` and ``q`` / ``Q`` operators. This is
    important for PDFs that establish a page-wide transform before individual
    drawing blocks (a common pattern in office / print-generated PDFs).
    """
    matches = []
    identity = pymupdf.Matrix(1, 0, 0, 1, 0, 0)
    current_ctm = pymupdf.Matrix(identity)

    # A /Contents array is semantically one concatenated content stream, so a
    # graphics-state transform established in one stream can affect the next.
    for xref in page.get_contents():
        data = doc.xref_stream(xref)
        tokens = _pdf_tokens(data)
        # Each entry stores (q token index, CTM at q entry).
        stack = []
        block_ranges = []

        for index, token in enumerate(tokens):
            value = token.value

            if value == "q":
                stack.append((index, pymupdf.Matrix(current_ctm)))
                continue

            if value == "Q":
                if not stack:
                    # Malformed / unexpected restore: fail closed for state
                    # tracking rather than guessing the inherited CTM.
                    current_ctm = pymupdf.Matrix(identity)
                    continue
                start_index, entry_ctm = stack.pop()
                inner = tokens[start_index + 1:index]
                rect = _analyze_simple_rectangle_block(
                    inner, page, doc, initial_ctm=entry_ctm
                )
                if rect is not None and _rect_close(rect, target_box):
                    block_ranges.append((tokens[start_index].start, token.end))
                # q/Q restores the complete graphics state, including CTM.
                current_ctm = pymupdf.Matrix(entry_ctm)
                continue

            if value == "cm":
                # For state propagation we only need the six immediately
                # preceding numeric operands. The block analyzer performs the
                # stricter syntax validation before any bytes are removed.
                if index >= 6:
                    raw = [tokens[index - off].value for off in range(6, 0, -1)]
                    vals = [_number(v) for v in raw]
                    if all(v is not None for v in vals):
                        current_ctm = _matrix_from_operands(vals) * current_ctm

        # Some generators omit q/Q when a stream contains only the rectangle.
        # The inherited CTM at stream entry matters here too. Reconstruct it by
        # replaying only leading top-level cm operations before the rectangle is
        # validated by the strict analyzer.
        if not block_ranges and not any(t.value in {"q", "Q"} for t in tokens):
            rect = _analyze_simple_rectangle_block(tokens, page, doc)
            if rect is not None and _rect_close(rect, target_box):
                block_ranges.append((0, len(data)))

        if block_ranges:
            matches.append((xref, block_ranges))

    return matches


def _mark_removability(pdf_path: str, findings: List[RedactionCandidate]) -> None:
    doc = pymupdf.open(pdf_path)
    try:
        by_page = {}
        for candidate in findings:
            if not candidate.recoverable:
                candidate.removable = False
                candidate.removal_method = None
                continue
            if candidate.source.startswith("annotation:"):
                candidate.removable = True
                candidate.removal_method = "annotation_delete"
                continue
            if candidate.source == "dark_rectangle":
                by_page.setdefault(candidate.page, []).append(candidate)

        for page_no, candidates in by_page.items():
            page = doc[page_no - 1]
            for candidate in candidates:
                matches = _rewrite_ranges_for_box(doc, page, candidate.box)
                if matches:
                    candidate.removable = True
                    candidate.removal_method = "content_stream_rewrite"
                else:
                    candidate.removable = False
                    candidate.removal_method = "unsupported"
    finally:
        doc.close()


def _rewrite_dark_rectangles(doc, findings: List[RedactionCandidate], page_numbers=None) -> tuple[int, int, int]:
    """Remove safely isolated recoverable dark rectangles from page content streams.

    Returns ``(removed, attempts, failures)``. A failure means a candidate was
    classified as rewrite-capable during audit, but could not be matched again
    at export time. This is intentionally distinct from an unsupported candidate.
    """
    allowed_pages = set(page_numbers) if page_numbers is not None else None
    by_page = {}
    for candidate in findings:
        if (
            candidate.source == "dark_rectangle"
            and candidate.recoverable
            and candidate.removal_method == "content_stream_rewrite"
            and (allowed_pages is None or candidate.page in allowed_pages)
        ):
            by_page.setdefault(candidate.page, []).append(candidate)

    removed = 0
    attempts = 0
    failures = 0
    for page_no, candidates in by_page.items():
        page = doc[page_no - 1]
        modifications = {}
        matched_candidates = set()

        for candidate_index, candidate in enumerate(candidates):
            attempts += 1
            matches = _rewrite_ranges_for_box(doc, page, candidate.box)
            if not matches:
                failures += 1
                continue
            matched_candidates.add(candidate_index)
            for xref, ranges in matches:
                modifications.setdefault(xref, set()).update(ranges)

        if not modifications:
            continue

        merged_parts = []
        for xref in page.get_contents():
            data = doc.xref_stream(xref)
            ranges = sorted(modifications.get(xref, ()), reverse=True)
            for start, end in ranges:
                data = data[:start] + b"\n" + data[end:]
            merged_parts.append(data)

        new_xref = doc.get_new_xref()
        doc.update_object(new_xref, "<<>>")
        doc.update_stream(new_xref, b"\n".join(merged_parts))
        page.set_contents(new_xref)
        removed += len(matched_candidates)

    return removed, attempts, failures


def _candidate_from_dict(data: dict) -> RedactionCandidate:
    return RedactionCandidate(
        page=int(data["page"]),
        box=tuple(float(v) for v in data["box"]),
        source=str(data["source"]),
        recoverable=bool(data.get("recoverable", False)),
        removable=bool(data.get("removable", False)),
        removal_method=data.get("removal_method"),
        probable_applied=bool(data.get("probable_applied", False)),
        words_under=int(data.get("words_under", 0)),
        chars_under=int(data.get("chars_under", 0)),
    )


def _clean_document(doc, findings: List[RedactionCandidate], remove: str = "redactions", page_numbers=None) -> CleanResult:
    result = CleanResult()
    result.annotations_removed = _remove_annotations(doc, mode=remove, page_numbers=page_numbers)
    (
        result.content_stream_rectangles_removed,
        result.rewrite_attempts,
        result.rewrite_failures,
    ) = _rewrite_dark_rectangles(doc, findings, page_numbers=page_numbers)
    result.rewrite_successes = result.content_stream_rectangles_removed
    allowed_pages = set(page_numbers) if page_numbers is not None else None
    result.unsupported_recoverable_rectangles = sum(
        1
        for candidate in findings
        if candidate.source == "dark_rectangle"
        and candidate.recoverable
        and not candidate.removable
        and (allowed_pages is None or candidate.page in allowed_pages)
    )
    return result


def _looks_like_text_redaction_box(box: BBox) -> bool:
    """Heuristic for an opaque page rectangle with no surviving live text.

    This is intentionally conservative: compact, line-like bars can plausibly be
    an applied redaction; large design blocks are left uncertain instead of being
    labelled as probably applied.
    """
    x0, y0, x1, y1 = box
    width = max(0.0, x1 - x0)
    height = max(0.0, y1 - y0)
    return 8.0 <= height <= 45.0 and width >= max(24.0, height * 2.5)


def compute_redaction_stats(pdf_path: str) -> RedactionStats:
    candidates_by_page = detect_redaction_candidates(pdf_path)
    total_words = total_chars = words_under = chars_under = 0

    # Use PyMuPDF word geometry so coordinates are in the same page coordinate
    # system as annotations / drawings, including rotated pages.
    doc = pymupdf.open(pdf_path)
    try:
        for page_index, page in enumerate(doc):
            words = page.get_text("words")
            page_candidates = candidates_by_page[page_index]
            for word in words:
                text = str(word[4])
                if not text.strip():
                    continue
                total_words += 1
                total_chars += len(text)
                word_bbox = tuple(float(v) for v in word[:4])
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
    finally:
        doc.close()

    findings = [c for page in candidates_by_page for c in page]
    for candidate in findings:
        if candidate.recoverable:
            candidate.probable_applied = False
        elif candidate.source == "dark_rectangle":
            # A dark graphic with no text underneath is ambiguous. Only compact
            # text-line-like bars are labelled as probably applied redactions.
            candidate.probable_applied = _looks_like_text_redaction_box(candidate.box)

    _mark_removability(pdf_path, findings)

    recoverable_count = sum(c.recoverable for c in findings)
    removable_count = sum(c.removable for c in findings)
    annotation_removals = sum(c.removal_method == "annotation_delete" for c in findings)
    content_stream_removals = sum(c.removal_method == "content_stream_rewrite" for c in findings)
    unsupported_recoverable = sum(c.recoverable and not c.removable for c in findings)
    probable_applied_count = sum(c.probable_applied for c in findings)
    uncertain_count = len(findings) - recoverable_count - probable_applied_count
    hidden_text_percentage = (chars_under / total_chars * 100) if total_chars else 0.0

    return RedactionStats(
        redaction_boxes_found=len(findings),
        recoverable_redactions=recoverable_count,
        removable_redactions=removable_count,
        annotation_removals=annotation_removals,
        content_stream_removals=content_stream_removals,
        unsupported_recoverable_redactions=unsupported_recoverable,
        probable_applied_redactions=probable_applied_count,
        uncertain_redactions=uncertain_count,
        words_under_redactions=words_under,
        chars_under_redactions=chars_under,
        total_words_extracted=total_words,
        total_chars_extracted=total_chars,
        hidden_text_percentage=hidden_text_percentage,
        findings=findings,
    )


def _remove_annotations(doc, mode: str = "redactions", page_numbers=None) -> int:
    if mode not in {"redactions", "all-annotations"}:
        raise ValueError(f"Unsupported annotation removal mode: {mode}")

    removed = 0
    allowed_pages = set(page_numbers) if page_numbers is not None else None
    for page_index, page in enumerate(doc, start=1):
        if allowed_pages is not None and page_index not in allowed_pages:
            continue
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


def _validate_saved_pdf(path: str, expected_pages: int | None = None) -> None:
    """Fail fast if an exported PDF cannot be reopened or lost pages."""
    check = pymupdf.open(path)
    try:
        if check.page_count <= 0:
            raise ValueError("exported PDF contains no pages")
        if expected_pages is not None and check.page_count != expected_pages:
            raise ValueError(
                f"exported PDF page count changed: expected {expected_pages}, got {check.page_count}"
            )
    finally:
        check.close()


def make_clean_pdf(input_pdf: str, output_pdf: str, remove: str = "redactions") -> CleanResult:
    stats = compute_redaction_stats(input_pdf)
    doc = pymupdf.open(input_pdf)
    try:
        result = _clean_document(doc, stats.findings, remove=remove)
        expected_pages = doc.page_count
        doc.save(output_pdf, garbage=4, deflate=True)
        _validate_saved_pdf(output_pdf, expected_pages=expected_pages)
        return result
    finally:
        doc.close()


def make_side_by_side(input_pdf: str, output_pdf: str, remove: str = "redactions") -> CleanResult:
    stats = compute_redaction_stats(input_pdf)
    src = pymupdf.open(input_pdf)
    clean = pymupdf.open(input_pdf)
    out = pymupdf.open()
    try:
        result = _clean_document(clean, stats.findings, remove=remove)
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
        expected_pages = out.page_count
        out.save(output_pdf, garbage=4, deflate=True)
        _validate_saved_pdf(output_pdf, expected_pages=expected_pages)
        return result
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
