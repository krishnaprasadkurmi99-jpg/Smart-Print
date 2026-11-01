import unittest

import pymupdf

from document_intelligence import analyze_files, analyze_pdf


def make_pdf(pages: list[dict[str, object]]) -> bytes:
    document = pymupdf.open()
    for spec in pages:
        width, height = spec.get("size", (595.3, 841.9))
        page = document.new_page(width=width, height=height)
        if spec.get("rotation"):
            page.set_rotation(int(spec["rotation"]))
        if spec.get("text"):
            page.insert_text((50, 80), str(spec["text"]))
        if spec.get("color"):
            page.draw_rect(pymupdf.Rect(50, 100, 200, 220), color=(1, 0, 0), fill=(1, 0, 0))
    content = document.tobytes()
    document.close()
    return content


class DocumentIntelligenceTests(unittest.TestCase):
    def test_detects_blank_pages_and_page_orientations(self):
        data = make_pdf([{}, {"text": "Notes", "size": (841.9, 595.3)}])

        result = analyze_pdf(data, "notes.pdf")

        self.assertEqual(result["blank_pages"], [1])
        self.assertEqual(result["page_orientations"], ["Portrait", "Landscape"])
        self.assertEqual(result["paper_size"], "A4")

    def test_detects_display_orientation_of_rotated_pages(self):
        result = analyze_pdf(make_pdf([{"rotation": 90}]), "rotated.pdf")

        self.assertEqual(result["page_orientations"], ["Landscape"])

    def test_classifies_color_and_uses_transparent_category_rules(self):
        data = make_pdf([{"text": "Project Report methodology", "color": True}, {"text": "references"}])

        result = analyze_pdf(data, "semester.pdf")

        self.assertEqual(result["color_pages"], 1)
        self.assertEqual(result["bw_pages"], 1)
        self.assertEqual(result["category"], "Project Report")
        self.assertIn("not a trained AI", result["category_method"])

    def test_marks_identical_files_as_duplicates(self):
        data = make_pdf([{"text": "College Notes"}])

        results = analyze_files([("notes-a.pdf", data), ("notes-b.pdf", data)])

        self.assertFalse(results[0]["duplicate"])
        self.assertTrue(results[1]["duplicate"])
        self.assertEqual(results[1]["duplicate_of"], "notes-a.pdf")

    def test_recommendations_do_not_claim_unconfigured_currency_savings(self):
        result = analyze_pdf(make_pdf([{"text": "one"}, {"text": "two"}]), "notes.pdf")
        duplex = next(item for item in result["recommendations"] if item["type"] == "duplex")

        self.assertEqual(duplex["paper_sheets_saved"], 1)
        self.assertIsNone(duplex["price_savings"])
        self.assertIn("no configured per-sheet paper cost", duplex["reason"])

    def test_warns_about_large_low_resolution_images(self):
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
        image.clear_with(255)
        document = pymupdf.open()
        page = document.new_page()
        page.insert_image(pymupdf.Rect(100, 100, 400, 400), stream=image.tobytes("png"))
        data = document.tobytes()
        document.close()

        result = analyze_pdf(data, "low-resolution.pdf")

        self.assertEqual(len(result["low_resolution_images"]), 1)
        self.assertLess(result["low_resolution_images"][0]["effective_dpi"], 150)

    def test_rejects_encrypted_and_invalid_documents(self):
        document = pymupdf.open()
        document.new_page()
        encrypted = document.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw="secret",
            owner_pw="owner",
        )
        document.close()

        with self.assertRaisesRegex(ValueError, "password-protected"):
            analyze_pdf(encrypted, "locked.pdf")
        with self.assertRaises(Exception):
            analyze_pdf(b"not a PDF", "invalid.pdf")

    def test_cutoff_warning_is_explicitly_a_potential(self):
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((2, 20), "Near edge")
        data = document.tobytes()
        document.close()

        result = analyze_pdf(data, "edge.pdf")

        self.assertEqual(result["potential_cutoff_pages"], [1])
        self.assertTrue(result["pages"][0]["potential_cutoff"])


if __name__ == "__main__":
    unittest.main()
