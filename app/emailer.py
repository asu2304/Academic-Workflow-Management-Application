import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy.orm import Session

from .config import settings
from .models import OutboxEmail

log = logging.getLogger("grademate.email")


def send_email(db: Session, to: str, subject: str, body: str) -> OutboxEmail:
    """Send via SMTP when configured; otherwise record it in the Outbox. Always records a row."""
    record = OutboxEmail(to_email=to, subject=subject, body=body, status="logged")
    if settings.smtp_host:
        msg = EmailMessage()
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                if settings.smtp_starttls:
                    smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
            record.status = "sent"
        except Exception as exc:  # keep the app working if the mail server is down
            log.exception("SMTP send failed")
            record.status = "failed"
            record.error = str(exc)
    else:
        log.info("SMTP not configured; email to %s stored in outbox: %s", to, subject)
    db.add(record)
    db.commit()
    return record


def results_email(submission) -> tuple[str, str]:
    a = submission.assignment
    r = submission.results
    lines = [
        f"Hi {submission.student.name},",
        "",
        f"Your submission for \"{a.title}\" ({a.course.code} — {a.course.title}) has been evaluated.",
        "",
        f"Total score: {submission.total_score:g} / {a.max_marks:g}",
        "",
    ]
    for q in r.get("questions", []):
        lines.append(f"{q['question']}: {q['score']:g} / {q['max_score']:g}")
        lines.append(f"  {q['feedback']}")
        lines.append("")
    if submission.overall_feedback:
        lines += ["Overall feedback:", submission.overall_feedback, ""]
    lines += ["You can also see these results in GradeMate.", "", "— GradeMate"]
    return f"Results: {a.title} ({submission.total_score:g}/{a.max_marks:g})", "\n".join(lines)
