"""Tests for the review_cycle action's helpers."""

import datetime

import pytest

import review_cycle as rc
import save_record as sr

DUE = datetime.date(2026, 12, 10)
REP = {"name": "Jane Doe", "github": "janedoe", "contact": "jane@example.org"}
CRITERIA = {
    "source": "https://github.com/example/greenDiSC",
    "source_commit": "abc123",
    "tiers": [
        {
            "name": "Bronze",
            "file": "Criteria/Bronze/Research Group - Bronze.md",
            "criteria": [
                {
                    "id": "RG-01-B",
                    "label": "criterion:RG-01-B",
                    "title": "Computing hardware inventory",
                    "category": "offices",
                }
            ],
        },
        {"name": "Gold", "criteria": []},
    ],
}


@pytest.mark.parametrize("value", ["10/12/2026", "2026-02-30", "soon"])
def test_due_must_be_iso_date(value):
    """The due date must be a real YYYY-MM-DD date."""
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        rc.parse_due(value)


def test_cycle_name_defaults_to_month():
    """Without a name, the cycle is named after the due date's month."""
    assert rc.cycle_name(DUE) == "Review 2026-12"
    assert rc.cycle_name(DUE, " Winter review ") == "Winter review"


def test_representative_required():
    """All three representative fields are needed; @ is dropped from github."""
    rep = rc.representative({"representative": {**REP, "github": "@janedoe"}})
    assert rep["github"] == "janedoe"
    with pytest.raises(ValueError, match="github, contact"):
        rc.representative({"representative": {"name": "Jane"}})


def test_criteria_for_tiers():
    """Criteria of the chosen tiers get their tier and file; unknown tiers fail."""
    chosen = rc.criteria_for(CRITERIA, ["Bronze", "Gold"])
    assert [(c["id"], c["tier"]) for c in chosen] == [("RG-01-B", "Bronze")]
    with pytest.raises(ValueError, match="Silver"):
        rc.criteria_for(CRITERIA, ["Silver"])


def test_criterion_body_links_previous_and_upstream():
    """The body links the upstream file and the previous cycle's issue."""
    c = rc.criteria_for(CRITERIA, ["Bronze"])[0]
    body = rc.criterion_body(c, "Review 2026-12", DUE, "https://x/blob/abc", 12)
    assert "https://x/blob/abc/Criteria/Bronze/Research%20Group%20-%20Bronze.md" in body
    assert "- [ ] #12" in body
    assert "Previous" not in rc.criterion_body(c, "R", DUE, "https://x", None)


def test_check_in_closes_after_member_saves():
    """A member's check-in closes once their own record is saved."""
    body = rc.check_in_body("Review 2026-12", DUE, REP, 8, 9, is_rep=False)
    assert "Jane Doe (jane@example.org)" in body
    assert sr.tick_check_in(body, 8)[1]


def test_rep_check_in_needs_both_records():
    """The representative's check-in also waits for the group record's save."""
    body = rc.check_in_body("Review 2026-12", DUE, REP, 8, 9, is_rep=True)
    body, complete = sr.tick_check_in(body, 8)
    assert not complete
    assert sr.tick_check_in(body, 9)[1]


def test_previous_issue_skips_current_milestone():
    """The previous issue is the latest one outside this cycle's milestone."""
    issues = [
        {"number": 3, "milestone": {"number": 1}},
        {"number": 7, "milestone": {"number": 2}},
        {"number": 5, "milestone": None},
    ]
    assert rc.previous_issue(issues, 2) == 5
    assert rc.previous_issue([], 2) is None


def test_rep_check_in_doesnt_refer_to_self():
    """Only members' check-ins name the representative."""
    assert "Jane Doe" not in rc.check_in_body("R", DUE, REP, 8, 9, is_rep=True)
