"""Create demo accounts and a sample course: python -m app.seed"""
from sqlalchemy import select

from .db import SessionLocal, init_db
from .models import Assignment, Course, Enrollment, Material, User
from .security import hash_password


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.scalar(select(User).where(User.email == "teacher@example.com")):
            print("Demo data already exists.")
            return
        teacher = User(name="Demo Teacher", email="teacher@example.com", password_hash=hash_password("password123"),
                       role="instructor")
        student = User(name="Demo Student", email="student@example.com", password_hash=hash_password("password123"),
                       role="student")
        db.add_all([teacher, student])
        db.flush()
        course = Course(instructor_id=teacher.id, code="PHY-101", title="Physics: Motion and Forces",
                        description="Kinematics, Newton's laws and simple problem solving.")
        db.add(course)
        db.flush()
        db.add(Enrollment(course_id=course.id, student_id=student.id))
        db.add(Material(course_id=course.id, title="Equations of motion (reading)",
                        description="Background reading for week 1.",
                        link_url="https://en.wikipedia.org/wiki/Equations_of_motion"))
        db.add(Assignment(
            course_id=course.id, title="Homework 1: Kinematics", max_marks=10,
            instructions=("Q1 (5 marks) A car starts from rest and accelerates uniformly at 2 m/s² for 10 s. "
                          "Find its final velocity.\n"
                          "Q2 (5 marks) How far does the car travel in those 10 s? Show your working.")))
        db.commit()
        print("Created teacher@example.com and student@example.com (password: password123).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
