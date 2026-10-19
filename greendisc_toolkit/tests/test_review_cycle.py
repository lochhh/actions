"""Tests for the review_cycle action's helpers."""

import datetime

import pytest

import review_cycle as rc
import save_record as sr

DUE = datetime.date(2026, 12, 10)
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
    """Names have no spaces; without one, the due date's month is used."""
    assert rc.cycle_name(DUE) == "Review-2026-12"
    assert rc.cycle_name(DUE, " Winter  review ") == "Winter-review"


def test_criteria_for_tiers():
    """Criteria of the chosen tiers get their tier and file; unknown tiers fail."""
    chosen = rc.criteria_for(CRITERIA, ["Bronze", "Gold"])
    assert [(c["id"], c["tier"]) for c in chosen] == [("RG-01-B", "Bronze")]
    with pytest.raises(ValueError, match="Silver"):
        rc.criteria_for(CRITERIA, ["Silver"])


def body(is_rep):
    """Return a check-in body for record 8 and group record 9."""
    return rc.check_in_body("R", DUE, 8, 9, is_rep, "o/r", "[Bronze](https://x)")


def test_member_check_in_closes_after_saving_own_record():
    """A member's check-in closes once their own record is saved."""
    assert "start a pull request" in body(False)
    assert sr.tick_check_in(body(False), 8)[1]


def test_criteria_line_only_with_sync_pr():
    """The rep is asked to merge the criteria sync only when there is one."""
    assert "criteria changed" not in body(True)
    with_pr = rc.check_in_body("R", DUE, 8, 9, True, "o/r", "[B](x)", "42")
    assert "- [ ] #42: Green DiSC's criteria changed" in with_pr


def test_rep_check_in_stays_open():
    """Saving both records ticks the rep's lines but leaves it open."""
    text, complete = sr.tick_check_in(body(True), 8)
    text, complete = sr.tick_check_in(text, 9)
    assert "- [x] #8" in text and "- [x] #9" in text and not complete
    assert "[Bronze](https://x)" in text
