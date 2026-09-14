from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from scripts.check_dist import (
    EXPECTED_INTERNATIONAL_TARIFF_SERVICES,
    EXPECTED_TARIFF_SERVICES,
    DocumentParser,
    target_file,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CHECK_DIST = REPOSITORY_ROOT / "scripts" / "check_dist.py"


def rate_table(
    row_id: str,
    *,
    rows: int = 1,
    services: tuple[str, ...] | None = None,
    cells_per_row: int = 10,
) -> str:
    service_names = services or tuple(sorted(EXPECTED_TARIFF_SERVICES))
    headers = "".join(
        f'<th scope="col" data-service="{service}">{service}</th>'
        for service in service_names
    )
    body = "".join(
        f'<tr id="{row_id}-{index}"><th scope="row">A</th>'
        + "<td>Rp1.000</td>" * cells_per_row
        + "</tr>"
        for index in range(rows)
    )
    return (
        '<table data-rate-table><thead><tr><th scope="col">Wilayah</th>'
        f"{headers}</tr></thead><tbody>{body}</tbody></table>"
    )


def international_rate_table(*, rows: int = 1, cells_per_row: int = 13) -> str:
    headers = "".join(
        f'<th scope="col" data-service="{service}">{service}</th>'
        for service in sorted(EXPECTED_INTERNATIONAL_TARIFF_SERVICES)
    )
    body = "".join(
        f'<tr id="country-{index}"><th scope="row">A</th>'
        + "<td>Rp1.000</td>" * cells_per_row
        + "</tr>"
        for index in range(rows)
    )
    return (
        '<table data-rate-table><thead><tr><th scope="col">Negara</th>'
        f"{headers}</tr></thead><tbody>{body}</tbody></table>"
    )


def write_fixture(dist: Path, *, from_table: str, to_table: str) -> None:
    documents = {
        "index.html": '<a href="/postindo/dari/a-1/">Dari</a>',
        "dari/index.html": '<a href="/postindo/dari/a-1/">A</a>',
        "ke/index.html": '<a href="/postindo/ke/a-1/">A</a>',
        "internasional/index.html": international_rate_table(),
        "tentang/index.html": '<a href="https://example.test/source">Sumber</a>',
        "dari/a-1/index.html": (
            f'{from_table}<div id="ke-1"></div>'
            '<a href="../../ke/a-1/#dari-1">Ke</a>'
        ),
        "ke/a-1/index.html": (
            f'{to_table}<div id="dari-1"></div>'
            '<a href="../../dari/a-1/#ke-1">Dari</a>'
        ),
    }
    for relative, body in documents.items():
        path = dist / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"<!doctype html><html><body>{body}</body></html>\n", encoding="utf-8"
        )


class StaticOutputCheckTests(unittest.TestCase):
    def test_resolves_project_pages_link_and_fragment(self) -> None:
        target, fragment, reason = target_file(
            PurePosixPath("dari/jakarta-10000/index.html"),
            "/postindo/ke/bandung-40000/#dari-10000",
            "/postindo",
        )

        self.assertEqual(target, PurePosixPath("ke/bandung-40000/index.html"))
        self.assertEqual(fragment, "dari-10000")
        self.assertIsNone(reason)

    def test_rejects_absolute_link_outside_base(self) -> None:
        target, _, reason = target_file(
            PurePosixPath("index.html"), "/situs-lain/", "/postindo"
        )

        self.assertIsNone(target)
        self.assertIn("di luar base", reason or "")

    def test_resolves_city_page_relative_links_from_directory_url(self) -> None:
        source = PurePosixPath("dari/jakarta-10000/index.html")

        opposite, fragment, reason = target_file(
            source, "../../ke/bandung-40000/#dari-10000", "/postindo"
        )
        reverse, reverse_fragment, reverse_reason = target_file(
            source, "../bandung-40000/#ke-10000", "/postindo"
        )

        self.assertEqual(opposite, PurePosixPath("ke/bandung-40000/index.html"))
        self.assertEqual(fragment, "dari-10000")
        self.assertIsNone(reason)
        self.assertEqual(reverse, PurePosixPath("dari/bandung-40000/index.html"))
        self.assertEqual(reverse_fragment, "ke-10000")
        self.assertIsNone(reverse_reason)

    def test_streaming_parser_counts_semantic_rate_shape(self) -> None:
        parser = DocumentParser()
        parser.feed(rate_table("ke-1", rows=2))
        parser.close()

        self.assertEqual(parser.rate_table_count, 1)
        self.assertEqual(parser.rate_route_rows, 2)
        self.assertEqual(set(parser.rate_service_headers), EXPECTED_TARIFF_SERVICES)
        self.assertEqual(parser.invalid_rate_rows, [])

    def test_streaming_parser_counts_international_rate_shape(self) -> None:
        parser = DocumentParser(EXPECTED_INTERNATIONAL_TARIFF_SERVICES)
        parser.feed(international_rate_table(rows=2))
        parser.close()

        self.assertEqual(parser.rate_table_count, 1)
        self.assertEqual(parser.rate_route_rows, 2)
        self.assertEqual(
            set(parser.rate_service_headers),
            EXPECTED_INTERNATIONAL_TARIFF_SERVICES,
        )
        self.assertEqual(parser.invalid_rate_rows, [])

    def test_accepts_small_complete_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary)
            write_fixture(
                dist,
                from_table=rate_table("ke"),
                to_table=rate_table("dari"),
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(CHECK_DIST),
                    "--dist",
                    str(dist),
                    "--base",
                    "/postindo",
                    "--expected-locations",
                    "1",
                    "--expected-international-rates",
                    "1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Build valid", result.stdout)

    def test_rejects_wrong_route_count_and_tariff_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary)
            nine_services = tuple(sorted(EXPECTED_TARIFF_SERVICES))[:-1]
            write_fixture(
                dist,
                from_table=rate_table(
                    "ke", rows=2, services=nine_services, cells_per_row=9
                ),
                to_table=rate_table("dari"),
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(CHECK_DIST),
                    "--dist",
                    str(dist),
                    "--base",
                    "/postindo",
                    "--expected-locations",
                    "1",
                    "--expected-international-rates",
                    "1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("memuat 2 baris rute; seharusnya 1", result.stderr)
        self.assertIn("9 kolom tarif; seharusnya 10", result.stderr)
        self.assertIn("9 sel tarif", result.stderr)

    def test_rejects_leaked_international_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary)
            write_fixture(
                dist,
                from_table=rate_table("ke"),
                to_table=rate_table("dari"),
            )
            (dist / "international_rates.csv").write_text(
                "source_row,source_name\n1,Afghanistan\n", encoding="utf-8"
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(CHECK_DIST),
                    "--dist",
                    str(dist),
                    "--base",
                    "/postindo",
                    "--expected-locations",
                    "1",
                    "--expected-international-rates",
                    "1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("international_rates.csv", result.stderr)


if __name__ == "__main__":
    unittest.main()
