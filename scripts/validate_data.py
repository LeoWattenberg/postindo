#!/usr/bin/env python3
"""Validasi artefak data ongkirstempel yang sudah dikomit tanpa membaca PDF sumber."""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Sequence


EXPECTED_SOURCE_FILENAME = "Kepmen Kominfo No. 222 Tahun 2022 ttg Besaran Tarif LPU (Lengkap).pdf"
EXPECTED_SOURCE_SHA256 = "811d1fb3ac805f172ea7f2d922cc1915b05c63226023bb9c68fc71a66c39bf3d"
EXPECTED_INTERNATIONAL_CSV_SHA256 = (
    "a10b6e61250b85833aa256cca678aa48e2146f4f0f850089e15a3ab3f6fa59d1"
)
EXPECTED_SCHEMA_VERSION = "1.1.0"
EXPECTED_LOCATIONS = 603
EXPECTED_KPRKS = 205
EXPECTED_ALPHANUMERIC_IDS = 27
EXPECTED_RATES = EXPECTED_LOCATIONS * EXPECTED_LOCATIONS
EXPECTED_INTERNATIONAL_RATES = 236
EXPECTED_INTERNATIONAL_DISTINCT_COUNTRY_CODES = 235
EXPECTED_INTERNATIONAL_PARCEL_AVAILABLE = 190
EXPECTED_INTERNATIONAL_PARCEL_UNAVAILABLE = 46
EXPECTED_INTERNATIONAL_ROWS_WITHOUT_IDR_SERVICES = 2
EXPECTED_INTERNATIONAL_PARCEL_UNAVAILABLE_ROWS = (
    4, 11, 64, 66, 67, 81, 84, 85, 86, 97, 99, 102, 104, 109, 111,
    119, 120, 131, 132, 138, 140, 156, 160, 161, 167, 170, 176, 182,
    184, 188, 189, 191, 194, 195, 196, 197, 198, 204, 206, 211, 221,
    230, 231, 232, 233, 234,
)
EXPECTED_INTERNATIONAL_PAGE_RANGES = (
    (20_198, 1, 22, 22),
    (20_199, 23, 44, 22),
    (20_200, 45, 68, 24),
    (20_201, 69, 93, 25),
    (20_202, 94, 115, 22),
    (20_203, 116, 139, 24),
    (20_204, 140, 164, 25),
    (20_205, 165, 188, 24),
    (20_206, 189, 209, 21),
    (20_207, 210, 231, 22),
    (20_208, 232, 236, 5),
)
EXPECTED_SOURCE_ANCHORS = {
    1: (
        1, 4,
        "Jakartapusat", "10000", "10000",
        "Jakartapusat", "10000", "10000",
        3_500, 4_000, 4_500, 5_000, 10_000, 3_000, 0, 6_500, 19_500, 6_500,
    ),
    355_190: (
        355_190, 19_729,
        "Pegununganbintang", "99573B1", "99000",
        "Pematangsiantar", "21100", "21100",
        69_500, 79_500, 89_500, 99_000, 198_000, 68_000, 0, 108_500, 297_000, 99_000,
    ),
    363_609: (
        363_609, 20_197,
        "Tembagapura", "99930", "99900",
        "Tembagapura", "99930", "99900",
        3_500, 4_000, 4_500, 5_000, 10_000, 3_000, 0, 8_000, 24_000, 8_000,
    ),
}

LOCATION_COLUMNS = (
    "office_id",
    "source_name",
    "kprk_id",
    "ordinal",
    "slug",
)
RATE_COLUMNS = (
    "source_row",
    "source_page",
    "origin_id",
    "destination_id",
    "letter_up_to_100g",
    "letter_over_100g_to_250g",
    "letter_over_250g_to_500g",
    "letter_over_500g_to_1000g",
    "letter_over_1000g_to_2000g",
    "postcard",
    "sekogram",
    "m_bag_per_kg",
    "parcel_over_2kg_to_3kg",
    "parcel_each_additional_kg",
)
TARIFF_COLUMNS = RATE_COLUMNS[4:]
INTERNATIONAL_RATE_COLUMNS = (
    "source_row",
    "source_page",
    "source_name",
    "country_code",
    "slug",
    "letter_printed_matter_small_packet_up_to_20g",
    "letter_printed_matter_small_packet_over_20g_to_50g",
    "letter_printed_matter_small_packet_over_50g_to_100g",
    "letter_printed_matter_small_packet_over_100g_to_250g",
    "letter_printed_matter_small_packet_over_250g_to_500g",
    "letter_printed_matter_small_packet_over_500g_to_1000g",
    "letter_printed_matter_small_packet_over_1000g_to_1500g",
    "letter_printed_matter_small_packet_over_1500g_to_2000g",
    "postcard",
    "sekogram_up_to_7kg",
    "m_bag_per_kg_up_to_30kg",
    "parcel_up_to_3kg_usd_cents",
    "parcel_each_additional_kg_usd_cents",
)
INTERNATIONAL_OPTIONAL_IDR_COLUMNS = (
    *INTERNATIONAL_RATE_COLUMNS[5:13],
    "postcard",
    "m_bag_per_kg_up_to_30kg",
)
INTERNATIONAL_TARIFF_COLUMNS = INTERNATIONAL_RATE_COLUMNS[5:]
ARTIFACT_NAMES = (
    "postindo.sqlite",
    "locations.csv",
    "rates.csv",
    "international_rates.csv",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_INTERNATIONAL_ANCHORS = {
    1: (
        1, 20_198, "Afghanistan", "AF", "afghanistan-af",
        7_000, 16_000, 32_000, 78_000, 132_000, 263_000, 394_000, 525_000,
        7_000, 0, 252_000, 6_514, 1_440,
    ),
    64: (
        64, 20_200, "Eswatini (Swaziland)", "SZ", "eswatini-swaziland-sz",
        8_000, 18_000, 35_000, 87_000, 173_000, 345_000, 517_000, 689_000,
        8_000, 0, 280_000, None, None,
    ),
    201: (
        201, 20_206, "Swaziland", "SZ", "swaziland-sz",
        8_000, 18_000, 35_000, 87_000, 148_000, 296_000, 444_000, 592_000,
        8_000, 0, 285_000, 3_710, 980,
    ),
    214: (
        214, 20_207, "Tristan Da Cunha", "TA", "tristan-da-cunha-ta",
        None, None, None, None, None, None, None, None,
        None, 0, None, 3_772, 754,
    ),
    217: (
        217, 20_207, "Turkmenistan", "TM", "turkmenistan-tm",
        None, None, None, None, None, None, None, None,
        None, 0, None, 4_920, 1_074,
    ),
    223: (
        223, 20_207, "United States of America", "US", "united-states-of-america-us",
        10_000, 17_000, 33_000, 78_000, 155_000, 257_000, 403_500, 513_000,
        10_000, 0, 243_000, 6_390, 1_952,
    ),
    236: (
        236, 20_208, "Zimbabwe", "ZW", "zimbabwe-zw",
        8_000, 18_000, 35_000, 87_000, 148_000, 296_000, 444_000, 592_000,
        8_000, 0, 285_000, 4_362, 1_064,
    ),
}


class ValidationError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--allow-unverified-source",
        action="store_true",
        help="izinkan identitas PDF nonbaku; validasi struktur tetap ketat",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ValidationError(f"manifest tidak ditemukan: {path}") from exc
    if b"\r" in raw:
        raise ValidationError("manifest.json harus memakai line ending LF")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"manifest.json bukan JSON UTF-8 yang valid: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError("akar manifest.json harus berupa object")
    return value


def require_mapping(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise ValidationError(f"manifest.{key} harus berupa object")
    return value


def validate_manifest(
    data_dir: Path,
    manifest: dict[str, Any],
    allow_unverified_source: bool = False,
) -> None:
    schema_version = manifest.get("schema_version")
    if schema_version != EXPECTED_SCHEMA_VERSION:
        raise ValidationError(f"schema_version tidak didukung: {schema_version!r}")

    expected_top_level = {
        "schema_version",
        "extractor_version",
        "generated_at",
        "source",
        "ocr",
        "counts",
        "artifacts",
    }
    if set(manifest) != expected_top_level:
        raise ValidationError(
            f"key manifest berbeda: {sorted(manifest)}; seharusnya {sorted(expected_top_level)}"
        )
    if not isinstance(manifest.get("extractor_version"), str):
        raise ValidationError("manifest.extractor_version wajib berupa string")
    if not isinstance(manifest.get("generated_at"), str):
        raise ValidationError("manifest.generated_at wajib berupa string")

    source = require_mapping(manifest, "source")
    source_hash = source.get("sha256")
    if not isinstance(source.get("filename"), str) or not source["filename"]:
        raise ValidationError("nama berkas sumber pada manifest tidak valid")
    if not isinstance(source_hash, str) or not SHA256_RE.fullmatch(source_hash):
        raise ValidationError("SHA-256 sumber pada manifest tidak valid")
    if not allow_unverified_source:
        if source.get("filename") != EXPECTED_SOURCE_FILENAME:
            raise ValidationError("nama berkas sumber pada manifest tidak sesuai")
        if source_hash != EXPECTED_SOURCE_SHA256:
            raise ValidationError("SHA-256 sumber pada manifest tidak sesuai sumber baku")
    if not isinstance(source.get("bytes"), int) or source["bytes"] <= 0:
        raise ValidationError("ukuran PDF sumber pada manifest tidak valid")
    if not isinstance(source.get("pdf_pages"), int) or source["pdf_pages"] < 20_208:
        raise ValidationError("jumlah halaman PDF sumber pada manifest tidak valid")
    if source.get("domestic_pdf_pages") != {"first": 4, "last": 20_197}:
        raise ValidationError("rentang halaman domestik pada manifest tidak sesuai")
    if source.get("international_pdf_pages") != {"first": 20_198, "last": 20_208}:
        raise ValidationError("rentang halaman internasional pada manifest tidak sesuai")

    ocr = require_mapping(manifest, "ocr")
    expected_ocr_keys = {
        "engine",
        "version",
        "extractor_version",
        "language",
        "page_segmentation_mode",
        "whole_page_passes",
        "cell_fallback_passes",
        "identity_passes",
        "reviewed_numeric_cells",
        "reviewed_identity_cells",
        "source_layer",
        "rotation_degrees",
    }
    if set(ocr) != expected_ocr_keys:
        raise ValidationError(f"key manifest.ocr berbeda: {sorted(ocr)}")
    expected_ocr_values = {
        "engine": "tesseract",
        "extractor_version": "1.1.0",
        "language": "eng",
        "page_segmentation_mode": 6,
        "whole_page_passes": ["eng:6", "eng+ind:6"],
        "cell_fallback_passes": ["eng:7", "eng+ind:7", "eng:8"],
        "identity_passes": ["eng:6", "eng+ind:7"],
        "source_layer": "largest_monochrome_page_image",
        "rotation_degrees": 270,
    }
    for key, expected in expected_ocr_values.items():
        if ocr.get(key) != expected:
            raise ValidationError(
                f"manifest.ocr.{key}={ocr.get(key)!r}; seharusnya {expected!r}"
            )
    if not isinstance(ocr.get("version"), str) or not ocr["version"].strip():
        raise ValidationError("manifest.ocr.version wajib berupa string yang tidak kosong")
    reviewed_count_limits = {
        "reviewed_numeric_cells": 16,
        "reviewed_identity_cells": 50,
    }
    for key, maximum in reviewed_count_limits.items():
        value = ocr.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= maximum
        ):
            raise ValidationError(
                f"manifest.ocr.{key}={value!r}; harus bilangan bulat 0..{maximum}"
            )
        if source_hash == EXPECTED_SOURCE_SHA256 and value != maximum:
            raise ValidationError(
                f"manifest.ocr.{key}={value!r}; sumber resmi harus menerapkan {maximum}"
            )

    counts = require_mapping(manifest, "counts")
    expected_counts = {
        "locations": EXPECTED_LOCATIONS,
        "rates": EXPECTED_RATES,
        "kprk_ids": EXPECTED_KPRKS,
        "alphanumeric_office_ids": EXPECTED_ALPHANUMERIC_IDS,
        "self_routes": EXPECTED_LOCATIONS,
        "international_rates": EXPECTED_INTERNATIONAL_RATES,
        "international_distinct_country_codes": (
            EXPECTED_INTERNATIONAL_DISTINCT_COUNTRY_CODES
        ),
        "international_parcel_available": EXPECTED_INTERNATIONAL_PARCEL_AVAILABLE,
        "international_parcel_unavailable": EXPECTED_INTERNATIONAL_PARCEL_UNAVAILABLE,
        "international_rows_without_idr_services": (
            EXPECTED_INTERNATIONAL_ROWS_WITHOUT_IDR_SERVICES
        ),
    }
    for key, expected in expected_counts.items():
        if counts.get(key) != expected:
            raise ValidationError(
                f"manifest.counts.{key}={counts.get(key)!r}; seharusnya {expected}"
            )
    if set(counts) != set(expected_counts):
        raise ValidationError(f"key manifest.counts berbeda: {sorted(counts)}")

    artifacts = require_mapping(manifest, "artifacts")
    if set(artifacts) != set(ARTIFACT_NAMES):
        raise ValidationError(f"daftar artefak manifest berbeda: {sorted(artifacts)}")
    for name in ARTIFACT_NAMES:
        descriptor = artifacts.get(name)
        if not isinstance(descriptor, dict):
            raise ValidationError(f"manifest.artifacts.{name} wajib berupa object")
        if descriptor.get("path") != name:
            raise ValidationError(f"path artefak {name} harus sama dengan nama berkas")
        expected_hash = descriptor.get("sha256")
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            raise ValidationError(f"SHA-256 artefak {name} tidak valid")
        path = data_dir / name
        if not path.is_file():
            raise ValidationError(f"artefak tidak ditemukan: {path}")
        actual_size = path.stat().st_size
        if descriptor.get("bytes") != actual_size:
            raise ValidationError(
                f"ukuran {name} tidak cocok: manifest {descriptor.get('bytes')!r}, "
                f"aktual {actual_size}"
            )
        actual_hash = sha256_file(path)
        if expected_hash != actual_hash:
            raise ValidationError(
                f"SHA-256 {name} tidak cocok: manifest {expected_hash}, aktual {actual_hash}"
            )
        if (
            not allow_unverified_source
            and source_hash == EXPECTED_SOURCE_SHA256
            and name == "international_rates.csv"
            and actual_hash != EXPECTED_INTERNATIONAL_CSV_SHA256
        ):
            raise ValidationError(
                "SHA-256 international_rates.csv tidak cocok dengan tabel resmi baku"
            )


def table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))


def scalar(connection: sqlite3.Connection, sql: str, parameters: Sequence[Any] = ()) -> Any:
    row = connection.execute(sql, parameters).fetchone()
    if row is None:
        raise ValidationError(f"query validasi tidak menghasilkan nilai: {sql}")
    return row[0]


def require_equal(actual: Any, expected: Any, description: str) -> None:
    if actual != expected:
        raise ValidationError(f"{description}: ditemukan {actual!r}, seharusnya {expected!r}")


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "negara"


def validate_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    require_equal(
        tables,
        {"metadata", "locations", "rates", "international_rates"},
        "tabel publik SQLite",
    )
    require_equal(table_columns(connection, "metadata"), ("key", "value"), "kolom metadata")
    require_equal(table_columns(connection, "locations"), LOCATION_COLUMNS, "kolom locations")
    require_equal(table_columns(connection, "rates"), RATE_COLUMNS, "kolom rates")
    require_equal(
        table_columns(connection, "international_rates"),
        INTERNATIONAL_RATE_COLUMNS,
        "kolom international_rates",
    )

    location_pk = [
        row[1]
        for row in sorted(
            connection.execute("PRAGMA table_info(locations)"), key=lambda row: row[5]
        )
        if row[5]
    ]
    rate_pk = [
        row[1]
        for row in sorted(connection.execute("PRAGMA table_info(rates)"), key=lambda row: row[5])
        if row[5]
    ]
    require_equal(location_pk, ["office_id"], "primary key locations")
    require_equal(rate_pk, ["origin_id", "destination_id"], "primary key rates")
    international_rate_pk = [
        row[1]
        for row in sorted(
            connection.execute("PRAGMA table_info(international_rates)"),
            key=lambda row: row[5],
        )
        if row[5]
    ]
    require_equal(
        international_rate_pk,
        ["source_row"],
        "primary key international_rates",
    )

    foreign_keys = {
        (row[3], row[2], row[4]) for row in connection.execute("PRAGMA foreign_key_list(rates)")
    }
    expected_foreign_keys = {
        ("origin_id", "locations", "office_id"),
        ("destination_id", "locations", "office_id"),
    }
    if not expected_foreign_keys.issubset(foreign_keys):
        raise ValidationError("foreign key origin/destination pada rates tidak lengkap")

    destination_index_found = False
    for index in connection.execute("PRAGMA index_list(rates)"):
        columns = [row[2] for row in connection.execute(f"PRAGMA index_info('{index[1]}')")]
        if columns[:2] == ["destination_id", "origin_id"]:
            destination_index_found = True
            break
    if not destination_index_found:
        raise ValidationError("indeks rates yang diawali destination_id, origin_id tidak ditemukan")

    country_code_index_found = False
    for index in connection.execute("PRAGMA index_list(international_rates)"):
        columns = [
            row[2] for row in connection.execute(f"PRAGMA index_info('{index[1]}')")
        ]
        if columns[:2] == ["country_code", "source_row"]:
            country_code_index_found = True
            break
    if not country_code_index_found:
        raise ValidationError(
            "indeks international_rates yang diawali country_code, source_row tidak ditemukan"
        )


def validate_database(path: Path, manifest: dict[str, Any]) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ValidationError(f"SQLite tidak dapat dibuka read-only: {exc}") from exc

    try:
        require_equal(scalar(connection, "PRAGMA integrity_check"), "ok", "integrity_check")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchone()
        require_equal(foreign_key_errors, None, "foreign_key_check")
        validate_schema(connection)

        source = require_mapping(manifest, "source")
        ocr = require_mapping(manifest, "ocr")
        counts = require_mapping(manifest, "counts")
        expected_metadata = {
            "schema_version": str(manifest["schema_version"]),
            "extractor_version": str(manifest["extractor_version"]),
            "source_filename": str(source["filename"]),
            "source_sha256": str(source["sha256"]),
            "source_bytes": str(source["bytes"]),
            "source_pdf_pages": str(source["pdf_pages"]),
            "domestic_page_first": "4",
            "domestic_page_last": "20197",
            "international_page_first": "20198",
            "international_page_last": "20208",
            "locations_count": str(counts["locations"]),
            "rates_count": str(counts["rates"]),
            "kprk_ids_count": str(counts["kprk_ids"]),
            "alphanumeric_office_ids_count": str(counts["alphanumeric_office_ids"]),
            "self_routes_count": str(counts["self_routes"]),
            "international_rates_count": str(counts["international_rates"]),
            "international_distinct_country_codes_count": str(
                counts["international_distinct_country_codes"]
            ),
            "international_parcel_available_count": str(
                counts["international_parcel_available"]
            ),
            "international_parcel_unavailable_count": str(
                counts["international_parcel_unavailable"]
            ),
            "international_rows_without_idr_services_count": str(
                counts["international_rows_without_idr_services"]
            ),
            "international_ocr_engine": str(ocr["engine"]),
            "international_ocr_version": str(ocr["version"]),
            "international_ocr_extractor_version": str(ocr["extractor_version"]),
            "international_ocr_language": str(ocr["language"]),
            "international_ocr_page_segmentation_mode": str(
                ocr["page_segmentation_mode"]
            ),
            "international_ocr_whole_page_passes": ",".join(
                ocr["whole_page_passes"]
            ),
            "international_ocr_cell_fallback_passes": ",".join(
                ocr["cell_fallback_passes"]
            ),
            "international_ocr_identity_passes": ",".join(
                ocr["identity_passes"]
            ),
            "international_ocr_reviewed_numeric_cells": str(
                ocr["reviewed_numeric_cells"]
            ),
            "international_ocr_reviewed_identity_cells": str(
                ocr["reviewed_identity_cells"]
            ),
            "international_ocr_source_layer": str(ocr["source_layer"]),
            "international_ocr_rotation_degrees": str(ocr["rotation_degrees"]),
            "generated_at": str(manifest["generated_at"]),
        }
        actual_metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        require_equal(actual_metadata, expected_metadata, "metadata SQLite terhadap manifest")

        require_equal(scalar(connection, "SELECT count(*) FROM locations"), EXPECTED_LOCATIONS, "jumlah lokasi")
        require_equal(scalar(connection, "SELECT count(*) FROM rates"), EXPECTED_RATES, "jumlah tarif")
        require_equal(
            scalar(connection, "SELECT count(DISTINCT kprk_id) FROM locations"),
            EXPECTED_KPRKS,
            "jumlah KPRK",
        )
        require_equal(
            scalar(connection, "SELECT count(*) FROM locations WHERE office_id GLOB '*[A-Za-z]*'"),
            EXPECTED_ALPHANUMERIC_IDS,
            "jumlah ID kantor yang mengandung huruf",
        )
        require_equal(
            scalar(
                connection,
                "SELECT count(*) FROM locations WHERE NOT ("
                "(length(office_id) = 5 AND office_id NOT GLOB '*[^0-9]*') OR "
                "(length(office_id) = 7 AND substr(office_id, 1, 5) NOT GLOB '*[^0-9]*' "
                "AND substr(office_id, 6) = 'B1'))",
            ),
            0,
            "format ID kantor",
        )
        require_equal(
            scalar(
                connection,
                "SELECT count(*) FROM locations WHERE length(kprk_id) != 5 "
                "OR kprk_id GLOB '*[^0-9]*'",
            ),
            0,
            "format ID KPRK",
        )
        require_equal(
            scalar(
                connection,
                "SELECT count(*) FROM locations "
                "WHERE slug NOT LIKE '%-' || lower(office_id)",
            ),
            0,
            "slug yang tidak berakhiran ID kantor",
        )
        require_equal(
            connection.execute(
                "SELECT min(ordinal), max(ordinal), count(DISTINCT ordinal) FROM locations"
            ).fetchone(),
            (1, EXPECTED_LOCATIONS, EXPECTED_LOCATIONS),
            "rentang ordinal lokasi",
        )
        require_equal(
            connection.execute(
                "SELECT min(source_row), max(source_row), count(DISTINCT source_row) FROM rates"
            ).fetchone(),
            (1, EXPECTED_RATES, EXPECTED_RATES),
            "rentang baris sumber",
        )
        require_equal(
            scalar(connection, "SELECT count(*) FROM rates WHERE origin_id = destination_id"),
            EXPECTED_LOCATIONS,
            "jumlah rute ke kantor yang sama",
        )

        anchor_select = f"""
            SELECT
                r.source_row,
                r.source_page,
                origin.source_name,
                origin.office_id,
                origin.kprk_id,
                destination.source_name,
                destination.office_id,
                destination.kprk_id,
                {", ".join(f"r.{column}" for column in TARIFF_COLUMNS)}
            FROM rates AS r
            JOIN locations AS origin ON origin.office_id = r.origin_id
            JOIN locations AS destination ON destination.office_id = r.destination_id
            WHERE r.source_row = ?
        """
        for source_row, expected_anchor in EXPECTED_SOURCE_ANCHORS.items():
            actual_anchor = connection.execute(anchor_select, (source_row,)).fetchone()
            require_equal(
                actual_anchor,
                expected_anchor,
                f"jangkar sumber lengkap pada baris {source_row}",
            )

        ordering_error = connection.execute(
            "SELECT r.source_row, r.origin_id, r.destination_id "
            "FROM rates AS r "
            "JOIN locations AS origin ON origin.office_id = r.origin_id "
            "JOIN locations AS destination ON destination.office_id = r.destination_id "
            "WHERE r.source_row != ((origin.ordinal - 1) * ? + destination.ordinal) "
            "LIMIT 1",
            (EXPECTED_LOCATIONS,),
        ).fetchone()
        require_equal(ordering_error, None, "urutan tujuan pada setiap blok asal")

        invalid_location = connection.execute(
            "SELECT office_id FROM locations "
            "WHERE source_name IS NULL OR trim(source_name) = '' "
            "OR kprk_id IS NULL OR trim(kprk_id) = '' "
            "OR slug IS NULL OR trim(slug) = '' LIMIT 1"
        ).fetchone()
        require_equal(invalid_location, None, "metadata lokasi kosong")

        page_error = connection.execute(
            "SELECT source_row, source_page FROM rates "
            "WHERE typeof(source_page) != 'integer' OR source_page < 4 OR source_page > 20197 "
            "LIMIT 1"
        ).fetchone()
        require_equal(page_error, None, "nomor halaman sumber")
        page_order_error = connection.execute(
            "SELECT source_row FROM ("
            "SELECT source_row, source_page, "
            "lag(source_page) OVER (ORDER BY source_row) AS previous_page FROM rates"
            ") WHERE source_page < previous_page LIMIT 1"
        ).fetchone()
        require_equal(page_order_error, None, "urutan halaman sumber")

        tariff_predicates = " OR ".join(
            (
                f"typeof({column}) != 'integer' OR {column} != 0"
                if column == "sekogram"
                else f"typeof({column}) != 'integer' OR {column} <= 0"
            )
            for column in TARIFF_COLUMNS
        )
        tariff_error = connection.execute(
            f"SELECT source_row FROM rates WHERE {tariff_predicates} LIMIT 1"
        ).fetchone()
        require_equal(tariff_error, None, "nilai tarif bukan integer rupiah nonnegatif")
        require_equal(
            scalar(connection, "SELECT count(*) FROM rates WHERE sekogram != 0"),
            0,
            "tarif sekogram yang bukan Bebas Biaya",
        )

        require_equal(
            scalar(connection, "SELECT count(*) FROM international_rates"),
            EXPECTED_INTERNATIONAL_RATES,
            "jumlah tarif internasional",
        )
        require_equal(
            connection.execute(
                "SELECT min(source_row), max(source_row), count(DISTINCT source_row) "
                "FROM international_rates"
            ).fetchone(),
            (1, EXPECTED_INTERNATIONAL_RATES, EXPECTED_INTERNATIONAL_RATES),
            "rentang baris sumber tarif internasional",
        )
        require_equal(
            connection.execute(
                "SELECT source_page, min(source_row), max(source_row), count(*) "
                "FROM international_rates GROUP BY source_page ORDER BY source_page"
            ).fetchall(),
            list(EXPECTED_INTERNATIONAL_PAGE_RANGES),
            "pembagian baris tarif internasional per halaman sumber",
        )

        invalid_country = connection.execute(
            "SELECT source_row FROM international_rates "
            "WHERE typeof(source_name) != 'text' OR trim(source_name) = '' "
            "OR typeof(country_code) != 'text' OR length(country_code) != 2 "
            "OR country_code GLOB '*[^A-Z]*' "
            "OR typeof(slug) != 'text' OR trim(slug) = '' LIMIT 1"
        ).fetchone()
        require_equal(invalid_country, None, "metadata negara tujuan internasional")
        require_equal(
            scalar(
                connection,
                "SELECT count(DISTINCT source_name) FROM international_rates",
            ),
            EXPECTED_INTERNATIONAL_RATES,
            "jumlah nama negara tujuan internasional unik",
        )
        require_equal(
            scalar(
                connection,
                "SELECT count(DISTINCT country_code) FROM international_rates",
            ),
            EXPECTED_INTERNATIONAL_DISTINCT_COUNTRY_CODES,
            "jumlah kode negara internasional unik",
        )
        require_equal(
            connection.execute(
                "SELECT country_code, count(*) FROM international_rates "
                "GROUP BY country_code HAVING count(*) > 1 ORDER BY country_code"
            ).fetchall(),
            [("SZ", 2)],
            "kode negara internasional yang berulang",
        )

        bad_slug: tuple[Any, ...] | None = None
        for source_row, source_name, country_code, slug in connection.execute(
            "SELECT source_row, source_name, country_code, slug "
            "FROM international_rates ORDER BY source_row"
        ):
            expected_slug = f"{slugify(source_name)}-{country_code.lower()}"
            if slug != expected_slug:
                bad_slug = (source_row, slug, expected_slug)
                break
        require_equal(bad_slug, None, "slug negara tujuan internasional")

        optional_idr_invalid = " OR ".join(
            f"({column} IS NOT NULL AND "
            f"(typeof({column}) != 'integer' OR {column} <= 0))"
            for column in INTERNATIONAL_OPTIONAL_IDR_COLUMNS
        )
        invalid_optional_idr = connection.execute(
            f"SELECT source_row FROM international_rates "
            f"WHERE {optional_idr_invalid} LIMIT 1"
        ).fetchone()
        require_equal(
            invalid_optional_idr,
            None,
            "tarif rupiah internasional yang bukan integer positif atau NULL",
        )

        idr_null_count = " + ".join(
            f"({column} IS NULL)" for column in INTERNATIONAL_OPTIONAL_IDR_COLUMNS
        )
        partial_idr_row = connection.execute(
            f"SELECT source_row FROM international_rates "
            f"WHERE ({idr_null_count}) NOT IN (0, ?) LIMIT 1",
            (len(INTERNATIONAL_OPTIONAL_IDR_COLUMNS),),
        ).fetchone()
        require_equal(
            partial_idr_row,
            None,
            "kelengkapan kelompok layanan rupiah internasional",
        )
        international_letter_columns = INTERNATIONAL_RATE_COLUMNS[5:13]
        increasing_predicate = " AND ".join(
            f"{left} < {right}"
            for left, right in zip(
                international_letter_columns,
                international_letter_columns[1:],
            )
        )
        non_increasing_row = connection.execute(
            "SELECT source_row FROM international_rates "
            "WHERE letter_printed_matter_small_packet_up_to_20g IS NOT NULL "
            f"AND NOT ({increasing_predicate}) LIMIT 1"
        ).fetchone()
        require_equal(
            non_increasing_row,
            None,
            "pita berat tarif internasional yang tidak meningkat",
        )
        postcard_mismatch = connection.execute(
            "SELECT source_row FROM international_rates "
            "WHERE postcard IS NOT "
            "letter_printed_matter_small_packet_up_to_20g LIMIT 1"
        ).fetchone()
        require_equal(
            postcard_mismatch,
            None,
            "kartu pos internasional terhadap tarif pita pertama",
        )
        rows_without_idr_services = connection.execute(
            "SELECT source_row FROM international_rates "
            "WHERE letter_printed_matter_small_packet_up_to_20g IS NULL "
            "ORDER BY source_row"
        ).fetchall()
        require_equal(
            rows_without_idr_services,
            [(214,), (217,)],
            "baris tanpa layanan rupiah internasional",
        )

        invalid_sekogram = connection.execute(
            "SELECT source_row FROM international_rates "
            "WHERE typeof(sekogram_up_to_7kg) != 'integer' "
            "OR sekogram_up_to_7kg != 0 LIMIT 1"
        ).fetchone()
        require_equal(
            invalid_sekogram,
            None,
            "sekogram internasional yang bukan Bebas biaya kirim s.d. 7 Kg",
        )

        parcel_pair_error = connection.execute(
            "SELECT source_row FROM international_rates WHERE "
            "(parcel_up_to_3kg_usd_cents IS NULL) "
            "!= (parcel_each_additional_kg_usd_cents IS NULL) LIMIT 1"
        ).fetchone()
        require_equal(parcel_pair_error, None, "pasangan tarif paket internasional")
        parcel_type_error = connection.execute(
            "SELECT source_row FROM international_rates WHERE "
            "(parcel_up_to_3kg_usd_cents IS NOT NULL AND "
            " (typeof(parcel_up_to_3kg_usd_cents) != 'integer' "
            "  OR parcel_up_to_3kg_usd_cents <= 0)) OR "
            "(parcel_each_additional_kg_usd_cents IS NOT NULL AND "
            " (typeof(parcel_each_additional_kg_usd_cents) != 'integer' "
            "  OR parcel_each_additional_kg_usd_cents <= 0)) LIMIT 1"
        ).fetchone()
        require_equal(
            parcel_type_error,
            None,
            "tarif paket internasional yang bukan integer sen USD positif atau NULL",
        )
        require_equal(
            scalar(
                connection,
                "SELECT count(*) FROM international_rates "
                "WHERE parcel_up_to_3kg_usd_cents IS NOT NULL",
            ),
            EXPECTED_INTERNATIONAL_PARCEL_AVAILABLE,
            "jumlah tujuan dengan layanan paket internasional",
        )
        require_equal(
            scalar(
                connection,
                "SELECT count(*) FROM international_rates "
                "WHERE parcel_up_to_3kg_usd_cents IS NULL",
            ),
            EXPECTED_INTERNATIONAL_PARCEL_UNAVAILABLE,
            "jumlah tujuan tanpa layanan paket internasional",
        )
        require_equal(
            tuple(
                row[0]
                for row in connection.execute(
                    "SELECT source_row FROM international_rates "
                    "WHERE parcel_up_to_3kg_usd_cents IS NULL ORDER BY source_row"
                )
            ),
            EXPECTED_INTERNATIONAL_PARCEL_UNAVAILABLE_ROWS,
            "baris tujuan tanpa layanan paket internasional",
        )

        international_anchor_select = (
            f"SELECT {', '.join(INTERNATIONAL_RATE_COLUMNS)} "
            "FROM international_rates WHERE source_row = ?"
        )
        for source_row, expected_anchor in EXPECTED_INTERNATIONAL_ANCHORS.items():
            actual_anchor = connection.execute(
                international_anchor_select, (source_row,)
            ).fetchone()
            require_equal(
                actual_anchor,
                expected_anchor,
                f"jangkar internasional pada baris {source_row}",
            )
    except Exception:
        connection.close()
        raise
    return connection


def ensure_lf_utf8(path: Path) -> None:
    saw_data = False
    last_byte = b""
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            saw_data = True
            last_byte = block[-1:]
            if b"\r" in block:
                raise ValidationError(f"{path.name} mengandung CR; line ending harus LF")
    if not saw_data or last_byte != b"\n":
        raise ValidationError(f"{path.name} harus diakhiri LF")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for _ in handle:
                pass
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{path.name} bukan UTF-8 yang valid: {exc}") from exc


def compare_csv(
    connection: sqlite3.Connection,
    path: Path,
    table: str,
    columns: Sequence[str],
    order_by: str,
) -> None:
    ensure_lf_utf8(path)
    query = f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order_by}"
    cursor: Iterable[Sequence[Any]] = connection.execute(query)
    sentinel = object()

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        try:
            header = next(reader)
        except (StopIteration, csv.Error) as exc:
            raise ValidationError(f"{path.name} tidak memiliki header CSV yang valid") from exc
        require_equal(tuple(header), tuple(columns), f"header {path.name}")

        try:
            pairs = itertools.zip_longest(reader, cursor, fillvalue=sentinel)
            for csv_number, pair in enumerate(pairs, start=2):
                csv_row, db_row = pair
                if csv_row is sentinel:
                    raise ValidationError(f"{path.name} berakhir sebelum baris SQLite {csv_number - 1}")
                if db_row is sentinel:
                    raise ValidationError(f"{path.name} memiliki baris tambahan pada baris {csv_number}")
                expected = ["" if value is None else str(value) for value in db_row]
                if csv_row != expected:
                    raise ValidationError(
                        f"{path.name} baris {csv_number} berbeda dari SQLite: "
                        f"CSV={csv_row!r}, SQLite={expected!r}"
                    )
        except csv.Error as exc:
            raise ValidationError(f"CSV rusak pada {path.name} baris {reader.line_num}: {exc}") from exc


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    try:
        manifest = load_manifest(data_dir / "manifest.json")
        validate_manifest(data_dir, manifest, args.allow_unverified_source)
        connection = validate_database(data_dir / "postindo.sqlite", manifest)
        try:
            compare_csv(
                connection,
                data_dir / "locations.csv",
                "locations",
                LOCATION_COLUMNS,
                "ordinal",
            )
            compare_csv(
                connection,
                data_dir / "rates.csv",
                "rates",
                RATE_COLUMNS,
                "source_row",
            )
            compare_csv(
                connection,
                data_dir / "international_rates.csv",
                "international_rates",
                INTERNATIONAL_RATE_COLUMNS,
                "source_row",
            )
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValidationError) as exc:
        print(f"Validasi data gagal: {exc}", file=sys.stderr)
        return 1

    print(
        f"Data valid: {EXPECTED_LOCATIONS} lokasi, {EXPECTED_KPRKS} KPRK, "
        f"{EXPECTED_RATES} rute domestik, dan {EXPECTED_INTERNATIONAL_RATES} "
        "tujuan internasional; checksum artefak dan CSV cocok."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
