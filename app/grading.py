"""Evaluation workflow: runs the agent, normalises scores, records events, approves and emails."""
import json
import logging
from typing import Optional

from . import agent
from .db import SessionLocal
from .emailer import results_email, send_email
from .models import Assignment, EvaluationEvent, Submission, utcnow

log = logging.getLogger("grademate.grading")


def _event(db, submission: Submission, kind: str, actor: str, payload: dict) -> None:
    db.add(EvaluationEvent(submission_id=submission.id, kind=kind, actor=actor, payload_json=json.dumps(payload)))


def normalise(result: dict) -> tuple[dict, float]:
    """Clamp every score into [0, max] and compute the total in code."""
    total = 0.0
    for q in result.get("questions", []):
        q["max_score"] = max(0.0, float(q.get("max_score") or 0))
        q["score"] = min(max(0.0, float(q.get("score") or 0)), q["max_score"])
        total += q["score"]
    return result, round(total, 2)


def apply_result(db, submission: Submission, result: dict, kind: str, actor: str) -> None:
    result, total = normalise(result)
    max_total = submission.assignment.max_marks
    if total > max_total:  # rubric/questions disagree with the assignment total
        result.setdefault("flags", []).append(
            f"Question marks add up to more than the assignment total ({total:g} > {max_total:g}); total capped.")
        total = max_total
    submission.results_json = json.dumps(result)
    submission.total_score = total
    submission.overall_feedback = result.get("overall_feedback")
    submission.status = "evaluated"
    submission.evaluated_at = utcnow()
    submission.error = None
    _event(db, submission, kind, actor, {"total": total, **result})


def approve_and_email(db, submission: Submission, actor: str) -> None:
    submission.status = "approved"
    submission.approved_at = utcnow()
    _event(db, submission, "approve", actor, {"total": submission.total_score})
    db.commit()
    subject, body = results_email(submission)
    record = send_email(db, submission.student.email, subject, body)
    if record.status in ("sent", "logged"):
        submission.status = "emailed"
        submission.emailed_at = utcnow()
    _event(db, submission, "email", "system", {"outbox_id": record.id, "status": record.status})
    db.commit()


def ensure_rubric(db, assignment: Assignment) -> None:
    if assignment.rubric and assignment.rubric.strip():
        return
    key = agent.generate_answer_key(assignment)
    assignment.rubric = key.rubric
    assignment.rubric_source = "ai"
    db.commit()


def run_evaluation(assignment_id: int, submission_ids: list[int]) -> None:
    """Background task: build the answer key if needed, then grade each submission."""
    db = SessionLocal()
    try:
        assignment = db.get(Assignment, assignment_id)
        try:
            ensure_rubric(db, assignment)
        except Exception as exc:
            log.exception("answer key generation failed")
            for sid in submission_ids:
                s = db.get(Submission, sid)
                s.status, s.error = "failed", f"Could not build answer key: {exc}"
            db.commit()
            return

        for sid in submission_ids:
            s = db.get(Submission, sid)
            try:
                result = agent.grade_submission(assignment, s).model_dump()
                apply_result(db, s, result, "ai_grade", "agent")
                db.commit()
                if assignment.skip_review:
                    approve_and_email(db, s, "auto (skip review)")
            except Exception as exc:
                log.exception("grading failed for submission %s", sid)
                db.rollback()
                s = db.get(Submission, sid)
                s.status, s.error = "failed", str(exc)
                _event(db, s, "error", "agent", {"error": str(exc)})
                db.commit()
    finally:
        db.close()


def run_reevaluation(submission_id: int, feedback: str, actor: str) -> None:
    db = SessionLocal()
    try:
        s = db.get(Submission, submission_id)
        previous = s.results
        try:
            result = agent.reevaluate_submission(s.assignment, s, previous, feedback).model_dump()
            result["instructor_feedback"] = feedback
            apply_result(db, s, result, "reeval", "agent")
            db.commit()
        except Exception as exc:
            log.exception("re-evaluation failed for submission %s", submission_id)
            db.rollback()
            s = db.get(Submission, submission_id)
            s.status = "evaluated" if previous else "failed"  # keep the previous grade
            s.error = f"Re-evaluation failed: {exc}"
            _event(db, s, "error", "agent", {"error": str(exc), "feedback": feedback})
            db.commit()
    finally:
        db.close()


def manual_edit(db, submission: Submission, scores: dict[int, float], feedbacks: dict[int, str],
                overall: Optional[str], note: str, actor: str) -> None:
    result = submission.results
    for i, q in enumerate(result.get("questions", [])):
        if i in scores:
            q["score"] = scores[i]
        if i in feedbacks:
            q["feedback"] = feedbacks[i]
    if overall is not None:
        result["overall_feedback"] = overall
    result["edit_note"] = note
    apply_result(db, submission, result, "manual_edit", actor)
    db.commit()
