import asyncio
import json
import unittest
import zipfile

from fixtures import UNICODE_LINE, make
from mcp import Client
from PIL import Image
from test_bridge import FixtureCase

from keyhole.errors import KeyholeError
from keyhole.readers import parse_document


class ReaderTests(FixtureCase):
    def setUp(self):
        super().setUp()
        make(self.a, "Alpha")

    def test_formats_and_ranges(self):
        bridge = self.bridge()
        doc = bridge.read_file("Alpha", "sample.docx")
        self.assertIn(UNICODE_LINE, json.dumps(doc, ensure_ascii=False))
        self.assertIn("Marker", json.dumps(doc))
        deck = bridge.read_file("Alpha", "sample.pptx")
        self.assertIn(UNICODE_LINE, json.dumps(deck, ensure_ascii=False))
        sheet = bridge.read_file("Alpha", "sample.xlsx", sheet="Evidence", cell_range="B2:D2")
        self.assertEqual(sheet["rows"][0][2]["formula"], "=B2+C2")
        self.assertEqual(sheet["rows"][0][2]["value"], 18)
        self.assertFalse(sheet["recalculated"])
        pdf = bridge.read_file("Alpha", "sample.pdf")
        self.assertIn("Alpha PDF evidence", json.dumps(pdf))
        scan = bridge.read_file("Alpha", "scanned.pdf")
        self.assertFalse(scan["ocr_performed"])
        self.assertEqual(scan["items"][0]["text"], "")
        rendered = bridge.read_file("Alpha", "scanned.pdf", mode="image")
        self.assertEqual(rendered["image"]["mime_type"], "image/png")
        for ext in ("png", "jpg", "webp"):
            self.assertIn("image", bridge.read_file("Alpha", "shapes." + ext))

    def test_invalid_document_and_budgets(self):
        bridge = self.bridge()
        self.refused("invalid_document", lambda: bridge.read_file("Alpha", "damaged.pdf"))
        self.refused(
            "invalid_range",
            lambda: bridge.read_file("Alpha", "sample.xlsx", cell_range="A1:XFD1048576"),
        )
        self.refused("invalid_range", lambda: bridge.read_file("Alpha", "sample.pdf", limit=6))
        self.refused(
            "unsupported_mode", lambda: bridge.read_file("Alpha", "sample.docx", mode="image")
        )
        from pypdf import PdfWriter

        writer = PdfWriter(clone_from=self.a / "sample.pdf")
        writer.encrypt("synthetic-password")
        writer.write(self.a / "encrypted.pdf")
        self.refused("encrypted_document", lambda: bridge.read_file("Alpha", "encrypted.pdf"))
        huge = Image.new("1", (6000, 5000))
        huge.save(self.a / "too-many-pixels.png")
        self.refused("invalid_document", lambda: bridge.read_file("Alpha", "too-many-pixels.png"))
        with zipfile.ZipFile(self.a / "zip-bomb.docx", "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("big.xml", b"x" * (2 * 1024 * 1024))
        self.refused("archive_budget", lambda: bridge.read_file("Alpha", "zip-bomb.docx"))
        with zipfile.ZipFile(self.a / "entity.docx", "w") as z:
            z.writestr(
                "word/document.xml",
                b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
            )
        self.refused("invalid_document", lambda: bridge.read_file("Alpha", "entity.docx"))
        with self.assertRaises(KeyholeError) as error:
            parse_document(
                (self.a / "sample.pdf").read_bytes(),
                "pdf",
                {},
                self.store.read()["generation"],
                timeout=0.00001,
            )
        self.assertEqual(error.exception.code, "parser_timeout")

    def test_later_pdf_range_does_not_extract_an_unrequested_broken_page(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import NameObject, NumberObject

        writer = PdfWriter()
        first = writer.add_blank_page(width=600, height=800)
        first[NameObject("/Contents")] = NumberObject(7)
        writer.add_page(PdfReader(self.a / "sample.pdf").pages[0])
        writer.write(self.a / "partial.pdf")
        result = self.bridge().read_file("Alpha", "partial.pdf", start=2, limit=1)
        self.assertEqual(result["range"], {"unit": "page", "start": 2, "end": 2, "total": 2})
        self.assertIn("Alpha PDF evidence", result["items"][0]["text"])

    def test_xlsx_merged_cells_and_missing_formula_cache(self):
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        sheet.merge_cells("A1:B2")
        sheet["A1"] = "Zusammengeführte Kopfzeile"
        sheet["C1"] = "=1+1"
        sheet["D1"] = "https://example.invalid/not-fetched"
        book.save(self.a / "merged.xlsx")
        result = self.bridge().read_file("Alpha", "merged.xlsx", cell_range="A1:D2")
        self.assertEqual(result["rows"][0][0]["value"], "Zusammengeführte Kopfzeile")
        self.assertIsNone(result["rows"][1][1]["value"])
        self.assertEqual(result["rows"][0][2]["formula"], "=1+1")
        self.assertIsNone(result["rows"][0][2]["value"])
        self.assertFalse(result["rows"][0][2]["cached_value_available"])
        self.assertFalse(result["external_links_loaded"])

    def test_actual_mcp_image_content(self):
        async def check():
            async with Client(self.server_params(), read_timeout_seconds=30) as client:
                result = await client.call_tool(
                    "read_file", {"workspace": "Alpha", "path": "shapes.png"}
                )
                self.assertFalse(result.is_error)
                self.assertEqual([block.type for block in result.content], ["text", "image"])
                self.assertEqual(result.content[1].mime_type, "image/png")

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
