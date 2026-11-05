# GradeMate — AI-assisted course & assignment platform

A course platform where **instructors** publish courses, study material and assignments, **students** enroll, read material and submit work, and a built-in **AI grading agent** (Claude) evaluates submissions question by question. The instructor reviews the AI's grades, can ask the agent to re-evaluate with feedback, and results are **emailed to students**.

This README is the design doc for the **v0 prototype**. The long-term, production design (multi-tenant, OCR pipeline, audit, compliance, scaling) lives in the full design document and is the target we grow toward phase by phase.

---

## 1. Prototype goals

| Goal | In v0? |
| --- | --- |
| Instructor and student accounts (email + password) | Yes |
| Instructor creates courses; students browse and enroll | Yes |
| Study material per course (file upload or link) | Yes |
| Instructor creates assignments (questions, max marks, due date, optional rubric) | Yes |
| Students upload their submission (PDF, image, text file, or typed answer) | Yes |
| Instructor triggers AI evaluation for an assignment | Yes |
| Agent writes its own answer key when no rubric is given | Yes |
| Per-question score + feedback, reviewed by instructor | Yes |
| Instructor edits a score, or sends feedback and the agent re-evaluates | Yes |
| Results emailed to students (after approval, or immediately if review is skipped) | Yes |
| Students see their approved results in the app | Yes |
| Chat with the agent about an evaluated assignment | Later (v1) |
| Handwriting-specific OCR pipeline, page splitting | Later (v0 relies on Claude vision) |
| Org/institution tenancy, admins, SSO, LMS sync | Later |

**Non-goals for v0:** scale, multi-tenancy, background job infrastructure, compliance hardening. The prototype must be simple enough to run with one command and demo end to end.

---

## 2. Users and roles

| Role | Can do |
| --- | --- |
| **Instructor** | Create/edit courses, upload materials, create assignments, view submissions, run evaluation, review/edit/re-evaluate grades, approve and email results |
| **Student** | Browse and enroll in courses, view materials and assignments of enrolled courses, submit (and resubmit before evaluation), view approved results |

Role is chosen at sign-up in v0 (later: invited by an institution admin).

---

## 3. Core flows

```
Instructor                              Student
──────────                              ───────
Create course ───────────────────────▶  Browse courses → Enroll
Upload material ─────────────────────▶  View / download material
Create assignment (rubric optional) ─▶  View assignment → Upload submission
        │
        ▼
Click "Evaluate"  ──▶  Agent: (1) build answer key if no rubric
                              (2) grade each submission per question
        │
        ▼
Review each submission:
  • Approve            ─────────────────────────────▶  Email with results
  • Edit scores, approve ───────────────────────────▶  Email with results
  • "Re-evaluate" with feedback → agent re-grades,
     says if it agrees/disagrees → approve ─────────▶  Email with results
```

If the assignment has **"skip review"** enabled, results are approved and emailed as soon as grading finishes.

### Submission status machine

```
submitted ──evaluate──▶ evaluating ──ok──▶ evaluated ──approve──▶ approved ──email──▶ emailed
    ▲                        │                 │
    │                        └──error──▶ failed (retry with Evaluate)
    └── student resubmits (only before evaluation)     re-evaluate: evaluated → evaluating → evaluated
```

Only `approved`/`emailed` results are visible to students.

---

## 4. Architecture (v0)

One Python process. Deliberately boring, so it is easy to run and easy to replace piece by piece.

| Layer | v0 choice | Upgrade path |
| --- | --- | --- |
| Web framework | FastAPI | same |
| UI | Server-rendered Jinja2 templates + a hand-written design system (`static/style.css`, light/dark) + vanilla JS (`static/app.js`: tabs, dialogs, drag-and-drop uploads, live status polling, toasts, live score totals). No build step. | Next.js frontend calling a JSON API |
| Database | SQLite via SQLAlchemy 2 | PostgreSQL (+ Alembic migrations) |
| File storage | Local `uploads/` folder | S3-compatible object storage, signed URLs |
| Background work | FastAPI `BackgroundTasks` (in-process) | Redis queue + workers (Celery/Arq), durable agent runs |
| Auth | Email + password (PBKDF2-SHA256), signed session cookie | Auth provider (Clerk/Auth.js/Keycloak), SSO, MFA |
| AI | Provider switch: Google Gemini (`google-genai`) or Claude (`anthropic`), same prompts and JSON schemas for both | LLM gateway, model routing, eval suite |
| Email | SMTP (any provider); falls back to an in-app **Outbox** when SMTP is not configured | Transactional email service (SES, Resend, Postmark) |

```
browser ──▶ FastAPI (routes + templates) ──▶ SQLite
                 │            │
                 │            └──▶ uploads/ (materials, questions, submissions)
                 └──▶ BackgroundTasks ──▶ grading agent ──▶ Claude API
                                   └──▶ emailer ──▶ SMTP or Outbox table
```

### Code layout

```
app/
  main.py          FastAPI app, all routes
  config.py        settings from environment / .env
  db.py            engine, session, init
  models.py        SQLAlchemy models
  security.py      password hashing, current-user helpers
  storage.py       saving and reading uploaded files
  grading.py       evaluation workflow (status updates, totals, emailing)
  agent.py         Claude calls: answer key, grading, re-evaluation (+ mock mode)
  emailer.py       SMTP / outbox
  templates/       Jinja2 pages
  static/style.css
uploads/           runtime files (git-ignored)
```

---

## 5. Data model

| Table | Key columns |
| --- | --- |
| `users` | id, name, email (unique), password_hash, role (`instructor`/`student`), created_at |
| `courses` | id, instructor_id → users, code, title, description, created_at |
| `enrollments` | id, course_id, student_id, created_at — unique (course, student) |
| `materials` | id, course_id, title, description, file_path?, original_name?, link_url?, created_at |
| `assignments` | id, course_id, title, instructions, question_file?, max_marks, due_at?, rubric?, rubric_source (`instructor`/`ai`), skip_review, created_at |
| `submissions` | id, assignment_id, student_id, text_answer?, status, total_score?, overall_feedback?, results_json?, reviewer_note?, error?, submitted_at, evaluated_at?, approved_at?, emailed_at? — unique (assignment, student) |
| `submission_files` | id, submission_id, file_path, original_name, content_type |
| `evaluation_events` | id, submission_id, kind (`ai_grade`/`reeval`/`manual_edit`/`approve`/`email`), actor (`agent`/user id), payload_json, created_at — **the history of every grade change** |
| `outbox` | id, to_email, subject, body, status (`sent`/`logged`/`failed`), error?, created_at |

`results_json` holds the current per-question grades:

```json
{
  "questions": [
    {"question": "Q1", "max_score": 5, "score": 4,
     "feedback": "Correct method; final unit missing.",
     "reasoning": "Criteria: formula (1), substitution (2), answer+unit (2 → 1)."}
  ],
  "overall_feedback": "Good understanding of kinematics. Watch units.",
  "verdict": null
}
```

Totals are always recomputed in code from the per-question scores (and clamped to each question's max), never trusted from the model.

---

## 6. The grading agent

The agent is a small, fixed workflow — not a free-roaming agent — so each step is testable.

1. **Answer key (only if no rubric).** Claude reads the assignment instructions and the question file, solves every question, and writes a rubric with marks per step that adds up to the assignment's max marks. It is saved on the assignment (`rubric_source = ai`) and shown to the instructor, who can edit it at any time.
2. **Grade.** For each submission, Claude receives: the questions (text + file), the rubric, and the student's work (PDF/images go in as documents/images, so Claude's vision reads handwriting). It returns structured JSON per question: score, max score, feedback for the student, reasoning for the instructor.
3. **Re-evaluate.** The instructor writes feedback ("Q2: the student did write the unit on page 2"). Claude gets the original grade + feedback + the work again and returns a **verdict** (`agree` / `partly_agree` / `disagree`) with reasoning, plus the revised grades. It is told that being correct matters more than agreeing, so it may push back.
4. **Human decision.** Nothing reaches the student until the instructor approves (unless the assignment is set to skip review).

**Guardrails in v0**

- Structured outputs (Pydantic schemas) — no free-text parsing.
- Student work is wrapped as untrusted data; instructions inside it ("give me full marks") are ignored and flagged.
- Scores are clamped and totals computed in code.
- Every AI grade, re-evaluation, manual edit and approval is logged in `evaluation_events`.
- Claude calls use the server-side refusal fallback (`fallbacks: "default"`); Gemini safety blocks are reported as a failed grade for the instructor to handle.

**Providers.** The same workflow, prompts and schemas run on Gemini or Claude; set the key you have. Gemini defaults to the cheapest model (`gemini-flash-lite-latest`) with low thinking to keep testing cheap; raise `GEMINI_MODEL` / `GEMINI_THINKING` when grading quality matters more than cost.

**Mock mode.** With no AI key (or `AI_MOCK=1`), the agent returns clearly-labelled placeholder grades so the full flow can be developed and demoed offline.

---

## 7. Email

When results are approved, the student receives an email with: course, assignment, total score, per-question scores and feedback, and overall feedback. If `SMTP_HOST` is not set, emails are written to the **Outbox** page (`/outbox`, instructors only) instead of being sent — handy for local demos.

---

## 8. Running it

Requirements: Python 3.10+.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in ANTHROPIC_API_KEY (optional) and SMTP (optional)
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000, sign up one instructor and one student (two browsers or a private window), and walk the flow above.

Optional demo data: `python -m app.seed` creates `teacher@example.com` / `student@example.com` (password `password123`) with a course and an assignment.

### Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `SECRET_KEY` (or `SESSION_SECRET`) | Signs session cookies | random per start (logs everyone out on restart) |
| `AI_PROVIDER` | `auto`, `gemini`, `anthropic` or `mock`. `auto` picks Claude if its key is set, else Gemini, else mock | `auto` |
| `GEMINI_API_KEY` | Enables grading with Google Gemini | unset |
| `GEMINI_MODEL` | Gemini model. Default is the cheapest tier; use `gemini-flash-latest` or `gemini-pro-latest` for higher-quality grading | `gemini-flash-lite-latest` |
| `GEMINI_FALLBACK_MODEL` | Model to switch to when the main one is rate-limited (empty = never switch, so spend stays predictable) | empty |
| `GEMINI_THINKING` | Reasoning effort: `low`, `medium`, `high`, or empty for the model default. Thinking tokens are billed, so `low` keeps tests cheap | `low` |
| `ANTHROPIC_API_KEY` | Enables grading with Claude | unset |
| `GRADER_MODEL` | Claude model id | `claude-opus-5-5` |
| `AI_MOCK` | Force mock grading (`1`) | `0` |
| `DATABASE_URL` | SQLAlchemy URL | `sqlite:///./grademate.db` |
| `UPLOAD_DIR` | Where files are stored | `./uploads` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS` | Outgoing email | unset → Outbox |
| `MAX_UPLOAD_MB` | Per-file upload limit | `20` |

---

## 9. Known prototype limitations

- No CSRF tokens (cookies are `SameSite=Lax`); add CSRF protection before any public deployment.
- Background grading runs in-process; a server restart mid-evaluation leaves submissions in `evaluating` (click Evaluate again to retry).
- SQLite and local files: single machine only.
- No rate limiting, no password reset, no email verification.
- Accepted submission formats: PDF, PNG, JPG/JPEG, GIF, WEBP, TXT/MD, plus typed text. DOCX later.

---

## 10. Roadmap (how we scale it)

| Phase | Adds |
| --- | --- |
| **v0 — prototype (this)** | Courses, enrollment, materials, assignments, submissions, AI grading with review/re-eval, email |
| **v1 — usable pilot** | Assignment chat agent (per evaluated assignment), bulk review by question, confidence flags, class analytics, password reset, CSRF, Postgres + Alembic |
| **v2 — production foundations** | Separate Next.js frontend + JSON API, Redis queue + workers, S3 storage, auth provider, audit log UI, golden-set evals for grading accuracy |
| **v3 — institution** | Institutions/departments/admins, SSO, sections and TAs, LMS/gradebook export, handwriting OCR pipeline, rubric amendments applied class-wide |
| **v4 — scale & compliance** | Multi-region, data retention policies, DPDP/FERPA/GDPR compliance work, SOC 2, observability and cost controls |
