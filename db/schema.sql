-- University Database Schema
-- SQLite-compatible DDL for the Genpact Text-to-SQL Agent homework task.
-- Run once at DB initialization via db/connection.py:init_db().

-- SQLite does not enforce foreign keys by default.
-- This is also set programmatically in the connection event listener.
PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────
-- Table: teachers
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS teachers (
    teacher_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name      TEXT    NOT NULL,
    last_name       TEXT    NOT NULL,
    email           TEXT    UNIQUE NOT NULL,
    department      TEXT    NOT NULL
);

-- ─────────────────────────────────────────
-- Table: students
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS students (
    student_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name      TEXT    NOT NULL,
    last_name       TEXT    NOT NULL,
    email           TEXT    UNIQUE NOT NULL,
    major           TEXT    NOT NULL,
    enrollment_year INTEGER NOT NULL CHECK (enrollment_year >= 2000),
    -- Optional advisory relationship: a teacher may advise zero or more students
    advisor_id      INTEGER,
    FOREIGN KEY (advisor_id) REFERENCES teachers(teacher_id)
);

-- ─────────────────────────────────────────
-- Table: courses
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS courses (
    course_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Human-readable identifier used in displays (e.g. 'CS101'), not used in JOINs
    course_code     TEXT    UNIQUE NOT NULL,
    title           TEXT    NOT NULL,
    department      TEXT    NOT NULL,
    credits         INTEGER NOT NULL CHECK (credits > 0 AND credits <= 6),
    -- Every course must have an assigned teacher
    teacher_id      INTEGER NOT NULL,
    FOREIGN KEY (teacher_id) REFERENCES teachers(teacher_id)
);

-- ─────────────────────────────────────────
-- Table: enrollments  (junction: students ↔ courses)
-- ─────────────────────────────────────────
-- Represents one student's enrollment in one course for one semester.
-- grade is nullable: NULL until the course is completed.
-- status tracks enrollment lifecycle: active → completed or active → dropped.
CREATE TABLE IF NOT EXISTS enrollments (
    enrollment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL,
    course_id       INTEGER NOT NULL,
    semester        TEXT    NOT NULL CHECK (semester IN ('Fall', 'Spring', 'Summer')),
    year            INTEGER NOT NULL CHECK (year >= 2000),
    -- 0-100 scale, NULL when course is not yet completed
    grade           REAL             CHECK (grade >= 0 AND grade <= 100),
    -- 'active'    : currently enrolled, no grade yet
    -- 'completed' : course finished, grade recorded
    -- 'dropped'   : student withdrew; grade remains NULL
    status          TEXT    NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'completed', 'dropped')),
    enrollment_date DATE    NOT NULL DEFAULT CURRENT_DATE,
    FOREIGN KEY (student_id) REFERENCES students(student_id),
    FOREIGN KEY (course_id)  REFERENCES courses(course_id),
    -- A student cannot enroll in the same course in the same semester/year twice
    UNIQUE (student_id, course_id, semester, year)
);

-- ─────────────────────────────────────────
-- Indexes
-- All FK columns indexed for JOIN performance.
-- Additional indexes on common WHERE / ORDER BY columns.
-- ─────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_enrollments_student      ON enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_course       ON enrollments(course_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_semester_year ON enrollments(semester, year);
CREATE INDEX IF NOT EXISTS idx_enrollments_status       ON enrollments(status);
CREATE INDEX IF NOT EXISTS idx_courses_teacher          ON courses(teacher_id);
CREATE INDEX IF NOT EXISTS idx_courses_department       ON courses(department);
CREATE INDEX IF NOT EXISTS idx_students_major           ON students(major);
CREATE INDEX IF NOT EXISTS idx_students_advisor         ON students(advisor_id);
