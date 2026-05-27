# Example Queries & Outputs

Curated examples demonstrating all capability tiers of the University QA LangGraph Agent.
Each example shows the question, generated SQL, raw database result, formatted answer, and
full execution trace (as produced by `print_trace()`).

All examples are grounded in the deterministic seed data from `db/seed.py`:
6 teachers · 20 students · 12 courses · 52 enrollments across Fall 2024 / Spring 2025 / Summer 2025.

---

## Simple Queries

### Example 1: Student Count

**Question:** How many students are there?

**SQL Generated:**
```sql
SELECT COUNT(*) FROM students
```

**Raw Result:**
```
[{"COUNT(*)": 20}]
```

**Answer:** There are 20 students currently enrolled at the university.

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — SELECT COUNT(*) FROM students
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 1 rows
6. [format_answer] format_answer: answer formatted (57 chars)
```

---

### Example 2: All Teachers and Their Departments

**Question:** List all teachers and their departments.

**SQL Generated:**
```sql
SELECT first_name, last_name, department
FROM teachers
ORDER BY department, last_name
```

**Raw Result:**
```
[
  {"first_name": "Wei",    "last_name": "Chen",     "department": "Computer Science"},
  {"first_name": "Sarah",  "last_name": "Mitchell", "department": "Computer Science"},
  {"first_name": "David",  "last_name": "Okafor",   "department": "Mathematics"},
  {"first_name": "Priya",  "last_name": "Sharma",   "department": "Mathematics"},
  {"first_name": "Carlos", "last_name": "Rivera",   "department": "Physics"},
  {"first_name": "Emma",   "last_name": "Walsh",    "department": "English"}
]
```

**Answer:** The university has 6 teachers across 4 departments:

- **Computer Science**: Wei Chen, Sarah Mitchell
- **Mathematics**: David Okafor, Priya Sharma
- **Physics**: Carlos Rivera
- **English**: Emma Walsh

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — SELECT first_name, last_name, department FROM teachers ORDER BY departme
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 6 rows
6. [format_answer] format_answer: answer formatted (198 chars)
```

---

### Example 3: Courses Taught by Prof. Chen

**Question:** What courses does Prof. Chen teach?

**SQL Generated:**
```sql
SELECT c.course_code, c.title, c.credits
FROM courses c
JOIN teachers t ON c.teacher_id = t.id
WHERE t.last_name = 'Chen'
ORDER BY c.course_code
```

**Raw Result:**
```
[
  {"course_code": "CS101", "title": "Introduction to Programming", "credits": 3},
  {"course_code": "CS201", "title": "Data Structures",            "credits": 3},
  {"course_code": "CS301", "title": "Database Systems",           "credits": 3}
]
```

**Answer:** Prof. Wei Chen (Computer Science) teaches 3 courses:

1. **CS101** — Introduction to Programming (3 credits)
2. **CS201** — Data Structures (3 credits)
3. **CS301** — Database Systems (3 credits)

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — SELECT c.course_code, c.title, c.credits FROM courses c JOIN teachers t
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 3 rows
6. [format_answer] format_answer: answer formatted (197 chars)
```

---

## Medium Queries

### Example 4: Enrollment Count Per Course

**Question:** How many students are enrolled in each course?

**SQL Generated:**
```sql
SELECT c.course_code, c.title, COUNT(e.id) AS enrollment_count
FROM courses c
LEFT JOIN enrollments e ON c.id = e.course_id
GROUP BY c.course_code, c.title
ORDER BY enrollment_count DESC, c.course_code
```

**Raw Result:**
```
[
  {"course_code": "CS101",   "title": "Introduction to Programming",  "enrollment_count": 9},
  {"course_code": "CS301",   "title": "Database Systems",             "enrollment_count": 6},
  {"course_code": "MATH101", "title": "Calculus I",                   "enrollment_count": 6},
  {"course_code": "CS201",   "title": "Data Structures",              "enrollment_count": 5},
  {"course_code": "CS202",   "title": "Algorithms",                   "enrollment_count": 5},
  {"course_code": "MATH201", "title": "Linear Algebra",               "enrollment_count": 5},
  {"course_code": "PHYS101", "title": "Classical Mechanics",          "enrollment_count": 5},
  {"course_code": "CS401",   "title": "Machine Learning",             "enrollment_count": 3},
  {"course_code": "MATH301", "title": "Probability and Statistics",   "enrollment_count": 3},
  {"course_code": "PHYS201", "title": "Electromagnetism",             "enrollment_count": 3},
  {"course_code": "MATH202", "title": "Discrete Mathematics",         "enrollment_count": 2},
  {"course_code": "ENG101",  "title": "Academic Writing",             "enrollment_count": 0}
]
```

**Answer:** Here is the enrollment count across all 12 courses. CS101 (Introduction to Programming) leads with 9 students, followed by CS301 and MATH101 with 6 each. ENG101 (Academic Writing) is the only course with no enrollments. In total, 11 out of 12 courses have at least one enrolled student.

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — SELECT c.course_code, c.title, COUNT(e.id) AS enrollment_count FROM co
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 12 rows
6. [format_answer] format_answer: answer formatted (285 chars)
```

---

### Example 5: Students Enrolled in CS101 in Fall 2024

**Question:** Which students are enrolled in CS101 in Fall 2024?

**SQL Generated:**
```sql
SELECT s.first_name, s.last_name, s.major, e.grade, e.status
FROM students s
JOIN enrollments e ON s.id = e.student_id
JOIN courses c ON e.course_id = c.id
WHERE c.course_code = 'CS101'
  AND e.semester = 'Fall'
  AND e.year = 2024
ORDER BY s.last_name
```

**Raw Result:**
```
[
  {"first_name": "Alice",  "last_name": "Johnson", "major": "Computer Science", "grade": 92.0, "status": "completed"},
  {"first_name": "Bob",    "last_name": "Kim",     "major": "Computer Science", "grade": 78.5, "status": "completed"},
  {"first_name": "Grace",  "last_name": "Lee",     "major": "Mathematics",      "grade": 95.0, "status": "completed"},
  {"first_name": "Clara",  "last_name": "Nguyen",  "major": "Computer Science", "grade": 85.0, "status": "completed"},
  {"first_name": "Daniel", "last_name": "Park",    "major": "Computer Science", "grade": 70.0, "status": "completed"},
  {"first_name": "Liam",   "last_name": "Patel",   "major": "Physics",          "grade": 80.0, "status": "completed"},
  {"first_name": "Pedro",  "last_name": "Santos",  "major": "English",          "grade": 74.0, "status": "completed"},
  {"first_name": "Hiro",   "last_name": "Tanaka",  "major": "Mathematics",      "grade": 62.0, "status": "completed"},
  {"first_name": "Eva",    "last_name": "Torres",  "major": "Computer Science", "grade": 88.0, "status": "completed"}
]
```

**Answer:** 9 students were enrolled in CS101 (Introduction to Programming) during Fall 2024, and all have since completed the course. The class included students from four different majors. Grades ranged from 62.0 (Hiro Tanaka) to 95.0 (Grace Lee), with an average of 80.50.

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — SELECT s.first_name, s.last_name, s.major, e.grade, e.status FROM stud
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 9 rows
6. [format_answer] format_answer: answer formatted (316 chars)
```

---

### Example 6: Average Grade Per Course

**Question:** What is the average grade in each course?

**SQL Generated:**
```sql
SELECT c.course_code, c.title, ROUND(AVG(e.grade), 2) AS avg_grade
FROM courses c
JOIN enrollments e ON c.id = e.course_id
WHERE e.status = 'completed'
GROUP BY c.course_code, c.title
ORDER BY avg_grade DESC
```

> The `WHERE e.status = 'completed'` filter is critical: active and dropped enrollments have
> NULL grades and must be excluded to avoid skewing averages. MATH202 and ENG101 are omitted
> because they have no completed enrollments.

**Raw Result:**
```
[
  {"course_code": "CS401",   "title": "Machine Learning",                 "avg_grade": 91.5},
  {"course_code": "MATH301", "title": "Probability and Statistics",       "avg_grade": 87.5},
  {"course_code": "MATH101", "title": "Calculus I",                       "avg_grade": 85.0},
  {"course_code": "CS202",   "title": "Algorithms",                       "avg_grade": 84.0},
  {"course_code": "PHYS201", "title": "Electromagnetism",                 "avg_grade": 83.33},
  {"course_code": "CS201",   "title": "Data Structures",                  "avg_grade": 83.0},
  {"course_code": "MATH201", "title": "Linear Algebra",                   "avg_grade": 82.0},
  {"course_code": "CS301",   "title": "Database Systems",                 "avg_grade": 81.33},
  {"course_code": "CS101",   "title": "Introduction to Programming",      "avg_grade": 80.5},
  {"course_code": "PHYS101", "title": "Classical Mechanics",              "avg_grade": 79.5}
]
```

**Answer:** Here are the average grades for the 10 courses that have completed enrollments, ranked highest to lowest:

1. **CS401** Machine Learning — 91.50
2. **MATH301** Probability and Statistics — 87.50
3. **MATH101** Calculus I — 85.00
4. **CS202** Algorithms — 84.00
5. **PHYS201** Electromagnetism — 83.33
6. **CS201** Data Structures — 83.00
7. **MATH201** Linear Algebra — 82.00
8. **CS301** Database Systems — 81.33
9. **CS101** Introduction to Programming — 80.50
10. **PHYS101** Classical Mechanics — 79.50

MATH202 and ENG101 are excluded as they have no completed grades.

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — SELECT c.course_code, c.title, ROUND(AVG(e.grade), 2) AS avg_grade FRO
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 10 rows
6. [format_answer] format_answer: answer formatted (487 chars)
```

---

## Hard Queries

### Example 7: Average Grade Per Teacher

**Question:** What is the average grade per teacher?

**SQL Generated:**
```sql
SELECT t.first_name || ' ' || t.last_name AS teacher_name,
       t.department,
       ROUND(AVG(e.grade), 2) AS avg_grade,
       COUNT(e.id) AS grades_recorded
FROM teachers t
JOIN courses c ON t.id = c.teacher_id
JOIN enrollments e ON c.id = e.course_id
WHERE e.status = 'completed'
GROUP BY t.id, t.first_name, t.last_name, t.department
ORDER BY avg_grade DESC
```

> Three-table JOIN: teachers → courses → enrollments.
> Prof. Walsh is absent from results because ENG101 has zero enrollments.

**Raw Result:**
```
[
  {"teacher_name": "Priya Sharma",   "department": "Mathematics",      "avg_grade": 87.5,  "grades_recorded": 2},
  {"teacher_name": "Sarah Mitchell", "department": "Computer Science", "avg_grade": 86.5,  "grades_recorded": 6},
  {"teacher_name": "David Okafor",   "department": "Mathematics",      "avg_grade": 83.8,  "grades_recorded": 10},
  {"teacher_name": "Wei Chen",       "department": "Computer Science", "avg_grade": 81.38, "grades_recorded": 20},
  {"teacher_name": "Carlos Rivera",  "department": "Physics",          "avg_grade": 81.14, "grades_recorded": 7}
]
```

**Answer:** Here are the average grades per teacher across all completed enrollments:

1. **Priya Sharma** (Mathematics) — 87.50 avg (2 grades: MATH301 only; MATH202 has no completions)
2. **Sarah Mitchell** (Computer Science) — 86.50 avg (6 grades: CS401 and CS202)
3. **David Okafor** (Mathematics) — 83.80 avg (10 grades: MATH101 and MATH201)
4. **Wei Chen** (Computer Science) — 81.38 avg (20 grades: CS101, CS201, CS301 — most recorded)
5. **Carlos Rivera** (Physics) — 81.14 avg (7 grades: PHYS101 and PHYS201)

Emma Walsh is not listed as ENG101 has no completed enrollments.

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — SELECT t.first_name || ' ' || t.last_name AS teacher_name, t.departmen
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 5 rows
6. [format_answer] format_answer: answer formatted (512 chars)
```

---

### Example 8: Students With Grades Above 90

**Question:** Which students have grades above 90?

**SQL Generated:**
```sql
SELECT s.first_name || ' ' || s.last_name AS student_name,
       c.course_code,
       c.title,
       e.grade
FROM students s
JOIN enrollments e ON s.id = e.student_id
JOIN courses c ON e.course_id = c.id
WHERE e.grade > 90 AND e.status = 'completed'
ORDER BY e.grade DESC, student_name
```

**Raw Result:**
```
[
  {"student_name": "Alice Johnson", "course_code": "CS201",   "title": "Data Structures",                 "grade": 97.0},
  {"student_name": "Grace Lee",     "course_code": "MATH101", "title": "Calculus I",                      "grade": 96.0},
  {"student_name": "Alice Johnson", "course_code": "CS401",   "title": "Machine Learning",                "grade": 95.0},
  {"student_name": "Grace Lee",     "course_code": "CS101",   "title": "Introduction to Programming",     "grade": 95.0},
  {"student_name": "Alice Johnson", "course_code": "CS301",   "title": "Database Systems",               "grade": 94.0},
  {"student_name": "Grace Lee",     "course_code": "MATH201", "title": "Linear Algebra",                  "grade": 93.0},
  {"student_name": "Alice Johnson", "course_code": "CS101",   "title": "Introduction to Programming",     "grade": 92.0},
  {"student_name": "Noah Garcia",   "course_code": "PHYS101", "title": "Classical Mechanics",             "grade": 92.0},
  {"student_name": "Grace Lee",     "course_code": "MATH301", "title": "Probability and Statistics",      "grade": 91.0},
  {"student_name": "Grace Lee",     "course_code": "CS201",   "title": "Data Structures",                 "grade": 91.0}
]
```

**Answer:** 10 completed enrollments have a grade above 90, belonging to 3 distinct students:

- **Alice Johnson** — 4 high-grade courses: CS201 (97), CS401 (95), CS301 (94), CS101 (92)
- **Grace Lee** — 5 high-grade courses: MATH101 (96), CS101 (95), MATH201 (93), MATH301 (91), CS201 (91)
- **Noah Garcia** — 1 high-grade course: PHYS101 (92)

Note: grades of exactly 90 (Alice in CS202, Maya in MATH101) are not included as the query filters for strictly greater than 90.

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — SELECT s.first_name || ' ' || s.last_name AS student_name, c.course_co
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 10 rows
6. [format_answer] format_answer: answer formatted (443 chars)
```

---

### Example 9: Courses With More Than 4 Enrolled Students

**Question:** Which courses have more than 4 enrolled students?

**SQL Generated:**
```sql
SELECT c.course_code, c.title, COUNT(e.id) AS enrollment_count
FROM courses c
JOIN enrollments e ON c.id = e.course_id
GROUP BY c.course_code, c.title
HAVING COUNT(e.id) > 4
ORDER BY enrollment_count DESC, c.course_code
```

**Raw Result:**
```
[
  {"course_code": "CS101",   "title": "Introduction to Programming", "enrollment_count": 9},
  {"course_code": "CS301",   "title": "Database Systems",            "enrollment_count": 6},
  {"course_code": "MATH101", "title": "Calculus I",                  "enrollment_count": 6},
  {"course_code": "CS201",   "title": "Data Structures",             "enrollment_count": 5},
  {"course_code": "CS202",   "title": "Algorithms",                  "enrollment_count": 5},
  {"course_code": "MATH201", "title": "Linear Algebra",              "enrollment_count": 5},
  {"course_code": "PHYS101", "title": "Classical Mechanics",         "enrollment_count": 5}
]
```

**Answer:** 7 courses have more than 4 enrolled students (across all enrollment statuses):

1. **CS101** Introduction to Programming — 9 students
2. **CS301** Database Systems — 6 students
3. **MATH101** Calculus I — 6 students
4. **CS201** Data Structures — 5 students
5. **CS202** Algorithms — 5 students
6. **MATH201** Linear Algebra — 5 students
7. **PHYS101** Classical Mechanics — 5 students

The remaining 5 courses (CS401, MATH301, PHYS201, MATH202, ENG101) each have 3 or fewer enrollments.

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — SELECT c.course_code, c.title, COUNT(e.id) AS enrollment_count FROM co
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 7 rows
6. [format_answer] format_answer: answer formatted (394 chars)
```

---

## Very Hard Queries

### Example 10: Top Student Per Department (CTE + Window Function)

**Question:** Which student has the highest average grade in each department?

**SQL Generated:**
```sql
WITH student_avgs AS (
    SELECT s.id,
           s.first_name || ' ' || s.last_name AS student_name,
           s.major AS department,
           ROUND(AVG(e.grade), 2) AS avg_grade
    FROM students s
    JOIN enrollments e ON s.id = e.student_id
    WHERE e.status = 'completed'
    GROUP BY s.id, s.first_name, s.last_name, s.major
),
ranked AS (
    SELECT *,
           RANK() OVER (PARTITION BY department ORDER BY avg_grade DESC) AS rnk
    FROM student_avgs
)
SELECT student_name, department, avg_grade
FROM ranked
WHERE rnk = 1
ORDER BY department
```

> `RANK()` is used instead of `ROW_NUMBER()` so ties within a department both appear.
> Only students with at least one completed enrollment are included (4 of the 20 unenrolled
> or all-active/dropped students are naturally excluded by the JOIN).

**Raw Result:**
```
[
  {"student_name": "Alice Johnson", "department": "Computer Science", "avg_grade": 92.67},
  {"student_name": "Pedro Santos",  "department": "English",          "avg_grade": 74.0},
  {"student_name": "Grace Lee",     "department": "Mathematics",      "avg_grade": 92.17},
  {"student_name": "Noah Garcia",   "department": "Physics",          "avg_grade": 85.5}
]
```

**Answer:** The top-performing student in each department (by average grade across completed courses) is:

| Department       | Student        | Average Grade | Basis                        |
|------------------|----------------|---------------|------------------------------|
| Computer Science | Alice Johnson  | 92.67         | 6 completed courses          |
| English          | Pedro Santos   | 74.00         | 1 completed course (CS101)   |
| Mathematics      | Grace Lee      | 92.17         | 6 completed courses          |
| Physics          | Noah Garcia    | 85.50         | 2 completed courses          |

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — WITH student_avgs AS ( SELECT s.id, s.first_name || ' ' || s.last_name
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 4 rows
6. [format_answer] format_answer: answer formatted (368 chars)
```

---

## Special Scenarios

### Example 11: Retry Scenario — SQL Error and Auto-Correction

**Question:** What is Alice's GPA?

The first SQL attempt uses the non-existent column `name` instead of the correct
`first_name` / `last_name` columns. The agent detects the error, increments its retry
counter, and regenerates a corrected query on the second attempt.

**First SQL Attempt (contains bug):**
```sql
SELECT name, ROUND(AVG(grade), 2) AS gpa
FROM students s
JOIN enrollments e ON s.id = e.student_id
WHERE name = 'Alice'
  AND e.status = 'completed'
```

**Execution Error:**
```
no such column: name
```

**Corrected SQL (second attempt):**
```sql
SELECT s.first_name || ' ' || s.last_name AS student_name,
       ROUND(AVG(e.grade), 2) AS gpa
FROM students s
JOIN enrollments e ON s.id = e.student_id
WHERE s.first_name = 'Alice'
  AND e.status = 'completed'
GROUP BY s.id, s.first_name, s.last_name
```

**Raw Result:**
```
[{"student_name": "Alice Johnson", "gpa": 92.67}]
```

> Alice Johnson has 6 completed enrollments: CS101 (92), CS201 (97), MATH101 (88),
> CS301 (94), CS202 (90), CS401 (95) → average = 556 / 6 = **92.67**.

**Answer:** Alice Johnson has a GPA of **92.67** based on 6 completed courses — the highest among all Computer Science students.

**Execution Trace (10 steps — includes retry cycle):**
```
 1. [check_relevance]  check_relevance: relevant
 2. [fetch_schema]     fetch_schema: loaded schema (1432 chars)
 3. [generate_sql]     generate_sql: generated SQL — SELECT name, ROUND(AVG(grade), 2) AS gpa FROM students s JOIN enr
 4. [validate_sql]     validate_sql: query passed safety check
 5. [execute_sql]      execute_sql: FAILED — no such column: name
 6. [regenerate_sql]   regenerate_sql: preparing retry (attempt 1 of 3)
 7. [generate_sql]     generate_sql: regenerated SQL — SELECT s.first_name || ' ' || s.last_name AS student_name, ROUN
 8. [validate_sql]     validate_sql: query passed safety check
 9. [execute_sql]      execute_sql: returned 1 rows
10. [format_answer]    format_answer: answer formatted (97 chars)
```

---

### Example 12: Declined Question — Off-Topic Input

**Question:** What is the weather like today?

The agent's `check_relevance` node classifies this question as `not_relevant` (it contains
no reference to students, teachers, courses, or enrollments). The graph routes directly to
`polite_decline` without touching the database.

**SQL Generated:** *(none — graph exits before SQL generation)*

**Raw Result:** *(none)*

**Answer:** I'm sorry, but I can only answer questions about the university database — covering students, teachers, courses, and enrollments. For weather information, please check a dedicated weather service like weather.com.

**Execution Trace (2 steps):**
```
1. [check_relevance] check_relevance: not_relevant
2. [polite_decline]  polite_decline: declined off-topic question
```

---

### Example 13: Multi-Turn Conversation — Follow-Up Context

This example shows two consecutive questions within the same session. The second question
("What are their average grades?") uses the pronoun *their*, which would be ambiguous in
isolation. `ConversationManager` prepends the prior Q&A as context so the LLM can resolve
the reference to CS101 students.

---

**Turn 1**

**Question:** How many students are enrolled in CS101?

**SQL Generated:**
```sql
SELECT COUNT(*) AS enrollment_count
FROM enrollments e
JOIN courses c ON e.course_id = c.id
WHERE c.course_code = 'CS101'
```

**Raw Result:**
```
[{"enrollment_count": 9}]
```

**Answer:** There are 9 students enrolled in CS101 (Introduction to Programming).

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — SELECT COUNT(*) AS enrollment_count FROM enrollments e JOIN courses c O
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 1 rows
6. [format_answer] format_answer: answer formatted (63 chars)
```

---

**Turn 2**

**Original question:** What are their average grades?

**Augmented question sent to the graph** (built by `ConversationManager._build_contextual_question()`):
```
Previous conversation (for context only):
User asked: How many students are enrolled in CS101?
Answer: There are 9 students enrolled in CS101 (Introduction to Programming).

Current question: What are their average grades?
```

**SQL Generated** (LLM resolves "their" → CS101 students from context):
```sql
SELECT ROUND(AVG(e.grade), 2) AS avg_grade,
       MIN(e.grade) AS min_grade,
       MAX(e.grade) AS max_grade
FROM enrollments e
JOIN courses c ON e.course_id = c.id
WHERE c.course_code = 'CS101'
  AND e.status = 'completed'
```

**Raw Result:**
```
[{"avg_grade": 80.5, "min_grade": 62.0, "max_grade": 95.0}]
```

> CS101 Fall 2024 completed grades: 92, 78.5, 85, 70, 88, 95, 62, 80, 74 → sum = 724.5 / 9 = **80.50**

**Answer:** The 9 students in CS101 (Introduction to Programming) have an average grade of **80.50**, with a range from 62.0 (lowest) to 95.0 (highest). All 9 enrollments have the status `completed`.

**Execution Trace:**
```
1. [check_relevance] check_relevance: relevant
2. [fetch_schema] fetch_schema: loaded schema (1432 chars)
3. [generate_sql] generate_sql: generated SQL — SELECT ROUND(AVG(e.grade), 2) AS avg_grade, MIN(e.grade) AS min_grade,
4. [validate_sql] validate_sql: query passed safety check
5. [execute_sql] execute_sql: returned 1 rows
6. [format_answer] format_answer: answer formatted (185 chars)
```

---

## Summary

| # | Question (abbreviated)                        | Tier      | Nodes | Has Retry | Declined |
|---|-----------------------------------------------|-----------|-------|-----------|----------|
| 1 | How many students are there?                  | Simple    | 6     | No        | No       |
| 2 | List all teachers and their departments       | Simple    | 6     | No        | No       |
| 3 | What courses does Prof. Chen teach?           | Simple    | 6     | No        | No       |
| 4 | Enrollment count per course                   | Medium    | 6     | No        | No       |
| 5 | Students in CS101 Fall 2024                   | Medium    | 6     | No        | No       |
| 6 | Average grade per course                      | Medium    | 6     | No        | No       |
| 7 | Average grade per teacher                     | Hard      | 6     | No        | No       |
| 8 | Students with grades above 90                 | Hard      | 6     | No        | No       |
| 9 | Courses with more than 4 enrolled students    | Hard      | 6     | No        | No       |
|10 | Top student per department (CTE + RANK())     | Very Hard | 6     | No        | No       |
|11 | Alice's GPA (retry on wrong column name)      | Special   | 10    | **Yes**   | No       |
|12 | What is the weather like today?               | Special   | 2     | No        | **Yes**  |
|13 | CS101 enrollment + follow-up average grades   | Special   | 6+6   | No        | No       |
