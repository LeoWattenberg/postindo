#!/usr/bin/env python3
"""Periksa keluaran statis Astro, tautan internal, dan anggaran ukuran."""

from __future__ import annotations

import argparse
import gzip
import os
import re
import sys
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


RESOURCE_LINK_RELS = {
    "dns-prefetch",
    "icon",
    "manifest",
    "modulepreload",
    "preconnect",
    "prefetch",
    "preload",
    "stylesheet",
}
REMOTE_URL_RE = re.compile(r"(?:https?:)?//[^\s'\"()<>]+", re.IGNORECASE)
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
EXPECTED_TARIFF_SERVICES = {
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
}
EXPECTED_INTERNATIONAL_TARIFF_SERVICES = {
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
}


class DocumentParser(HTMLParser):
    """Kumpulkan tautan serta bentuk tabel tarif tanpa membangun DOM."""

    def __init__(self, expected_services: set[str] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.expected_services = expected_services or EXPECTED_TARIFF_SERVICES
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.resources: list[str] = []
        self.rate_table_count = 0
        self.rate_route_rows = 0
        self.rate_service_headers: list[str] = []
        self.invalid_rate_rows: list[tuple[int, int]] = []
        self._rate_table_level = 0
        self._rate_thead_depth = 0
        self._rate_tbody_depth = 0
        self._rate_row_cells: int | None = None

    def _finish_rate_row(self) -> None:
        if self._rate_row_cells is None:
            return
        if self._rate_row_cells != len(self.expected_services):
            # A few examples are enough for a useful diagnostic while keeping
            # malformed, very large pages bounded in memory.
            if len(self.invalid_rate_rows) < 5:
                self.invalid_rate_rows.append(
                    (self.rate_route_rows, self._rate_row_cells)
                )
        self._rate_row_cells = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_names = {name.lower() for name, _ in attrs}
        values = {name.lower(): value for name, value in attrs if value is not None}
        element_id = values.get("id")
        if element_id:
            self.ids.add(element_id)

        if tag == "table":
            if self._rate_table_level:
                self._rate_table_level += 1
            elif "data-rate-table" in attr_names:
                self.rate_table_count += 1
                self._rate_table_level = 1
        elif self._rate_table_level == 1:
            if tag == "thead":
                self._rate_thead_depth += 1
            elif tag == "tbody":
                self._rate_tbody_depth += 1
            elif tag == "tr" and self._rate_tbody_depth:
                self._finish_rate_row()
                self.rate_route_rows += 1
                self._rate_row_cells = 0
            elif tag == "td" and self._rate_row_cells is not None:
                self._rate_row_cells += 1
            elif tag == "th" and self._rate_thead_depth and "data-service" in attr_names:
                self.rate_service_headers.append(values.get("data-service", ""))

        href = values.get("href")
        if href:
            rels = set(values.get("rel", "").lower().split())
            if tag == "a":
                self.links.append(href)
            elif tag == "link" and rels & RESOURCE_LINK_RELS:
                self.resources.append(href)

        src = values.get("src")
        if src:
            self.resources.append(src)

        srcset = values.get("srcset")
        if srcset:
            for candidate in srcset.split(","):
                url = candidate.strip().split(maxsplit=1)[0]
                if url:
                    self.resources.append(url)

        style = values.get("style", "")
        self.resources.extend(match.group(2) for match in CSS_URL_RE.finditer(style))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self._rate_table_level:
            return
        if tag == "table":
            if self._rate_table_level == 1:
                self._finish_rate_row()
                self._rate_thead_depth = 0
                self._rate_tbody_depth = 0
            self._rate_table_level -= 1
        elif self._rate_table_level == 1:
            if tag == "tr" and self._rate_row_cells is not None:
                self._finish_rate_row()
            elif tag == "thead" and self._rate_thead_depth:
                self._rate_thead_depth -= 1
            elif tag == "tbody" and self._rate_tbody_depth:
                self._rate_tbody_depth -= 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"), help="Direktori hasil build")
    parser.add_argument(
        "--base",
        default=os.environ.get("BASE_PATH", "/"),
        help="Base path publik (default: BASE_PATH atau /)",
    )
    parser.add_argument("--expected-locations", type=int, default=603)
    parser.add_argument("--expected-international-rates", type=int, default=236)
    parser.add_argument("--max-total-mib", type=float, default=300.0)
    parser.add_argument("--max-page-gzip-kib", type=float, default=50.0)
    parser.add_argument("--max-css-gzip-kib", type=float, default=10.0)
    parser.add_argument("--max-js-gzip-kib", type=float, default=5.0)
    return parser.parse_args()


def normalise_base(value: str) -> str:
    value = value.strip()
    if not value or value == "/":
        return "/"
    return f"/{value.strip('/')}"


def gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9, mtime=0))


def public_path_for_file(relative: PurePosixPath) -> str:
    if relative.name == "index.html":
        parent = relative.parent.as_posix()
        return "/" if parent == "." else f"/{parent}/"
    return f"/{relative.as_posix()}"


def target_file(
    source: PurePosixPath,
    reference: str,
    base: str,
) -> tuple[PurePosixPath | None, str | None, str | None]:
    """Ubah URL internal menjadi (berkas, fragmen, kesalahan)."""

    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or reference.startswith("//"):
        return None, None, None
    if parsed.scheme in {"data", "mailto", "tel"}:
        return None, None, None

    raw_path = unquote(parsed.path)
    fragment = unquote(parsed.fragment) or None
    source_public = public_path_for_file(source)
    source_public_path = PurePosixPath(source_public)
    # An index file represents a directory URL. PurePosixPath discards its
    # trailing slash, so taking .parent here would incorrectly move one level
    # too high before resolving links such as ../foo/ and ../../ke/foo/.
    source_dir = (
        source_public_path if source_public.endswith("/") else source_public_path.parent
    )

    if raw_path.startswith("/"):
        if base != "/":
            if raw_path == base:
                raw_path = "/"
            elif raw_path.startswith(f"{base}/"):
                raw_path = raw_path[len(base) :]
            else:
                return None, fragment, f"path absolut berada di luar base {base!r}"
        public = PurePosixPath(raw_path)
    elif raw_path:
        public = source_dir / raw_path
    else:
        public = PurePosixPath(source_public)

    parts: list[str] = []
    for part in public.parts:
        if part in {"", "/", "."}:
            continue
        if part == "..":
            if not parts:
                return None, fragment, "path keluar dari akar situs"
            parts.pop()
        else:
            parts.append(part)

    clean = PurePosixPath(*parts) if parts else PurePosixPath(".")
    ends_as_directory = not raw_path or raw_path.endswith("/")
    if clean == PurePosixPath("."):
        resolved = PurePosixPath("index.html")
    elif ends_as_directory:
        resolved = clean / "index.html"
    else:
        resolved = clean
    return resolved, fragment, None


def format_size(value: int) -> str:
    return f"{value / 1024:.1f} KiB"


def append_rate_table_errors(
    errors: list[str],
    relative: PurePosixPath,
    parser: DocumentParser,
    expected_rows: int,
    expected_services: set[str],
    row_label: str,
) -> None:
    if parser.rate_table_count != 1:
        errors.append(
            f"{relative}: memuat {parser.rate_table_count} tabel tarif; seharusnya 1"
        )
    if parser.rate_route_rows != expected_rows:
        errors.append(
            f"{relative}: memuat {parser.rate_route_rows} {row_label}; "
            f"seharusnya {expected_rows}"
        )

    actual_services = set(parser.rate_service_headers)
    if (
        len(parser.rate_service_headers) != len(expected_services)
        or actual_services != expected_services
    ):
        missing = sorted(expected_services - actual_services)
        unexpected = sorted(actual_services - expected_services)
        details = [
            f"{len(parser.rate_service_headers)} kolom tarif; seharusnya "
            f"{len(expected_services)}"
        ]
        if missing:
            details.append("hilang: " + ", ".join(missing))
        if unexpected:
            details.append("tidak dikenal: " + ", ".join(unexpected))
        errors.append(f"{relative}: " + "; ".join(details))

    if parser.invalid_rate_rows:
        examples = ", ".join(
            f"baris {row} ({cells} sel tarif)"
            for row, cells in parser.invalid_rate_rows
        )
        errors.append(
            f"{relative}: setiap {row_label} harus memuat "
            f"{len(expected_services)} sel tarif; {examples}"
        )


def main() -> int:
    args = parse_args()
    dist = args.dist.resolve()
    base = normalise_base(args.base)
    errors: list[str] = []

    if not dist.is_dir():
        print(f"GALAT: direktori build tidak ditemukan: {dist}", file=sys.stderr)
        return 1

    symlinks = [path for path in dist.rglob("*") if path.is_symlink()]
    if symlinks:
        errors.append(f"keluaran memuat symlink: {symlinks[0].relative_to(dist)}")

    files = [path for path in dist.rglob("*") if path.is_file() and not path.is_symlink()]
    relative_files = {PurePosixPath(path.relative_to(dist).as_posix()) for path in files}
    total_bytes = sum(path.stat().st_size for path in files)
    total_limit = int(args.max_total_mib * 1024 * 1024)
    if total_bytes > total_limit:
        errors.append(
            f"ukuran dist {total_bytes / 1024 / 1024:.1f} MiB melebihi "
            f"{args.max_total_mib:g} MiB"
        )

    forbidden_names = {
        "postindo.sqlite",
        "locations.csv",
        "rates.csv",
        "international_rates.csv",
    }
    leaked = sorted(path for path in relative_files if path.name in forbidden_names)
    if leaked:
        errors.append("artefak data ikut dipublikasikan: " + ", ".join(map(str, leaked)))

    required = {
        PurePosixPath("index.html"),
        PurePosixPath("dari/index.html"),
        PurePosixPath("ke/index.html"),
        PurePosixPath("internasional/index.html"),
        PurePosixPath("tentang/index.html"),
    }
    missing_required = sorted(required - relative_files)
    if missing_required:
        errors.append("route pokok hilang: " + ", ".join(map(str, missing_required)))

    html_files = sorted(path for path in files if path.suffix.lower() == ".html")
    city_pages: list[Path] = []
    for direction in ("dari", "ke"):
        pages = [
            path
            for path in html_files
            if path.relative_to(dist).parts[0:1] == (direction,)
            and len(path.relative_to(dist).parts) == 3
            and path.name == "index.html"
        ]
        city_pages.extend(pages)
        if len(pages) != args.expected_locations:
            errors.append(
                f"jumlah halaman /{direction}/ adalah {len(pages)}, "
                f"seharusnya {args.expected_locations}"
            )

    if city_pages:
        page_sizes = [(gzip_size(path), path) for path in city_pages]
        worst_size, worst_path = max(page_sizes)
        page_limit = int(args.max_page_gzip_kib * 1024)
        if worst_size > page_limit:
            errors.append(
                f"halaman terbesar {worst_path.relative_to(dist)} ({format_size(worst_size)}) "
                f"melebihi {args.max_page_gzip_kib:g} KiB gzip"
            )
    else:
        worst_size, worst_path = 0, None

    css_files = [path for path in files if path.suffix.lower() == ".css"]
    js_files = [path for path in files if path.suffix.lower() in {".js", ".mjs"}]
    css_gzip = sum(gzip_size(path) for path in css_files)
    js_gzip = sum(gzip_size(path) for path in js_files)
    if css_gzip > int(args.max_css_gzip_kib * 1024):
        errors.append(
            f"total CSS {format_size(css_gzip)} melebihi {args.max_css_gzip_kib:g} KiB gzip"
        )
    if js_gzip > int(args.max_js_gzip_kib * 1024):
        errors.append(
            f"total JavaScript {format_size(js_gzip)} melebihi {args.max_js_gzip_kib:g} KiB gzip"
        )

    ids_by_file: dict[PurePosixPath, set[str]] = {}
    pending_fragments: dict[PurePosixPath, set[str]] = defaultdict(set)
    broken_links: list[str] = []
    remote_resources: list[str] = []
    city_page_set = set(city_pages)
    international_relative = PurePosixPath("internasional/index.html")

    for path in html_files:
        relative = PurePosixPath(path.relative_to(dist).as_posix())
        expected_services = (
            EXPECTED_INTERNATIONAL_TARIFF_SERVICES
            if relative == international_relative
            else EXPECTED_TARIFF_SERVICES
        )
        parser = DocumentParser(expected_services)
        try:
            parser.feed(path.read_text(encoding="utf-8"))
            parser.close()
        except (UnicodeDecodeError, ValueError) as exc:
            errors.append(f"HTML tidak valid UTF-8 ({relative}): {exc}")
            continue
        ids_by_file[relative] = parser.ids

        if path in city_page_set:
            append_rate_table_errors(
                errors,
                relative,
                parser,
                args.expected_locations,
                EXPECTED_TARIFF_SERVICES,
                "baris rute",
            )
        elif relative == international_relative:
            append_rate_table_errors(
                errors,
                relative,
                parser,
                args.expected_international_rates,
                EXPECTED_INTERNATIONAL_TARIFF_SERVICES,
                "baris tujuan internasional",
            )

        for resource in parser.resources:
            parsed = urlsplit(resource)
            if parsed.scheme in {"http", "https"} or parsed.netloc or resource.startswith("//"):
                remote_resources.append(f"{relative}: {resource}")
                continue
            target, _, reason = target_file(relative, resource, base)
            if reason or (target is not None and target not in relative_files):
                broken_links.append(f"{relative}: {resource} ({reason or 'berkas tidak ada'})")

        for link in parser.links:
            target, fragment, reason = target_file(relative, link, base)
            if target is None and reason is None:
                continue
            if reason:
                broken_links.append(f"{relative}: {link} ({reason})")
                continue
            assert target is not None
            if target not in relative_files:
                broken_links.append(f"{relative}: {link} (berkas tidak ada: {target})")
            elif fragment:
                pending_fragments[target].add(fragment)

    for target, fragments in pending_fragments.items():
        target_ids = ids_by_file.get(target, set())
        for fragment in sorted(fragments - target_ids):
            broken_links.append(f"{target}: fragmen #{fragment} tidak ada")

    for path in css_files:
        relative = path.relative_to(dist)
        try:
            css = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"CSS tidak valid UTF-8 ({relative}): {exc}")
            continue
        for match in CSS_URL_RE.finditer(css):
            url = match.group(2)
            if REMOTE_URL_RE.match(url):
                remote_resources.append(f"{relative}: {url}")

    for path in js_files:
        relative = path.relative_to(dist)
        try:
            script = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"JavaScript tidak valid UTF-8 ({relative}): {exc}")
            continue
        for url in REMOTE_URL_RE.findall(script):
            remote_resources.append(f"{relative}: {url}")

    if remote_resources:
        errors.append(
            "sumber daya pihak ketiga ditemukan: " + "; ".join(remote_resources[:10])
        )
    if broken_links:
        errors.append("tautan internal rusak: " + "; ".join(broken_links[:20]))

    summary = (
        f"{len(html_files)} HTML; {len(files)} berkas; "
        f"{total_bytes / 1024 / 1024:.1f} MiB; "
        f"CSS {format_size(css_gzip)} gzip; JavaScript {format_size(js_gzip)} gzip"
    )
    if worst_path is not None:
        summary += (
            f"; halaman kota terbesar {worst_path.relative_to(dist)} "
            f"{format_size(worst_size)} gzip"
        )

    if errors:
        print("Pemeriksaan build gagal:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        print(f"Ringkasan: {summary}", file=sys.stderr)
        return 1

    print(f"Build valid: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
