import logging
import json
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import grading
from .config import settings
from .db import SessionLocal, get_db, init_db
from .models import (Assignment, Course, Enrollment, Material, OutboxEmail, Submission, SubmissionFile, User,
                     utcnow)
from .security import (LoginRequired, current_user, current_user_optional, hash_password, instructor_required,
                       student_required, verify_password)
from .storage import SUBMISSION_TYPES, absolute_path, has_file, save_upload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BASE_DIR = Path(__file__).parent
app = FastAPI(title="GradeMate")
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax", max_age=14 * 24 * 3600)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _initials(name: str) -> str:
    parts = [p for p in (name or "?").split() if p]
    return ((parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")) if parts else "?").upper()


def _hue(value) -> int:
    return zlib.crc32(str(value).encode()) % 360


def _iso(dt: Optional[datetime]) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt else ""


def _num(value) -> str:
    return "" if value is None else f"{value:g}"


def _pct(score, total) -> int:
    return int(round(100 * score / total)) if score is not None and total else 0


templates.env.filters.update(initials=_initials, hue=_hue, iso=_iso, num=_num)
templates.env.globals.update(pct=_pct)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    if settings.ai_mock:
        logging.getLogger("grademate").warning("AI grading is in MOCK mode (no GEMINI_API_KEY/ANTHROPIC_API_KEY, or AI_MOCK=1).")
    else:
        logging.getLogger("grademate").info("AI grading uses %s", settings.ai_label)


# ---------- helpers ----------

def flash(request: Request, message: str, kind: str = "success") -> None:
    request.session.setdefault("flashes", []).append({"message": message, "kind": kind})


def _review_count(user: Optional[User]) -> int:
    if not user or not user.is_instructor:
        return 0
    with SessionLocal() as db:
        return db.scalar(select(func.count(Submission.id)).join(Assignment).join(Course).where(
            Course.instructor_id == user.id, Submission.status == "evaluated")) or 0


def render(request: Request, name: str, user: Optional[User] = None, status_code: int = 200, **ctx):
    flashes = request.session.pop("flashes", [])
    return templates.TemplateResponse(
        request, name, {"user": user, "flashes": flashes, "flashes_json": json.dumps(flashes),
                        "ai_mock": settings.ai_mock, "path": request.url.path,
                        "review_count": _review_count(user), **ctx},
        status_code=status_code)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


def is_enrolled(db: Session, course_id: int, student_id: int) -> bool:
    return db.scalar(select(Enrollment.id).where(Enrollment.course_id == course_id,
                                                  Enrollment.student_id == student_id)) is not None


def require_owner(course: Course, user: User) -> None:
    if course.instructor_id != user.id:
        raise HTTPException(403, "You are not the instructor of this course")


def require_course_access(db: Session, course: Course, user: User) -> None:
    if user.is_instructor:
        require_owner(course, user)
    elif not is_enrolled(db, course.id, user.id):
        raise HTTPException(403, "Enroll in this course to see its content")


def parse_due(value: str, tz_offset: str = "") -> Optional[datetime]:
    """datetime-local values are in the browser's local time; store UTC (naive)."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        local = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, "Invalid due date")
    try:  # JS getTimezoneOffset(): minutes to add to local time to get UTC
        return local + timedelta(minutes=int(tz_offset))
    except (TypeError, ValueError):
        return local.astimezone(timezone.utc).replace(tzinfo=None)  # fall back to server timezone


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    return redirect(f"/login?next={request.url.path}")


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    user = None
    if request.session.get("user_id"):
        with SessionLocal() as db:
            user = db.get(User, request.session["user_id"])
    return render(request, "error.html", user=user, status_code=exc.status_code,
                  code=exc.status_code, detail=exc.detail)


# ---------- auth ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: Optional[User] = Depends(current_user_optional)):
    if user:
        return redirect("/dashboard")
    return render(request, "home.html")


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return render(request, "signup.html")


@app.post("/signup")
def signup(request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(...),
           role: str = Form(...), db: Session = Depends(get_db)):
    email = email.strip().lower()
    if role not in ("instructor", "student"):
        raise HTTPException(400, "Invalid role")
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return redirect("/signup")
    user = User(name=name.strip(), email=email, password_hash=hash_password(password), role=role)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "An account with that email already exists.", "error")
        return redirect("/signup")
    request.session["user_id"] = user.id
    flash(request, f"Welcome, {user.name}!")
    return redirect("/dashboard")


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/dashboard"):
    return render(request, "login.html", next=next if next.startswith("/") else "/dashboard")


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/dashboard"),
          db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not verify_password(password, user.password_hash):
        flash(request, "Wrong email or password.", "error")
        return redirect("/login")
    request.session["user_id"] = user.id
    return redirect(next if next.startswith("/") and not next.startswith("//") else "/dashboard")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/")


# ---------- dashboard & courses ----------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.is_instructor:
        courses = db.scalars(select(Course).where(Course.instructor_id == user.id).order_by(Course.created_at.desc())).all()
        to_review = db.scalars(
            select(Submission).join(Assignment).join(Course)
            .where(Course.instructor_id == user.id, Submission.status.in_(["submitted", "evaluated", "failed"]))
            .order_by(Submission.submitted_at)).all()
        stats = {
            "courses": len(courses),
            "students": len({e.student_id for c in courses for e in c.enrollments}),
            "pending": sum(1 for s in to_review if s.status in ("submitted", "failed")),
            "review": sum(1 for s in to_review if s.status == "evaluated"),
        }
        return render(request, "dashboard_instructor.html", user=user, courses=courses, to_review=to_review,
                      stats=stats)

    courses = db.scalars(select(Course).join(Enrollment).where(Enrollment.student_id == user.id)).all()
    my_subs = {s.assignment_id: s for s in db.scalars(select(Submission).where(Submission.student_id == user.id))}
    assignments = [a for c in courses for a in c.assignments]
    assignments.sort(key=lambda a: (a.due_at is None, a.due_at or a.created_at))
    graded = [s for s in my_subs.values() if s.visible_to_student and s.total_score is not None]
    stats = {
        "courses": len(courses),
        "todo": sum(1 for a in assignments if a.id not in my_subs or my_subs[a.id].status == "failed"),
        "awaiting": sum(1 for s in my_subs.values() if not s.visible_to_student),
        "average": (round(sum(100 * s.total_score / s.assignment.max_marks for s in graded) / len(graded))
                    if graded else None),
    }
    return render(request, "dashboard_student.html", user=user, courses=courses, assignments=assignments,
                  my_subs=my_subs, now=utcnow(), stats=stats)


@app.get("/courses", response_class=HTMLResponse)
def course_catalog(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    courses = db.scalars(select(Course).order_by(Course.created_at.desc())).all()
    enrolled = set(db.scalars(select(Enrollment.course_id).where(Enrollment.student_id == user.id)))
    return render(request, "courses.html", user=user, courses=courses, enrolled=enrolled)


@app.get("/courses/new", response_class=HTMLResponse)
def course_new_form(request: Request, user: User = Depends(instructor_required)):
    return render(request, "course_form.html", user=user)


@app.post("/courses/new")
def course_create(request: Request, code: str = Form(...), title: str = Form(...), description: str = Form(""),
                  user: User = Depends(instructor_required), db: Session = Depends(get_db)):
    course = Course(instructor_id=user.id, code=code.strip(), title=title.strip(), description=description.strip())
    db.add(course)
    db.commit()
    flash(request, "Course created. Add materials and assignments below.")
    return redirect(f"/courses/{course.id}")


@app.get("/courses/{course_id}", response_class=HTMLResponse)
def course_detail(request: Request, course_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    course = get_or_404(db, Course, course_id)
    owner = user.is_instructor and course.instructor_id == user.id
    enrolled = (not user.is_instructor) and is_enrolled(db, course.id, user.id)
    my_subs = {}
    if enrolled:
        my_subs = {s.assignment_id: s for s in db.scalars(select(Submission).where(Submission.student_id == user.id))}
    return render(request, "course_detail.html", user=user, course=course, owner=owner, enrolled=enrolled,
                  my_subs=my_subs, now=utcnow())


@app.post("/courses/{course_id}/enroll")
def enroll(request: Request, course_id: int, user: User = Depends(student_required), db: Session = Depends(get_db)):
    course = get_or_404(db, Course, course_id)
    if not is_enrolled(db, course.id, user.id):
        db.add(Enrollment(course_id=course.id, student_id=user.id))
        db.commit()
        flash(request, f"Enrolled in {course.title}.")
    return redirect(f"/courses/{course.id}")


@app.post("/courses/{course_id}/unenroll")
def unenroll(request: Request, course_id: int, user: User = Depends(student_required), db: Session = Depends(get_db)):
    e = db.scalar(select(Enrollment).where(Enrollment.course_id == course_id, Enrollment.student_id == user.id))
    if e:
        db.delete(e)
        db.commit()
        flash(request, "You left the course.")
    return redirect("/courses")


# ---------- materials ----------

@app.post("/courses/{course_id}/materials")
def material_add(request: Request, course_id: int, title: str = Form(...), description: str = Form(""),
                 link_url: str = Form(""), file: Optional[UploadFile] = File(None),
                 user: User = Depends(instructor_required), db: Session = Depends(get_db)):
    course = get_or_404(db, Course, course_id)
    require_owner(course, user)
    link_url = link_url.strip()
    if link_url and not link_url.startswith(("http://", "https://")):
        raise HTTPException(400, "Links must start with http:// or https://")
    if not has_file(file) and not link_url:
        flash(request, "Add a file or a link.", "error")
        return redirect(f"/courses/{course.id}")
    m = Material(course_id=course.id, title=title.strip(), description=description.strip(), link_url=link_url or None)
    if has_file(file):
        m.file_path, m.original_name, _ = save_upload(file, f"materials/{course.id}")
    db.add(m)
    db.commit()
    flash(request, "Material added.")
    return redirect(f"/courses/{course.id}#materials")


@app.get("/materials/{material_id}/download")
def material_download(material_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = get_or_404(db, Material, material_id)
    require_course_access(db, m.course, user)
    if not m.file_path:
        raise HTTPException(404, "This material has no file")
    return FileResponse(absolute_path(m.file_path), filename=m.original_name)


@app.post("/materials/{material_id}/delete")
def material_delete(request: Request, material_id: int, user: User = Depends(instructor_required),
                    db: Session = Depends(get_db)):
    m = get_or_404(db, Material, material_id)
    require_owner(m.course, user)
    course_id = m.course_id
    db.delete(m)
    db.commit()
    flash(request, "Material removed.")
    return redirect(f"/courses/{course_id}#materials")


# ---------- assignments ----------

@app.get("/courses/{course_id}/assignments/new", response_class=HTMLResponse)
def assignment_new_form(request: Request, course_id: int, user: User = Depends(instructor_required),
                        db: Session = Depends(get_db)):
    course = get_or_404(db, Course, course_id)
    require_owner(course, user)
    return render(request, "assignment_form.html", user=user, course=course)


@app.post("/courses/{course_id}/assignments/new")
def assignment_create(request: Request, course_id: int, title: str = Form(...), instructions: str = Form(""),
                      max_marks: float = Form(...), due_at: str = Form(""), rubric: str = Form(""),
                      skip_review: bool = Form(False), question_file: Optional[UploadFile] = File(None),
                      tz_offset: str = Form(""), user: User = Depends(instructor_required),
                      db: Session = Depends(get_db)):
    course = get_or_404(db, Course, course_id)
    require_owner(course, user)
    if max_marks <= 0:
        raise HTTPException(400, "Max marks must be positive")
    if not instructions.strip() and not has_file(question_file):
        flash(request, "Give the questions as text or upload a question file.", "error")
        return redirect(f"/courses/{course.id}/assignments/new")
    a = Assignment(course_id=course.id, title=title.strip(), instructions=instructions.strip(), max_marks=max_marks,
                   due_at=parse_due(due_at, tz_offset), rubric=rubric.strip() or None,
                   rubric_source="instructor" if rubric.strip() else None, skip_review=skip_review)
    if has_file(question_file):
        a.question_file, a.question_file_name, _ = save_upload(question_file, f"questions/{course.id}", SUBMISSION_TYPES)
    db.add(a)
    db.commit()
    flash(request, "Assignment published to enrolled students.")
    return redirect(f"/assignments/{a.id}")


@app.get("/assignments/{assignment_id}", response_class=HTMLResponse)
def assignment_detail(request: Request, assignment_id: int, user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    a = get_or_404(db, Assignment, assignment_id)
    require_course_access(db, a.course, user)
    if user.is_instructor:
        enrolled = db.scalar(select(Enrollment.id).where(Enrollment.course_id == a.course_id).limit(1))
        counts = {}
        for s in a.submissions:
            counts[s.status] = counts.get(s.status, 0) + 1
        enrolled_count = len(a.course.enrollments)
        return render(request, "assignment_instructor.html", user=user, a=a, counts=counts,
                      enrolled_count=enrolled_count, has_students=enrolled is not None,
                      busy=counts.get("evaluating", 0) > 0, poll_key=_assignment_key(a),
                      key_pending=not a.rubric and request.session.pop(f"keygen_{a.id}", False))
    sub = db.scalar(select(Submission).where(Submission.assignment_id == a.id, Submission.student_id == user.id))
    return render(request, "assignment_student.html", user=user, a=a, sub=sub, now=utcnow(),
                  accepted=", ".join(sorted(SUBMISSION_TYPES)))


def _assignment_key(a: Assignment) -> str:
    return "|".join(f"{s.id}:{s.status}" for s in a.submissions) + f"|rubric:{bool(a.rubric)}"


@app.get("/assignments/{assignment_id}/status")
def assignment_status(assignment_id: int, user: User = Depends(instructor_required), db: Session = Depends(get_db)):
    a = get_or_404(db, Assignment, assignment_id)
    require_owner(a.course, user)
    return JSONResponse({"key": _assignment_key(a),
                         "busy": any(s.status == "evaluating" for s in a.submissions)})


@app.get("/assignments/{assignment_id}/question-file")
def assignment_question_file(assignment_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = get_or_404(db, Assignment, assignment_id)
    require_course_access(db, a.course, user)
    if not a.question_file:
        raise HTTPException(404, "No question file")
    return FileResponse(absolute_path(a.question_file), filename=a.question_file_name)


@app.post("/assignments/{assignment_id}/rubric")
def assignment_rubric_save(request: Request, assignment_id: int, rubric: str = Form(""),
                           skip_review: bool = Form(False), user: User = Depends(instructor_required),
                           db: Session = Depends(get_db)):
    a = get_or_404(db, Assignment, assignment_id)
    require_owner(a.course, user)
    a.rubric = rubric.strip() or None
    a.rubric_source = "instructor" if a.rubric else None
    a.skip_review = skip_review
    db.commit()
    flash(request, "Saved. The rubric applies to the next evaluation or re-evaluation.")
    return redirect(f"/assignments/{a.id}")


@app.post("/assignments/{assignment_id}/answer-key")
def assignment_generate_key(request: Request, assignment_id: int, background: BackgroundTasks,
                            user: User = Depends(instructor_required), db: Session = Depends(get_db)):
    a = get_or_404(db, Assignment, assignment_id)
    require_owner(a.course, user)
    a.rubric, a.rubric_source = None, None
    db.commit()

    def _job(aid: int) -> None:
        from .db import SessionLocal
        s = SessionLocal()
        try:
            grading.ensure_rubric(s, s.get(Assignment, aid))
        except Exception:
            logging.getLogger("grademate").exception("answer key generation failed")
        finally:
            s.close()

    background.add_task(_job, a.id)
    request.session[f"keygen_{a.id}"] = True
    flash(request, "The agent is solving the assignment — the answer key appears here when it's ready.", "info")
    return redirect(f"/assignments/{a.id}")


@app.post("/assignments/{assignment_id}/evaluate")
def assignment_evaluate(request: Request, assignment_id: int, background: BackgroundTasks,
                        user: User = Depends(instructor_required), db: Session = Depends(get_db)):
    a = get_or_404(db, Assignment, assignment_id)
    require_owner(a.course, user)
    pending = [s for s in a.submissions if s.status in ("submitted", "failed", "evaluating")]
    if not pending:
        flash(request, "No submissions waiting for evaluation.", "warn")
        return redirect(f"/assignments/{a.id}")
    for s in pending:
        s.status, s.error = "evaluating", None
    db.commit()
    background.add_task(grading.run_evaluation, a.id, [s.id for s in pending])
    flash(request, f"Grading {len(pending)} submission(s). Results appear here as they finish.", "info")
    return redirect(f"/assignments/{a.id}")


@app.post("/assignments/{assignment_id}/approve-all")
def assignment_approve_all(request: Request, assignment_id: int, user: User = Depends(instructor_required),
                           db: Session = Depends(get_db)):
    a = get_or_404(db, Assignment, assignment_id)
    require_owner(a.course, user)
    ready = [s for s in a.submissions if s.status == "evaluated"]
    for s in ready:
        grading.approve_and_email(db, s, f"user:{user.id}")
    flash(request, f"Approved and emailed {len(ready)} result(s).")
    return redirect(f"/assignments/{a.id}")


@app.post("/assignments/{assignment_id}/submit")
def assignment_submit(request: Request, assignment_id: int, text_answer: str = Form(""),
                      files: list[UploadFile] = File(default=[]), user: User = Depends(student_required),
                      db: Session = Depends(get_db)):
    a = get_or_404(db, Assignment, assignment_id)
    require_course_access(db, a.course, user)
    uploads = [f for f in files if has_file(f)]
    if not text_answer.strip() and not uploads:
        flash(request, "Type an answer or attach at least one file.", "error")
        return redirect(f"/assignments/{a.id}")

    sub = db.scalar(select(Submission).where(Submission.assignment_id == a.id, Submission.student_id == user.id))
    if sub and sub.status not in ("submitted", "failed"):
        raise HTTPException(400, "This submission is already being evaluated and can't be changed")
    if sub is None:
        sub = Submission(assignment_id=a.id, student_id=user.id)
        db.add(sub)
    else:
        sub.files.clear()
    sub.text_answer = text_answer.strip() or None
    sub.status, sub.error, sub.submitted_at = "submitted", None, utcnow()
    for f in uploads:
        path, name, ctype = save_upload(f, f"submissions/{a.id}", SUBMISSION_TYPES)
        sub.files.append(SubmissionFile(file_path=path, original_name=name, content_type=ctype))
    db.commit()
    flash(request, "Submitted! You'll get an email when it's graded.")
    return redirect(f"/assignments/{a.id}")


# ---------- submissions / review ----------

def submission_for(db: Session, submission_id: int, user: User) -> Submission:
    s = get_or_404(db, Submission, submission_id)
    if user.is_instructor:
        require_owner(s.assignment.course, user)
    elif s.student_id != user.id:
        raise HTTPException(403, "Not your submission")
    return s


@app.get("/submissions/{submission_id}", response_class=HTMLResponse)
def submission_detail(request: Request, submission_id: int, user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    s = submission_for(db, submission_id, user)
    if not user.is_instructor:
        return redirect(f"/assignments/{s.assignment_id}")
    subs = s.assignment.submissions
    idx = next(i for i, x in enumerate(subs) if x.id == s.id)
    nxt = next((x for x in subs[idx + 1:] + subs[:idx] if x.status == "evaluated"), None)
    return render(request, "submission_review.html", user=user, s=s, a=s.assignment, results=s.results,
                  poll_key=f"{s.status}:{len(s.events)}", next_sub=nxt, position=idx + 1, total=len(subs))


@app.get("/submissions/{submission_id}/status")
def submission_status(submission_id: int, user: User = Depends(instructor_required), db: Session = Depends(get_db)):
    s = submission_for(db, submission_id, user)
    return JSONResponse({"key": f"{s.status}:{len(s.events)}", "status": s.status})


@app.get("/submission-files/{file_id}")
def submission_file(file_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    f = get_or_404(db, SubmissionFile, file_id)
    submission_for(db, f.submission_id, user)
    return FileResponse(absolute_path(f.file_path), filename=f.original_name, content_disposition_type="inline")


@app.post("/submissions/{submission_id}/edit")
async def submission_edit(request: Request, submission_id: int, user: User = Depends(instructor_required),
                          db: Session = Depends(get_db)):
    s = submission_for(db, submission_id, user)
    if s.status not in ("evaluated", "approved", "emailed"):
        raise HTTPException(400, "Only graded submissions can be edited")
    form = await request.form()
    scores, feedbacks = {}, {}
    for key, value in form.items():
        if key.startswith("score_"):
            try:
                scores[int(key[6:])] = float(value)
            except ValueError:
                raise HTTPException(400, f"Invalid score '{value}'")
        elif key.startswith("feedback_"):
            feedbacks[int(key[9:])] = str(value)
    grading.manual_edit(db, s, scores, feedbacks, str(form.get("overall_feedback", "")),
                        str(form.get("note", "")), f"user:{user.id}")
    flash(request, "Grades updated. Approve to send them to the student.")
    return redirect(f"/submissions/{s.id}")


@app.post("/submissions/{submission_id}/reevaluate")
def submission_reevaluate(request: Request, submission_id: int, background: BackgroundTasks,
                          feedback: str = Form(...), user: User = Depends(instructor_required),
                          db: Session = Depends(get_db)):
    s = submission_for(db, submission_id, user)
    if s.status not in ("evaluated", "approved", "emailed"):
        raise HTTPException(400, "Only graded submissions can be re-evaluated")
    if not feedback.strip():
        flash(request, "Write what the agent should reconsider.", "error")
        return redirect(f"/submissions/{s.id}")
    s.status, s.error = "evaluating", None
    db.commit()
    background.add_task(grading.run_reevaluation, s.id, feedback.strip(), f"user:{user.id}")
    flash(request, "The agent is re-evaluating with your feedback…", "info")
    return redirect(f"/submissions/{s.id}")


@app.post("/submissions/{submission_id}/approve")
def submission_approve(request: Request, submission_id: int, user: User = Depends(instructor_required),
                       db: Session = Depends(get_db)):
    s = submission_for(db, submission_id, user)
    if s.status != "evaluated":
        raise HTTPException(400, "Only evaluated submissions can be approved")
    grading.approve_and_email(db, s, f"user:{user.id}")
    flash(request, f"Approved and emailed to {s.student.email}.")
    return redirect(f"/assignments/{s.assignment_id}")


# ---------- outbox (dev email viewer) ----------

@app.get("/outbox", response_class=HTMLResponse)
def outbox(request: Request, user: User = Depends(instructor_required), db: Session = Depends(get_db)):
    student_emails = select(User.email).join(Enrollment, Enrollment.student_id == User.id).join(Course).where(
        Course.instructor_id == user.id)
    emails = db.scalars(select(OutboxEmail).where(OutboxEmail.to_email.in_(student_emails))
                        .order_by(OutboxEmail.created_at.desc()).limit(200)).all()
    return render(request, "outbox.html", user=user, emails=emails, smtp=bool(settings.smtp_host))
