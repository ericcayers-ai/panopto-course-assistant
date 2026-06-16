"""§6 study planner: assessments, SM-2 scheduling, .ics, plan, progress."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from app import database, study_planner


@pytest.fixture()
def db(tmp_path: Path):
    d = database.Database(tmp_path / "t.db")
    d.create_course("COMPX234", code="COMPX234")
    yield d
    d.close()


def test_assessment_crud_and_status_validation(db):
    a = study_planner.create_assessment(db, 1, "A1", due_date="2026-04-10",
                                       weight=10, status="not_started")
    assert a["status"] == "not_started"
    upd = study_planner.update_assessment(db, a["id"], status="submitted")
    assert upd["status"] == "submitted"
    with pytest.raises(ValueError):
        study_planner.create_assessment(db, 1, "Bad", status="invalid")
    assert study_planner.delete_assessment(db, a["id"]) is True


def test_sm2_progression_and_lapse():
    # first three good recalls: 1 -> 6 -> ~15 days
    s1 = study_planner.schedule_after(5, reps=0, interval=1, ease=2.5,
                                     today=dt.date(2026, 1, 1))
    assert s1["interval"] == 1 and s1["reps"] == 1
    s2 = study_planner.schedule_after(5, reps=1, interval=1, ease=s1["ease"],
                                     today=dt.date(2026, 1, 1))
    assert s2["interval"] == 6
    s3 = study_planner.schedule_after(4, reps=2, interval=6, ease=s2["ease"],
                                     today=dt.date(2026, 1, 1))
    assert s3["interval"] > 6
    # a lapse (quality<3) resets the interval to 1
    lapse = study_planner.schedule_after(1, reps=5, interval=30, ease=2.5)
    assert lapse["interval"] == 1 and lapse["reps"] == 0


def test_review_items_seed_due_and_grade(db):
    n = study_planner.add_review_items(db, 1, [
        {"front": "What is TCP?", "back": "transport protocol"},
        {"front": "", "back": "skipped"}])
    assert n == 1
    due = study_planner.due_reviews(db, 1)
    assert len(due) == 1
    graded = study_planner.grade_review(db, due[0]["id"], 5)
    assert graded["reps"] == 1
    # after grading well it is no longer due today
    assert study_planner.due_reviews(db, 1) == []


def test_build_ics_has_deadline_event(db, tmp_path):
    study_planner.create_assessment(db, 1, "A2", due_date="2026-05-19", weight=15)
    ics = study_planner.build_ics(db, tmp_path, 1, course_name="COMPX234")
    assert "BEGIN:VCALENDAR" in ics and "END:VCALENDAR" in ics
    assert "DTSTART;VALUE=DATE:20260519" in ics
    assert "A2 due" in ics


def test_generate_plan_ramps_assessment_prep(db, tmp_path):
    today = dt.date.today()
    due = (today + dt.timedelta(days=3)).isoformat()
    study_planner.create_assessment(db, 1, "A1", due_date=due, weight=10)
    study_planner.add_review_items(db, 1, [{"front": "q", "back": "a"}])
    plan = study_planner.generate_plan(db, tmp_path, 1, horizon_days=7, hours_per_week=14)
    assert plan["daily_budget_hours"] == 2.0
    # an assessment-prep task appears within the lead window
    has_prep = any(t["type"] == "assessment"
                   for day in plan["days"] for t in day["tasks"])
    assert has_prep
    assert plan["upcoming_assessments"][0]["days_away"] == 3


def test_progress_metrics(db):
    study_planner.create_assessment(db, 1, "A1", status="graded")
    study_planner.create_assessment(db, 1, "A2", status="not_started")
    db.log_study_session(1, dt.date.today().isoformat(), 90, "review")
    db.record_quiz_attempt(1, "course", score=8, total=10, mode="practice")
    p = study_planner.progress(db, 1)
    assert p["assessments_completed"] == 1 and p["assessments_total"] == 2
    assert p["completion_pct"] == 50.0
    assert p["mastery_pct"] == 80.0
    assert p["study_hours"] == 1.5
    assert p["current_streak_days"] >= 1
