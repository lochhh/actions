# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Save a Green DiSC record issue to CSV files.

A record issue holds one Markdown table per section (e.g. ``### Hardware``).
Tables are read by column header, validated, and written to
``<data-dir>/<section>.csv``, replacing the rows previously saved from the same
issue. Validation is all-or-nothing: if any row is invalid, nothing is written.
"""

import argparse
import csv
import datetime
import re
import sys
from pathlib import Path

UNKNOWN = "unknown"
YEAR = "year"

# Section heading -> CSV file and columns. A column is either free text (None),
# a list of allowed values, or YEAR. "unknown" is always allowed, and blank
# cells are stored as "unknown".
SECTIONS = {
    "Hardware": {
        "file": "hardware.csv",
        "columns": {
            "Type": [
                "laptop",
                "desktop",
                "monitor",
                "tablet",
                "external drive",
                "docking station",
                "other",
            ],
            "Model": None,
            "Supplier": None,
            "Purchased": YEAR,
            "Warranty": None,
            "Status": ["in use", "unused", "disposed"],
            "Action": None,
            "Location": None,
            "Asset tag": None,
        },
    },
}
RECORD_KINDS = {"personal": ["Hardware"]}


class RecordError(Exception):
    """Raised when a record issue's tables are invalid."""


def column_key(header: str) -> str:
    """Return the CSV column name for a table header.

    Parameters
    ----------
    header
        Column header as written in the issue, e.g. ``"Asset tag"``.

    Returns
    -------
    str
        Lower-case name with underscores, e.g. ``"asset_tag"``.

    """
    return re.sub(r"\W+", "_", header.strip().lower()).strip("_")


def split_sections(body: str) -> dict[str, str]:
    """Split an issue form body into its ``### `` sections.

    Parameters
    ----------
    body
        Issue body as rendered by a GitHub issue form.

    Returns
    -------
    dict of str to str
        Section heading to section text.

    """
    parts = re.split(r"^### +(.+?)\s*$", body, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def record_kind(body: str) -> str | None:
    """Identify which kind of record an issue is from its section headings.

    Parameters
    ----------
    body
        Issue body.

    Returns
    -------
    str or None
        A key of `RECORD_KINDS`, or None if the issue is not a record.

    """
    headings = split_sections(body)
    for kind, sections in RECORD_KINDS.items():
        if any(s in headings for s in sections):
            return kind
    return None


def parse_table(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse a Markdown table.

    Parameters
    ----------
    text
        Section text containing a pipe table; other lines are ignored.

    Returns
    -------
    headers : list of str
        Column headers.
    rows : list of list of str
        Cell values, stripped, excluding the separator line.

    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
    cells = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines]
    cells = [r for r in cells if not all(re.fullmatch(r":?-+:?", c) for c in r)]
    if not cells:
        return [], []
    return cells[0], cells[1:]


def validate(value: str, spec: list[str] | str | None, year_max: int) -> str:
    """Normalise one cell and check it against its column spec.

    Parameters
    ----------
    value
        Cell text.
    spec
        Column spec: None (free text), a list of allowed values, or `YEAR`.
    year_max
        Latest acceptable year.

    Returns
    -------
    str
        The normalised value; blank becomes ``"unknown"``.

    Raises
    ------
    ValueError
        If the value is not allowed.

    """
    value = " ".join(value.split())
    if not value or value.lower() == UNKNOWN:
        return UNKNOWN
    if isinstance(spec, list):
        if value.lower() not in spec:
            raise ValueError(f"'{value}' is not one of: {', '.join(spec)}, unknown")
        return value.lower()
    if spec == YEAR and not (
        value.isdigit() and len(value) == 4 and 1980 <= int(value) <= year_max
    ):
        raise ValueError(f"'{value}' is not a year (e.g. 2023)")
    return value


def parse_record(body: str) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    """Parse and validate every known section of a record issue.

    Parameters
    ----------
    body
        Issue body.

    Returns
    -------
    rows : dict of str to list of dict
        Section heading to validated rows (CSV column name to value). Blank
        rows are skipped; sections that are missing or empty have no rows.
    warnings : list of str
        Non-fatal problems, such as unrecognised columns.

    Raises
    ------
    RecordError
        If any row is invalid; the message lists every problem found.

    """
    year_max = datetime.datetime.now(datetime.UTC).year + 1
    sections = split_sections(body)
    result, warnings, errors = {}, [], []
    for name, section in SECTIONS.items():
        columns = section["columns"]
        headers, rows = parse_table(sections.get(name, ""))
        known = {h.lower(): h for h in columns}
        for h in headers:
            if h.lower() not in known:
                warnings.append(f"{name}: ignored unrecognised column '{h}'")
        result[name] = []
        for n, cells in enumerate(rows, start=1):
            if not any(cells):
                continue
            if len(cells) != len(headers):
                errors.append(
                    f"{name}, row {n}: has {len(cells)} cells, expected {len(headers)}"
                )
                continue
            given = {h.lower(): c for h, c in zip(headers, cells, strict=True)}
            row = {}
            for col, spec in columns.items():
                try:
                    row[column_key(col)] = validate(
                        given.get(col.lower(), ""), spec, year_max
                    )
                except ValueError as e:
                    errors.append(f"{name}, row {n}, {col}: {e}")
            result[name].append(row)
    if errors:
        raise RecordError("\n".join(f"- {e}" for e in errors))
    return result, warnings


def write_csv(path: Path, issue: int, columns: list[str], rows: list[dict]) -> None:
    """Replace one issue's rows in a CSV file, keeping all other rows.

    Parameters
    ----------
    path
        CSV file; created if missing.
    issue
        Issue number, stored in the ``record`` column.
    columns
        CSV column names after ``record``. Columns missing from existing rows
        are filled with ``"unknown"``.
    rows
        New rows for this issue.

    """
    header = ["record", *columns]
    kept = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            kept = [r for r in csv.DictReader(f) if r["record"] != str(issue)]
    new = [{"record": str(issue), **r} for r in rows]
    all_rows = sorted(kept + new, key=lambda r: int(r["record"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, header, restval=UNKNOWN, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)


def save(body: str, issue: int, data_dir: Path) -> str:
    """Validate a record issue and write its rows to the CSV files.

    Parameters
    ----------
    body
        Issue body.
    issue
        Issue number.
    data_dir
        Directory holding the CSV files.

    Returns
    -------
    str
        Markdown summary for the reply comment.

    Raises
    ------
    RecordError
        If any row is invalid; nothing is written.

    """
    result, warnings = parse_record(body)
    lines = []
    for name, rows in result.items():
        section = SECTIONS[name]
        columns = [column_key(c) for c in section["columns"]]
        write_csv(data_dir / section["file"], issue, columns, rows)
        unknown = sum(v == UNKNOWN for r in rows for v in r.values())
        lines.append(
            f"- **{name}**: {len(rows)} item(s) in `{data_dir.as_posix()}/"
            f"{section['file']}`" + (f", {unknown} unknown value(s)" if unknown else "")
        )
    lines += [f"- {w}" for w in warnings]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    """Run the command-line interface.

    ``kind`` prints the record kind (or nothing). ``save`` writes the CSV
    files and the reply message; it exits with status 1 and writes the errors
    to the message file if the record is invalid.

    Parameters
    ----------
    argv
        Command-line arguments; defaults to ``sys.argv[1:]``.

    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["kind", "save"])
    p.add_argument("--body-file", type=Path, required=True)
    p.add_argument("--issue", type=int)
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument("--message-file", type=Path)
    args = p.parse_args(argv)
    body = args.body_file.read_text(encoding="utf-8")
    if args.command == "kind":
        print(record_kind(body) or "")
        return
    try:
        message = save(body, args.issue, args.data_dir)
    except RecordError as e:
        args.message_file.write_text(str(e), encoding="utf-8")
        sys.exit(1)
    args.message_file.write_text(message, encoding="utf-8")


if __name__ == "__main__":
    main()
