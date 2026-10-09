"""Tests for the save_record action's parser and CSV writer."""

import csv
from pathlib import Path

import pytest

import save_record as sr

TABLE = """\
| Type | Model | Supplier | Purchased | Warranty | Status | Action | Location | Asset tag |
|------|-------|----------|-----------|----------|--------|--------|----------|-----------|
| Laptop | XPS 13 | Dell | 2022 | 3 years | in use | | | |
| monitor | | | unknown | | Unused | donate | | |
| | | | | | | | | |
"""


def body(table: str = TABLE) -> str:
    """Return an issue body as rendered by GitHub for the record form.

    Parameters
    ----------
    table
        Content of the Hardware field.

    Returns
    -------
    str
        Body with CRLF line endings, as GitHub sends it.

    """
    text = f"### Hardware\n\n{table}\n### Notes\n\n_No response_\n"
    return text.replace("\n", "\r\n")


def read(path: Path) -> list[dict[str, str]]:
    """Read a CSV file into a list of rows.

    Parameters
    ----------
    path
        CSV file.

    Returns
    -------
    list of dict
        One dict per row.

    """
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_record_kind():
    """A body with a Hardware section is a personal record; others aren't."""
    assert sr.record_kind(body()) == "personal"
    assert sr.record_kind("### Something else\r\n\r\ntext") is None


def test_parse_normalises_and_skips_blank_rows():
    """Values are lower-cased where enumerated, blanks become unknown."""
    rows, warnings = sr.parse_record(body())
    assert warnings == []
    assert rows["Hardware"] == [
        {
            "type": "laptop",
            "model": "XPS 13",
            "supplier": "Dell",
            "purchased": "2022",
            "warranty": "3 years",
            "status": "in use",
            "action": "unknown",
            "location": "unknown",
            "asset_tag": "unknown",
        },
        {
            "type": "monitor",
            "model": "unknown",
            "supplier": "unknown",
            "purchased": "unknown",
            "warranty": "unknown",
            "status": "unused",
            "action": "donate",
            "location": "unknown",
            "asset_tag": "unknown",
        },
    ]


def test_columns_matched_by_header():
    """Reordered, missing and extra columns are handled by header name."""
    table = "| Status | Type | Colour |\n|---|---|---|\n| in use | desktop | red |\n"
    rows, warnings = sr.parse_record(body(table))
    row = rows["Hardware"][0]
    assert (row["type"], row["status"], row["model"]) == (
        "desktop",
        "in use",
        "unknown",
    )
    assert warnings == ["Hardware: ignored unrecognised column 'Colour'"]


def test_empty_or_missing_section_has_no_rows():
    """An empty field or a body without the section yields no rows."""
    assert sr.parse_record(body("_No response_"))[0] == {"Hardware": []}
    assert sr.parse_record("### Notes\r\n\r\nhi")[0] == {"Hardware": []}


def test_invalid_rows_report_every_problem():
    """All invalid cells are reported together, with section, row and column."""
    table = (
        "| Type | Purchased | Status |\n|---|---|---|\n"
        "| phone | 22 | in use |\n| laptop | 2020 |\n"
    )
    with pytest.raises(sr.RecordError) as e:
        sr.parse_record(body(table))
    message = str(e.value)
    assert "Hardware, row 1, Type: 'phone' is not one of" in message
    assert "Hardware, row 1, Purchased: '22' is not a year" in message
    assert "Hardware, row 2: has 2 cells, expected 3" in message


def test_save_replaces_only_this_issues_rows(tmp_path):
    """Saving again replaces the issue's rows and keeps other issues' rows."""
    sr.save(body(), 5, tmp_path)
    sr.save(body("| Type |\n|---|\n| tablet |\n"), 3, tmp_path)
    one = "| Type | Status |\n|---|---|\n| laptop | disposed |\n"
    message = sr.save(body(one), 5, tmp_path)
    rows = read(tmp_path / "hardware.csv")
    assert [(r["record"], r["type"], r["status"]) for r in rows] == [
        ("3", "tablet", "unknown"),
        ("5", "laptop", "disposed"),
    ]
    assert "1 item(s)" in message and "7 unknown value(s)" in message


def test_invalid_record_writes_nothing(tmp_path):
    """A record with errors leaves the CSV untouched."""
    sr.save(body(), 5, tmp_path)
    before = (tmp_path / "hardware.csv").read_bytes()
    with pytest.raises(sr.RecordError):
        sr.save(body("| Type |\n|---|\n| phone |\n"), 5, tmp_path)
    assert (tmp_path / "hardware.csv").read_bytes() == before


def test_cli_exit_status(tmp_path):
    """The CLI exits 1 and writes the errors when the record is invalid."""
    src = tmp_path / "body.md"
    msg = tmp_path / "msg.md"
    args = ["save", "--body-file", str(src), "--issue", "1"]
    args += ["--data-dir", str(tmp_path), "--message-file", str(msg)]
    src.write_text(body("| Type |\n|---|\n| phone |\n"), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        sr.main(args)
    assert e.value.code == 1
    assert "phone" in msg.read_text(encoding="utf-8")
    src.write_text(body(), encoding="utf-8")
    sr.main(args)
    assert "2 item(s)" in msg.read_text(encoding="utf-8")
