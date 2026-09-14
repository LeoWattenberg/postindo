from __future__ import annotations

import csv
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from scripts.extract_international_rates import (
    EXPECTED_INTERNATIONAL_CSV_SHA256,
    INTERNATIONAL_COLUMNS,
    REVIEWED_SOURCE_SHA256,
    InternationalExtractionError,
    InternationalRate,
    OcrProvenance,
    OcrWord,
    _load_cache,
    _majority_cell_value,
    _row_centers,
    _source_slug,
    _write_cache,
    foreground_sha256,
    international_rows_csv_sha256,
    parse_idr_ocr,
    parse_usd_ocr,
    reviewed_correction_counts,
    validate_international_rows,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEXT_COLUMNS = {"source_name", "country_code", "slug"}


def load_committed_rows() -> list[InternationalRate]:
    with (REPOSITORY_ROOT / "data/international_rates.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        records = list(csv.DictReader(handle))
    rows: list[InternationalRate] = []
    for record in records:
        values: dict[str, object] = {}
        for column in INTERNATIONAL_COLUMNS:
            raw = record[column]
            if column in TEXT_COLUMNS:
                values[column] = raw
            elif raw == "":
                values[column] = None
            else:
                values[column] = int(raw)
        rows.append(InternationalRate(**values))  # type: ignore[arg-type]
    return rows


class NumericOcrParserTests(unittest.TestCase):
    def test_rupiah_thousands_separators_and_source_dash(self) -> None:
        self.assertEqual(parse_idr_ocr("Rp 403.500"), 403_500)
        self.assertEqual(parse_idr_ocr("17,000"), 17_000)
        self.assertEqual(parse_idr_ocr("403-500"), 403_500)
        self.assertIsNone(parse_idr_ocr("—"))
        self.assertIsNone(parse_idr_ocr(""))

    def test_rupiah_ambiguous_or_unseparated_value_is_rejected(self) -> None:
        with self.assertRaises(InternationalExtractionError):
            parse_idr_ocr("7000")
        with self.assertRaises(InternationalExtractionError):
            parse_idr_ocr("7.000 8.000")

    def test_usd_decimal_is_converted_to_integer_cents(self) -> None:
        self.assertEqual(parse_usd_ocr("65,14"), 6_514)
        self.assertEqual(parse_usd_ocr("14.40"), 1_440)
        self.assertIsNone(parse_usd_ocr("-"))
        with self.assertRaises(InternationalExtractionError):
            parse_usd_ocr("6514")


class PositionalOcrTests(unittest.TestCase):
    def test_foreground_digest_includes_mode_and_dimensions(self) -> None:
        image = Image.new("L", (2, 3), 255)
        self.assertEqual(foreground_sha256(image), foreground_sha256(image.copy()))
        self.assertNotEqual(
            foreground_sha256(image),
            foreground_sha256(Image.new("L", (3, 2), 255)),
        )

    def test_row_ordinals_must_be_complete_and_increasing(self) -> None:
        words = [
            OcrWord("1", 5, 100, 10, 10, 90),
            OcrWord("2", 5, 200, 10, 10, 90),
            OcrWord("ignored", 200, 150, 50, 10, 99),
        ]
        self.assertEqual(_row_centers(words, 1_000, 1, 2), {1: 105.0, 2: 205.0})
        with self.assertRaisesRegex(InternationalExtractionError, "ordinal OCR berbeda"):
            _row_centers(words[:1], 1_000, 1, 2)
        with self.assertRaisesRegex(InternationalExtractionError, "tidak meningkat"):
            _row_centers([replace(words[0], top=200), replace(words[1], top=100)], 1_000, 1, 2)

    def test_source_slug_preserves_duplicate_country_codes_via_name(self) -> None:
        self.assertEqual(_source_slug("Eswatini (Swaziland)", "SZ"), "eswatini-swaziland-sz")
        self.assertEqual(_source_slug("Swaziland", "SZ"), "swaziland-sz")

    def test_cell_fallback_requires_its_own_consensus(self) -> None:
        image = Image.new("L", (1_000, 100), 255)
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "scripts.extract_international_rates._run_tesseract_text",
                side_effect=("tidak-terbaca", "?", "kosong"),
            ):
                with self.assertRaisesRegex(
                    InternationalExtractionError, "OCR sel tidak terbaca"
                ):
                    _majority_cell_value(
                        image,
                        Path(directory),
                        1,
                        "letter_printed_matter_small_packet_up_to_20g",
                        (10, 90),
                        "tesseract",
                        (None, None),
                    )

            with patch(
                "scripts.extract_international_rates._run_tesseract_text",
                side_effect=("-", "-", "tidak-terbaca"),
            ):
                self.assertIsNone(
                    _majority_cell_value(
                        image,
                        Path(directory),
                        1,
                        "letter_printed_matter_small_packet_up_to_20g",
                        (10, 90),
                        "tesseract",
                        (None, None),
                    )
                )


class InternationalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = load_committed_rows()

    def test_committed_rows_pass_the_extraction_contract(self) -> None:
        counts = validate_international_rows(
            self.rows, source_sha256=REVIEWED_SOURCE_SHA256
        )
        self.assertEqual(counts["international_rates"], 236)
        self.assertEqual(counts["international_distinct_country_codes"], 235)
        self.assertEqual(
            international_rows_csv_sha256(self.rows),
            EXPECTED_INTERNATIONAL_CSV_SHA256,
        )

    def test_reviewed_correction_counts_follow_the_trusted_pages(self) -> None:
        self.assertEqual(reviewed_correction_counts(()), (0, 0))
        self.assertEqual(reviewed_correction_counts((20_208,)), (1, 1))
        self.assertEqual(
            reviewed_correction_counts(range(20_198, 20_209)),
            (16, 50),
        )

    def test_official_source_rejects_a_valid_looking_non_anchor_change(self) -> None:
        changed = list(self.rows)
        changed[1] = replace(
            changed[1],
            letter_printed_matter_small_packet_over_20g_to_50g=18_000,
        )
        with self.assertRaisesRegex(InternationalExtractionError, "digest seluruh tabel"):
            validate_international_rows(
                changed, source_sha256=REVIEWED_SOURCE_SHA256
            )

    def test_partial_idr_service_group_is_rejected(self) -> None:
        changed = list(self.rows)
        changed[1] = replace(
            changed[1],
            letter_printed_matter_small_packet_over_20g_to_50g=None,
        )
        with self.assertRaisesRegex(InternationalExtractionError, "tidak lengkap"):
            validate_international_rows(changed)

    def test_cache_rejects_tampered_ocr_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "international.json"
            _write_cache(
                cache,
                REVIEWED_SOURCE_SHA256,
                self.rows,
                OcrProvenance(
                    engine="tesseract",
                    version="5.3.4",
                    reviewed_numeric_cells=16,
                    reviewed_identity_cells=50,
                ),
            )
            document = json.loads(cache.read_text(encoding="utf-8"))
            document["ocr"]["source_layer"] = "rendered_page"
            cache.write_text(json.dumps(document), encoding="utf-8")
            self.assertIsNone(_load_cache(cache, REVIEWED_SOURCE_SHA256))


if __name__ == "__main__":
    unittest.main()
