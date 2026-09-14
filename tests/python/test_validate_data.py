from __future__ import annotations

import copy
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import validate_data


def make_manifest(data_dir: Path) -> dict[str, object]:
    artifacts: dict[str, dict[str, object]] = {}
    for name in validate_data.ARTIFACT_NAMES:
        contents = f"fixture:{name}\n".encode()
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
        },
        "counts": {
            "locations": validate_data.EXPECTED_LOCATIONS,
            "rates": validate_data.EXPECTED_RATES,
            "kprk_ids": validate_data.EXPECTED_KPRKS,
            "alphanumeric_office_ids": validate_data.EXPECTED_ALPHANUMERIC_IDS,
            "self_routes": validate_data.EXPECTED_LOCATIONS,
        },
        "artifacts": artifacts,
    }


def make_schema(*, destination_index: bool = True) -> sqlite3.Connection:
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
        """
    )
    if destination_index:
        connection.execute(
            "CREATE INDEX rates_destination_origin_idx "
            "ON rates (destination_id, origin_id)"
        )
    return connection


class ManifestArtifactTests(unittest.TestCase):
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

    def test_accepts_required_schema_and_destination_index(self) -> None:
        connection = make_schema()
        self.addCleanup(connection.close)

        validate_data.validate_schema(connection)


if __name__ == "__main__":
    unittest.main()
