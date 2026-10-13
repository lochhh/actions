# Save Green DiSC records

This action saves Green DiSC *record issues* to CSV files. A record issue holds one Markdown table per section (personal records: Hardware, Data resources, Data cleaning, Pipelines), created from the toolkit's record issue form.

- **When a record issue is opened**, the action labels it (`personal-record` and `unsaved changes`) and replies with how to save it. Each person can have one personal record: a second one is closed with a link to the first.
- **When someone comments exactly `/save`** (other comments, like `/saved it`, are ignored), the action reads every table by its column headers, validates the rows and writes them to `<data-dir>/<section>.csv` (e.g. `data/hardware.csv`), replacing the rows previously saved from that issue. It commits to the default branch, replies with a summary (including how many values are `unknown`) and removes the `unsaved changes` label. The issue stays open, so it can be updated later.
- **When a record's body is edited**, the action adds the `unsaved changes` label, so edits that were never saved are easy to find (`label:"unsaved changes"`).
- **Only the record's owner (with write access) or a maintainer/admin can save it.** The action checks the commenter's role, since the workflow's `author_association` filter also lets read-only organisation members through; anyone else gets a reply explaining why nothing was saved.
- **If any row is invalid**, nothing is written: the reply lists each problem by section, row and column, and the `unsaved changes` label stays.

Blank cells are saved as `unknown`, so missing data stays visible. Missing or reordered columns are fine; unrecognised columns are ignored with a warning. Each CSV has a `record` column with the issue number, which is how a later `/save` finds the rows to replace; edits made directly to the CSV are overwritten by the next `/save` of that issue.

If another save lands at the same time, the push is retried on the latest branch (up to 5 times), so concurrent saves don't conflict.

Issue content is untrusted: it reaches the script only through environment variables and files, never through the workflow's shell code.

## Inputs

| Input | Default | Description |
|---|---|---|
| `data-dir` | `data` | Directory for the CSV files |
| `token` | `github.token` | Token with `contents: write` and `issues: write` |

## Examples of usage

The calling workflow decides who may trigger it. The Green DiSC toolkit template ships a complete workflow (`.github/workflows/save-record.yml`); the core is:

```yaml
on:
  issues:
    types: [opened, edited]
  issue_comment:
    types: [created]

jobs:
  record:
    # Issues only (not PRs); a cheap first filter, the action checks roles.
    if: >-
      !github.event.issue.pull_request &&
      (github.event_name == 'issues' || startsWith(github.event.comment.body, '/save')) &&
      contains(fromJSON('["OWNER", "MEMBER", "COLLABORATOR"]'),
        github.event_name == 'issues' && github.event.issue.author_association || github.event.comment.author_association)
    runs-on: ubuntu-latest
    permissions:
      contents: write
      issues: write
    steps:
      - uses: actions/checkout@<sha> # v7.0.1
      - uses: lochhh/actions/greendisc_toolkit/save_record@<sha>
```
