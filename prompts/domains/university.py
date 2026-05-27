from prompts.domains.base import (
    ClassificationExample,
    CorrectionExample,
    DomainPromptData,
    FewShotExample,
)

UNIVERSITY = DomainPromptData(
    domain_name="university",
    domain_description="a university database question-answering system",

    data_description=(
        "Teachers (names, departments, emails)",
        "Students (names, majors, enrollment years, advisors)",
        "Courses (codes, titles, departments, credits, assigned teachers)",
        "Enrollments (which students are in which courses, semesters, grades, status)",
    ),

    classification_examples=(
        ClassificationExample("How many students are enrolled in CS101?", "relevant"),
        ClassificationExample("How many students are there?", "relevant"),
        ClassificationExample("How many teachers are in the university?", "relevant"),
        ClassificationExample("List all courses offered by the Mathematics department", "relevant"),
        ClassificationExample("What is the weather like today?", "not_relevant"),
        ClassificationExample("What are Professor Chen's office hours?", "not_relevant"),
    ),

    business_rules=(
        "For grade-related queries, ALWAYS filter by status = 'completed' or "
        "grade IS NOT NULL. Active and dropped enrollments have NULL grades.",
    ),

    relationship_guide=(
        "- teachers.teacher_id -> courses.teacher_id (one teacher teaches many courses)\n"
        "- students.student_id -> enrollments.student_id (one student has many enrollments)\n"
        "- courses.course_id -> enrollments.course_id (one course has many enrollments)\n"
        "- students.advisor_id -> teachers.teacher_id (optional advisory relationship)\n"
        "- enrollments is the junction table connecting students and courses, "
        "with semester/year/grade/status"
    ),

    few_shot_examples=(
        FewShotExample(
            question="How many students are there?",
            sql={
                "sqlite": "SELECT COUNT(*) AS student_count FROM students;",
                "postgresql": "SELECT COUNT(*) AS student_count FROM students;",
            },
        ),
        FewShotExample(
            question="Which courses does Prof. Chen teach?",
            sql={
                "sqlite": (
                    "SELECT c.course_code, c.title FROM courses c "
                    "JOIN teachers t ON c.teacher_id = t.teacher_id "
                    "WHERE t.last_name = 'Chen';"
                ),
                "postgresql": (
                    "SELECT c.course_code, c.title FROM courses c "
                    "JOIN teachers t ON c.teacher_id = t.teacher_id "
                    "WHERE t.last_name = 'Chen';"
                ),
            },
        ),
        FewShotExample(
            question="What is the average grade per teacher?",
            sql={
                "sqlite": (
                    "SELECT t.first_name || ' ' || t.last_name AS teacher_name, "
                    "ROUND(AVG(e.grade), 2) AS avg_grade "
                    "FROM teachers t "
                    "JOIN courses c ON t.teacher_id = c.teacher_id "
                    "JOIN enrollments e ON c.course_id = e.course_id "
                    "WHERE e.status = 'completed' "
                    "GROUP BY t.teacher_id;"
                ),
                "postgresql": (
                    "SELECT CONCAT(t.first_name, ' ', t.last_name) AS teacher_name, "
                    "ROUND(AVG(e.grade), 2) AS avg_grade "
                    "FROM teachers t "
                    "JOIN courses c ON t.teacher_id = c.teacher_id "
                    "JOIN enrollments e ON c.course_id = e.course_id "
                    "WHERE e.status = 'completed' "
                    "GROUP BY t.teacher_id, t.first_name, t.last_name;"
                ),
            },
        ),
        FewShotExample(
            question="Which student has the highest average grade in each department?",
            sql={
                "sqlite": (
                    "WITH student_dept_avg AS ("
                    "SELECT s.student_id, s.first_name || ' ' || s.last_name AS student_name, "
                    "c.department, ROUND(AVG(e.grade), 2) AS avg_grade, "
                    "RANK() OVER (PARTITION BY c.department ORDER BY AVG(e.grade) DESC) AS rk "
                    "FROM students s "
                    "JOIN enrollments e ON s.student_id = e.student_id "
                    "JOIN courses c ON e.course_id = c.course_id "
                    "WHERE e.status = 'completed' "
                    "GROUP BY s.student_id, c.department) "
                    "SELECT department, student_name, avg_grade "
                    "FROM student_dept_avg WHERE rk = 1;"
                ),
                "postgresql": (
                    "WITH student_dept_avg AS ("
                    "SELECT s.student_id, CONCAT(s.first_name, ' ', s.last_name) AS student_name, "
                    "c.department, ROUND(AVG(e.grade), 2) AS avg_grade, "
                    "RANK() OVER (PARTITION BY c.department ORDER BY AVG(e.grade) DESC) AS rk "
                    "FROM students s "
                    "JOIN enrollments e ON s.student_id = e.student_id "
                    "JOIN courses c ON e.course_id = c.course_id "
                    "WHERE e.status = 'completed' "
                    "GROUP BY s.student_id, s.first_name, s.last_name, c.department) "
                    "SELECT department, student_name, avg_grade "
                    "FROM student_dept_avg WHERE rk = 1;"
                ),
            },
        ),
    ),

    correction_examples=(
        CorrectionExample(
            failed_sql="SELECT name FROM students;",
            error="no such column: name",
            corrected_sql={
                "sqlite": "SELECT first_name || ' ' || last_name AS name FROM students;",
                "postgresql": "SELECT CONCAT(first_name, ' ', last_name) AS name FROM students;",
            },
        ),
    ),

    answerable_topics=(
        "Students, teachers, and their information",
        "Courses and departments",
        "Enrollments, grades, and academic performance",
        "Semester and year-based academic data",
    ),

    fallback_decline=(
        "I can only answer questions about the university database, including students, "
        "teachers, courses, and enrollments. Please try asking about those topics!"
    ),

    cannot_answer_message=(
        "I can't answer that question with the information in the university database. "
        "Please ask about students, teachers, courses, enrollments, grades, or departments."
    ),
)

from prompts.domains import register_domain  # noqa: E402

register_domain("university", UNIVERSITY)
