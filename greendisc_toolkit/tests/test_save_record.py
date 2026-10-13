"""Tests for the save_record action's parser and CSV writer."""

import csv
from pathlib import Path

import pytest

import save_record as sr

TABLE = """\
<!--
Type: laptop, desktop, ...
Status: in use, unused, disposed
-->
| Type | Model | Supplier | Purchased | Warranty | Status | Action | Location | Asset tag |
|------|-------|----------|-----------|----------|--------|--------|----------|-----------|
| Laptop | XPS 13 | Dell | 2022 | 3 years | in use | | | |
| External  Drive | | | unknown | | Unused | donate | | |
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
    """Hint comments are ignored, Type and listed values are lower-cased."""
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
            "notes": "",
        },
        {
            "type": "external drive",
            "model": "unknown",
            "supplier": "unknown",
            "purchased": "unknown",
            "warranty": "unknown",
            "status": "unused",
            "action": "donate",
            "location": "unknown",
            "asset_tag": "unknown",
            "notes": "",
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
    """Empty or missing sections of a record have no rows; non-records none."""
    rows = sr.parse_record(body("_No response_"))[0]
    assert rows == {s: [] for s in sr.RECORD_KINDS["personal"]}
    assert sr.parse_record("### Notes\r\n\r\nhi")[0] == {}


def test_invalid_rows_report_every_problem():
    """All invalid cells are reported together, with section, row and column."""
    table = (
        "| Type | Purchased | Status |\n|---|---|---|\n"
        "| phone | 22 | broken |\n| laptop | 2020 |\n"
    )
    with pytest.raises(sr.RecordError) as e:
        sr.parse_record(body(table))
    message = str(e.value)
    assert "Hardware, row 1, Status: 'broken' is not one of" in message
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
    assert "1 row(s)" in message and "7 unknown value(s)" in message


def test_invalid_record_writes_nothing(tmp_path):
    """A record with errors leaves the CSV untouched."""
    sr.save(body(), 5, tmp_path)
    before = (tmp_path / "hardware.csv").read_bytes()
    with pytest.raises(sr.RecordError):
        sr.save(body("| Status |\n|---|\n| broken |\n"), 5, tmp_path)
    assert (tmp_path / "hardware.csv").read_bytes() == before


def test_cli_exit_status(tmp_path):
    """The CLI exits 1 and writes the errors when the record is invalid."""
    src = tmp_path / "body.md"
    msg = tmp_path / "msg.md"
    args = ["save", "--body-file", str(src), "--issue", "1"]
    args += ["--data-dir", str(tmp_path), "--message-file", str(msg)]
    src.write_text(body("| Status |\n|---|\n| broken |\n"), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        sr.main(args)
    assert e.value.code == 1
    assert "broken" in msg.read_text(encoding="utf-8")
    src.write_text(body(), encoding="utf-8")
    sr.main(args)
    assert "2 row(s)" in msg.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("500GB", "500 GB"),
        ("~3 tb", "~3 TB"),
        ("about 1.5 TB", "~1.5 TB"),
        ("approximately 20 mb", "~20 MB"),
        ("", "unknown"),
    ],
)
def test_size_normalised(value, expected):
    """Sizes accept an approximate marker and any case, and are tidied."""
    assert sr.validate(value, sr.SIZE, 2030) == expected


@pytest.mark.parametrize("value", ["3", "big", "3 TiB", "~ TB"])
def test_size_needs_number_and_unit(value):
    """A size without a number and a unit is rejected."""
    with pytest.raises(ValueError, match="is not a size"):
        sr.validate(value, sr.SIZE, 2030)


@pytest.mark.parametrize("value", ["2026-02-30", "13/10/2026", "20261013"])
def test_date_must_be_iso(value):
    """Dates must be real YYYY-MM-DD dates."""
    with pytest.raises(ValueError, match="is not a date"):
        sr.validate(value, sr.DATE, 2030)
    assert sr.validate("2026-10-13", sr.DATE, 2030) == "2026-10-13"


def test_sections_saved_to_own_files(tmp_path):
    """Each section of a record goes to its own CSV file."""
    text = (
        "### Hardware\n\n| Type |\n|---|\n| laptop |\n\n"
        "### Data resources\n\n| Name | Size |\n|---|---|\n| scans | ~2 tb |\n\n"
        "### Data cleaning\n\n| Date | Freed |\n|---|---|\n| 2026-09-01 | 40GB |\n\n"
        "### Pipelines\n\n| Name | Runs on |\n|---|---|\n| sort | HPC |\n"
    )
    message = sr.save(text, 7, tmp_path)
    assert read(tmp_path / "data-resources.csv")[0]["size"] == "~2 TB"
    assert read(tmp_path / "data-cleaning.csv")[0]["freed"] == "40 GB"
    assert read(tmp_path / "pipelines.csv")[0]["runs_on"] == "hpc"
    assert message.count("1 row(s)") == 4


def test_kind_from_unique_heading(monkeypatch):
    """A heading shared by two kinds doesn't identify a record on its own."""
    kinds = {**sr.RECORD_KINDS, "group": ["Compute", "Data cleaning"]}
    monkeypatch.setattr(sr, "RECORD_KINDS", kinds)
    assert sr.record_kind("### Data cleaning\r\n\r\n| Date |") is None
    assert sr.record_kind("### Compute\r\n\r\n### Data cleaning\r\n") == "group"
    assert sr.record_kind("### Pipelines\r\n\r\n### Data cleaning\r\n") == "personal"


def test_blank_notes_stay_blank(tmp_path):
    """Notes are optional: a blank note is not counted as unknown."""
    sr.save(body("| Type | Notes |\n|---|---|\n| laptop | |\n"), 1, tmp_path)
    assert read(tmp_path / "hardware.csv")[0]["notes"] == ""
