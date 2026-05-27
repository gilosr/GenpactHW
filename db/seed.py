"""
db/seed.py
──────────
Populates the university SQLite database with deterministic seed data.

Data volumes (wiki-recommended):
  - 6 teachers   across 4 departments
  - 20 students  across 4 majors, enrollment years 2022–2025
  - 12 courses   across departments
  - 50 enrollments across Fall 2024, Spring 2025, Summer 2025

Key data patterns (required to cover all 4 query complexity tiers):
  - Prof. Chen teaches 3 courses   → "which teacher teaches the most courses?"
  - Alice Johnson enrolled in 5 courses → "most active student"
  - CS101 has 9 students            → meaningful AVG/MIN/MAX
  - 3 students with zero enrollments → LEFT JOIN edge cases
  - 1 course with zero enrollments  → "courses with no students"
  - 4 enrollments with status='active' (NULL grade) → in-progress courses
  - 3 enrollments with status='dropped' (NULL grade) → drop-rate queries
  - Grade range: 62–97              → rich aggregation results
  - Three semesters of data         → time-based filtering

Run:
    python -m db.seed          # from project root (resets + repopulates)
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import text

from db.connection import get_engine, reset_db

# ── Seed data ──────────────────────────────────────────────────────────────────

TEACHERS: list[dict[str, Any]] = [
    # id 1 — teaches 3 CS courses (most courses for tier-2 queries)
    {"first_name": "Wei",      "last_name": "Chen",     "email": "w.chen@university.edu",     "department": "Computer Science"},
    # id 2 — teaches 2 CS courses, advises many students
    {"first_name": "Sarah",    "last_name": "Mitchell", "email": "s.mitchell@university.edu", "department": "Computer Science"},
    # id 3 — Mathematics
    {"first_name": "David",    "last_name": "Okafor",   "email": "d.okafor@university.edu",   "department": "Mathematics"},
    # id 4 — Mathematics
    {"first_name": "Priya",    "last_name": "Sharma",   "email": "p.sharma@university.edu",   "department": "Mathematics"},
    # id 5 — Physics
    {"first_name": "Carlos",   "last_name": "Rivera",   "email": "c.rivera@university.edu",   "department": "Physics"},
    # id 6 — English (teaches 1 course, no enrollments → zero-enrollment edge case)
    {"first_name": "Emma",     "last_name": "Walsh",    "email": "e.walsh@university.edu",    "department": "English"},
]

# advisor_id references teacher_id (1-indexed, matching AUTOINCREMENT order above)
STUDENTS: list[dict[str, Any]] = [
    # CS majors (students 1–6)
    {"first_name": "Alice",    "last_name": "Johnson",  "email": "alice.j@university.edu",    "major": "Computer Science", "enrollment_year": 2022, "advisor_id": 1},
    {"first_name": "Bob",      "last_name": "Kim",      "email": "bob.k@university.edu",      "major": "Computer Science", "enrollment_year": 2023, "advisor_id": 1},
    {"first_name": "Clara",    "last_name": "Nguyen",   "email": "clara.n@university.edu",    "major": "Computer Science", "enrollment_year": 2024, "advisor_id": 2},
    {"first_name": "Daniel",   "last_name": "Park",     "email": "daniel.p@university.edu",   "major": "Computer Science", "enrollment_year": 2023, "advisor_id": 2},
    {"first_name": "Eva",      "last_name": "Torres",   "email": "eva.t@university.edu",      "major": "Computer Science", "enrollment_year": 2024, "advisor_id": 2},
    {"first_name": "Felix",    "last_name": "Müller",   "email": "felix.m@university.edu",    "major": "Computer Science", "enrollment_year": 2025, "advisor_id": 2},
    # Math majors (students 7–11)
    {"first_name": "Grace",    "last_name": "Lee",      "email": "grace.l@university.edu",    "major": "Mathematics",      "enrollment_year": 2022, "advisor_id": 3},
    {"first_name": "Hiro",     "last_name": "Tanaka",   "email": "hiro.t@university.edu",     "major": "Mathematics",      "enrollment_year": 2023, "advisor_id": 3},
    {"first_name": "Isla",     "last_name": "Brown",    "email": "isla.b@university.edu",     "major": "Mathematics",      "enrollment_year": 2024, "advisor_id": 4},
    {"first_name": "James",    "last_name": "Singh",    "email": "james.s@university.edu",    "major": "Mathematics",      "enrollment_year": 2023, "advisor_id": 4},
    {"first_name": "Keiko",    "last_name": "Yamamoto", "email": "keiko.y@university.edu",    "major": "Mathematics",      "enrollment_year": 2025, "advisor_id": 4},
    # Physics majors (students 12–15)
    {"first_name": "Liam",     "last_name": "Patel",    "email": "liam.p@university.edu",     "major": "Physics",          "enrollment_year": 2022, "advisor_id": 5},
    {"first_name": "Maya",     "last_name": "Robinson", "email": "maya.r@university.edu",     "major": "Physics",          "enrollment_year": 2023, "advisor_id": 5},
    {"first_name": "Noah",     "last_name": "Garcia",   "email": "noah.g@university.edu",     "major": "Physics",          "enrollment_year": 2024, "advisor_id": 5},
    {"first_name": "Olivia",   "last_name": "Wilson",   "email": "olivia.w@university.edu",   "major": "Physics",          "enrollment_year": 2022, "advisor_id": 5},
    # English majors (students 16–17)
    {"first_name": "Pedro",    "last_name": "Santos",   "email": "pedro.s@university.edu",    "major": "English",          "enrollment_year": 2023, "advisor_id": 6},
    {"first_name": "Quinn",    "last_name": "O'Brien",  "email": "quinn.o@university.edu",    "major": "English",          "enrollment_year": 2024, "advisor_id": 6},
    # Students with NO enrollments (students 18–20) — LEFT JOIN edge cases
    {"first_name": "Rachel",   "last_name": "Adams",    "email": "rachel.a@university.edu",   "major": "Computer Science", "enrollment_year": 2025, "advisor_id": 1},
    {"first_name": "Sam",      "last_name": "Liu",      "email": "sam.l@university.edu",      "major": "Mathematics",      "enrollment_year": 2025, "advisor_id": 3},
    {"first_name": "Tara",     "last_name": "Evans",    "email": "tara.e@university.edu",     "major": "Physics",          "enrollment_year": 2025, "advisor_id": 5},
]

# teacher_id matches AUTOINCREMENT order of TEACHERS list above
COURSES: list[dict[str, Any]] = [
    # Computer Science — Prof. Chen (teacher_id=1) teaches 3 courses
    {"course_code": "CS101", "title": "Introduction to Programming",       "department": "Computer Science", "credits": 3, "teacher_id": 1},
    {"course_code": "CS201", "title": "Data Structures",                    "department": "Computer Science", "credits": 3, "teacher_id": 1},
    {"course_code": "CS301", "title": "Database Systems",                   "department": "Computer Science", "credits": 3, "teacher_id": 1},
    # Computer Science — Prof. Mitchell (teacher_id=2) teaches 2 courses
    {"course_code": "CS401", "title": "Machine Learning",                   "department": "Computer Science", "credits": 4, "teacher_id": 2},
    {"course_code": "CS202", "title": "Algorithms",                         "department": "Computer Science", "credits": 3, "teacher_id": 2},
    # Mathematics — Prof. Okafor (teacher_id=3) teaches 2 courses
    {"course_code": "MATH101", "title": "Calculus I",                       "department": "Mathematics",      "credits": 4, "teacher_id": 3},
    {"course_code": "MATH201", "title": "Linear Algebra",                   "department": "Mathematics",      "credits": 3, "teacher_id": 3},
    # Mathematics — Prof. Sharma (teacher_id=4) teaches 2 courses
    {"course_code": "MATH301", "title": "Probability and Statistics",       "department": "Mathematics",      "credits": 3, "teacher_id": 4},
    {"course_code": "MATH202", "title": "Discrete Mathematics",             "department": "Mathematics",      "credits": 3, "teacher_id": 4},
    # Physics — Prof. Rivera (teacher_id=5) teaches 2 courses
    {"course_code": "PHYS101", "title": "Classical Mechanics",              "department": "Physics",          "credits": 4, "teacher_id": 5},
    {"course_code": "PHYS201", "title": "Electromagnetism",                 "department": "Physics",          "credits": 4, "teacher_id": 5},
    # English — Prof. Walsh (teacher_id=6) — 1 course, ZERO enrollments (edge case)
    {"course_code": "ENG101",  "title": "Academic Writing",                 "department": "English",          "credits": 2, "teacher_id": 6},
]

# Enrollments: (student_id, course_id, semester, year, grade, status, enrollment_date)
# student_id and course_id are 1-indexed per insertion order above.
# status='completed' → grade is set; status='active'|'dropped' → grade is None.
ENROLLMENTS: list[dict[str, Any]] = [
    # ── Fall 2024 ──────────────────────────────────────────────────────────────
    # CS101 (course_id=1): 9 students — largest course, enables rich aggregations
    {"student_id": 1,  "course_id": 1,  "semester": "Fall",   "year": 2024, "grade": 92.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 2,  "course_id": 1,  "semester": "Fall",   "year": 2024, "grade": 78.5, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 3,  "course_id": 1,  "semester": "Fall",   "year": 2024, "grade": 85.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 4,  "course_id": 1,  "semester": "Fall",   "year": 2024, "grade": 70.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 5,  "course_id": 1,  "semester": "Fall",   "year": 2024, "grade": 88.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 7,  "course_id": 1,  "semester": "Fall",   "year": 2024, "grade": 95.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 8,  "course_id": 1,  "semester": "Fall",   "year": 2024, "grade": 62.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 12, "course_id": 1,  "semester": "Fall",   "year": 2024, "grade": 80.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 16, "course_id": 1,  "semester": "Fall",   "year": 2024, "grade": 74.0, "status": "completed", "enrollment_date": "2024-09-01"},
    # CS201 (course_id=2): Data Structures — 5 students
    {"student_id": 1,  "course_id": 2,  "semester": "Fall",   "year": 2024, "grade": 97.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 2,  "course_id": 2,  "semester": "Fall",   "year": 2024, "grade": 83.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 4,  "course_id": 2,  "semester": "Fall",   "year": 2024, "grade": 76.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 7,  "course_id": 2,  "semester": "Fall",   "year": 2024, "grade": 91.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 9,  "course_id": 2,  "semester": "Fall",   "year": 2024, "grade": 68.0, "status": "completed", "enrollment_date": "2024-09-01"},
    # MATH101 (course_id=6): Calculus I — 6 students
    {"student_id": 1,  "course_id": 6,  "semester": "Fall",   "year": 2024, "grade": 88.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 7,  "course_id": 6,  "semester": "Fall",   "year": 2024, "grade": 96.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 8,  "course_id": 6,  "semester": "Fall",   "year": 2024, "grade": 72.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 9,  "course_id": 6,  "semester": "Fall",   "year": 2024, "grade": 85.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 12, "course_id": 6,  "semester": "Fall",   "year": 2024, "grade": 79.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 13, "course_id": 6,  "semester": "Fall",   "year": 2024, "grade": 90.0, "status": "completed", "enrollment_date": "2024-09-01"},
    # PHYS101 (course_id=10): Classical Mechanics — 4 students
    {"student_id": 12, "course_id": 10, "semester": "Fall",   "year": 2024, "grade": 84.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 13, "course_id": 10, "semester": "Fall",   "year": 2024, "grade": 77.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 14, "course_id": 10, "semester": "Fall",   "year": 2024, "grade": 92.0, "status": "completed", "enrollment_date": "2024-09-01"},
    {"student_id": 15, "course_id": 10, "semester": "Fall",   "year": 2024, "grade": 65.0, "status": "completed", "enrollment_date": "2024-09-01"},
    # dropped enrollment — grade stays NULL
    {"student_id": 6,  "course_id": 10, "semester": "Fall",   "year": 2024, "grade": None, "status": "dropped",   "enrollment_date": "2024-09-01"},

    # ── Spring 2025 ────────────────────────────────────────────────────────────
    # CS301 (course_id=3): Database Systems — 5 students
    {"student_id": 1,  "course_id": 3,  "semester": "Spring", "year": 2025, "grade": 94.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 2,  "course_id": 3,  "semester": "Spring", "year": 2025, "grade": 81.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 4,  "course_id": 3,  "semester": "Spring", "year": 2025, "grade": 73.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 5,  "course_id": 3,  "semester": "Spring", "year": 2025, "grade": 87.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 8,  "course_id": 3,  "semester": "Spring", "year": 2025, "grade": 66.0, "status": "completed", "enrollment_date": "2025-01-15"},
    # CS202 (course_id=5): Algorithms — 4 students
    {"student_id": 1,  "course_id": 5,  "semester": "Spring", "year": 2025, "grade": 90.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 3,  "course_id": 5,  "semester": "Spring", "year": 2025, "grade": 82.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 10, "course_id": 5,  "semester": "Spring", "year": 2025, "grade": 75.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 11, "course_id": 5,  "semester": "Spring", "year": 2025, "grade": 89.0, "status": "completed", "enrollment_date": "2025-01-15"},
    # MATH201 (course_id=7): Linear Algebra — 4 students
    {"student_id": 7,  "course_id": 7,  "semester": "Spring", "year": 2025, "grade": 93.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 9,  "course_id": 7,  "semester": "Spring", "year": 2025, "grade": 78.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 10, "course_id": 7,  "semester": "Spring", "year": 2025, "grade": 86.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 11, "course_id": 7,  "semester": "Spring", "year": 2025, "grade": 71.0, "status": "completed", "enrollment_date": "2025-01-15"},
    # PHYS201 (course_id=11): Electromagnetism — 3 students
    {"student_id": 13, "course_id": 11, "semester": "Spring", "year": 2025, "grade": 88.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 14, "course_id": 11, "semester": "Spring", "year": 2025, "grade": 79.0, "status": "completed", "enrollment_date": "2025-01-15"},
    {"student_id": 15, "course_id": 11, "semester": "Spring", "year": 2025, "grade": 83.0, "status": "completed", "enrollment_date": "2025-01-15"},
    # Dropped enrollments
    {"student_id": 6,  "course_id": 5,  "semester": "Spring", "year": 2025, "grade": None, "status": "dropped",   "enrollment_date": "2025-01-15"},
    {"student_id": 16, "course_id": 7,  "semester": "Spring", "year": 2025, "grade": None, "status": "dropped",   "enrollment_date": "2025-01-15"},

    # ── Summer 2025 ────────────────────────────────────────────────────────────
    # CS401 (course_id=4): Machine Learning — 3 students, 1 active (in-progress)
    {"student_id": 1,  "course_id": 4,  "semester": "Summer", "year": 2025, "grade": 95.0, "status": "completed", "enrollment_date": "2025-06-01"},
    {"student_id": 3,  "course_id": 4,  "semester": "Summer", "year": 2025, "grade": 88.0, "status": "completed", "enrollment_date": "2025-06-01"},
    {"student_id": 5,  "course_id": 4,  "semester": "Summer", "year": 2025, "grade": None, "status": "active",    "enrollment_date": "2025-06-01"},
    # MATH301 (course_id=8): Probability — 3 students, 1 active
    {"student_id": 7,  "course_id": 8,  "semester": "Summer", "year": 2025, "grade": 91.0, "status": "completed", "enrollment_date": "2025-06-01"},
    {"student_id": 10, "course_id": 8,  "semester": "Summer", "year": 2025, "grade": 84.0, "status": "completed", "enrollment_date": "2025-06-01"},
    {"student_id": 11, "course_id": 8,  "semester": "Summer", "year": 2025, "grade": None, "status": "active",    "enrollment_date": "2025-06-01"},
    # MATH202 (course_id=9): Discrete Math — 2 students active (current semester)
    {"student_id": 9,  "course_id": 9,  "semester": "Summer", "year": 2025, "grade": None, "status": "active",    "enrollment_date": "2025-06-01"},
    {"student_id": 6,  "course_id": 9,  "semester": "Summer", "year": 2025, "grade": None, "status": "active",    "enrollment_date": "2025-06-01"},
    # CS301 (course_id=3): Database Systems — repeat semester for student 7 (different year/sem)
    {"student_id": 7,  "course_id": 3,  "semester": "Summer", "year": 2025, "grade": 87.0, "status": "completed", "enrollment_date": "2025-06-01"},
]


# ── Insertion helpers ──────────────────────────────────────────────────────────

def _insert_teachers(conn) -> None:
    conn.execute(
        text("""
            INSERT INTO teachers (first_name, last_name, email, department)
            VALUES (:first_name, :last_name, :email, :department)
        """),
        TEACHERS,
    )


def _insert_students(conn) -> None:
    conn.execute(
        text("""
            INSERT INTO students (first_name, last_name, email, major, enrollment_year, advisor_id)
            VALUES (:first_name, :last_name, :email, :major, :enrollment_year, :advisor_id)
        """),
        STUDENTS,
    )


def _insert_courses(conn) -> None:
    conn.execute(
        text("""
            INSERT INTO courses (course_code, title, department, credits, teacher_id)
            VALUES (:course_code, :title, :department, :credits, :teacher_id)
        """),
        COURSES,
    )


def _insert_enrollments(conn) -> None:
    conn.execute(
        text("""
            INSERT INTO enrollments (student_id, course_id, semester, year, grade, status, enrollment_date)
            VALUES (:student_id, :course_id, :semester, :year, :grade, :status, :enrollment_date)
        """),
        ENROLLMENTS,
    )


def _print_summary(conn) -> None:
    tables = ["teachers", "students", "courses", "enrollments"]
    print("\nDatabase seeded successfully.")
    print("─" * 35)
    for table in tables:
        row = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()
        print(f"  {table:<20} {row[0]:>4} rows")
    print("─" * 35)

    # Quick sanity: enrollment breakdown by status
    rows = conn.execute(
        text("SELECT status, COUNT(*) FROM enrollments GROUP BY status ORDER BY status")
    ).fetchall()
    print("\nEnrollment status breakdown:")
    for status, count in rows:
        print(f"  {status:<15} {count:>4}")
    print()


# ── Entry point ────────────────────────────────────────────────────────────────

def seed(db_path: str | None = None) -> None:
    """Reset the database and insert all seed data.

    Args:
        db_path: Optional path override (defaults to university.db in project root).
                 Pass ':memory:' for in-memory seeding in tests.
    """
    reset_db(db_path)
    engine = get_engine(db_path)

    with engine.begin() as conn:
        _insert_teachers(conn)
        _insert_students(conn)
        _insert_courses(conn)
        _insert_enrollments(conn)
        _print_summary(conn)


if __name__ == "__main__":
    seed()
