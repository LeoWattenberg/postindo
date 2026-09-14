#!/usr/bin/env python3
"""Extract domestic and international tariffs from Kepmen Kominfo No. 222/2022.

The public command verifies the source PDF, extracts the text-based domestic
matrix in sequential subprocesses, OCRs the scanned international table,
validates both datasets, and only then publishes SQLite/CSV/JSON artifacts.
Caches are deliberately retained so an interrupted extraction can resume.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import pypdf
from pypdf import PdfReader

try:
    from scripts.extract_international_rates import (
        INTERNATIONAL_COLUMNS,
        INTERNATIONAL_PAGE_FIRST,
        INTERNATIONAL_PAGE_LAST,
        InternationalExtractionError,
        InternationalRate,
        OcrProvenance,
        prepare_international_rates,
        validate_international_rows,
    )
except ModuleNotFoundError:  # Direct execution: python scripts/extract_rates.py
    from extract_international_rates import (  # type: ignore[no-redef]
        INTERNATIONAL_COLUMNS,
        INTERNATIONAL_PAGE_FIRST,
        INTERNATIONAL_PAGE_LAST,
        InternationalExtractionError,
        InternationalRate,
        OcrProvenance,
        prepare_international_rates,
        validate_international_rows,
    )


EXPECTED_SHA256 = "811d1fb3ac805f172ea7f2d922cc1915b05c63226023bb9c68fc71a66c39bf3d"
EXPECTED_PYPDF_VERSION = "6.16.2"
EXTRACTOR_VERSION = "1.1.0"
DOMESTIC_CACHE_EXTRACTOR_VERSION = "1.0.0"
CACHE_FORMAT_VERSION = 1
SCHEMA_VERSION = "1.1.0"

DOMESTIC_PAGE_FIRST = 4
DOMESTIC_PAGE_LAST = 20_197
EXPECTED_LOCATION_COUNT = 603
EXPECTED_RATE_COUNT = 363_609
EXPECTED_KPRK_COUNT = 205
EXPECTED_ALPHANUMERIC_OFFICE_COUNT = 27

LOCATION_COLUMNS = ("office_id", "source_name", "kprk_id", "ordinal", "slug")
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
EXPECTED_ANCHORS = (
    (
        1,
        4,
        "10000",
        "Jakartapusat",
        "10000",
        "10000",
        "Jakartapusat",
        "10000",
        3_500,
        4_000,
        4_500,
        5_000,
        10_000,
        3_000,
        0,
        6_500,
        19_500,
        6_500,
    ),
    (
        355_190,
        19_729,
        "99573B1",
        "Pegununganbintang",
        "99000",
        "21100",
        "Pematangsiantar",
        "21100",
        69_500,
        79_500,
        89_500,
        99_000,
        198_000,
        68_000,
        0,
        108_500,
        297_000,
        99_000,
    ),
    (
        363_609,
        20_197,
        "99930",
        "Tembagapura",
        "99900",
        "99930",
        "Tembagapura",
        "99900",
        3_500,
        4_000,
        4_500,
        5_000,
        10_000,
        3_000,
        0,
        8_000,
        24_000,
        8_000,
    ),
)

_ID = r"[0-9]{5}(?:B1)?"
_KPRK = r"[0-9]{5}"
_PRICE = r"[0-9]{1,3}(?:\.[0-9]{3})*"
ROW_RE = re.compile(
    rf"""
    ^\s*(?P<source_row>[0-9]+)\s+
    (?P<origin_name>\S.*?)\s+(?P<origin_id>{_ID})\s+(?P<origin_kprk>{_KPRK})\s+
    (?P<destination_name>\S.*?)\s+(?P<destination_id>{_ID})\s+(?P<destination_kprk>{_KPRK})\s+
    (?P<letter_up_to_100g>{_PRICE})\s+
    (?P<letter_over_100g_to_250g>{_PRICE})\s+
    (?P<letter_over_250g_to_500g>{_PRICE})\s+
    (?P<letter_over_500g_to_1000g>{_PRICE})\s+
    (?P<letter_over_1000g_to_2000g>{_PRICE})\s+
    (?P<postcard>{_PRICE})\s+
    Bebas\s+Biaya\s+
    (?P<m_bag_per_kg>{_PRICE})\s+
    (?P<parcel_over_2kg_to_3kg>{_PRICE})\s+
    (?P<parcel_each_additional_kg>{_PRICE})\s*$
    """,
    re.VERBOSE,
)
ROW_CANDIDATE_RE = re.compile(r"^\s*[0-9]+\s+")
NAME_FRAGMENT_RE = re.compile(r"^[^0-9]+$")


class ExtractionError(RuntimeError):
    """Raised when source text or the complete matrix violates its contract."""


@dataclass
class ParsedRow:
    source_row: int
    source_page: int
    origin_name: str
    origin_id: str
    origin_kprk: str
    destination_name: str
    destination_id: str
    destination_kprk: str
    letter_up_to_100g: int
    letter_over_100g_to_250g: int
    letter_over_250g_to_500g: int
    letter_over_500g_to_1000g: int
    letter_over_1000g_to_2000g: int
    postcard: int
    sekogram: int
    m_bag_per_kg: int
    parcel_over_2kg_to_3kg: int
    parcel_each_additional_kg: int

    def rate_values(self) -> tuple[int, ...]:
        return tuple(getattr(self, column) for column in TARIFF_COLUMNS)

    def database_values(self) -> tuple[object, ...]:
        return tuple(getattr(self, column) for column in RATE_COLUMNS)


@dataclass(frozen=True)
class Location:
    office_id: str
    source_name: str
    kprk_id: str
    ordinal: int
    slug: str

    def values(self) -> tuple[object, ...]:
        return tuple(getattr(self, column) for column in LOCATION_COLUMNS)


def rupiah_to_int(value: str) -> int:
    """Convert the decree's Indonesian thousands notation to whole rupiah."""
    if not re.fullmatch(_PRICE, value):
        raise ValueError(f"format rupiah tidak valid: {value!r}")
    parts = value.split(".")
    if len(parts) > 1 and any(len(part) != 3 for part in parts[1:]):
        raise ValueError(f"format rupiah tidak valid: {value!r}")
    return int("".join(parts))


def _clean_name(value: str) -> str:
    return " ".join(value.split())


def _row_from_match(match: re.Match[str], source_page: int) -> ParsedRow:
    groups = match.groupdict()
    tariffs = {
        column: rupiah_to_int(groups[column])
        for column in TARIFF_COLUMNS
        if column != "sekogram"
    }
    return ParsedRow(
        source_row=int(groups["source_row"]),
        source_page=source_page,
        origin_name=_clean_name(groups["origin_name"]),
        origin_id=groups["origin_id"],
        origin_kprk=groups["origin_kprk"],
        destination_name=_clean_name(groups["destination_name"]),
        destination_id=groups["destination_id"],
        destination_kprk=groups["destination_kprk"],
        sekogram=0,
        **tariffs,
    )


def _continuation_fragments(line: str) -> tuple[str, str] | None:
    """Return wrapped origin/destination fragments using table column position."""
    # Wrapped source names start around columns 8-12 and wrapped destinations
    # around columns 50-52.  Column 40 is safely inside the gap between them on
    # every table page, including pages whose row number widens to six digits.
    if not line[:1].isspace() or len(line) > 90 or not line.strip():
        return None
    origin = line[:40].strip()
    destination = line[40:].strip()
    if not origin and not destination:
        return None
    if (origin and not NAME_FRAGMENT_RE.fullmatch(origin)) or (
        destination and not NAME_FRAGMENT_RE.fullmatch(destination)
    ):
        return None
    return origin, destination


def parse_layout_page(text: str, source_page: int) -> list[ParsedRow]:
    """Parse one position-preserving pypdf layout extraction."""
    rows: list[ParsedRow] = []
    current: ParsedRow | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = ROW_RE.fullmatch(line)
        if match:
            row = _row_from_match(match, source_page)
            if rows and row.source_row <= rows[-1].source_row:
                raise ExtractionError(
                    f"halaman {source_page}, baris teks {line_number}: nomor sumber "
                    f"tidak meningkat ({row.source_row} setelah {rows[-1].source_row})"
                )
            rows.append(row)
            current = row
            continue

        if ROW_CANDIDATE_RE.match(line):
            raise ExtractionError(
                f"halaman {source_page}, baris teks {line_number}: baris tarif tidak dapat "
                f"diurai: {line.strip()!r}"
            )

        if current is not None:
            fragments = _continuation_fragments(line)
            if fragments is not None:
                origin, destination = fragments
                if origin:
                    current.origin_name += _clean_name(origin)
                if destination:
                    current.destination_name += _clean_name(destination)

    if not rows:
        raise ExtractionError(f"halaman {source_page}: tidak menemukan baris tarif")
    return rows


def slugify(source_name: str) -> str:
    value = unicodedata.normalize("NFKD", source_name)
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "wilayah"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def extract_chunk(
    input_path: Path,
    output_path: Path,
    page_first: int,
    page_last: int,
    source_sha256: str,
) -> None:
    """Worker entry point: extract an inclusive human-numbered page range."""
    if pypdf.__version__ != EXPECTED_PYPDF_VERSION:
        raise ExtractionError(
            f"pypdf {EXPECTED_PYPDF_VERSION} diperlukan; ditemukan {pypdf.__version__}"
        )
    reader = PdfReader(str(input_path))
    if page_first < 1 or page_last > len(reader.pages) or page_first > page_last:
        raise ExtractionError(
            f"rentang halaman tidak valid: {page_first}-{page_last} dari {len(reader.pages)}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp-{os.getpid()}")
    row_count = 0
    first_source_row: int | None = None
    last_source_row: int | None = None
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(
                _json_line(
                    {
                        "record_type": "header",
                        "cache_format_version": CACHE_FORMAT_VERSION,
                        "extractor_version": DOMESTIC_CACHE_EXTRACTOR_VERSION,
                        "source_sha256": source_sha256,
                        "page_first": page_first,
                        "page_last": page_last,
                    }
                )
            )
            for source_page in range(page_first, page_last + 1):
                text = reader.pages[source_page - 1].extract_text(extraction_mode="layout")
                page_rows = parse_layout_page(text, source_page)
                if last_source_row is not None and page_rows[0].source_row <= last_source_row:
                    raise ExtractionError(
                        f"halaman {source_page}: nomor sumber tidak meningkat pada batas halaman"
                    )
                for row in page_rows:
                    if first_source_row is None:
                        first_source_row = row.source_row
                    last_source_row = row.source_row
                    row_count += 1
                    handle.write(_json_line({"record_type": "rate", **asdict(row)}))
            handle.write(
                _json_line(
                    {
                        "record_type": "trailer",
                        "rows": row_count,
                        "first_source_row": first_source_row,
                        "last_source_row": last_source_row,
                    }
                )
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(
        f"Diekstrak: halaman {page_first}-{page_last}, {row_count:,} baris -> {output_path}",
        flush=True,
    )


def _cache_header_matches(
    path: Path, source_sha256: str, page_first: int, page_last: int
) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            header = json.loads(handle.readline())
        if header != {
            "record_type": "header",
            "cache_format_version": CACHE_FORMAT_VERSION,
            "extractor_version": DOMESTIC_CACHE_EXTRACTOR_VERSION,
            "source_sha256": source_sha256,
            "page_first": page_first,
            "page_last": page_last,
        }:
            return False

        seen = 0
        previous_source_row: int | None = None
        previous_source_page: int | None = None
        for row in iter_cached_rows((path,), source_sha256):
            if not page_first <= row.source_page <= page_last:
                return False
            if previous_source_row is not None and row.source_row != previous_source_row + 1:
                return False
            if previous_source_page is not None and row.source_page < previous_source_page:
                return False
            if seen == 0 and row.source_page != page_first:
                return False
            seen += 1
            previous_source_row = row.source_row
            previous_source_page = row.source_page
        return seen > 0 and previous_source_page == page_last
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ExtractionError,
        TypeError,
        ValueError,
    ):
        return False


def prepare_chunks(
    input_path: Path, output_dir: Path, source_sha256: str, chunk_pages: int
) -> list[Path]:
    cache_dir = output_dir / ".extract-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[Path] = []
    page_first = DOMESTIC_PAGE_FIRST
    while page_first <= DOMESTIC_PAGE_LAST:
        page_last = min(page_first + chunk_pages - 1, DOMESTIC_PAGE_LAST)
        path = cache_dir / (
            f"chunk-{page_first:05d}-{page_last:05d}-{source_sha256[:12]}.jsonl"
        )
        chunks.append(path)
        if _cache_header_matches(path, source_sha256, page_first, page_last):
            print(f"Cache digunakan: halaman {page_first}-{page_last} ({path})", flush=True)
        else:
            if path.exists():
                path.unlink()
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--_extract-chunk",
                "--input",
                str(input_path),
                "--_chunk-output",
                str(path),
                "--_page-first",
                str(page_first),
                "--_page-last",
                str(page_last),
                "--_source-sha256",
                source_sha256,
            ]
            subprocess.run(command, check=True)
        page_first = page_last + 1
    return chunks


def get_pdf_page_count(input_path: Path) -> int:
    """Count pages in a disposable process so its PDF object graph is released."""
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_count-pages",
        "--input",
        str(input_path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    try:
        return int(result.stdout.strip())
    except ValueError as error:
        raise ExtractionError(
            f"hasil penghitung halaman tidak valid: {result.stdout!r}"
        ) from error


def iter_cached_rows(
    chunk_paths: Sequence[Path], source_sha256: str
) -> Iterator[ParsedRow]:
    for path in chunk_paths:
        with path.open("r", encoding="utf-8") as handle:
            try:
                header = json.loads(handle.readline())
            except json.JSONDecodeError as error:
                raise ExtractionError(f"header cache rusak: {path}") from error
            if (
                header.get("record_type") != "header"
                or header.get("cache_format_version") != CACHE_FORMAT_VERSION
                or header.get("extractor_version") != DOMESTIC_CACHE_EXTRACTOR_VERSION
                or header.get("source_sha256") != source_sha256
            ):
                raise ExtractionError(f"header cache tidak cocok: {path}")

            seen = 0
            first_source_row: int | None = None
            last_source_row: int | None = None
            trailer: dict[str, object] | None = None
            for line_number, line in enumerate(handle, start=2):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ExtractionError(
                        f"JSON cache rusak: {path}:{line_number}"
                    ) from error
                if not isinstance(record, dict):
                    raise ExtractionError(
                        f"rekaman cache bukan objek: {path}:{line_number}"
                    )
                record_type = record.pop("record_type", None)
                if record_type == "rate":
                    if trailer is not None:
                        raise ExtractionError(f"data ditemukan setelah trailer: {path}")
                    try:
                        row = ParsedRow(**record)
                    except TypeError as error:
                        raise ExtractionError(
                            f"kontrak baris cache tidak valid: {path}:{line_number}"
                        ) from error
                    integer_fields = ("source_row", "source_page", *TARIFF_COLUMNS)
                    text_fields = (
                        "origin_name",
                        "origin_id",
                        "origin_kprk",
                        "destination_name",
                        "destination_id",
                        "destination_kprk",
                    )
                    if any(
                        type(getattr(row, field)) is not int for field in integer_fields
                    ) or any(
                        not isinstance(getattr(row, field), str) for field in text_fields
                    ):
                        raise ExtractionError(
                            f"tipe field cache tidak valid: {path}:{line_number}"
                        )
                    if (
                        not row.origin_name.strip()
                        or not row.destination_name.strip()
                        or not re.fullmatch(_ID, row.origin_id)
                        or not re.fullmatch(_ID, row.destination_id)
                        or not re.fullmatch(_KPRK, row.origin_kprk)
                        or not re.fullmatch(_KPRK, row.destination_kprk)
                    ):
                        raise ExtractionError(
                            f"identitas field cache tidak valid: {path}:{line_number}"
                        )
                    seen += 1
                    if first_source_row is None:
                        first_source_row = row.source_row
                    last_source_row = row.source_row
                    yield row
                elif record_type == "trailer":
                    if trailer is not None:
                        raise ExtractionError(f"trailer cache ganda: {path}")
                    trailer = record
                else:
                    raise ExtractionError(
                        f"jenis rekaman cache tidak valid: {path}:{line_number}"
                    )
            if trailer is None or trailer != {
                "rows": seen,
                "first_source_row": first_source_row,
                "last_source_row": last_source_row,
            }:
                raise ExtractionError(f"trailer cache tidak cocok: {path}")


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE locations (
            office_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            kprk_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL UNIQUE CHECK (ordinal BETWEEN 1 AND 603),
            slug TEXT NOT NULL UNIQUE
        );

        CREATE TABLE rates (
            source_row INTEGER NOT NULL UNIQUE CHECK (source_row BETWEEN 1 AND 363609),
            source_page INTEGER NOT NULL CHECK (source_page BETWEEN 4 AND 20197),
            origin_id TEXT NOT NULL REFERENCES locations(office_id),
            destination_id TEXT NOT NULL REFERENCES locations(office_id),
            letter_up_to_100g INTEGER NOT NULL CHECK (letter_up_to_100g > 0),
            letter_over_100g_to_250g INTEGER NOT NULL CHECK (letter_over_100g_to_250g > 0),
            letter_over_250g_to_500g INTEGER NOT NULL CHECK (letter_over_250g_to_500g > 0),
            letter_over_500g_to_1000g INTEGER NOT NULL CHECK (letter_over_500g_to_1000g > 0),
            letter_over_1000g_to_2000g INTEGER NOT NULL CHECK (letter_over_1000g_to_2000g > 0),
            postcard INTEGER NOT NULL CHECK (postcard > 0),
            sekogram INTEGER NOT NULL CHECK (sekogram = 0),
            m_bag_per_kg INTEGER NOT NULL CHECK (m_bag_per_kg > 0),
            parcel_over_2kg_to_3kg INTEGER NOT NULL CHECK (parcel_over_2kg_to_3kg > 0),
            parcel_each_additional_kg INTEGER NOT NULL CHECK (parcel_each_additional_kg > 0),
            PRIMARY KEY (origin_id, destination_id)
        );

        CREATE INDEX rates_by_destination
            ON rates(destination_id, origin_id);

        CREATE TABLE international_rates (
            source_row INTEGER PRIMARY KEY CHECK (source_row BETWEEN 1 AND 236),
            source_page INTEGER NOT NULL CHECK (source_page BETWEEN 20198 AND 20208),
            source_name TEXT NOT NULL CHECK (trim(source_name) <> ''),
            country_code TEXT NOT NULL
                CHECK (length(country_code) = 2 AND country_code NOT GLOB '*[^A-Z]*'),
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
            sekogram_up_to_7kg INTEGER NOT NULL CHECK (sekogram_up_to_7kg = 0),
            m_bag_per_kg_up_to_30kg INTEGER,
            parcel_up_to_3kg_usd_cents INTEGER,
            parcel_each_additional_kg_usd_cents INTEGER,
            CHECK (
                (parcel_up_to_3kg_usd_cents IS NULL
                 AND parcel_each_additional_kg_usd_cents IS NULL)
                OR
                (parcel_up_to_3kg_usd_cents > 0
                 AND parcel_each_additional_kg_usd_cents > 0)
            )
        );

        CREATE INDEX international_rates_by_country_code
            ON international_rates(country_code, source_row);
        """
    )


def _unique_slugs(raw_locations: Sequence[tuple[str, str, str]]) -> list[Location]:
    return [
        Location(
            office_id=office_id,
            source_name=source_name,
            kprk_id=kprk_id,
            ordinal=index,
            slug=f"{slugify(source_name)}-{office_id.lower()}",
        )
        for index, (office_id, source_name, kprk_id) in enumerate(raw_locations, start=1)
    ]


def _insert_rates(
    connection: sqlite3.Connection, values: list[tuple[object, ...]]
) -> None:
    if not values:
        return
    placeholders = ",".join("?" for _ in RATE_COLUMNS)
    connection.executemany(
        f"INSERT INTO rates ({','.join(RATE_COLUMNS)}) VALUES ({placeholders})", values
    )
    values.clear()


def _artifact_details(path: Path) -> dict[str, object]:
    return {"path": path.name, "sha256": file_sha256(path), "bytes": path.stat().st_size}


def _validate_first_block_origin(
    row: ParsedRow, expected: tuple[str, str, str] | None
) -> tuple[str, str, str]:
    actual = (row.origin_id, row.origin_name, row.origin_kprk)
    if expected is not None and actual != expected:
        raise ExtractionError(
            f"baris {row.source_row}: metadata asal berubah di blok tujuan pertama"
        )
    return actual


def validate_and_build(
    chunk_paths: Sequence[Path],
    international_rows: Sequence[InternationalRate],
    ocr_provenance: OcrProvenance,
    output_dir: Path,
    input_path: Path,
    source_sha256: str,
    pdf_page_count: int,
) -> dict[str, object]:
    """Stream cached rows through full validation into unpublished artifacts."""
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".build-", dir=output_dir) as staging_name:
        staging = Path(staging_name)
        sqlite_path = staging / "postindo.sqlite"
        locations_csv_path = staging / "locations.csv"
        rates_csv_path = staging / "rates.csv"
        international_csv_path = staging / "international_rates.csv"
        manifest_path = staging / "manifest.json"

        connection = sqlite3.connect(sqlite_path)
        _create_schema(connection)
        raw_locations: list[tuple[str, str, str]] = []
        locations: list[Location] | None = None
        pending_rates: list[tuple[object, ...]] = []
        expected_row = 1
        previous_page = DOMESTIC_PAGE_FIRST
        self_routes = 0
        first_block_origin: tuple[str, str, str] | None = None

        try:
            with locations_csv_path.open(
                "w", encoding="utf-8", newline=""
            ) as locations_handle, rates_csv_path.open(
                "w", encoding="utf-8", newline=""
            ) as rates_handle:
                locations_writer = csv.writer(locations_handle, lineterminator="\n")
                rates_writer = csv.writer(rates_handle, lineterminator="\n")
                locations_writer.writerow(LOCATION_COLUMNS)
                rates_writer.writerow(RATE_COLUMNS)

                for row in iter_cached_rows(chunk_paths, source_sha256):
                    if row.source_row != expected_row:
                        raise ExtractionError(
                            f"nomor sumber tidak berurutan: diharapkan {expected_row}, "
                            f"ditemukan {row.source_row}"
                        )
                    if not DOMESTIC_PAGE_FIRST <= row.source_page <= DOMESTIC_PAGE_LAST:
                        raise ExtractionError(
                            f"baris {row.source_row}: halaman di luar lampiran domestik"
                        )
                    if row.source_page < previous_page:
                        raise ExtractionError(
                            f"baris {row.source_row}: nomor halaman mundur"
                        )
                    previous_page = row.source_page
                    if any(value <= 0 for name, value in zip(TARIFF_COLUMNS, row.rate_values()) if name != "sekogram"):
                        raise ExtractionError(f"baris {row.source_row}: tarif harus positif")
                    if row.sekogram != 0:
                        raise ExtractionError(f"baris {row.source_row}: sekogram tidak bebas biaya")

                    block_index, destination_index = divmod(row.source_row - 1, EXPECTED_LOCATION_COUNT)
                    if block_index >= EXPECTED_LOCATION_COUNT:
                        raise ExtractionError(f"baris berlebih: {row.source_row}")

                    if block_index == 0:
                        first_block_origin = _validate_first_block_origin(
                            row, first_block_origin
                        )
                        if any(item[0] == row.destination_id for item in raw_locations):
                            raise ExtractionError(
                                f"ID tujuan ganda pada blok pertama: {row.destination_id}"
                            )
                        raw_locations.append(
                            (row.destination_id, row.destination_name, row.destination_kprk)
                        )
                    else:
                        assert locations is not None
                        destination = locations[destination_index]
                        if (
                            row.destination_id,
                            row.destination_name,
                            row.destination_kprk,
                        ) != (
                            destination.office_id,
                            destination.source_name,
                            destination.kprk_id,
                        ):
                            raise ExtractionError(
                                f"baris {row.source_row}: metadata/urutan tujuan berbeda "
                                f"untuk ordinal {destination_index + 1}"
                            )

                    if destination_index == EXPECTED_LOCATION_COUNT - 1 and block_index == 0:
                        locations = _unique_slugs(raw_locations)
                        if len(locations) != EXPECTED_LOCATION_COUNT:
                            raise ExtractionError("blok tujuan pertama tidak berisi 603 lokasi")
                        connection.executemany(
                            f"INSERT INTO locations ({','.join(LOCATION_COLUMNS)}) VALUES (?,?,?,?,?)",
                            [location.values() for location in locations],
                        )
                        locations_writer.writerows(location.values() for location in locations)

                    if locations is not None:
                        origin = locations[block_index]
                        if (row.origin_id, row.origin_name, row.origin_kprk) != (
                            origin.office_id,
                            origin.source_name,
                            origin.kprk_id,
                        ):
                            raise ExtractionError(
                                f"baris {row.source_row}: metadata asal tidak cocok dengan "
                                f"ordinal {block_index + 1}"
                            )

                    pending_rates.append(row.database_values())
                    rates_writer.writerow(row.database_values())
                    if len(pending_rates) >= 5_000 and locations is not None:
                        _insert_rates(connection, pending_rates)
                    if row.origin_id == row.destination_id:
                        self_routes += 1
                    expected_row += 1

                if locations is None:
                    raise ExtractionError("data berakhir sebelum blok lokasi pertama lengkap")
                _insert_rates(connection, pending_rates)

            rate_count = expected_row - 1
            if rate_count != EXPECTED_RATE_COUNT:
                raise ExtractionError(
                    f"jumlah baris salah: diharapkan {EXPECTED_RATE_COUNT}, ditemukan {rate_count}"
                )
            if len(locations) != EXPECTED_LOCATION_COUNT:
                raise ExtractionError(f"jumlah lokasi salah: {len(locations)}")
            kprk_count = len({location.kprk_id for location in locations})
            if kprk_count != EXPECTED_KPRK_COUNT:
                raise ExtractionError(
                    f"jumlah KPRK salah: diharapkan {EXPECTED_KPRK_COUNT}, ditemukan {kprk_count}"
                )
            alphanumeric_count = sum(
                any(character.isalpha() for character in location.office_id)
                for location in locations
            )
            if alphanumeric_count != EXPECTED_ALPHANUMERIC_OFFICE_COUNT:
                raise ExtractionError(
                    "jumlah ID kantor alfanumerik salah: "
                    f"diharapkan {EXPECTED_ALPHANUMERIC_OFFICE_COUNT}, ditemukan {alphanumeric_count}"
                )
            if self_routes != EXPECTED_LOCATION_COUNT:
                raise ExtractionError(
                    f"jumlah rute ke diri sendiri salah: {self_routes}"
                )

            international_counts = validate_international_rows(
                international_rows, source_sha256=source_sha256
            )
            placeholders = ",".join("?" for _ in INTERNATIONAL_COLUMNS)
            connection.executemany(
                f"INSERT INTO international_rates ({','.join(INTERNATIONAL_COLUMNS)}) "
                f"VALUES ({placeholders})",
                [row.values() for row in international_rows],
            )
            with international_csv_path.open(
                "w", encoding="utf-8", newline=""
            ) as international_handle:
                international_writer = csv.writer(
                    international_handle, lineterminator="\n"
                )
                international_writer.writerow(INTERNATIONAL_COLUMNS)
                international_writer.writerows(row.values() for row in international_rows)

            anchor_query = f"""
                SELECT r.source_row, r.source_page,
                       r.origin_id, origin.source_name, origin.kprk_id,
                       r.destination_id, destination.source_name, destination.kprk_id,
                       {','.join('r.' + column for column in TARIFF_COLUMNS)}
                FROM rates AS r
                JOIN locations AS origin ON origin.office_id = r.origin_id
                JOIN locations AS destination ON destination.office_id = r.destination_id
                WHERE r.source_row = ?
            """
            for expected_anchor in EXPECTED_ANCHORS:
                actual_anchor = connection.execute(
                    anchor_query, (expected_anchor[0],)
                ).fetchone()
                if actual_anchor != expected_anchor:
                    raise ExtractionError(
                        f"jangkar sumber {expected_anchor[0]} salah: {actual_anchor}"
                    )
            foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise ExtractionError(f"pelanggaran foreign key: {foreign_key_errors[:3]}")

            metadata = {
                "schema_version": SCHEMA_VERSION,
                "extractor_version": EXTRACTOR_VERSION,
                "source_filename": input_path.name,
                "source_sha256": source_sha256,
                "source_bytes": str(input_path.stat().st_size),
                "source_pdf_pages": str(pdf_page_count),
                "domestic_page_first": str(DOMESTIC_PAGE_FIRST),
                "domestic_page_last": str(DOMESTIC_PAGE_LAST),
                "international_page_first": str(INTERNATIONAL_PAGE_FIRST),
                "international_page_last": str(INTERNATIONAL_PAGE_LAST),
                "locations_count": str(EXPECTED_LOCATION_COUNT),
                "rates_count": str(EXPECTED_RATE_COUNT),
                "kprk_ids_count": str(kprk_count),
                "alphanumeric_office_ids_count": str(alphanumeric_count),
                "self_routes_count": str(self_routes),
                **{
                    f"{key}_count": str(value)
                    for key, value in international_counts.items()
                },
                "international_ocr_engine": ocr_provenance.engine,
                "international_ocr_version": ocr_provenance.version,
                "international_ocr_extractor_version": ocr_provenance.extractor_version,
                "international_ocr_language": ocr_provenance.language,
                "international_ocr_page_segmentation_mode": str(
                    ocr_provenance.page_segmentation_mode
                ),
                "international_ocr_whole_page_passes": ",".join(
                    ocr_provenance.whole_page_passes
                ),
                "international_ocr_cell_fallback_passes": ",".join(
                    ocr_provenance.cell_fallback_passes
                ),
                "international_ocr_identity_passes": ",".join(
                    ocr_provenance.identity_passes
                ),
                "international_ocr_reviewed_numeric_cells": str(
                    ocr_provenance.reviewed_numeric_cells
                ),
                "international_ocr_reviewed_identity_cells": str(
                    ocr_provenance.reviewed_identity_cells
                ),
                "international_ocr_source_layer": ocr_provenance.source_layer,
                "international_ocr_rotation_degrees": str(
                    ocr_provenance.rotation_degrees
                ),
                "generated_at": generated_at,
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items()
            )
            connection.commit()
            connection.execute("ANALYZE")
            connection.commit()
            connection.execute("VACUUM")
        except Exception:
            connection.close()
            raise
        else:
            connection.close()

        artifacts = {
            path.name: _artifact_details(path)
            for path in (
                sqlite_path,
                locations_csv_path,
                rates_csv_path,
                international_csv_path,
            )
        }
        manifest: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "extractor_version": EXTRACTOR_VERSION,
            "generated_at": generated_at,
            "source": {
                "filename": input_path.name,
                "sha256": source_sha256,
                "bytes": input_path.stat().st_size,
                "pdf_pages": pdf_page_count,
                "domestic_pdf_pages": {
                    "first": DOMESTIC_PAGE_FIRST,
                    "last": DOMESTIC_PAGE_LAST,
                },
                "international_pdf_pages": {
                    "first": INTERNATIONAL_PAGE_FIRST,
                    "last": INTERNATIONAL_PAGE_LAST,
                },
            },
            "counts": {
                "locations": EXPECTED_LOCATION_COUNT,
                "rates": EXPECTED_RATE_COUNT,
                "kprk_ids": EXPECTED_KPRK_COUNT,
                "alphanumeric_office_ids": EXPECTED_ALPHANUMERIC_OFFICE_COUNT,
                "self_routes": EXPECTED_LOCATION_COUNT,
                **international_counts,
            },
            "ocr": asdict(ocr_provenance),
            "artifacts": artifacts,
        }
        with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        # The manifest is the completion marker and is therefore replaced last.
        for filename in (
            "postindo.sqlite",
            "locations.csv",
            "rates.csv",
            "international_rates.csv",
        ):
            os.replace(staging / filename, output_dir / filename)
        os.replace(manifest_path, output_dir / "manifest.json")
        return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Konversi tarif domestik dan internasional Kepmen Kominfo 222/2022"
    )
    parser.add_argument("--input", required=True, type=Path, help="berkas PDF sumber")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data"), help="direktori keluaran (default: data)"
    )
    parser.add_argument(
        "--chunk-pages", type=int, default=1_000, help="halaman per subprocess (default: 1000)"
    )
    parser.add_argument(
        "--allow-unverified-source",
        action="store_true",
        help="izinkan SHA-256 sumber yang berbeda (validasi struktur tetap dijalankan)",
    )
    parser.add_argument(
        "--tesseract-command",
        default="tesseract",
        help="nama/path executable Tesseract untuk lampiran internasional",
    )
    parser.add_argument("--_extract-chunk", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_count-pages", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_chunk-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_page-first", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--_page-last", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--_source-sha256", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        input_path = args.input.expanduser().resolve(strict=True)
        if args._count_pages:
            if pypdf.__version__ != EXPECTED_PYPDF_VERSION:
                raise ExtractionError(
                    f"pypdf {EXPECTED_PYPDF_VERSION} diperlukan; ditemukan {pypdf.__version__}"
                )
            print(len(PdfReader(str(input_path)).pages))
            return 0
        if args._extract_chunk:
            if not all(
                value is not None
                for value in (
                    args._chunk_output,
                    args._page_first,
                    args._page_last,
                    args._source_sha256,
                )
            ):
                raise ExtractionError("argumen worker chunk tidak lengkap")
            extract_chunk(
                input_path,
                args._chunk_output.resolve(),
                args._page_first,
                args._page_last,
                args._source_sha256,
            )
            return 0

        if args.chunk_pages <= 0:
            raise ExtractionError("--chunk-pages harus lebih besar dari nol")
        if pypdf.__version__ != EXPECTED_PYPDF_VERSION:
            raise ExtractionError(
                f"pypdf {EXPECTED_PYPDF_VERSION} diperlukan; ditemukan {pypdf.__version__}"
            )
        source_sha256 = file_sha256(input_path)
        if source_sha256 != EXPECTED_SHA256 and not args.allow_unverified_source:
            raise ExtractionError(
                "SHA-256 PDF tidak cocok. Gunakan --allow-unverified-source hanya jika Anda "
                "sengaja memakai salinan sumber lain.\n"
                f"Diharapkan: {EXPECTED_SHA256}\nDitemukan: {source_sha256}"
            )
        pdf_page_count = get_pdf_page_count(input_path)
        if pdf_page_count < INTERNATIONAL_PAGE_LAST:
            raise ExtractionError(
                f"PDF hanya memiliki {pdf_page_count} halaman; perlu halaman "
                f"{INTERNATIONAL_PAGE_LAST}"
            )
        output_dir = args.output_dir.expanduser().resolve()
        print(
            f"Sumber terverifikasi: {input_path.name} ({pdf_page_count:,} halaman, "
            f"SHA-256 {source_sha256})",
            flush=True,
        )
        chunks = prepare_chunks(input_path, output_dir, source_sha256, args.chunk_pages)
        international_rows, ocr_provenance = prepare_international_rates(
            input_path,
            output_dir / ".extract-cache",
            source_sha256,
            args.tesseract_command,
        )
        print("Memvalidasi matriks lengkap dan menulis artefak...", flush=True)
        manifest = validate_and_build(
            chunks,
            international_rows,
            ocr_provenance,
            output_dir,
            input_path,
            source_sha256,
            pdf_page_count,
        )
        counts = manifest["counts"]
        assert isinstance(counts, dict)
        print(
            f"Selesai: {counts['locations']:,} lokasi, {counts['rates']:,} rute domestik, "
            f"{counts['international_rates']:,} tarif internasional -> {output_dir}",
            flush=True,
        )
        return 0
    except (
        ExtractionError,
        InternationalExtractionError,
        OSError,
        sqlite3.Error,
        subprocess.CalledProcessError,
    ) as error:
        print(f"Gagal: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
