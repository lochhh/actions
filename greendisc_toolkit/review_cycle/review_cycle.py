# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Start a Green DiSC review cycle.

Creates the cycle's milestone and one check-in per record owner (the
representative's also covers the guides, README and meeting), then closes the
previous cycle. Safe to re-run: anything that already exists in the
cycle's milestone is left alone.
"""

import argparse
import csv
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CYCLE_MARKER = "<!-- greendisc:review-cycle -->"
CHECK_IN_LABEL = "check-in"
KEEP_OPEN = "<!-- keep-open -->"


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
        `name` with spaces replaced by hyphens, or ``Review-<year>-<month>``
        of the due date. GitHub's milestone links don't quote names, so a name
        with spaces leads to a broken search.

    """
    return "-".join(name.split()) or f"Review-{due:%Y-%m}"


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


def record_owner(issue: dict) -> str:
    """Return a record's owner: its assignee, or else whoever created it.

    Parameters
    ----------
    issue
        Record issue from the GitHub API.

    Returns
    -------
    str
        GitHub username.

    """
    assignees = issue.get("assignees") or []
    return assignees[0]["login"] if assignees else issue["user"]["login"]


def members(collaborators: list[dict], actor: str) -> list[str]:
    """Return who should have a personal record.

    Parameters
    ----------
    collaborators
        Repository collaborators from the GitHub API, with ``role_name``.
    actor
        Who started the review; always included.

    Returns
    -------
    list of str
        GitHub usernames of people with the Write or Maintain role, and
        `actor`. Admins are left out: in an organisation they are often owners
        who aren't in the group. Bots are left out too.

    """
    logins = [
        c["login"]
        for c in collaborators
        if c.get("role_name") in {"write", "maintain"} and c.get("type") != "Bot"
    ]
    return sorted(set(logins) | {actor})


def form_body(form: dict) -> str:
    """Render an issue form's pre-filled fields as GitHub renders an issue body.

    Parameters
    ----------
    form
        Parsed issue form.

    Returns
    -------
    str
        One ``### <label>`` section per textarea, with its pre-filled value.

    """
    return "\n\n".join(
        f"### {b['attributes']['label']}\n\n{b['attributes'].get('value', '').strip()}"
        for b in form.get("body", [])
        if b.get("type") == "textarea"
    )


def orphaned_rows(data_dir: Path, records: set[int]) -> dict[int, list[str]]:
    """Find saved rows whose record no longer exists.

    Parameters
    ----------
    data_dir
        Directory with the CSV files written by ``save_record``.
    records
        Numbers of every record issue, open or closed.

    Returns
    -------
    dict of int to list of str
        Missing record number to the CSV files (paths as ``<data_dir>/<name>``)
        that still have its rows.

    """
    found: dict[int, list[str]] = {}
    for path in sorted(data_dir.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as f:
            numbers = {int(r["record"]) for r in csv.DictReader(f) if r.get("record")}
        for n in sorted(numbers - records):
            found.setdefault(n, []).append(f"{data_dir.as_posix()}/{path.name}")
    return found


def check_in_body(
    cycle: str,
    due: datetime.date,
    record: int | None,
    group: int | None,
    is_rep: bool,
    repo: str,
    criteria_links: str,
    criteria_pr: str = "",
    orphans: dict[int, list[str]] | None = None,
) -> str:
    """Return the body of a member's check-in.

    Lines for records the bot tracks end with a hidden record marker, so a
    successful ``/save`` of that record can tick them. A member's check-in
    closes once their record is saved; the representative's carries
    `KEEP_OPEN`, so it stays open until they close it after the meeting.

    Parameters
    ----------
    cycle
        Cycle name.
    due
        Review date.
    record
        The member's personal record, if they have one.
    group
        The group record, if there is one.
    is_rep
        Whether this check-in is the representative's.
    repo
        ``owner/name``, for links to the guides and README.
    criteria_links
        Markdown links to the upstream criteria text.
    criteria_pr
        Number of an open criteria sync pull request, if any.
    orphans
        Saved rows whose record no longer exists (see `orphaned_rows`).

    Returns
    -------
    str
        Markdown body.

    """
    site = f"https://github.com/{repo}"
    lines = [
        (
            f"**{cycle}, due {due:%Y-%m-%d}.** Please go over these and make "
            "sure everything is up to date:"
        ),
        "",
    ]
    if record:
        lines.append(
            f"- [ ] Update your personal record (#{record}), then comment "
            f"`/save` on it. {record_marker(record)}"
        )
    if group and is_rep:
        lines.append(
            f"- [ ] Update the group record (#{group}) and review members' "
            f"changes, then comment `/save` on it. {record_marker(group)}"
        )
    elif group:
        lines.append(f"- [ ] Update the group record (#{group}).")
    guides = f"- [ ] [Guides]({site}/tree/HEAD/guides): "
    if is_rep:
        lines += [
            guides + "check they're up to date, and review members' pull requests.",
            (
                f"- [ ] [README]({site}#your-green-disc-representative): check the "
                "*Your Green DiSC representative* section and the *Where the "
                "evidence lives* table are still right."
            ),
        ]
        if criteria_pr:
            lines.append(
                f"- [ ] #{criteria_pr}: Green DiSC's criteria changed; check the "
                "upstream changes and merge it."
            )
        if orphans:
            where = "; ".join(
                f"#{n} ("
                + ", ".join(f"[{Path(f).name}]({site}/blob/HEAD/{f})" for f in files)
                + ")"
                for n, files in sorted(orphans.items())
            )
            lines.append(
                "- [ ] Saved rows belong to records that no longer exist: "
                f"{where}. If that was intended, delete those rows (the ones "
                "with that `record` number) from the files in `data/` on GitHub. "
                "If not, add the items to their owner's record and `/save` it, "
                "then delete the old rows. Editing these files by hand is fine "
                "here, since no record will overwrite those rows."
            )
        lines += [
            (
                "- [ ] At the review meeting, go through the evidence for each "
                f"criterion ({criteria_links}) with the group, then close this "
                "check-in."
            ),
            "",
            (
                "Saving a record ticks its line; tick the others yourself. "
                f"This check-in stays open until you close it. {KEEP_OPEN}"
            ),
        ]
    else:
        lines += [
            guides + "read them. To suggest anything new or outdated, edit the "
            "page on GitHub and choose to start a pull request.",
            "",
            "Saving your record ticks its line and closes this check-in.",
        ]
    return "\n".join(lines) + "\n"


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
    actor: str,
    criteria_pr: str,
    personal_form: dict | None,
    group_form: dict | None,
    data_dir: Path,
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
    actor
        Who started the review: the representative, whose check-in also covers
        the group record, the guides, the README and the meeting.
    criteria_pr
        Number of an open criteria sync pull request, or blank.
    personal_form
        Parsed personal record issue form, used as the body of records
        created for members who don't have one; None to create none.
    group_form
        Parsed group record issue form, used if the repository has never had
        a group record; None to create none.
    data_dir
        Directory with the saved CSV files, checked for rows whose record no
        longer exists.
    summary
        File to append a Markdown summary to (e.g. the job summary), if any.

    """
    cycle = cycle_name(due, name)
    tiers = config.get("tiers") or ["Bronze"]
    chosen = criteria_for(criteria, tiers)
    source = f"{criteria['source']}/blob/{criteria['source_commit']}"
    files = {c["tier"]: c["file"] for c in chosen}
    criteria_links = ", ".join(
        f"[{tier}]({source}/{file.replace(' ', '%20')})" for tier, file in files.items()
    )
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

    # One check-in per record owner; the representative's also covers saving
    # the group record.
    gh("label", "create", CHECK_IN_LABEL, "--color", "C5DEF5", "--force")
    # Everyone with access gets a personal record once; one that was closed
    # (someone who left, or doesn't need one) is never recreated.
    if personal_form:
        gh("label", "create", "personal-record", "--color", "2E7D32", "--force")
        owned = {
            record_owner(r)
            for r in issues_with(repo, "labels=personal-record&state=all")
        }
        people = members(
            api_list(f"repos/{repo}/collaborators?affiliation=all&per_page=100"),
            actor,
        )
        created = []
        for login in people:
            if login in owned:
                continue
            issue = json.loads(
                gh(
                    "api",
                    f"repos/{repo}/issues",
                    "-X",
                    "POST",
                    data={
                        "title": f"Green DiSC personal record: {login}",
                        "body": form_body(personal_form),
                        "labels": ["personal-record"],
                        "assignees": [login],
                    },
                )
            )
            created.append(f"#{issue['number']} ({login})")
        if created:
            report.append(f"Created personal records: {', '.join(created)}.")
    # The group record too, once, assigned to the representative.
    if group_form and not issues_with(repo, "labels=group-record&state=all"):
        gh("label", "create", "group-record", "--color", "2E7D32", "--force")
        issue = json.loads(
            gh(
                "api",
                f"repos/{repo}/issues",
                "-X",
                "POST",
                data={
                    "title": "Green DiSC group record",
                    "body": form_body(group_form),
                    "labels": ["group-record"],
                    "assignees": [actor],
                },
            )
        )
        report.append(f"Created the group record, #{issue['number']}.")
    records = issues_with(repo, "labels=personal-record&state=open")
    groups = issues_with(repo, "labels=group-record&state=open")
    # Rows saved from records that were since deleted (or unlabelled) would
    # otherwise stay in the data forever: ask the representative about them.
    every_record = {
        i["number"]
        for kind in ("personal-record", "group-record")
        for i in issues_with(repo, f"labels={kind}&state=all")
    }
    orphans = orphaned_rows(data_dir, every_record)
    if orphans:
        report.append(
            "Saved rows from records that no longer exist: "
            + ", ".join(f"#{n}" for n in orphans)
            + "."
        )
    group = min((g["number"] for g in groups), default=None)
    owners = {}
    for r in sorted(records, key=lambda r: r["number"]):
        owners.setdefault(record_owner(r), r["number"])
    owners.setdefault(actor, None)
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
            f"{cycle} check-in: {login}",
            check_in_body(
                cycle,
                due,
                record,
                group,
                login == actor,
                repo,
                criteria_links,
                criteria_pr if login == actor else "",
                orphans if login == actor else None,
            ),
            [CHECK_IN_LABEL],
            [login],
        )
        created += 1
    report.append(f"Created {created} check-in(s).")

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
            comment = f"Replaced by {cycle}'s check-ins."
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
    p.add_argument("--criteria-pr", default="")
    p.add_argument(
        "--personal-form", type=Path, help="personal record issue form as JSON"
    )
    p.add_argument("--group-form", type=Path, help="group record issue form as JSON")
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    args = p.parse_args(argv)
    try:
        due = parse_due(args.due)
        start_cycle(
            os.environ["GITHUB_REPOSITORY"],
            json.loads(args.criteria.read_text(encoding="utf-8")),
            json.loads(args.config.read_text(encoding="utf-8")),
            due,
            args.name,
            os.environ["GITHUB_ACTOR"],
            args.criteria_pr.strip(),
            json.loads(args.personal_form.read_text(encoding="utf-8"))
            if args.personal_form
            else None,
            json.loads(args.group_form.read_text(encoding="utf-8"))
            if args.group_form
            else None,
            args.data_dir,
            Path(os.environ["GITHUB_STEP_SUMMARY"])
            if os.environ.get("GITHUB_STEP_SUMMARY")
            else None,
        )
    except ValueError as e:
        sys.exit(f"::error::{e}")


if __name__ == "__main__":
    main()
