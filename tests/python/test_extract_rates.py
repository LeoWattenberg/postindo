from __future__ import annotations

import unittest
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from scripts.extract_rates import (
    CACHE_FORMAT_VERSION,
    DOMESTIC_CACHE_EXTRACTOR_VERSION,
    ExtractionError,
    ParsedRow,
    _cache_header_matches,
    _unique_slugs,
    _validate_first_block_origin,
    parse_layout_page,
    rupiah_to_int,
    slugify,
)


NORMAL_ROW = (
    "  1  Jakartapusat  10000  10000  Jakartabarat  11000  11000  "
    "3.500  4.000  4.500  5.000  10.000  3.000  Bebas Biaya  6.500  19.500  6.500"
)


class RupiahTests(unittest.TestCase):
    def test_indonesian_thousands_are_integers(self) -> None:
        self.assertEqual(rupiah_to_int("3.500"), 3500)
        self.assertEqual(rupiah_to_int("105.500"), 105500)

    def test_bad_separator_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            rupiah_to_int("3.50")


class PageParserTests(unittest.TestCase):
    def test_normal_row_and_free_sekogram(self) -> None:
        row = parse_layout_page(NORMAL_ROW, 4)[0]
        self.assertEqual(row.source_row, 1)
        self.assertEqual(row.origin_id, "10000")
        self.assertEqual(row.destination_id, "11000")
        self.assertEqual(row.letter_up_to_100g, 3500)
        self.assertEqual(row.sekogram, 0)

    def test_wrapped_names_use_origin_and_destination_positions(self) -> None:
        text = (
            "355168  Pegununganbin  99573B1  99000  Pematangsianta  21100  21100  "
            "53.500 60.500 68.000 75.000 150.000 55.000 Bebas Biaya 87.500 225.000 75.000\n"
            "           tang                             r\n"
        )
        row = parse_layout_page(text, 19728)[0]
        self.assertEqual(row.origin_name, "Pegununganbintang")
        self.assertEqual(row.destination_name, "Pematangsiantar")

    def test_b1_id_and_punctuation_are_preserved(self) -> None:
        text = (
            "571 Rajaampat (Ba) 98481B1 98400 Makimi 98842B1 98800 "
            "65.000 74.000 83.000 91.500 183.000 58.000 Bebas Biaya 91.500 274.500 91.500"
        )
        row = parse_layout_page(text, 36)[0]
        self.assertEqual(row.origin_name, "Rajaampat (Ba)")
        self.assertEqual(row.origin_id, "98481B1")
        self.assertEqual(row.destination_id, "98842B1")

    def test_page_boundaries_retain_source_pages(self) -> None:
        first = parse_layout_page(NORMAL_ROW, 4)[0]
        second_text = NORMAL_ROW.replace("  1  ", "  2  ", 1)
        second = parse_layout_page(second_text, 5)[0]
        self.assertEqual((first.source_page, second.source_page), (4, 5))

    def test_non_monotonic_rows_are_rejected(self) -> None:
        with self.assertRaises(ExtractionError):
            parse_layout_page(f"{NORMAL_ROW}\n{NORMAL_ROW}", 4)

    def test_malformed_candidate_is_rejected(self) -> None:
        with self.assertRaisesRegex(ExtractionError, "tidak dapat diurai"):
            parse_layout_page("12 Jakartapusat 10000 data-terpotong", 5)


class SlugTests(unittest.TestCase):
    def test_slug_normalizes_punctuation(self) -> None:
        self.assertEqual(slugify("Rajaampat (Ba)"), "rajaampat-ba")

    def test_public_slug_always_includes_office_id(self) -> None:
        location = _unique_slugs([("98481B1", "Rajaampat (Ba)", "98400")])[0]
        self.assertEqual(location.slug, "rajaampat-ba-98481b1")


class CacheTests(unittest.TestCase):
    def _records(self, source_hash: str) -> tuple[dict[str, object], ...]:
        header = {
            "record_type": "header",
            "cache_format_version": CACHE_FORMAT_VERSION,
            "extractor_version": DOMESTIC_CACHE_EXTRACTOR_VERSION,
            "source_sha256": source_hash,
            "page_first": 4,
            "page_last": 4,
        }
        rate = {
            "record_type": "rate",
            **asdict(parse_layout_page(NORMAL_ROW, 4)[0]),
        }
        trailer = {
            "record_type": "trailer",
            "rows": 1,
            "first_source_row": 1,
            "last_source_row": 1,
        }
        return header, rate, trailer

    def _write(self, path: Path, records: tuple[object, ...]) -> None:
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    def test_cache_requires_completion_trailer_and_real_rows(self) -> None:
        source_hash = "a" * 64
        header, rate, trailer = self._records(source_hash)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chunk.jsonl"
            self._write(path, (header,))
            self.assertFalse(_cache_header_matches(path, source_hash, 4, 4))
            self._write(path, (header, trailer))
            self.assertFalse(_cache_header_matches(path, source_hash, 4, 4))
            self._write(path, (header, rate, trailer))
            self.assertTrue(_cache_header_matches(path, source_hash, 4, 4))

    def test_cache_rejects_corrupt_middle_and_wrong_trailer_count(self) -> None:
        source_hash = "b" * 64
        header, rate, trailer = self._records(source_hash)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chunk.jsonl"
            self._write(path, (header, "not a JSON object", trailer))
            self.assertFalse(_cache_header_matches(path, source_hash, 4, 4))
            wrong_trailer = dict(trailer, rows=2)
            self._write(path, (header, rate, wrong_trailer))
            self.assertFalse(_cache_header_matches(path, source_hash, 4, 4))
            wrong_type = dict(rate, letter_up_to_100g="3500")
            self._write(path, (header, wrong_type, trailer))
            self.assertFalse(_cache_header_matches(path, source_hash, 4, 4))


class MatrixValidationTests(unittest.TestCase):
    def _row(self, source_row: int, origin_name: str = "Jakartapusat") -> ParsedRow:
        return ParsedRow(
            source_row=source_row,
            source_page=4,
            origin_name=origin_name,
            origin_id="10000",
            origin_kprk="10000",
            destination_name="Jakartapusat",
            destination_id="10000",
            destination_kprk="10000",
            letter_up_to_100g=3500,
            letter_over_100g_to_250g=4000,
            letter_over_250g_to_500g=4500,
            letter_over_500g_to_1000g=5000,
            letter_over_1000g_to_2000g=10000,
            postcard=3000,
            sekogram=0,
            m_bag_per_kg=6500,
            parcel_over_2kg_to_3kg=19500,
            parcel_each_additional_kg=6500,
        )

    def test_first_block_origin_must_remain_constant(self) -> None:
        expected = _validate_first_block_origin(self._row(1), None)
        self.assertEqual(
            _validate_first_block_origin(self._row(602), expected), expected
        )
        with self.assertRaisesRegex(ExtractionError, "metadata asal berubah"):
            _validate_first_block_origin(
                self._row(602, origin_name="Jakartabarat"), expected
            )


if __name__ == "__main__":
    unittest.main()
