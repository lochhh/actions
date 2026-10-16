# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Start a Green DiSC review cycle.

Creates the cycle's milestone, one issue per criterion (linked to the previous
cycle's issue for the same criterion) and one check-in per record owner, then
closes the previous cycle. Safe to re-run: anything that already exists in the
cycle's milestone is left alone.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CYCLE_MARKER = "<!-- greendisc:review-cycle -->"
CHECK_IN_LABEL = "check-in"


def record_marker(record: int) -> str:
    """Return the hidden marker that ties a check-in line to a record.

    Parameters
    ----------
    record
        Record issue number.

    Returns
    -------
    str
        HTML comment, e.g. ``<!-- record:8 -->``.

    """
    return f"<!-- record:{record} -->"


def parse_due(due: str) -> datetime.date:
    """Parse the review date.

    Parameters
    ----------
    due
        Date as ``YYYY-MM-DD``.

    Returns
    -------
    datetime.date
        The date.

    Raises
    ------
    ValueError
        If `due` is not a real ``YYYY-MM-DD`` date.

    """
    due = due.strip()
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            raise ValueError
        return datetime.date.fromisoformat(due)
    except ValueError:
        raise ValueError(
            f"'{due}' is not a date as YYYY-MM-DD, e.g. 2026-12-10"
        ) from None


def cycle_name(due: datetime.date, name: str = "") -> str:
    """Return the milestone name for a cycle.

    Parameters
    ----------
    due
        Review date.
    name
        Name given by the user; blank for the default.

    Returns
    -------
    str
        `name`, or ``Review <year>-<month>`` of the due date.

    """
    return name.strip() or f"Review {due:%Y-%m}"


def representative(config: dict) -> dict[str, str]:
    """Return the representative's details from the config.

    Parameters
    ----------
    config
        Parsed ``config.yml``.

    Returns
    -------
    dict of str to str
        ``name``, ``github`` (without ``@``) and ``contact``.

    Raises
    ------
    ValueError
        If any of the three is missing.

    """
    given = config.get("representative") or {}
    rep = {k: str(given.get(k) or "").strip() for k in ("name", "github", "contact")}
    rep["github"] = rep["github"].lstrip("@")
    missing = [k for k, v in rep.items() if not v]
    if missing:
        raise ValueError(
            "Set representative "
            + ", ".join(missing)
            + " in config.yml before starting a review."
        )
    return rep


def criteria_for(criteria: dict, tiers: list[str]) -> list[dict]:
    """Return the criteria of the chosen tiers, with their tier's details.

    Parameters
    ----------
    criteria
        Parsed ``criteria.yml``.
    tiers
        Tier names to review, e.g. ``["Bronze"]``.

    Returns
    -------
    list of dict
        One dict per criterion, adding ``tier`` and ``file`` to its fields.

    Raises
    ------
    ValueError
        If a tier is not in `criteria`.

    """
    by_name = {t["name"]: t for t in criteria.get("tiers", [])}
    unknown = [t for t in tiers if t not in by_name]
    if unknown:
        raise ValueError(f"Tier(s) not in criteria.yml: {', '.join(unknown)}")
    return [
        {**c, "tier": t, "file": by_name[t].get("file", "")}
        for t in tiers
        for c in by_name[t].get("criteria") or []
    ]


def criterion_body(
    criterion: dict, cycle: str, due: datetime.date, source: str, previous: int | None
) -> str:
    """Return the body of a criterion's review issue.

    Parameters
    ----------
    criterion
        Criterion with ``tier`` and ``file`` added (see `criteria_for`).
    cycle
        Cycle name.
    due
        Review date.
    source
        Upstream source URL and commit, as ``<url>/blob/<commit>``.
    previous
        Issue number of the previous cycle's issue, if any.

    Returns
    -------
    str
        Markdown body.

    """
    upstream = f"{source}/{criterion['file']}".replace(" ", "%20")
    lines = [
        (
            f"**{criterion['id']} {criterion['title']}** "
            f"({criterion['tier']}, {criterion['category']})"
        ),
        "",
        (
            f"For **{cycle}**, due {due:%Y-%m-%d}. Read the criterion in the "
            f"[Green DiSC criteria]({upstream}), check that its evidence is "
            "complete and up to date, and close this issue when it is. The "
            "README's *Where the evidence lives* table says where to look."
        ),
    ]
    if previous:
        lines += ["", "Previous review:", f"- [ ] #{previous}"]
    return "\n".join(lines) + "\n"


def check_in_body(
    cycle: str,
    due: datetime.date,
    rep: dict[str, str],
    record: int | None,
    group: int | None,
    is_rep: bool,
) -> str:
    """Return the body of a member's check-in.

    Lines for records the bot tracks end with a hidden record marker, so a
    successful ``/save`` of that record can tick them.

    Parameters
    ----------
    cycle
        Cycle name.
    due
        Review date.
    rep
        Representative's details (see `representative`).
    record
        The member's personal record, if they have one.
    group
        The group record, if there is one.
    is_rep
        Whether this check-in is the representative's.

    Returns
    -------
    str
        Markdown body.

    """
    lines = [
        (
            f"**{cycle}, due {due:%Y-%m-%d}.** Please go over these and make "
            "sure everything is up to date:"
        ),
        "",
    ]
    if record:
        lines.append(
            f"- [ ] Your record #{record}: check every table is complete and up "
            f"to date, then comment `/save` on it. {record_marker(record)}"
        )
    if group:
        lines.append(
            f"- [ ] The group record #{group}: add anything the group did since "
            "the last review (training, shared material, promotion, group "
            f"cleanups). Your Green DiSC representative, {rep['name']} "
            f"({rep['contact']}), checks and saves it."
        )
        if is_rep:
            lines.append(
                f"- [ ] Once members have added their rows, check the group record "
                f"#{group} and comment `/save` on it. {record_marker(group)}"
            )
    lines += [
        "",
        "Ticked boxes and closing happen automatically when a record is saved.",
    ]
    return "\n".join(lines) + "\n"


def previous_issue(issues: list[dict], milestone: int) -> int | None:
    """Return the latest issue not in the given milestone.

    Parameters
    ----------
    issues
        Issues with the same criterion label, from the GitHub API.
    milestone
        Number of the current cycle's milestone.

    Returns
    -------
    int or None
        Issue number, or None if there is none.

    """
    numbers = [
        i["number"]
        for i in issues
        if (i.get("milestone") or {}).get("number") != milestone
    ]
    return max(numbers, default=None)


def gh(*args: str, data: dict | None = None) -> str:
    """Run the GitHub CLI.

    Parameters
    ----------
    *args
        Arguments after ``gh``.
    data
        JSON request body, passed on stdin with ``--input -``.

    Returns
    -------
    str
        Standard output.

    """
    if data is not None:
        args = (*args, "--input", "-")
    result = subprocess.run(
        ["gh", *args],
        input=json.dumps(data) if data is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        sys.exit(f"gh {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def api_list(path: str) -> list[dict]:
    """Return every item of a paginated GitHub API list.

    Parameters
    ----------
    path
        API path, e.g. ``repos/o/r/milestones?state=all``.

    Returns
    -------
    list of dict
        All items across pages.

    """
    out = gh("api", "--paginate", path, "--jq", ".[]")
    return [json.loads(line) for line in out.splitlines() if line]


def issues_with(repo: str, query: str) -> list[dict]:
    """Return issues (not pull requests) matching an issues API query.

    Parameters
    ----------
    repo
        ``owner/name``.
    query
        Query string for the issues API, e.g. ``labels=check-in&state=open``.

    Returns
    -------
    list of dict
        Issues.

    """
    items = api_list(f"repos/{repo}/issues?per_page=100&{query}")
    return [i for i in items if "pull_request" not in i]


def start_cycle(
    repo: str,
    criteria: dict,
    config: dict,
    due: datetime.date,
    name: str,
    summary: Path | None,
) -> None:
    """Create a review cycle on GitHub and close the previous one.

    Parameters
    ----------
    repo
        ``owner/name``.
    criteria
        Parsed ``criteria.yml``.
    config
        Parsed ``config.yml``.
    due
        Review date.
    name
        Cycle name; blank for the default.
    summary
        File to append a Markdown summary to (e.g. the job summary), if any.

    """
    rep = representative(config)
    cycle = cycle_name(due, name)
    tiers = config.get("tiers") or ["Bronze"]
    chosen = criteria_for(criteria, tiers)
    source = f"{criteria['source']}/blob/{criteria['source_commit']}"
    report = [f"## {cycle}, due {due:%Y-%m-%d}", ""]

    milestones = api_list(f"repos/{repo}/milestones?state=all&per_page=100")
    current = next((m for m in milestones if m["title"] == cycle), None)
    if current is None:
        current = json.loads(
            gh(
                "api",
                f"repos/{repo}/milestones",
                "-X",
                "POST",
                data={
                    "title": cycle,
                    "due_on": f"{due:%Y-%m-%d}T12:00:00Z",
                    "description": f"Green DiSC review. {CYCLE_MARKER}",
                },
            )
        )
        report.append(f"Created milestone **{cycle}**.")
    else:
        report.append(f"Milestone **{cycle}** already exists; adding what's missing.")
    number = current["number"]
    in_cycle = issues_with(repo, f"milestone={number}&state=all")

    def create(title: str, body: str, labels: list[str], assignees=()) -> int:
        issue = json.loads(
            gh(
                "api",
                f"repos/{repo}/issues",
                "-X",
                "POST",
                data={
                    "title": title,
                    "body": body,
                    "labels": labels,
                    "milestone": number,
                    "assignees": list(assignees),
                },
            )
        )
        return issue["number"]

    # One issue per criterion, linked to the previous cycle's.
    replacement = {}
    created = 0
    for c in chosen:
        existing = [
            i for i in in_cycle if c["label"] in {lb["name"] for lb in i["labels"]}
        ]
        if existing:
            replacement[c["label"]] = existing[0]["number"]
            continue
        gh("label", "create", c["label"], "--color", "1D76DB", "--force")
        previous = previous_issue(
            issues_with(repo, f"labels={c['label']}&state=all"), number
        )
        replacement[c["label"]] = create(
            f"{c['id']} {c['title']}",
            criterion_body(c, cycle, due, source, previous),
            [c["label"]],
        )
        created += 1
    report.append(f"Created {created} criterion issue(s) for {', '.join(tiers)}.")

    # One check-in per record owner; the representative's also covers saving
    # the group record.
    gh("label", "create", CHECK_IN_LABEL, "--color", "C5DEF5", "--force")
    records = issues_with(repo, "labels=personal-record&state=open")
    groups = issues_with(repo, "labels=group-record&state=open")
    group = min((g["number"] for g in groups), default=None)
    owners = {}
    for r in sorted(records, key=lambda r: r["number"]):
        owners.setdefault(r["user"]["login"], r["number"])
    owners.setdefault(rep["github"], None)
    existing = {
        a["login"]
        for i in in_cycle
        if CHECK_IN_LABEL in {lb["name"] for lb in i["labels"]}
        for a in i["assignees"]
    }
    created = 0
    for login, record in owners.items():
        if login in existing:
            continue
        create(
            f"{cycle}: update your Green DiSC records",
            check_in_body(cycle, due, rep, record, group, login == rep["github"]),
            [CHECK_IN_LABEL],
            [login],
        )
        created += 1
    report.append(f"Created {created} check-in(s).")
    if group is None:
        report.append("No group record yet: create one from **Issues → New issue**.")

    unsaved = [
        f"#{i['number']}"
        for i in records + groups
        if "unsaved changes" in {lb["name"] for lb in i["labels"]}
    ]
    if unsaved:
        report.append(f"Records with unsaved changes: {', '.join(unsaved)}.")

    # Close earlier cycles, pointing leftovers to their replacements.
    for m in milestones:
        if (
            m["state"] != "open"
            or m["number"] == number
            or CYCLE_MARKER not in (m.get("description") or "")
        ):
            continue
        for i in issues_with(repo, f"milestone={m['number']}&state=open"):
            labels = {lb["name"] for lb in i["labels"]}
            new = next((replacement[lb] for lb in labels if lb in replacement), None)
            comment = (
                f"Carried over to #{new} in {cycle}."
                if new
                else f"Replaced by {cycle}'s check-ins."
            )
            gh(
                "issue",
                "close",
                str(i["number"]),
                "--repo",
                repo,
                "--reason",
                "not planned",
                "--comment",
                comment,
            )
        gh(
            "api",
            f"repos/{repo}/milestones/{m['number']}",
            "-X",
            "PATCH",
            "-f",
            "state=closed",
        )
        report.append(f"Closed the previous cycle, **{m['title']}**.")

    text = "\n".join(report) + "\n"
    print(text)
    if summary:
        with summary.open("a", encoding="utf-8") as f:
            f.write(text)


def main(argv: list[str] | None = None) -> None:
    """Run the command-line interface.

    Parameters
    ----------
    argv
        Command-line arguments; defaults to ``sys.argv[1:]``.

    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--criteria", type=Path, required=True, help="criteria.yml as JSON")
    p.add_argument("--config", type=Path, required=True, help="config.yml as JSON")
    p.add_argument("--due", required=True)
    p.add_argument("--name", default="")
    args = p.parse_args(argv)
    try:
        due = parse_due(args.due)
        start_cycle(
            os.environ["GITHUB_REPOSITORY"],
            json.loads(args.criteria.read_text(encoding="utf-8")),
            json.loads(args.config.read_text(encoding="utf-8")),
            due,
            args.name,
            Path(os.environ["GITHUB_STEP_SUMMARY"])
            if os.environ.get("GITHUB_STEP_SUMMARY")
            else None,
        )
    except ValueError as e:
        sys.exit(f"::error::{e}")


if __name__ == "__main__":
    main()
