import hashlib
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf


PAPER_SIZES = {
    "A3": (841.9, 1190.6),
    "A4": (595.3, 841.9),
    "A5": (419.5, 595.3),
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
}

CATEGORY_RULES = {
    "Resume": ("resume", "curriculum vitae", "work experience", "professional summary"),
    "Project Report": ("project report", "abstract", "methodology", "conclusion", "references"),
    "College Notes": ("lecture notes", "unit objectives", "class notes", "chapter", "syllabus"),
    "ID Documents": ("identity card", "date of birth", "passport", "aadhar", "aadhaar"),
    "Photo Printing": ("photo print", "photograph", "image gallery"),
}


def _paper_size(width: float, height: float) -> str:
    short_side, long_side = sorted((width, height))
    best_name, best_error = "Custom", float("inf")
    for name, dimensions in PAPER_SIZES.items():
        expected_short, expected_long = sorted(dimensions)
        error = abs(short_side - expected_short) + abs(long_side - expected_long)
        if error < best_error:
            best_name, best_error = name, error
    return best_name if best_error <= 18 else "Custom"


def _category(filename: str, text: str) -> str:
    haystack = f"{Path(filename).stem} {text}".lower()
    scores = {
        category: sum(2 if term in Path(filename).stem.lower() else 1 for term in terms if term in haystack)
        for category, terms in CATEGORY_RULES.items()
    }
    selected = max(scores, key=scores.get)
    return selected if scores[selected] else "General Document"


def _page_metrics(page: pymupdf.Page) -> dict[str, Any]:
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(0.15, 0.15),
        colorspace=pymupdf.csRGB,
        alpha=False,
    )
    samples = pixmap.samples
    non_white = 0
    colored = False
    border_content = False
    width = pixmap.width
    height = pixmap.height
    channels = pixmap.n
    band_x = max(1, round(width * 0.018))
    band_y = max(1, round(height * 0.018))

    for y in range(height):
        row_offset = y * width * channels
        is_border_row = y < band_y or y >= height - band_y
        for x in range(width):
            offset = row_offset + x * channels
            red, green, blue = samples[offset : offset + 3]
            if max(red, green, blue) - min(red, green, blue) > 10:
                colored = True
            if min(red, green, blue) < 245:
                non_white += 1
                if is_border_row or x < band_x or x >= width - band_x:
                    border_content = True

    page_rect = page.rect
    image_info = page.get_image_info(xrefs=True)
    image_warnings = []
    for image in image_info:
        image_rect = pymupdf.Rect(image["bbox"])
        if image_rect.width <= 0 or image_rect.height <= 0:
            continue
        dpi_x = image["width"] * 72 / image_rect.width
        dpi_y = image["height"] * 72 / image_rect.height
        area_ratio = (image_rect.width * image_rect.height) / max(page_rect.width * page_rect.height, 1)
        if min(dpi_x, dpi_y) < 150 and area_ratio >= 0.02:
            image_warnings.append({
                "effective_dpi": round(min(dpi_x, dpi_y)),
                "message": f"An image covering {round(area_ratio * 100)}% of this page is approximately {round(min(dpi_x, dpi_y))} DPI.",
            })

    page_text = page.get_text("text").strip()
    drawing_count = len(page.get_drawings())
    blank = (
        not page_text
        and not image_info
        and drawing_count == 0
        and non_white <= max(2, round(width * height * 0.0005))
    )
    content_ratio = non_white / max(width * height, 1)
    orientation = "Landscape" if page_rect.width > page_rect.height else "Portrait"

    return {
        "color": colored,
        "blank": blank,
        "orientation": orientation,
        "paper_size": _paper_size(page_rect.width, page_rect.height),
        "page_width_pt": round(page_rect.width, 1),
        "page_height_pt": round(page_rect.height, 1),
        "potential_cutoff": border_content,
        "low_resolution_images": image_warnings,
        "content_coverage_percent": round(content_ratio * 100, 2),
    }


def analyze_pdf(data: bytes | str | os.PathLike[str], filename: str) -> dict[str, Any]:
    if isinstance(data, bytes):
        digest = hashlib.sha256(data).hexdigest()
        document = pymupdf.open(stream=data, filetype="pdf")
    else:
        digest_builder = hashlib.sha256()
        with open(data, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest_builder.update(chunk)
        digest = digest_builder.hexdigest()
        document = pymupdf.open(os.fspath(data))
    if document.is_encrypted:
        document.close()
        raise ValueError(f"{filename} is password-protected and cannot be analyzed.")
    if not len(document):
        document.close()
        raise ValueError(f"{filename} has no pages.")

    page_results = []
    text_parts = []
    for page_number, page in enumerate(document, start=1):
        metrics = _page_metrics(page)
        metrics["page_number"] = page_number
        page_results.append(metrics)
        if len(text_parts) < 4:
            text_parts.append(page.get_text("text")[:4000])
    document.close()

    blank_pages = [page["page_number"] for page in page_results if page["blank"]]
    color_pages = sum(page["color"] for page in page_results)
    bw_pages = len(page_results) - color_pages
    total_pages = len(page_results)
    sizes = [page["paper_size"] for page in page_results]
    orientations = [page["orientation"] for page in page_results]
    most_common_size = Counter(sizes).most_common(1)[0][0]
    most_common_orientation = Counter(orientations).most_common(1)[0][0]
    low_resolution_warnings = [
        {"page_number": page["page_number"], **warning}
        for page in page_results
        for warning in page["low_resolution_images"]
    ]
    cutoff_pages = [page["page_number"] for page in page_results if page["potential_cutoff"]]

    recommendations = []
    if total_pages > 1:
        sheets_saved = total_pages // 2
        if sheets_saved:
            recommendations.append({
                "type": "duplex",
                "title": "Consider duplex printing",
                "detail": f"Two-sided printing could reduce paper use by about {sheets_saved} sheet(s) for this document.",
                "paper_sheets_saved": sheets_saved,
                "price_savings": None,
                "reason": "The app has no configured per-sheet paper cost, so currency savings are not estimated.",
            })
    recommendations.append({
        "type": "paper_size",
        "title": f"Use {most_common_size} paper",
        "detail": f"{most_common_size} matches the most common detected page size; {most_common_orientation.lower()} orientation.",
        "paper_size": most_common_size,
        "orientation": most_common_orientation,
    })
    if color_pages and not bw_pages:
        recommendations.append({
            "type": "color_mode",
            "title": "Review color printing",
            "detail": "Every page contains color. Choose black-and-white only if color is not important.",
            "color_mode": "color",
        })
    elif color_pages:
        recommendations.append({
            "type": "color_mode",
            "title": "Keep color for pages that need it",
            "detail": f"{color_pages} page(s) contain color; the rest can be printed in black and white.",
            "color_mode": "mixed",
        })
    if low_resolution_warnings:
        recommendations.append({
            "type": "quality",
            "title": "Review low-resolution images",
            "detail": f"{len(low_resolution_warnings)} large image(s) are below 150 DPI and may appear soft.",
        })

    average_coverage = round(
        sum(page["content_coverage_percent"] for page in page_results) / total_pages,
        2,
    )
    return {
        "filename": filename,
        "sha256": digest,
        "total_pages": total_pages,
        "bw_pages": bw_pages,
        "color_pages": color_pages,
        "blank_pages": blank_pages,
        "duplicate": False,
        "duplicate_of": None,
        "category": _category(filename, "\n".join(text_parts)),
        "category_method": "filename and extracted-text rules; not a trained AI model",
        "paper_size": most_common_size,
        "orientation": most_common_orientation,
        "page_sizes": sizes,
        "page_orientations": orientations,
        "potential_cutoff_pages": cutoff_pages,
        "low_resolution_images": low_resolution_warnings,
        "estimated_average_page_coverage_percent": average_coverage,
        "coverage_note": "Raster coverage is a visual proxy, not measured ink or toner consumption.",
        "recommendations": recommendations,
        "pages": page_results,
    }


def analyze_files(
    files: list[tuple[str, bytes | str | os.PathLike[str]]],
) -> list[dict[str, Any]]:
    results = []
    seen_hashes: dict[str, str] = {}
    for filename, data in files:
        result = analyze_pdf(data, filename)
        duplicate_name = seen_hashes.get(result["sha256"])
        if duplicate_name:
            result["duplicate"] = True
            result["duplicate_of"] = duplicate_name
            result["recommendations"].append({
                "type": "duplicate",
                "title": "Possible duplicate document",
                "detail": f"This file matches {duplicate_name} byte-for-byte.",
            })
        else:
            seen_hashes[result["sha256"]] = filename
        results.append(result)
    return results
