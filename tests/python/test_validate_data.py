from __future__ import annotations

import copy
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import validate_data


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def make_manifest(data_dir: Path) -> dict[str, object]:
    artifacts: dict[str, dict[str, object]] = {}
    for name in validate_data.ARTIFACT_NAMES:
        contents = (
            (REPOSITORY_ROOT / "data" / name).read_bytes()
            if name == "international_rates.csv"
            else f"fixture:{name}\n".encode()
        )
        (data_dir / name).write_bytes(contents)
        artifacts[name] = {
            "path": name,
            "bytes": len(contents),
            "sha256": hashlib.sha256(contents).hexdigest(),
        }

    return {
        "schema_version": validate_data.EXPECTED_SCHEMA_VERSION,
        "extractor_version": "test",
        "generated_at": "2026-01-01T00:00:00Z",
        "source": {
            "filename": validate_data.EXPECTED_SOURCE_FILENAME,
            "sha256": validate_data.EXPECTED_SOURCE_SHA256,
            "bytes": 1,
            "pdf_pages": 20_210,
            "domestic_pdf_pages": {"first": 4, "last": 20_197},
            "international_pdf_pages": {"first": 20_198, "last": 20_208},
        },
        "ocr": {
            "engine": "tesseract",
            "version": "5.3.4",
            "extractor_version": "1.1.0",
            "language": "eng",
            "page_segmentation_mode": 6,
            "whole_page_passes": ["eng:6", "eng+ind:6"],
            "cell_fallback_passes": ["eng:7", "eng+ind:7", "eng:8"],
            "identity_passes": ["eng:6", "eng+ind:7"],
            "reviewed_numeric_cells": 16,
            "reviewed_identity_cells": 50,
            "source_layer": "largest_monochrome_page_image",
            "rotation_degrees": 270,
        },
        "counts": {
            "locations": validate_data.EXPECTED_LOCATIONS,
            "rates": validate_data.EXPECTED_RATES,
            "kprk_ids": validate_data.EXPECTED_KPRKS,
            "alphanumeric_office_ids": validate_data.EXPECTED_ALPHANUMERIC_IDS,
            "self_routes": validate_data.EXPECTED_LOCATIONS,
            "international_rates": validate_data.EXPECTED_INTERNATIONAL_RATES,
            "international_distinct_country_codes": (
                validate_data.EXPECTED_INTERNATIONAL_DISTINCT_COUNTRY_CODES
            ),
            "international_parcel_available": (
                validate_data.EXPECTED_INTERNATIONAL_PARCEL_AVAILABLE
            ),
            "international_parcel_unavailable": (
                validate_data.EXPECTED_INTERNATIONAL_PARCEL_UNAVAILABLE
            ),
            "international_rows_without_idr_services": (
                validate_data.EXPECTED_INTERNATIONAL_ROWS_WITHOUT_IDR_SERVICES
            ),
        },
        "artifacts": artifacts,
    }


def make_schema(
    *, destination_index: bool = True, international_index: bool = True
) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE locations (
            office_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            kprk_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            slug TEXT NOT NULL
        );
        CREATE TABLE rates (
            source_row INTEGER NOT NULL,
            source_page INTEGER NOT NULL,
            origin_id TEXT NOT NULL,
            destination_id TEXT NOT NULL,
            letter_up_to_100g INTEGER NOT NULL,
            letter_over_100g_to_250g INTEGER NOT NULL,
            letter_over_250g_to_500g INTEGER NOT NULL,
            letter_over_500g_to_1000g INTEGER NOT NULL,
            letter_over_1000g_to_2000g INTEGER NOT NULL,
            postcard INTEGER NOT NULL,
            sekogram INTEGER NOT NULL,
            m_bag_per_kg INTEGER NOT NULL,
            parcel_over_2kg_to_3kg INTEGER NOT NULL,
            parcel_each_additional_kg INTEGER NOT NULL,
            PRIMARY KEY (origin_id, destination_id),
            FOREIGN KEY (origin_id) REFERENCES locations (office_id),
            FOREIGN KEY (destination_id) REFERENCES locations (office_id)
        );
        CREATE TABLE international_rates (
            source_row INTEGER PRIMARY KEY,
            source_page INTEGER NOT NULL,
            source_name TEXT NOT NULL,
            country_code TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            letter_printed_matter_small_packet_up_to_20g INTEGER,
            letter_printed_matter_small_packet_over_20g_to_50g INTEGER,
            letter_printed_matter_small_packet_over_50g_to_100g INTEGER,
            letter_printed_matter_small_packet_over_100g_to_250g INTEGER,
            letter_printed_matter_small_packet_over_250g_to_500g INTEGER,
            letter_printed_matter_small_packet_over_500g_to_1000g INTEGER,
            letter_printed_matter_small_packet_over_1000g_to_1500g INTEGER,
            letter_printed_matter_small_packet_over_1500g_to_2000g INTEGER,
            postcard INTEGER,
            sekogram_up_to_7kg INTEGER NOT NULL,
            m_bag_per_kg_up_to_30kg INTEGER,
            parcel_up_to_3kg_usd_cents INTEGER,
            parcel_each_additional_kg_usd_cents INTEGER
        );
        """
    )
    if destination_index:
        connection.execute(
            "CREATE INDEX rates_destination_origin_idx "
            "ON rates (destination_id, origin_id)"
        )
    if international_index:
        connection.execute(
            "CREATE INDEX international_country_code_source_row_idx "
            "ON international_rates (country_code, source_row)"
        )
    return connection


class ManifestArtifactTests(unittest.TestCase):
    def test_rejects_synchronised_change_to_official_international_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            manifest = make_manifest(data_dir)
            changed = (data_dir / "international_rates.csv").read_bytes().replace(
                b"Afghanistan", b"AfghanistaX", 1
            )
            (data_dir / "international_rates.csv").write_bytes(changed)
            descriptor = manifest["artifacts"]["international_rates.csv"]
            descriptor["bytes"] = len(changed)
            descriptor["sha256"] = hashlib.sha256(changed).hexdigest()

            with self.assertRaisesRegex(
                validate_data.ValidationError,
                r"tabel resmi baku",
            ):
                validate_data.validate_manifest(data_dir, manifest)

    def test_rejects_artifact_size_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            manifest = make_manifest(data_dir)
            broken = copy.deepcopy(manifest)
            descriptor = broken["artifacts"]["postindo.sqlite"]
            descriptor["bytes"] += 1

            with self.assertRaisesRegex(
                validate_data.ValidationError,
                r"ukuran postindo\.sqlite tidak cocok",
            ):
                validate_data.validate_manifest(data_dir, broken)

    def test_rejects_incomplete_ocr_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            manifest = make_manifest(data_dir)
            broken = copy.deepcopy(manifest)
            del broken["ocr"]["source_layer"]

            with self.assertRaisesRegex(
                validate_data.ValidationError,
                r"key manifest\.ocr berbeda",
            ):
                validate_data.validate_manifest(data_dir, broken)

    def test_rejects_empty_ocr_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            manifest = make_manifest(data_dir)
            broken = copy.deepcopy(manifest)
            broken["ocr"]["version"] = "  "

            with self.assertRaisesRegex(
                validate_data.ValidationError,
                r"manifest\.ocr\.version",
            ):
                validate_data.validate_manifest(data_dir, broken)

    def test_unverified_source_accepts_actual_reviewed_correction_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            manifest = make_manifest(data_dir)
            manifest["source"]["sha256"] = "1" * 64
            manifest["ocr"]["reviewed_numeric_cells"] = 0
            manifest["ocr"]["reviewed_identity_cells"] = 0

            validate_data.validate_manifest(
                data_dir,
                manifest,
                allow_unverified_source=True,
            )

    def test_official_source_requires_all_reviewed_correction_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            manifest = make_manifest(data_dir)
            manifest["ocr"]["reviewed_numeric_cells"] = 0

            with self.assertRaisesRegex(
                validate_data.ValidationError,
                r"sumber resmi harus menerapkan 16",
            ):
                validate_data.validate_manifest(data_dir, manifest)

    def test_rejects_artifact_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            manifest = make_manifest(data_dir)
            broken = copy.deepcopy(manifest)
            broken["artifacts"]["postindo.sqlite"]["sha256"] = "0" * 64

            with self.assertRaisesRegex(
                validate_data.ValidationError,
                r"SHA-256 postindo\.sqlite tidak cocok",
            ):
                validate_data.validate_manifest(data_dir, broken)


class CsvValidationTests(unittest.TestCase):
    def test_rejects_csv_value_that_differs_from_sqlite(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE items (ordinal INTEGER, value TEXT)")
        connection.execute("INSERT INTO items VALUES (1, 'benar')")

        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "items.csv"
            csv_path.write_bytes(b"ordinal,value\n1,salah\n")

            with self.assertRaisesRegex(
                validate_data.ValidationError,
                r"items\.csv baris 2 berbeda dari SQLite",
            ):
                validate_data.compare_csv(
                    connection,
                    csv_path,
                    "items",
                    ("ordinal", "value"),
                    "ordinal",
                )

    def test_rejects_cr_line_endings_missing_final_lf_and_invalid_utf8(self) -> None:
        cases = (
            (b"header\r\n", "mengandung CR"),
            (b"header", "harus diakhiri LF"),
            (b"header\n\xff\n", "bukan UTF-8"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.csv"
            for contents, message in cases:
                with self.subTest(message=message):
                    path.write_bytes(contents)
                    with self.assertRaisesRegex(validate_data.ValidationError, message):
                        validate_data.ensure_lf_utf8(path)

    def test_null_sqlite_value_matches_empty_csv_field(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE items (ordinal INTEGER, value INTEGER)")
        connection.execute("INSERT INTO items VALUES (1, NULL)")

        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "items.csv"
            csv_path.write_bytes(b"ordinal,value\n1,\n")

            validate_data.compare_csv(
                connection,
                csv_path,
                "items",
                ("ordinal", "value"),
                "ordinal",
            )


class SqliteSchemaTests(unittest.TestCase):
    def test_rejects_missing_destination_first_index(self) -> None:
        connection = make_schema(destination_index=False)
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE INDEX incomplete_destination_idx ON rates (destination_id)"
        )

        with self.assertRaisesRegex(
            validate_data.ValidationError,
            "indeks rates yang diawali destination_id, origin_id tidak ditemukan",
        ):
            validate_data.validate_schema(connection)

    def test_rejects_unexpected_public_schema(self) -> None:
        connection = make_schema()
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE unexpected (value TEXT)")

        with self.assertRaisesRegex(
            validate_data.ValidationError,
            "tabel publik SQLite",
        ):
            validate_data.validate_schema(connection)

    def test_rejects_missing_international_country_code_index(self) -> None:
        connection = make_schema(international_index=False)
        self.addCleanup(connection.close)

        with self.assertRaisesRegex(
            validate_data.ValidationError,
            "country_code, source_row tidak ditemukan",
        ):
            validate_data.validate_schema(connection)

    def test_accepts_required_schema_and_destination_index(self) -> None:
        connection = make_schema()
        self.addCleanup(connection.close)

        validate_data.validate_schema(connection)


if __name__ == "__main__":
    unittest.main()
