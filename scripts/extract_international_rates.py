#!/usr/bin/env python3
"""OCR the international tariff table in Kepmen Kominfo 222/2022.

The eleven scanned table pages use mixed-raster-content PDF pages: a JPEG
contains the table rules while a separate, high-resolution monochrome image
contains the text.  Reading that foreground image directly is both more
accurate and much cheaper than OCRing a rendered 20,210-page PDF.

This module is used by ``extract_rates.py``. It accepts agreeing non-null
whole-page readings directly; disagreements and unavailable-looking cells
must also receive a cell-crop reading that supports the combined consensus.
A small, documented set of visually reviewed corrections is applied only to
reviewed source pixels, and only fully validated rows are cached.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageOps
from pypdf import PdfReader


OCR_CACHE_FORMAT_VERSION = 2
INTERNATIONAL_EXTRACTOR_VERSION = "1.1.0"
REVIEWED_SOURCE_SHA256 = "811d1fb3ac805f172ea7f2d922cc1915b05c63226023bb9c68fc71a66c39bf3d"
EXPECTED_INTERNATIONAL_CSV_SHA256 = (
    "a10b6e61250b85833aa256cca678aa48e2146f4f0f850089e15a3ab3f6fa59d1"
)
INTERNATIONAL_PAGE_FIRST = 20_198
INTERNATIONAL_PAGE_LAST = 20_208
EXPECTED_INTERNATIONAL_RATE_COUNT = 236
EXPECTED_DISTINCT_COUNTRY_CODES = 235
EXPECTED_PARCEL_AVAILABLE = 190
EXPECTED_PARCEL_UNAVAILABLE = 46
EXPECTED_ROWS_WITHOUT_IDR_SERVICES = 2
EXPECTED_PARCEL_UNAVAILABLE_ROWS = (
    4, 11, 64, 66, 67, 81, 84, 85, 86, 97, 99, 102, 104, 109, 111,
    119, 120, 131, 132, 138, 140, 156, 160, 161, 167, 170, 176, 182,
    184, 188, 189, 191, 194, 195, 196, 197, 198, 204, 206, 211, 221,
    230, 231, 232, 233, 234,
)

# Inclusive PDF page and inclusive source-row bounds.  The table starts and
# ends on a row boundary on every page, which is a useful OCR integrity check.
INTERNATIONAL_PAGE_ROWS = (
    (20_198, 1, 22),
    (20_199, 23, 44),
    (20_200, 45, 68),
    (20_201, 69, 93),
    (20_202, 94, 115),
    (20_203, 116, 139),
    (20_204, 140, 164),
    (20_205, 165, 188),
    (20_206, 189, 209),
    (20_207, 210, 231),
    (20_208, 232, 236),
)

# Hashes of the exact rotated foreground layers used during manual review.
# They allow byte-repacked copies of the same scans to reuse the reviewed
# corrections without trusting a merely similar alternative PDF.
REVIEWED_FOREGROUND_SHA256 = {
    20_198: "15b732a32c27f95573f3c3f36d93a89213c440d0c2683a2052880379a18c4ba9",
    20_199: "982e2322047e9cda93c803dcd54c07dbf6e26592ae8ad74aeeb6df2a69a39a50",
    20_200: "f07228338a7dd8c61b41928f1ca35007c6ba5044ee9336ca09605dfe9de633b0",
    20_201: "5b93a46ccc8b33503fc9f3816124bdfbc5053c6e4f954d405a0c017e17e3bffb",
    20_202: "2777ad41d18aa1f0e7700916be7c398d00750654cedd7d54a92e2cf1b3b3d63d",
    20_203: "b4e03c7487c19f0b2dfb90242b2c7c655fad6ba1daf841bc7032325cb65b730b",
    20_204: "3d618796f6558d4acbc6afd211d03525647193b0f2e301cec7d80f4078ddf059",
    20_205: "f4eb5a904c1aa03fbd4b02cbb4b2841ff2ba46fb17f38b2607ddcd1714daef70",
    20_206: "36cf187b395650061ae8eaf1478635952c91dcd27130de72e5dce6c914f0afef",
    20_207: "36e51416e5bdd8306498378cc75e6b0522d7f1047bdb8650867aee3e6658fd7c",
    20_208: "67a0547ad8c6da658d6ace53fe48bd72178d14ff6249db5b7356d638f7da57b2",
}

INTERNATIONAL_COLUMNS = (
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
INTERNATIONAL_IDR_COLUMNS = INTERNATIONAL_COLUMNS[5:14] + (INTERNATIONAL_COLUMNS[15],)
INTERNATIONAL_PARCEL_COLUMNS = INTERNATIONAL_COLUMNS[16:]

# Horizontal bands in the rotated foreground image.  They are ratios rather
# than pixels because the MRC foreground width varies slightly between pages.
# The sekogram band is intentionally omitted: its one merged source cell says
# "Bebas biaya kirim s.d. 7 Kg" and is represented as zero on every row.
OCR_COLUMN_BANDS = {
    "letter_printed_matter_small_packet_up_to_20g": (0.195, 0.250),
    "letter_printed_matter_small_packet_over_20g_to_50g": (0.250, 0.302),
    "letter_printed_matter_small_packet_over_50g_to_100g": (0.302, 0.357),
    "letter_printed_matter_small_packet_over_100g_to_250g": (0.357, 0.415),
    "letter_printed_matter_small_packet_over_250g_to_500g": (0.415, 0.474),
    "letter_printed_matter_small_packet_over_500g_to_1000g": (0.474, 0.538),
    "letter_printed_matter_small_packet_over_1000g_to_1500g": (0.538, 0.607),
    "letter_printed_matter_small_packet_over_1500g_to_2000g": (0.607, 0.677),
    "postcard": (0.677, 0.752),
    "m_bag_per_kg_up_to_30kg": (0.806, 0.877),
    "parcel_up_to_3kg_usd_cents": (0.877, 0.947),
    "parcel_each_additional_kg_usd_cents": (0.947, 1.000),
}

# These cells are the complete set on which the three independent PSM-6 OCR
# passes disagreed.  They were read directly from isolated crops of the clean
# foreground layer. Overrides are used only for the exact reviewed PDF or an
# exact reviewed per-page foreground hash; other scans establish their own
# consensus.
REVIEWED_CELL_OVERRIDES = {
    (16, "parcel_up_to_3kg_usd_cents"): 3_986,
    (17, "parcel_up_to_3kg_usd_cents"): 3_186,
    (38, "parcel_up_to_3kg_usd_cents"): 8_684,
    (90, "letter_printed_matter_small_packet_over_100g_to_250g"): 89_000,
    (128, "postcard"): 7_000,
    (153, "letter_printed_matter_small_packet_over_20g_to_50g"): 18_000,
    (155, "letter_printed_matter_small_packet_over_1500g_to_2000g"): 834_000,
    (158, "parcel_each_additional_kg_usd_cents"): 828,
    (189, "letter_printed_matter_small_packet_up_to_20g"): 9_000,
    (194, "letter_printed_matter_small_packet_up_to_20g"): 10_000,
    (196, "letter_printed_matter_small_packet_up_to_20g"): 10_000,
    (205, "letter_printed_matter_small_packet_up_to_20g"): 6_000,
    (205, "parcel_each_additional_kg_usd_cents"): 910,
    (214, "parcel_each_additional_kg_usd_cents"): 754,
    (228, "letter_printed_matter_small_packet_over_20g_to_50g"): 18_000,
    (235, "letter_printed_matter_small_packet_over_100g_to_250g"): 118_000,
}

# A few short or low-contrast identity cells are consistently confused by one
# language model (for example AI as Al).  As above, these reviewed spellings
# and source codes are tied to the accepted source hash.
REVIEWED_IDENTITY_OVERRIDES = {
    1: ("Afghanistan", "AF"),
    6: ("Anguilla", "AI"),
    13: ("Austria", "AT"),
    14: ("Azerbaijan", "AZ"),
    18: ("Barbados", "BB"),
    20: ("Belgium", "BE"),
    21: ("Belize", "BZ"),
    22: ("Benin", "BJ"),
    34: ("Cameroon", "CM"),
    44: ("Congo (Rep. Dem. of The)", "CD"),
    50: ("Cuba", "CU"),
    61: ("Equatorial Guinea", "GQ"),
    64: ("Eswatini (Swaziland)", "SZ"),
    97: ("Iraq", "IQ"),
    102: ("Jamaica", "JM"),
    111: ("Kosovo", "XZ"),
    112: ("Kuwait", "KW"),
    113: ("Kyrgyzstan", "KG"),
    114: ("Lao People's Democratic Republic", "LA"),
    115: ("Latvia", "LV"),
    123: ("Macao", "MO"),
    124: ("Macedonia, The Former Yugoslav Rep of", "MK"),
    125: ("Madagascar", "MG"),
    133: ("Martinique", "MQ"),
    145: ("Mozambique", "MZ"),
    171: ("Qatar", "QA"),
    181: ("Seychelles", "SC"),
    194: ("St Christopher (St Kitts) and Nevis", "KN"),
    196: ("St Lucia", "LC"),
    197: ("St Pierre & Miquelon", "PM"),
    198: ("St Vincent and the Grenadines", "VC"),
    200: ("Suriname", "SR"),
    201: ("Swaziland", "SZ"),
    205: ("Taiwan (Republic of China)", "TW"),
    206: ("Tajikistan", "TJ"),
    207: ("Tanzania (United Rep.)", "TZ"),
    208: ("Thailand", "TH"),
    209: ("Timor-Leste (Dem. Rep.)", "TL"),
    211: ("Tokelau", "TK"),
    213: ("Trinidad and Tobago", "TT"),
    214: ("Tristan Da Cunha", "TA"),
    217: ("Turkmenistan", "TM"),
    218: ("Turks & Caicos Islands", "TC"),
    222: ("United Arab Emirates", "AE"),
    223: ("United States of America", "US"),
    224: ("Uruguay", "UY"),
    225: ("Uzbekistan", "UZ"),
    229: ("Viet nam", "VN"),
    231: ("Virgin Islands (US)", "VI"),
    236: ("Zimbabwe", "ZW"),
}

_IDR_RE = re.compile(r"(?<![0-9])[0-9]{1,3}(?:[.,-][0-9]{3})+(?![0-9])")
_USD_RE = re.compile(r"(?<![0-9])[0-9]{1,3}[,.][0-9]{2}(?![0-9])")
_CODE_RE = re.compile(r"[A-Z]{2}")
_NO_REVIEWED_VALUE = object()


class InternationalExtractionError(RuntimeError):
    """Raised when OCR or the scanned table violates its strict contract."""


@dataclass(frozen=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2


@dataclass(frozen=True)
class InternationalRate:
    source_row: int
    source_page: int
    source_name: str
    country_code: str
    slug: str
    letter_printed_matter_small_packet_up_to_20g: int | None
    letter_printed_matter_small_packet_over_20g_to_50g: int | None
    letter_printed_matter_small_packet_over_50g_to_100g: int | None
    letter_printed_matter_small_packet_over_100g_to_250g: int | None
    letter_printed_matter_small_packet_over_250g_to_500g: int | None
    letter_printed_matter_small_packet_over_500g_to_1000g: int | None
    letter_printed_matter_small_packet_over_1000g_to_1500g: int | None
    letter_printed_matter_small_packet_over_1500g_to_2000g: int | None
    postcard: int | None
    sekogram_up_to_7kg: int
    m_bag_per_kg_up_to_30kg: int | None
    parcel_up_to_3kg_usd_cents: int | None
    parcel_each_additional_kg_usd_cents: int | None

    def values(self) -> tuple[object, ...]:
        return tuple(getattr(self, column) for column in INTERNATIONAL_COLUMNS)


@dataclass(frozen=True)
class OcrProvenance:
    engine: str
    version: str
    extractor_version: str = INTERNATIONAL_EXTRACTOR_VERSION
    language: str = "eng"
    page_segmentation_mode: int = 6
    whole_page_passes: tuple[str, ...] = ("eng:6", "eng+ind:6")
    cell_fallback_passes: tuple[str, ...] = ("eng:7", "eng+ind:7", "eng:8")
    identity_passes: tuple[str, ...] = ("eng:6", "eng+ind:7")
    reviewed_numeric_cells: int = 0
    reviewed_identity_cells: int = 0
    source_layer: str = "largest_monochrome_page_image"
    rotation_degrees: int = 270


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
    111: (
        111, 20_202, "Kosovo", "XZ", "kosovo-xz",
        9_000, 18_000, 36_000, 90_000, 156_000, 310_000, 467_000, 622_000,
        9_000, 0, 302_000, None, None,
    ),
    189: (
        189, 20_206, "Somaliland", "XS", "somaliland-xs",
        9_000, 21_000, 42_000, 103_000, 206_000, 412_000, 618_000, 824_000,
        9_000, 0, 351_000, None, None,
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


def international_rows_csv_sha256(rows: Sequence[InternationalRate]) -> str:
    """Hash the deterministic public CSV representation of international rows."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(INTERNATIONAL_COLUMNS)
    writer.writerows(row.values() for row in rows)
    return hashlib.sha256(buffer.getvalue().encode("utf-8")).hexdigest()


def foreground_sha256(image: Image.Image) -> str:
    """Hash an extracted foreground with dimensions and mode included."""
    payload = (
        b"postindo-international-foreground-v1\0"
        + image.mode.encode("ascii")
        + b"\0"
        + image.width.to_bytes(4, "big")
        + image.height.to_bytes(4, "big")
        + image.tobytes()
    )
    return hashlib.sha256(payload).hexdigest()


def _slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "negara"


def _source_slug(source_name: str, country_code: str) -> str:
    return f"{_slugify(source_name)}-{country_code.lower()}"


def _clean_ocr_text(value: str) -> str:
    return " ".join(value.replace("|", " ").replace("_", " ").split())


def parse_idr_ocr(value: str) -> int | None:
    """Parse an OCR cell containing source rupiah notation or a dash."""
    cleaned = _clean_ocr_text(value).replace("—", "-").replace("–", "-")
    matches = _IDR_RE.findall(cleaned)
    if len(matches) == 1:
        return int(
            matches[0].replace(".", "").replace(",", "").replace("-", "")
        )
    if not matches and (not cleaned or set(cleaned) <= {"-", ".", ","}):
        return None
    raise InternationalExtractionError(f"sel rupiah OCR ambigu: {value!r}")


def parse_usd_ocr(value: str) -> int | None:
    """Parse source decimal-comma US dollars into integer cents."""
    cleaned = _clean_ocr_text(value).replace("—", "-").replace("–", "-")
    matches = _USD_RE.findall(cleaned)
    if len(matches) == 1:
        whole, cents = re.split(r"[,.]", matches[0])
        return int(whole) * 100 + int(cents)
    if not matches and (not cleaned or set(cleaned) <= {"-", ".", ","}):
        return None
    raise InternationalExtractionError(f"sel dolar OCR ambigu: {value!r}")


def _tesseract_environment(tesseract_command: str) -> dict[str, str]:
    # Normally no override is needed.  Keeping the current environment allows
    # callers to use a locally unpacked binary via TESSDATA_PREFIX/LD_LIBRARY_PATH.
    environment = os.environ.copy()
    environment.setdefault("OMP_THREAD_LIMIT", "1")
    return environment


def tesseract_version(tesseract_command: str) -> str:
    try:
        result = subprocess.run(
            [tesseract_command, "--version"],
            check=True,
            capture_output=True,
            text=True,
            env=_tesseract_environment(tesseract_command),
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise InternationalExtractionError(
            f"Tesseract tidak dapat dijalankan: {tesseract_command!r}"
        ) from error
    first_line = result.stdout.splitlines()[0] if result.stdout.splitlines() else ""
    match = re.search(r"tesseract\s+([^\s]+)", first_line, re.IGNORECASE)
    if match is None:
        raise InternationalExtractionError(
            f"versi Tesseract tidak dapat dibaca: {first_line!r}"
        )
    return match.group(1)


def _require_tesseract_languages(tesseract_command: str) -> None:
    try:
        result = subprocess.run(
            [tesseract_command, "--list-langs"],
            check=True,
            capture_output=True,
            text=True,
            env=_tesseract_environment(tesseract_command),
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise InternationalExtractionError("bahasa OCR Tesseract tidak dapat diperiksa") from error
    languages = set(result.stdout.split())
    missing = {"eng", "ind"} - languages
    if missing:
        raise InternationalExtractionError(
            "data bahasa Tesseract belum terpasang: " + ", ".join(sorted(missing))
        )


def _run_tesseract_tsv(
    image_path: Path, tesseract_command: str, language: str
) -> list[OcrWord]:
    command = [
        tesseract_command,
        str(image_path),
        "stdout",
        "-l",
        language,
        "--psm",
        "6",
        "tsv",
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=_tesseract_environment(tesseract_command),
        )
    except (OSError, subprocess.CalledProcessError) as error:
        stderr = getattr(error, "stderr", "")
        raise InternationalExtractionError(
            f"OCR TSV gagal untuk {image_path.name}: {stderr}"
        ) from error

    words: list[OcrWord] = []
    try:
        # OCR text can itself begin with a double quote.  Tesseract does not
        # quote TSV fields, so RFC-style quote handling would accidentally
        # swallow every following record until another quote appears.
        reader = csv.DictReader(
            io.StringIO(result.stdout), delimiter="\t", quoting=csv.QUOTE_NONE
        )
        for record in reader:
            if record.get("level") != "5" or not record.get("text", "").strip():
                continue
            words.append(
                OcrWord(
                    text=record["text"],
                    left=int(record["left"]),
                    top=int(record["top"]),
                    width=int(record["width"]),
                    height=int(record["height"]),
                    confidence=float(record["conf"]),
                )
            )
    except (KeyError, TypeError, ValueError, csv.Error) as error:
        raise InternationalExtractionError(
            f"TSV Tesseract rusak untuk {image_path.name}"
        ) from error
    if not words:
        raise InternationalExtractionError(f"OCR tidak menghasilkan kata: {image_path.name}")
    return words


def _run_tesseract_text(
    image_path: Path,
    tesseract_command: str,
    language: str,
    page_segmentation_mode: int,
    whitelist: str | None = None,
) -> str:
    command = [
        tesseract_command,
        str(image_path),
        "stdout",
        "-l",
        language,
        "--psm",
        str(page_segmentation_mode),
    ]
    if whitelist is not None:
        command.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=_tesseract_environment(tesseract_command),
        )
    except (OSError, subprocess.CalledProcessError) as error:
        stderr = getattr(error, "stderr", "")
        raise InternationalExtractionError(
            f"OCR sel gagal untuk {image_path.name}: {stderr}"
        ) from error
    return _clean_ocr_text(result.stdout)


def _extract_foreground(page: object, source_page: int) -> Image.Image:
    candidates: list[Image.Image] = []
    try:
        for image_file in page.images:  # type: ignore[attr-defined]
            image = image_file.image
            if image.mode == "1":
                candidates.append(image.copy())
    except Exception as error:
        raise InternationalExtractionError(
            f"halaman {source_page}: lapisan gambar PDF tidak dapat dibaca"
        ) from error
    if not candidates:
        raise InternationalExtractionError(
            f"halaman {source_page}: tidak menemukan lapisan teks monokrom"
        )
    foreground = max(candidates, key=lambda image: image.width * image.height)
    if foreground.width * foreground.height < 1_000_000:
        raise InternationalExtractionError(
            f"halaman {source_page}: lapisan monokrom utama terlalu kecil"
        )
    # The PDF stores the foreground in portrait orientation, rotated left from
    # the human-readable landscape table.  PIL angles are counter-clockwise.
    return foreground.convert("L").rotate(270, expand=True)


def _row_centers(
    words: Sequence[OcrWord], width: int, expected_first: int, expected_last: int
) -> dict[int, float]:
    found: dict[int, OcrWord] = {}
    for word in words:
        cleaned = word.text.strip()
        if not cleaned.isdigit() or word.center_x >= width * 0.035:
            continue
        value = int(cleaned)
        if expected_first <= value <= expected_last:
            previous = found.get(value)
            if previous is None or word.confidence > previous.confidence:
                found[value] = word
    expected = list(range(expected_first, expected_last + 1))
    if sorted(found) != expected:
        raise InternationalExtractionError(
            f"ordinal OCR berbeda: ditemukan {sorted(found)}, seharusnya {expected}"
        )
    centers = {row: found[row].center_y for row in expected}
    if any(centers[b] <= centers[a] for a, b in zip(expected, expected[1:])):
        raise InternationalExtractionError("posisi ordinal OCR tidak meningkat")
    return centers


def _row_bounds(centers: dict[int, float], image_height: int) -> dict[int, tuple[int, int]]:
    rows = sorted(centers)
    bounds: dict[int, tuple[int, int]] = {}
    for index, row in enumerate(rows):
        center = centers[row]
        if index == 0:
            half_before = (centers[rows[1]] - center) / 2
            top = center - half_before
        else:
            top = (centers[rows[index - 1]] + center) / 2
        if index == len(rows) - 1:
            half_after = (center - centers[rows[index - 1]]) / 2
            bottom = center + half_after
        else:
            bottom = (center + centers[rows[index + 1]]) / 2
        # A small overlap keeps ascenders/descenders and two-line names intact;
        # the generous blank row spacing prevents neighboring text leaking in.
        bounds[row] = (max(0, int(top) - 5), min(image_height, int(bottom) + 6))
    return bounds


def _cell_text(
    words: Sequence[OcrWord],
    width: int,
    row_bounds: tuple[int, int],
    column_band: tuple[float, float],
) -> str:
    top, bottom = row_bounds
    left = width * column_band[0]
    right = width * column_band[1]
    selected = [
        word
        for word in words
        if top <= word.center_y < bottom and left <= word.center_x < right
    ]
    selected.sort(key=lambda word: (word.top, word.left))
    return " ".join(word.text for word in selected)


def _write_crop(
    image: Image.Image,
    destination: Path,
    row_bounds: tuple[int, int],
    column_band: tuple[float, float],
    *,
    scale: int = 2,
) -> None:
    top, bottom = row_bounds
    left = max(0, int(image.width * column_band[0]) - 4)
    right = min(image.width, int(image.width * column_band[1]) + 4)
    crop = ImageOps.autocontrast(image.crop((left, top, right, bottom)))
    if scale != 1:
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
    # Padding prevents Tesseract treating edge-touching punctuation as noise.
    padded = Image.new("L", (crop.width + 32, crop.height + 24), 255)
    padded.paste(crop, (16, 12))
    padded.save(destination, format="PNG")


def _majority_cell_value(
    image: Image.Image,
    temporary_dir: Path,
    row: int,
    field: str,
    row_bounds: tuple[int, int],
    tesseract_command: str,
    initial_values: Iterable[int | None],
) -> int | None:
    crop_path = temporary_dir / f"cell-{row:03d}-{field}.png"
    _write_crop(image, crop_path, row_bounds, OCR_COLUMN_BANDS[field])
    parser = parse_usd_ocr if field in INTERNATIONAL_PARCEL_COLUMNS else parse_idr_ocr
    whitelist = "0123456789.,-"
    texts = (
        _run_tesseract_text(crop_path, tesseract_command, "eng", 7, whitelist),
        _run_tesseract_text(crop_path, tesseract_command, "eng+ind", 7, whitelist),
        _run_tesseract_text(crop_path, tesseract_command, "eng", 8, whitelist),
    )
    fallback_values: list[int | None] = []
    for text in texts:
        try:
            fallback_values.append(parser(text))
        except InternationalExtractionError:
            continue
    if not fallback_values:
        raise InternationalExtractionError(
            f"baris {row}, {field}: OCR sel tidak terbaca; "
            f"hasil halaman={list(initial_values)}"
        )
    counts = Counter([*initial_values, *fallback_values])
    value, votes = counts.most_common(1)[0]
    tied = [candidate for candidate, count in counts.items() if count == votes]
    if votes < 2 or len(tied) != 1 or value not in fallback_values:
        raise InternationalExtractionError(
            f"baris {row}, {field}: OCR tanpa konsensus yang didukung sel "
            f"{dict(counts)}; hasil sel={fallback_values}"
        )
    return value


def _read_numeric_cell(
    image: Image.Image,
    temporary_dir: Path,
    row: int,
    field: str,
    passes: Sequence[tuple[Sequence[OcrWord], dict[int, tuple[int, int]]]],
    primary_bounds: tuple[int, int],
    tesseract_command: str,
    reviewed_value: int | None | object,
) -> int | None:
    parser = parse_usd_ocr if field in INTERNATIONAL_PARCEL_COLUMNS else parse_idr_ocr
    parsed: list[int | None] = []
    for words, bounds_by_row in passes:
        text = _cell_text(words, image.width, bounds_by_row[row], OCR_COLUMN_BANDS[field])
        try:
            parsed.append(parser(text))
        except InternationalExtractionError:
            pass
    if reviewed_value is not _NO_REVIEWED_VALUE:
        return reviewed_value  # type: ignore[return-value]
    if len(parsed) >= 2 and parsed[0] == parsed[1] and parsed[0] is not None:
        return parsed[0]
    # Verify all unavailable-looking cells at cell resolution too.  This avoids
    # silently interpreting a completely missed number as a source dash.
    return _majority_cell_value(
        image,
        temporary_dir,
        row,
        field,
        primary_bounds,
        tesseract_command,
        parsed,
    )


def _read_identity(
    image: Image.Image,
    temporary_dir: Path,
    row: int,
    row_bounds: tuple[int, int],
    tesseract_command: str,
) -> tuple[str, str]:
    name_path = temporary_dir / f"name-{row:03d}.png"
    code_path = temporary_dir / f"code-{row:03d}.png"
    _write_crop(image, name_path, row_bounds, (0.022, 0.155), scale=2)
    _write_crop(image, code_path, row_bounds, (0.155, 0.198), scale=3)
    source_name = _run_tesseract_text(name_path, tesseract_command, "eng", 6).strip(
        " -_|~«»“”\"'"
    )
    country_code = _run_tesseract_text(
        code_path,
        tesseract_command,
        "eng+ind",
        7,
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    ).upper()
    if not source_name or len(source_name) > 100:
        raise InternationalExtractionError(
            f"baris {row}: nama negara OCR tidak valid {source_name!r}"
        )
    if not _CODE_RE.fullmatch(country_code):
        raise InternationalExtractionError(
            f"baris {row}: kode negara OCR tidak valid {country_code!r}"
        )
    return source_name, country_code


def extract_page_rows(
    image: Image.Image,
    source_page: int,
    expected_first: int,
    expected_last: int,
    temporary_dir: Path,
    tesseract_command: str,
    use_reviewed_overrides: bool,
) -> list[InternationalRate]:
    page_path = temporary_dir / f"foreground-{source_page}.png"
    image.save(page_path, format="PNG")
    primary_words = _run_tesseract_tsv(page_path, tesseract_command, "eng")
    secondary_words = _run_tesseract_tsv(page_path, tesseract_command, "eng+ind")
    primary_bounds = _row_bounds(
        _row_centers(primary_words, image.width, expected_first, expected_last), image.height
    )
    secondary_bounds = _row_bounds(
        _row_centers(secondary_words, image.width, expected_first, expected_last), image.height
    )
    passes = (
        (primary_words, primary_bounds),
        (secondary_words, secondary_bounds),
    )

    rows: list[InternationalRate] = []
    for source_row in range(expected_first, expected_last + 1):
        source_name, country_code = _read_identity(
            image,
            temporary_dir,
            source_row,
            primary_bounds[source_row],
            tesseract_command,
        )
        if use_reviewed_overrides and source_row in REVIEWED_IDENTITY_OVERRIDES:
            source_name, country_code = REVIEWED_IDENTITY_OVERRIDES[source_row]
        values = {
            field: _read_numeric_cell(
                image,
                temporary_dir,
                source_row,
                field,
                passes,
                primary_bounds[source_row],
                tesseract_command,
                REVIEWED_CELL_OVERRIDES.get((source_row, field), _NO_REVIEWED_VALUE)
                if use_reviewed_overrides
                else _NO_REVIEWED_VALUE,
            )
            for field in OCR_COLUMN_BANDS
        }
        rows.append(
            InternationalRate(
                source_row=source_row,
                source_page=source_page,
                source_name=source_name,
                country_code=country_code,
                slug=_source_slug(source_name, country_code),
                sekogram_up_to_7kg=0,
                **values,
            )
        )
    return rows


def validate_international_rows(
    rows: Sequence[InternationalRate], *, source_sha256: str | None = None
) -> dict[str, int]:
    if len(rows) != EXPECTED_INTERNATIONAL_RATE_COUNT:
        raise InternationalExtractionError(
            f"jumlah baris internasional salah: {len(rows)}"
        )
    if [row.source_row for row in rows] != list(
        range(1, EXPECTED_INTERNATIONAL_RATE_COUNT + 1)
    ):
        raise InternationalExtractionError("ordinal internasional tidak tepat 1..236")

    for page, first, last in INTERNATIONAL_PAGE_ROWS:
        actual = [row.source_row for row in rows if row.source_page == page]
        if actual != list(range(first, last + 1)):
            raise InternationalExtractionError(
                f"partisi halaman {page} salah: {actual[:3]}..{actual[-3:] if actual else []}"
            )

    codes = Counter(row.country_code for row in rows)
    if len(codes) != EXPECTED_DISTINCT_COUNTRY_CODES:
        raise InternationalExtractionError(
            f"jumlah kode negara unik salah: {len(codes)}"
        )
    duplicates = {code: count for code, count in codes.items() if count != 1}
    if duplicates != {"SZ": 2}:
        raise InternationalExtractionError(f"kode negara ganda tidak sesuai: {duplicates}")
    if len({row.slug for row in rows}) != len(rows):
        raise InternationalExtractionError("slug internasional tidak unik")

    for row in rows:
        if not row.source_name.strip() or not _CODE_RE.fullmatch(row.country_code):
            raise InternationalExtractionError(f"baris {row.source_row}: identitas tidak valid")
        idr_values = [getattr(row, column) for column in INTERNATIONAL_IDR_COLUMNS]
        if any(value is not None and (type(value) is not int or value <= 0)
               for value in idr_values):
            raise InternationalExtractionError(
                f"baris {row.source_row}: nilai rupiah tidak valid"
            )
        if any(value is None for value in idr_values) and not all(
            value is None for value in idr_values
        ):
            raise InternationalExtractionError(
                f"baris {row.source_row}: kelompok layanan rupiah tidak lengkap"
            )
        letter_values = [
            getattr(row, column)
            for column in INTERNATIONAL_COLUMNS[5:13]
        ]
        populated_letters = [value for value in letter_values if value is not None]
        if populated_letters and any(
            right <= left for left, right in zip(populated_letters, populated_letters[1:])
        ):
            raise InternationalExtractionError(
                f"baris {row.source_row}: pita berat surat tidak meningkat"
            )
        if row.postcard != row.letter_printed_matter_small_packet_up_to_20g:
            raise InternationalExtractionError(
                f"baris {row.source_row}: kartu pos tidak sama dengan tarif surat pertama"
            )
        if row.sekogram_up_to_7kg != 0:
            raise InternationalExtractionError(
                f"baris {row.source_row}: sekogram bukan bebas biaya"
            )
        parcel = (
            row.parcel_up_to_3kg_usd_cents,
            row.parcel_each_additional_kg_usd_cents,
        )
        if (parcel[0] is None) != (parcel[1] is None):
            raise InternationalExtractionError(
                f"baris {row.source_row}: pasangan tarif paket tidak lengkap"
            )
        if any(value is not None and (type(value) is not int or value <= 0) for value in parcel):
            raise InternationalExtractionError(
                f"baris {row.source_row}: sen dolar paket tidak valid"
            )

    parcel_available = sum(row.parcel_up_to_3kg_usd_cents is not None for row in rows)
    parcel_unavailable = len(rows) - parcel_available
    no_idr = sum(
        all(getattr(row, column) is None for column in INTERNATIONAL_IDR_COLUMNS)
        for row in rows
    )
    if parcel_available != EXPECTED_PARCEL_AVAILABLE:
        raise InternationalExtractionError(
            f"jumlah tujuan paket tersedia salah: {parcel_available}"
        )
    if parcel_unavailable != EXPECTED_PARCEL_UNAVAILABLE:
        raise InternationalExtractionError(
            f"jumlah tujuan paket tidak tersedia salah: {parcel_unavailable}"
        )
    unavailable_rows = tuple(
        row.source_row for row in rows if row.parcel_up_to_3kg_usd_cents is None
    )
    if unavailable_rows != EXPECTED_PARCEL_UNAVAILABLE_ROWS:
        raise InternationalExtractionError(
            f"baris paket tidak tersedia tidak sesuai: {unavailable_rows}"
        )
    if no_idr != EXPECTED_ROWS_WITHOUT_IDR_SERVICES:
        raise InternationalExtractionError(f"jumlah baris tanpa tarif rupiah salah: {no_idr}")
    if [row.source_row for row in rows if all(
        getattr(row, column) is None for column in INTERNATIONAL_IDR_COLUMNS
    )] != [214, 217]:
        raise InternationalExtractionError("baris tanpa layanan rupiah bukan 214 dan 217")

    by_row = {row.source_row: row for row in rows}
    for source_row, expected in EXPECTED_INTERNATIONAL_ANCHORS.items():
        actual = by_row[source_row].values()
        if actual != expected:
            raise InternationalExtractionError(
                f"jangkar internasional {source_row} salah: {actual}"
            )
    if source_sha256 == REVIEWED_SOURCE_SHA256:
        actual_digest = international_rows_csv_sha256(rows)
        if actual_digest != EXPECTED_INTERNATIONAL_CSV_SHA256:
            raise InternationalExtractionError(
                "digest seluruh tabel internasional tidak cocok: "
                f"{actual_digest}"
            )

    return {
        "international_rates": len(rows),
        "international_distinct_country_codes": len(codes),
        "international_parcel_available": parcel_available,
        "international_parcel_unavailable": parcel_unavailable,
        "international_rows_without_idr_services": no_idr,
    }


def _cache_path(cache_dir: Path, source_sha256: str) -> Path:
    return cache_dir / f"international-{source_sha256[:12]}-ocr-v{OCR_CACHE_FORMAT_VERSION}.json"


def reviewed_correction_counts(source_pages: Iterable[int]) -> tuple[int, int]:
    """Count reviewed corrections belonging to the trusted source pages."""
    trusted_rows: set[int] = set()
    pages = set(source_pages)
    for source_page, first_row, last_row in INTERNATIONAL_PAGE_ROWS:
        if source_page in pages:
            trusted_rows.update(range(first_row, last_row + 1))
    numeric = sum(row in trusted_rows for row, _column in REVIEWED_CELL_OVERRIDES)
    identity = sum(row in trusted_rows for row in REVIEWED_IDENTITY_OVERRIDES)
    return numeric, identity


def _load_cache(
    path: Path, source_sha256: str
) -> tuple[list[InternationalRate], OcrProvenance] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("header") != {
            "cache_format_version": OCR_CACHE_FORMAT_VERSION,
            "extractor_version": INTERNATIONAL_EXTRACTOR_VERSION,
            "source_sha256": source_sha256,
        }:
            return None
        raw_rows = value.get("rows")
        raw_ocr = value.get("ocr")
        if not isinstance(raw_rows, list) or not isinstance(raw_ocr, dict):
            return None
        for key in (
            "whole_page_passes",
            "cell_fallback_passes",
            "identity_passes",
        ):
            if isinstance(raw_ocr.get(key), list):
                raw_ocr[key] = tuple(raw_ocr[key])
        rows = [InternationalRate(**row) for row in raw_rows]
        provenance = OcrProvenance(**raw_ocr)
        expected_static = OcrProvenance(
            engine="tesseract",
            version=provenance.version,
            reviewed_numeric_cells=provenance.reviewed_numeric_cells,
            reviewed_identity_cells=provenance.reviewed_identity_cells,
        )
        if not provenance.version.strip() or provenance != expected_static:
            return None
        counts = (
            provenance.reviewed_numeric_cells,
            provenance.reviewed_identity_cells,
        )
        maxima = (len(REVIEWED_CELL_OVERRIDES), len(REVIEWED_IDENTITY_OVERRIDES))
        if any(
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum
            for value, maximum in zip(counts, maxima, strict=True)
        ):
            return None
        if source_sha256 == REVIEWED_SOURCE_SHA256 and counts != maxima:
            return None
        validate_international_rows(rows, source_sha256=source_sha256)
        return rows, provenance
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        InternationalExtractionError,
    ):
        return None


def _write_cache(
    path: Path,
    source_sha256: str,
    rows: Sequence[InternationalRate],
    provenance: OcrProvenance,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    value = {
        "header": {
            "cache_format_version": OCR_CACHE_FORMAT_VERSION,
            "extractor_version": INTERNATIONAL_EXTRACTOR_VERSION,
            "source_sha256": source_sha256,
        },
        "ocr": asdict(provenance),
        "rows": [asdict(row) for row in rows],
    }
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def prepare_international_rates(
    input_path: Path,
    cache_dir: Path,
    source_sha256: str,
    tesseract_command: str = "tesseract",
) -> tuple[list[InternationalRate], OcrProvenance]:
    cache = _cache_path(cache_dir, source_sha256)
    cached = _load_cache(cache, source_sha256)
    if cached is not None:
        print(f"Cache OCR internasional digunakan: {cache}", flush=True)
        return cached

    version = tesseract_version(tesseract_command)
    _require_tesseract_languages(tesseract_command)
    reader = PdfReader(str(input_path))
    if len(reader.pages) < INTERNATIONAL_PAGE_LAST:
        raise InternationalExtractionError(
            f"PDF hanya memiliki {len(reader.pages)} halaman; perlu halaman {INTERNATIONAL_PAGE_LAST}"
        )

    rows: list[InternationalRate] = []
    reviewed_pages: set[int] = set()
    with tempfile.TemporaryDirectory(prefix="postindo-international-ocr-") as name:
        temporary_dir = Path(name)
        for source_page, expected_first, expected_last in INTERNATIONAL_PAGE_ROWS:
            image = _extract_foreground(reader.pages[source_page - 1], source_page)
            reviewed_foreground = (
                foreground_sha256(image) == REVIEWED_FOREGROUND_SHA256[source_page]
            )
            use_reviewed_corrections = (
                source_sha256 == REVIEWED_SOURCE_SHA256 or reviewed_foreground
            )
            if use_reviewed_corrections:
                reviewed_pages.add(source_page)
            page_rows = extract_page_rows(
                image,
                source_page,
                expected_first,
                expected_last,
                temporary_dir,
                tesseract_command,
                use_reviewed_corrections,
            )
            rows.extend(page_rows)
            print(
                f"OCR internasional: halaman {source_page}, baris "
                f"{expected_first}-{expected_last}",
                flush=True,
            )
    validate_international_rows(rows, source_sha256=source_sha256)
    numeric_corrections, identity_corrections = reviewed_correction_counts(
        reviewed_pages
    )
    provenance = OcrProvenance(
        engine="tesseract",
        version=version,
        reviewed_numeric_cells=numeric_corrections,
        reviewed_identity_cells=identity_corrections,
    )
    _write_cache(cache, source_sha256, rows, provenance)
    return rows, provenance


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="berkas PDF sumber")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/.extract-cache"),
        help="direktori cache OCR",
    )
    parser.add_argument(
        "--tesseract-command",
        default="tesseract",
        help="nama/path executable Tesseract",
    )
    parser.add_argument(
        "--allow-unverified-source",
        action="store_true",
        help="izinkan SHA-256 sumber yang berbeda (validasi struktur tetap dijalankan)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        input_path = args.input.expanduser().resolve(strict=True)
        source_sha256 = file_sha256(input_path)
        if source_sha256 != REVIEWED_SOURCE_SHA256 and not args.allow_unverified_source:
            raise InternationalExtractionError(
                "SHA-256 PDF tidak cocok; gunakan --allow-unverified-source hanya "
                "untuk sumber alternatif yang sengaja diterima"
            )
        rows, provenance = prepare_international_rates(
            input_path,
            args.cache_dir.expanduser().resolve(),
            source_sha256,
            args.tesseract_command,
        )
        print(
            f"Selesai: {len(rows)} tarif internasional; "
            f"Tesseract {provenance.version}",
            flush=True,
        )
        return 0
    except (InternationalExtractionError, OSError, subprocess.CalledProcessError) as error:
        print(f"Gagal: {error}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
