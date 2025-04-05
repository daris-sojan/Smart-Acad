from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
import sqlite3
import os
from datetime import date, datetime, timedelta
import ollama
import json
import re
from ktu_question_generator import ktu_question_bp
from ktu_summary_generator import ktu_summary_bp


app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

def regex_search(value, pattern):
    """Custom filter for regex search in templates"""
    match = re.search(pattern, value)
    return match.groups() if match else None

app.jinja_env.filters['regex_search'] = regex_search

app.register_blueprint(ktu_question_bp)
app.register_blueprint(ktu_summary_bp)

def get_db_connection():
    """Create a connection to the SQLite database"""
    db_path = os.path.join(os.path.dirname(__file__), 'db.sqlite3')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

# LOGIN ROUTES
@app.route('/')
def index():
    """Home page route"""
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login route to handle user authentication"""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM info_user WHERE username = ?', (username,)).fetchone()
        
        if user:
            # Check if the user is a student
            student = conn.execute('SELECT * FROM info_student WHERE user_id = ?', (user['id'],)).fetchone()
            
            # Check if the user is a teacher
            teacher = conn.execute('SELECT * FROM info_teacher WHERE user_id = ?', (user['id'],)).fetchone()
            
            # Simplified password check - replace with proper hashing in production
            if user['password'] == password:
                # Store user info in session
                session['user_id'] = user['id']
                session['username'] = user['username']
                
                # Redirect based on user type
                if student:
                    session['user_type'] = 'student'
                    session['student_usn'] = student['USN']
                    conn.close()
                    return redirect(url_for('student_dashboard'))
                elif teacher:
                    session['user_type'] = 'teacher'
                    session['teacher_id'] = teacher['id']
                    conn.close()
                    return redirect(url_for('teacher_dashboard'))
                else:
                    conn.close()
                    return "User not found in student or teacher tables"
            else:
                conn.close()
                return "Invalid password"
        
        conn.close()
        return "User not found"
    
    return render_template('login.html')

@app.route('/student/dashboard')
def student_dashboard():
    """Student dashboard route"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    # Fetch student details
    student = conn.execute('''
        SELECT s.*, c.section, c.sem, d.name as dept_name 
        FROM info_student s
        JOIN info_class c ON s.class_id_id = c.id
        JOIN info_dept d ON c.dept_id = d.id
        WHERE s.USN = ?
    ''', (session['student_usn'],)).fetchone()
    
    # Fetch student's courses
    courses = conn.execute('''
        SELECT c.name, c.shortname 
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        WHERE sc.student_id = ?
    ''', (session['student_usn'],)).fetchall()
    
    conn.close()
    
    return render_template('student_dashboard.html', 
                           student=student, 
                           courses=courses)

@app.route('/teacher/dashboard')
def teacher_dashboard():
    """Teacher dashboard route"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    # Fetch teacher details
    teacher = conn.execute('''
        SELECT t.*, d.name as dept_name 
        FROM info_teacher t
        JOIN info_dept d ON t.dept_id = d.id
        WHERE t.id = ?
    ''', (session['teacher_id'],)).fetchone()
    
    # Fetch teacher's assigned courses
    assigned_courses = conn.execute('''
        SELECT c.name, c.shortname, cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    conn.close()
    
    return render_template('teacher_dashboard.html', 
                           teacher=teacher, 
                           assigned_courses=assigned_courses)

@app.route('/logout')
def logout():
    """Logout route to clear session"""
    session.clear()
    return redirect(url_for('index'))

# ATTENDANCE ROUTES
@app.route('/teacher/mark-attendance', methods=['GET', 'POST'])
def mark_attendance():
    """Route for teachers to mark attendance"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's assigned classes and courses
    assigned_courses = conn.execute('''
        SELECT a.id as assign_id, c.name as course_name, cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    if request.method == 'POST':
        assign_id = request.form['assign_id']
        attendance_date = request.form['attendance_date']
        
        # Get all students for this class
        students = conn.execute('''
            SELECT s.USN 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            JOIN info_assign a ON c.id = a.class_id_id
            WHERE a.id = ?
        ''', (assign_id,)).fetchall()
        
        # Get list of present students (those explicitly checked)
        present_students = request.form.getlist('students')
        
        try:
            # Begin transaction
            conn.execute('BEGIN')
            
            # Create an attendance class record
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO info_attendanceclass (date, status, assign_id) 
                VALUES (?, 1, ?)
            ''', (attendance_date, assign_id))
            attendanceclass_id = cursor.lastrowid
            
            # Fetch course details for the selected assignment
            course_details = conn.execute('''
                SELECT c.id as course_id, cl.section 
                FROM info_assign a
                JOIN info_course c ON a.course_id = c.id
                JOIN info_class cl ON a.class_id_id = cl.id
                WHERE a.id = ?
            ''', (assign_id,)).fetchone()
            
            # Mark attendance for ALL students
            for student in students:
                # Check if this student is in the present_students list
                status = 1 if student['USN'] in present_students else 0
                
                conn.execute('''
                    INSERT INTO info_attendance 
                    (date, status, attendanceclass_id, course_id, student_id) 
                    VALUES (?, ?, ?, ?, ?)
                ''', (attendance_date, status, attendanceclass_id, course_details['course_id'], student['USN']))
            
            # Commit transaction
            conn.commit()
            flash('Attendance marked successfully!', 'success')
            return redirect(url_for('mark_attendance'))
        
        except Exception as e:
            conn.rollback()
            flash(f'Error marking attendance: {str(e)}', 'error')
    
    # Fetch students for the first assigned course/class
    students = []
    if assigned_courses:
        first_assign = assigned_courses[0]
        students = conn.execute('''
            SELECT s.USN, s.name 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            WHERE c.section = ? AND c.sem = ?
            ORDER BY s.name
        ''', (first_assign['section'], first_assign['sem'])).fetchall()
    
    conn.close()
    
    return render_template('mark_attendance.html', 
                           assigned_courses=assigned_courses, 
                           students=students)
@app.route('/teacher/view-attendance', methods=['GET', 'POST'])
def view_attendance():
    """Route for teachers to view attendance records"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's assigned courses
    assigned_courses = conn.execute('''
        SELECT a.id as assign_id, c.name as course_name, cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    attendance_records = []
    selected_course = None
    
    if request.method == 'POST':
        assign_id = request.form['assign_id']
        
        # Fetch course details
        course_details = conn.execute('''
            SELECT c.id as course_id, c.name as course_name, 
                   cl.section, cl.sem 
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            JOIN info_class cl ON a.class_id_id = cl.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        # Fetch attendance records
        attendance_records = conn.execute('''
            SELECT s.USN, s.name, 
                   COUNT(CASE WHEN a.status = 1 THEN 1 END) as present_days,
                   COUNT(*) as total_days,
                   ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(*), 2) as attendance_percentage
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            LEFT JOIN info_attendance a ON s.USN = a.student_id
            LEFT JOIN info_attendanceclass ac ON a.attendanceclass_id = ac.id
            LEFT JOIN info_assign assign ON ac.assign_id = assign.id
            WHERE c.section = ? AND c.sem = ? AND assign.id = ?
            GROUP BY s.USN, s.name
            ORDER BY s.name
        ''', (course_details['section'], course_details['sem'], assign_id)).fetchall()
        
        selected_course = course_details
    
    conn.close()
    
    return render_template('view_attendance.html', 
                           assigned_courses=assigned_courses, 
                           attendance_records=attendance_records,
                           selected_course=selected_course)

@app.route('/student/attendance')
def student_attendance():
    """Route for students to view their attendance"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch all courses for the student
    all_courses = conn.execute('''
        SELECT c.id as course_id, c.name as course_name
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        WHERE sc.student_id = ?
    ''', (session['student_usn'],)).fetchall()
    
    # Fetch student's course-wise attendance
    attendance_records = conn.execute('''
        SELECT c.id as course_id, 
               c.name as course_name, 
               COALESCE(COUNT(CASE WHEN a.status = 1 THEN 1 END), 0) as present_days,
               COALESCE(COUNT(a.id), 0) as total_days,
               COALESCE(ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(a.id), 2), 0) as attendance_percentage
        FROM info_course c
        LEFT JOIN info_attendance a ON c.id = a.course_id AND a.student_id = ?
        WHERE c.id IN (
            SELECT course_id 
            FROM info_studentcourse 
            WHERE student_id = ?
        )
        GROUP BY c.id, c.name
        ORDER BY c.name
    ''', (session['student_usn'], session['student_usn'])).fetchall()
    
    # Fetch detailed attendance record
    detailed_attendance = conn.execute('''
        SELECT c.name as course_name, 
               a.date, 
               a.status
        FROM info_attendance a
        JOIN info_course c ON a.course_id = c.id
        WHERE a.student_id = ?
        ORDER BY a.date DESC
    ''', (session['student_usn'],)).fetchall()
    
    conn.close()
    
    return render_template('student_attendance.html', 
                           attendance_records=attendance_records,
                           detailed_attendance=detailed_attendance)
@app.route('/teacher/mark-attendance/get-students', methods=['GET'])
def get_students_for_course():
    """Route to fetch students for a specific course and class"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return jsonify([]), 403
    
    assign_id = request.args.get('assign_id')
    
    if not assign_id:
        return jsonify([]), 400
    
    conn = get_db_connection()
    
    try:
        # Fetch course and class details for the assignment
        course_details = conn.execute('''
            SELECT c.section, c.sem
            FROM info_assign a
            JOIN info_class c ON a.class_id_id = c.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        if not course_details:
            return jsonify([]), 404
        
        # Fetch students for the specific section and semester
        students = conn.execute('''
            SELECT USN, name 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            WHERE c.section = ? AND c.sem = ?
            ORDER BY s.name
        ''', (course_details['section'], course_details['sem'])).fetchall()
        
        # Convert to list of dictionaries
        student_list = [dict(student) for student in students]
        
        return jsonify(student_list)
    
    except Exception as e:
        print(f"Error fetching students: {e}")
        return jsonify([]), 500
    finally:
        conn.close()
@app.route('/student/timetable')
def student_timetable():
    """Route for students to view their timetable"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch student's class details
    student_class = conn.execute('''
        SELECT s.class_id_id, c.section, c.sem 
        FROM info_student s
        JOIN info_class c ON s.class_id_id = c.id
        WHERE s.USN = ?
    ''', (session['student_usn'],)).fetchone()
    
    # Fetch timetable for the student's class
    timetable = conn.execute('''
        SELECT 
            at.day, 
            at.period, 
            c.name as course_name, 
            t.name as teacher_name
        FROM info_assigntime at
        JOIN info_assign a ON at.assign_id = a.id
        JOIN info_course c ON a.course_id = c.id
        JOIN info_teacher t ON a.teacher_id = t.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE cl.id = ?
        ORDER BY 
            CASE at.day 
                WHEN 'Monday' THEN 1 
                WHEN 'Tuesday' THEN 2 
                WHEN 'Wednesday' THEN 3 
                WHEN 'Thursday' THEN 4 
                WHEN 'Friday' THEN 5 
                WHEN 'Saturday' THEN 6 
                ELSE 7 
            END,
            at.period
    ''', (student_class['class_id_id'],)).fetchall()
    
    conn.close()
    
    # Organize timetable by day
    organized_timetable = {}
    for row in timetable:
        if row['day'] not in organized_timetable:
            organized_timetable[row['day']] = []
        organized_timetable[row['day']].append(row)
    
    return render_template('student_timetable.html', 
                           timetable=organized_timetable,
                           student_class=student_class)

@app.route('/teacher/timetable')
def teacher_timetable():
    """Route for teachers to view their timetable"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's timetable
    timetable = conn.execute('''
        SELECT 
            at.day, 
            at.period, 
            c.name as course_name, 
            cl.section,
            cl.sem
        FROM info_assigntime at
        JOIN info_assign a ON at.assign_id = a.id
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
        ORDER BY 
            CASE at.day 
                WHEN 'Monday' THEN 1 
                WHEN 'Tuesday' THEN 2 
                WHEN 'Wednesday' THEN 3 
                WHEN 'Thursday' THEN 4 
                WHEN 'Friday' THEN 5 
                WHEN 'Saturday' THEN 6 
                ELSE 7 
            END,
            at.period
    ''', (session['teacher_id'],)).fetchall()
    
    conn.close()
    
    # Organize timetable by day
    organized_timetable = {}
    for row in timetable:
        if row['day'] not in organized_timetable:
            organized_timetable[row['day']] = []
        organized_timetable[row['day']].append(row)
    
    return render_template('teacher_timetable.html', 
                           timetable=organized_timetable)

@app.route('/teacher/enter-marks', methods=['GET', 'POST'])
def enter_marks():
    """Route for teachers to enter marks"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's assigned courses
    assigned_courses = conn.execute('''
        SELECT a.id as assign_id, c.name as course_name, 
               cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    students = []
    selected_course = None
    
    if request.method == 'POST':
        assign_id = request.form['assign_id']
        marks_name = request.form['marks_name']
        
        # Fetch students for this course/class
        students = conn.execute('''
            SELECT s.USN, s.name 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            JOIN info_assign a ON c.id = a.class_id_id
            WHERE a.id = ?
            ORDER BY s.name
        ''', (assign_id,)).fetchall()
        
        # Get course details
        selected_course = conn.execute('''
            SELECT c.id as course_id, c.name as course_name, 
                   cl.section, cl.sem 
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            JOIN info_class cl ON a.class_id_id = cl.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
    conn.close()
    
    return render_template('enter_marks.html', 
                           assigned_courses=assigned_courses, 
                           students=students,
                           selected_course=selected_course)

@app.route('/teacher/get-existing-marks', methods=['GET'])
def get_existing_marks():
    """Route to fetch existing marks for a specific course and assessment type"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return jsonify([]), 403
    
    assign_id = request.args.get('assign_id')
    marks_name = request.args.get('marks_name')
    
    if not assign_id or not marks_name:
        return jsonify([]), 400
    
    conn = get_db_connection()
    
    try:
        # Get the course_id for this assignment
        course_details = conn.execute('''
            SELECT c.id as course_id
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        if not course_details:
            return jsonify([]), 404
        
        # Fetch existing marks for this course and assessment type
        existing_marks = conn.execute('''
            SELECT s.USN as student_usn, 
                   m.marks1 as marks
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            JOIN info_assign a ON c.id = a.class_id_id
            LEFT JOIN info_studentcourse sc ON (sc.student_id = s.USN AND sc.course_id = ?)
            LEFT JOIN info_marks m ON (m.studentcourse_id = sc.id AND m.name = ?)
            WHERE a.id = ?
            ORDER BY s.name
        ''', (course_details['course_id'], marks_name, assign_id)).fetchall()
        
        # Convert to list of dictionaries
        marks_list = [dict(mark) for mark in existing_marks]
        
        return jsonify(marks_list)
    
    except Exception as e:
        print(f"Error fetching existing marks: {e}")
        return jsonify([]), 500
    finally:
        conn.close()

@app.route('/teacher/save-marks', methods=['POST'])
def save_marks():
    """Route to save marks for students with enhanced debugging"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    try:
        course_id = request.form.get('course_id')
        marks_name = request.form.get('marks_name')
        
        print(f"Saving marks for course_id: {course_id}, marks_name: {marks_name}")
        print(f"Form data received: {request.form}")
        
        if not course_id or not marks_name:
            flash('Missing required fields', 'error')
            return redirect(url_for('enter_marks'))
        
        # Begin transaction
        conn.execute('BEGIN')
        
        # Process each student's marks
        for key, value in request.form.items():
            if key.startswith('marks_'):
                usn = key.split('_')[1]
                marks = value or '0'
                
                print(f"Processing marks for USN: {usn}, Marks: {marks}")
                
                # Find or create studentcourse record
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO info_studentcourse (course_id, student_id)
                    VALUES (?, ?)
                ''', (course_id, usn))
                
                # Get studentcourse_id
                studentcourse = conn.execute('''
                    SELECT id FROM info_studentcourse 
                    WHERE course_id = ? AND student_id = ?
                ''', (course_id, usn)).fetchone()
                
                if not studentcourse:
                    raise ValueError(f"Failed to get studentcourse_id for USN: {usn}")
                
                # Insert or update marks
                cursor.execute('''
                    INSERT OR REPLACE INTO info_marks 
                    (name, marks1, studentcourse_id) 
                    VALUES (?, ?, ?)
                ''', (marks_name, marks, studentcourse['id']))
                
                print(f"Successfully saved marks for USN {usn}")
        
        # Commit transaction
        conn.commit()
        flash('Marks saved successfully!', 'success')
        return redirect(url_for('enter_marks'))
    
    except Exception as e:
        conn.rollback()
        print(f"Error saving marks: {str(e)}")
        flash(f'Error saving marks: {str(e)}', 'error')
        return redirect(url_for('enter_marks'))
    finally:
        conn.close()
    
    

@app.route('/student/marks')
def student_marks():
    """Route for students to view their marks"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch all courses for the student
    all_courses = conn.execute('''
        SELECT DISTINCT c.id as course_id, c.name as course_name
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        WHERE sc.student_id = ?
    ''', (session['student_usn'],)).fetchall()
    
    # Fetch student's marks for all courses
    marks_records = conn.execute('''
        SELECT 
            c.id as course_id,
            c.name as course_name, 
            m.name as marks_name,
            m.marks1 as marks
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        JOIN info_marks m ON sc.id = m.studentcourse_id
        WHERE sc.student_id = ?
        ORDER BY c.name, m.name
    ''', (session['student_usn'],)).fetchall()
    
    conn.close()
    
    return render_template('student_marks.html', 
                           marks_records=marks_records,
                           all_courses=all_courses)
@app.route('/teacher/get-course-details', methods=['GET'])
def get_course_details():
    """Route to fetch course details for a specific assignment"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return jsonify({}), 403
    
    assign_id = request.args.get('assign_id')
    
    if not assign_id:
        return jsonify({}), 400
    
    conn = get_db_connection()
    
    try:
        # Fetch course details for this assignment
        course_details = conn.execute('''
            SELECT c.id as course_id
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        if not course_details:
            return jsonify({}), 404
        
        return jsonify(dict(course_details))
    
    except Exception as e:
        print(f"Error fetching course details: {e}")
        return jsonify({}), 500
    finally:
        conn.close()

# Chat Routes
@app.route('/chat')
def chat_interface():
    """Chat interface route"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('chat.html')

@app.route('/chat/send', methods=['POST'])
def chat_send():
    """Handle chat messages with AI integration and database access"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    user_message = request.json.get('message', '').strip()
    if not user_message:
        return jsonify({'error': 'No message provided'}), 400
    
    try:
        conn = get_db_connection()
        
        # Get user context from session and database
        user_context = {
            'id': session.get('user_id'),
            'username': session.get('username'),
            'type': session.get('user_type'),
            'name': None,
            'usn': None,
            'teacher_id': None,
            'dept': None,
            'semester': None,
            'section': None
        }
        
        if user_context['type'] == 'student':
            # Get student details
            student = conn.execute('''
                SELECT s.*, c.section, c.sem, d.name as dept_name 
                FROM info_student s
                JOIN info_class c ON s.class_id_id = c.id
                JOIN info_dept d ON c.dept_id = d.id
                WHERE s.USN = ?
            ''', (session['student_usn'],)).fetchone()
            
            if student:
                user_context.update({
                    'usn': student['USN'],
                    'name': student['name'],
                    'dept': student['dept_name'],
                    'semester': student['sem'],
                    'section': student['section']
                })
                
        elif user_context['type'] == 'teacher':
            # Get teacher details
            teacher = conn.execute('''
                SELECT t.*, d.name as dept_name 
                FROM info_teacher t
                JOIN info_dept d ON t.dept_id = d.id
                WHERE t.id = ?
            ''', (session['teacher_id'],)).fetchone()
            
            if teacher:
                user_context.update({
                    'teacher_id': teacher['id'],
                    'name': teacher['name'],
                    'dept': teacher['dept_name']
                })

        if not user_context['name']:
            conn.close()
            return jsonify({'error': 'Could not retrieve user information'}), 400

        # Initialize conversation history if not exists
        if 'chat_history' not in session:
            session['chat_history'] = []
        
        # Add user message to history
        session['chat_history'].append({'role': 'user', 'content': user_message})
        
        # Keep only last 10 messages for context
        if len(session['chat_history']) > 10:
            session['chat_history'] = session['chat_history'][-10:]

        # Analysis prompt without conversation history for academic queries
        analysis_prompt = f"""
            Analyze the following user query in detail:

            Query: {user_message}
            User Type: {user_context['type']}

            Determine:
            1. Query type (timetable/attendance/marks/general)
            2. Request type (query/modify)
            3. For marks query:
            - Is a specific course/subject mentioned? Extract exact course name
            - Assessment type if mentioned
            4. For marks modification:
            - Student USN (format: CS## or similar)
            - Marks value (numeric)
            - Course/Subject name
            - Assessment type (test/quiz/assignment/internal/etc)
            5. For attendance modification:
                - Student USN
                - Status (present/absent)
                - Course name
                - Date information:
                    * For "today" → date_type: "today"
                    * For "yesterday" → date_type: "yesterday"
                    * For specific dates → specific_date in YYYY-MM-DD format
                    * If no date mentioned → date_type: "today"
            6. For timetable queries:
            - Is a specific day mentioned? Extract exact day name
            - Check for phrases like "timetable for [day]" or "[day] timetable"
            - Normalize day names (e.g., "monday", "Monday", "mon" → "Monday")

            Consider these examples for timetable queries:
            - "monday timetable" → day: "Monday"
            - "timetable for monday" → day: "Monday"
            - "show me monday's timetable" → day: "Monday"
            - "what classes do I have on monday" → day: "Monday"
            - "show my timetable" → day: null (show full week)

            Respond in JSON format with keys:
            - query_type: string
            - request_type: string
            - course_name: string or null (exact course name if mentioned)
            - assessment_type: string or null
            - student_usn: string or null
            - marks_value: number or null
            - attendance_status: string or null
            - date_type: string or null
            - specific_date: string or null
            - day: string or null (capitalized day name if mentioned)
            """

        # Get query analysis
        analysis_response = ollama.generate(
            model='llama3',
            prompt=analysis_prompt,
            format='json'
        )
        analysis = json.loads(analysis_response['response'])

        # Handle different types of queries
        if analysis.get("query_type") == "marks" and analysis.get("request_type") == "modify":
            if user_context['type'] != 'teacher':
                response = "Only teachers can enter marks."
            else:
                response = handle_marks_query(user_context, user_message, analysis)
        elif analysis.get("query_type") == "attendance" and analysis.get("request_type") == "modify":
            if user_context['type'] != 'teacher':
                response = "Only teachers can mark attendance."
            else:
                response = handle_attendance_query(user_context, user_message, analysis)
        elif analysis.get("query_type") == "timetable":
            response = handle_timetable_query(user_context, user_message, analysis)
        elif analysis.get("query_type") == "attendance" and analysis.get("request_type") == "query":
            response = handle_attendance_query(user_context, user_message, analysis)
        elif analysis.get("query_type") == "marks" and analysis.get("request_type") == "query":
            response = handle_marks_query(user_context, user_message, analysis)
        else:
            # Only include conversation history for general queries
            system_prompt = f"""
            You are an AI Academic Assistant for a university system. 
            Current User: {user_context['name']} ({user_context['type'].title()})
            Department: {user_context.get('dept', 'N/A')}
            {f"Semester: {user_context['semester']}, Section: {user_context['section']}" if user_context['type'] == 'student' else ""}
            
            Previous conversation:
            {format_chat_history(session['chat_history'][:-1])}
            
            Provide responses based only on actual information available.
            Do not generate fictional data.
            Keep responses concise and relevant to academic queries.
            Use emojis appropriately to enhance readability.
            """
            
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message}
            ]
            
            ollama_response = ollama.chat(
                model='llama3',
                messages=messages
            )
            response = ollama_response['message']['content']

        # Add response to history
        session['chat_history'].append({'role': 'assistant', 'content': response})
        session.modified = True

        conn.close()
        return jsonify({'response': response})

    except json.JSONDecodeError:
        if 'conn' in locals():
            conn.close()
        return jsonify({'error': 'Failed to parse AI response'}), 500
    except Exception as e:
        if 'conn' in locals():
            conn.close()
        return jsonify({'error': str(e)}), 500

def format_chat_history(history):
    """Format chat history for prompt context"""
    if not history:
        return "No previous conversation."
    
    formatted = []
    for msg in history:
        role = "User" if msg['role'] == 'user' else "Assistant"
        formatted.append(f"{role}: {msg['content']}")
    
    # Only include last 5 messages if history is too long
    if len(formatted) > 5:
        formatted = formatted[-5:]
        formatted.insert(0, "... (earlier conversation omitted)")
    
    return "\n".join(formatted)
@app.route('/chat/stream')
def chat_stream():
    """Server-Sent Events route for streaming chat responses"""
    if 'user_id' not in session:
        return Response("Not authenticated", status=401)
    
    def generate():
        conn = get_db_connection()
        
        # Get user context
        user_context = ""
        if session.get('user_type') == 'student':
            student = conn.execute('SELECT * FROM info_student WHERE USN = ?', (session['student_usn'],)).fetchone()
            if student:
                user_context = f"Student {student['name']} (USN: {student['USN']})"
        elif session.get('user_type') == 'teacher':
            teacher = conn.execute('SELECT * FROM info_teacher WHERE id = ?', (session['teacher_id'],)).fetchone()
            if teacher:
                user_context = f"Teacher {teacher['name']}"
        
        conn.close()
        
        user_message = request.args.get('message', '').strip()
        if not user_message:
            yield "data: No message provided\n\n"
            return
        
        system_prompt = f"""
        You are an AI Academic Assistant for {user_context}.
        For academic queries, provide accurate information.
        For general questions, be helpful and informative.
        Format responses clearly with appropriate emojis.
        """
        
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_message}
        ]
        
        stream = ollama.chat(
            model='llama3',
            messages=messages,
            stream=True
        )
        
        for chunk in stream:
            if 'message' in chunk and 'content' in chunk['message']:
                yield f"data: {chunk['message']['content']}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')

# Helper functions for chat routes
def handle_attendance_query(user_details, user_message, analysis):
    """Handle attendance-related queries"""
    conn = get_db_connection()
    response = ""
    
    try:
        if analysis.get('request_type') == 'modify':
            if user_details['type'] != 'teacher':
                return "Only teachers can mark attendance."

            student_usn = analysis.get('student_usn')
            status = 1 if analysis.get('attendance_status') == 'present' else 0
            course_name = analysis.get('course_name')
            
            # Fixed date handling
            current_date = date.today()
            if analysis.get('date_type') == 'yesterday':
                date_str = (current_date - timedelta(days=1)).strftime('%Y-%m-%d')
            elif analysis.get('date_type') == 'today':
                date_str = current_date.strftime('%Y-%m-%d')
            elif analysis.get('specific_date'):
                try:
                    # Parse and validate the specific date
                    specific_date = datetime.strptime(analysis.get('specific_date'), '%Y-%m-%d').date()
                    date_str = specific_date.strftime('%Y-%m-%d')
                except ValueError:
                    return "Invalid date format. Please use YYYY-MM-DD format."
            else:
                # If no date specified, use current date
                date_str = current_date.strftime('%Y-%m-%d')

            if not all([student_usn, date_str, course_name]):
                return "Please provide student USN, date, and course information."

            try:
                # Get course ID and verify teacher's assignment
                course_info = conn.execute('''
                    SELECT a.id as assign_id, c.id as course_id
                    FROM info_assign a
                    JOIN info_course c ON a.course_id = c.id
                    WHERE a.teacher_id = ? AND c.name = ?
                ''', (user_details['teacher_id'], course_name)).fetchone()

                if not course_info:
                    return f"You are not assigned to the course: {course_name}"

                # Verify student exists
                student = conn.execute('''
                    SELECT 1 FROM info_student WHERE USN = ?
                ''', (student_usn,)).fetchone()

                if not student:
                    return f"Student with USN {student_usn} not found."

                # Start transaction
                conn.execute('BEGIN')

                # Check/Create attendance class
                attendance_class = conn.execute('''
                    SELECT id FROM info_attendanceclass
                    WHERE date = ? AND assign_id = ?
                ''', (date_str, course_info['assign_id'])).fetchone()

                if not attendance_class:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO info_attendanceclass (date, status, assign_id)
                        VALUES (?, 1, ?)
                    ''', (date_str, course_info['assign_id']))
                    attendanceclass_id = cursor.lastrowid
                else:
                    attendanceclass_id = attendance_class['id']

                # Update or insert attendance record
                existing = conn.execute('''
                    SELECT id FROM info_attendance
                    WHERE date = ? AND student_id = ? AND course_id = ?
                ''', (date_str, student_usn, course_info['course_id'])).fetchone()

                if existing:
                    conn.execute('''
                        UPDATE info_attendance
                        SET status = ?, attendanceclass_id = ?
                        WHERE id = ?
                    ''', (status, attendanceclass_id, existing['id']))
                else:
                    conn.execute('''
                        INSERT INTO info_attendance
                        (date, status, attendanceclass_id, course_id, student_id)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (date_str, status, attendanceclass_id, course_info['course_id'], student_usn))

                conn.commit()
                status_word = "present" if status == 1 else "absent"
                formatted_date = datetime.strptime(date_str, '%Y-%m-%d').strftime('%d-%m-%Y')
                response = f"✅ Successfully marked {student_usn} as {status_word} for {course_name} on {formatted_date}"

            except Exception as e:
                conn.rollback()
                response = f"❌ Error marking attendance: {str(e)}"
                
        else:
            if user_details['type'] == "student":
                course_name = analysis.get('course_name')
                
                if course_name:
                    # Get attendance for specific course
                    attendance_records = conn.execute('''
                        SELECT c.name as course_name,
                               COUNT(CASE WHEN a.status = 1 THEN 1 END) as present_days,
                               COUNT(*) as total_days,
                               ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(*), 2) as attendance_percentage
                        FROM info_course c
                        LEFT JOIN info_attendance a ON c.id = a.course_id AND a.student_id = ?
                        WHERE c.name = ? AND c.id IN (
                            SELECT course_id 
                            FROM info_studentcourse 
                            WHERE student_id = ?
                        )
                        GROUP BY c.id, c.name
                    ''', (user_details['usn'], course_name, user_details['usn'])).fetchall()

                    if attendance_records:
                        response = f"📊 Your attendance for {course_name}:\n\n"
                        for record in attendance_records:
                            response += f"✅ Present: {record['present_days']} days\n"
                            response += f"📅 Total Classes: {record['total_days']} days\n"
                            response += f"📈 Attendance: {record['attendance_percentage']}%\n"
                            
                            # Get detailed attendance records
                            detailed = conn.execute('''
                                SELECT a.date, a.status
                                FROM info_attendance a
                                JOIN info_course c ON a.course_id = c.id
                                WHERE a.student_id = ? AND c.name = ?
                                ORDER BY a.date DESC
                            ''', (user_details['usn'], course_name)).fetchall()
                            
                            if detailed:
                                response += "\nDetailed attendance:\n"
                                for detail in detailed:
                                    status = "Present ✅" if detail['status'] == 1 else "Absent ❌"
                                    formatted_date = datetime.strptime(detail['date'], '%Y-%m-%d').strftime('%d-%m-%Y')
                                    response += f"📅 {formatted_date}: {status}\n"
                    else:
                        response = f"No attendance records found for {course_name}."
                else:
                    # Show all courses attendance if no specific course mentioned
                    attendance_records = conn.execute('''
                        SELECT c.name as course_name,
                               COUNT(CASE WHEN a.status = 1 THEN 1 END) as present_days,
                               COUNT(*) as total_days,
                               ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(*), 2) as attendance_percentage
                        FROM info_course c
                        LEFT JOIN info_attendance a ON c.id = a.course_id AND a.student_id = ?
                        WHERE c.id IN (
                            SELECT course_id 
                            FROM info_studentcourse 
                            WHERE student_id = ?
                        )
                        GROUP BY c.id, c.name
                        ORDER BY c.name
                    ''', (user_details['usn'], user_details['usn'])).fetchall()

                    if attendance_records:
                        response = "📊 Your attendance summary:\n\n"
                        for record in attendance_records:
                            response += f"📚 {record['course_name']}:\n"
                            response += f"  ✅ Present: {record['present_days']} days\n"
                            response += f"  📅 Total Classes: {record['total_days']} days\n"
                            response += f"  📈 Attendance: {record['attendance_percentage']}%\n\n"
                    else:
                        response = "No attendance records found."

            elif user_details['type'] == "teacher":
                student_usn = analysis.get('student_usn')
                course_name = analysis.get('course_name')

                if student_usn and course_name:
                    attendance = conn.execute('''
                        SELECT a.date, a.status,
                               s.name as student_name
                        FROM info_attendance a
                        JOIN info_student s ON a.student_id = s.USN
                        JOIN info_course c ON a.course_id = c.id
                        WHERE c.name = ? AND s.USN = ?
                        ORDER BY a.date DESC
                    ''', (course_name, student_usn)).fetchall()

                    if attendance:
                        response = f"📊 Attendance record for {student_usn} in {course_name}:\n\n"
                        for record in attendance:
                            status = "Present ✅" if record['status'] == 1 else "Absent ❌"
                            formatted_date = datetime.strptime(record['date'], '%Y-%m-%d').strftime('%d-%m-%Y')
                            response += f"📅 {formatted_date}: {status}\n"
                    else:
                        response = f"No attendance records found for {student_usn} in {course_name}."
                else:
                    response = "Please specify both student USN and course name to view attendance."

    finally:
        conn.close()
    
    return response

def handle_marks_query(user_details, user_message, analysis):
    """Handle marks-related queries"""
    conn = get_db_connection()
    response = ""
    
    try:
        if analysis.get('request_type') == 'modify':
            if user_details['type'] != 'teacher':
                return "Only teachers can enter marks."

            student_usn = analysis.get('student_usn')
            marks_value = analysis.get('marks_value')
            course_name = analysis.get('course_name')
            assessment_type = analysis.get('assessment_type')

            if not all([student_usn, marks_value is not None, course_name, assessment_type]):
                return "Please provide student USN, marks value, course name, and assessment type."

            try:
                # Verify teacher's assignment to the course
                course_info = conn.execute('''
                    SELECT a.id as assign_id, c.id as course_id
                    FROM info_assign a
                    JOIN info_course c ON a.course_id = c.id
                    WHERE a.teacher_id = ? AND c.name = ?
                ''', (user_details['teacher_id'], course_name)).fetchone()

                if not course_info:
                    return f"You are not assigned to teach {course_name}."

                # Verify student exists
                student = conn.execute('''
                    SELECT 1 FROM info_student WHERE USN = ?
                ''', (student_usn,)).fetchone()

                if not student:
                    return f"Student with USN {student_usn} not found."

                # Start transaction
                conn.execute('BEGIN')

                # Get or create studentcourse record
                studentcourse = conn.execute('''
                    SELECT id FROM info_studentcourse 
                    WHERE student_id = ? AND course_id = ?
                ''', (student_usn, course_info['course_id'])).fetchone()

                if not studentcourse:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO info_studentcourse (student_id, course_id)
                        VALUES (?, ?)
                    ''', (student_usn, course_info['course_id']))
                    studentcourse_id = cursor.lastrowid
                else:
                    studentcourse_id = studentcourse['id']

                # Update or insert marks
                existing_marks = conn.execute('''
                    SELECT id FROM info_marks 
                    WHERE studentcourse_id = ? AND name = ?
                ''', (studentcourse_id, assessment_type)).fetchone()

                if existing_marks:
                    conn.execute('''
                        UPDATE info_marks 
                        SET marks1 = ? 
                        WHERE id = ?
                    ''', (marks_value, existing_marks['id']))
                else:
                    conn.execute('''
                        INSERT INTO info_marks (name, marks1, studentcourse_id)
                        VALUES (?, ?, ?)
                    ''', (assessment_type, marks_value, studentcourse_id))

                conn.commit()
                response = f"✅ Successfully recorded {marks_value} marks for {student_usn} in {course_name} ({assessment_type})"

            except Exception as e:
                conn.rollback()
                response = f"❌ Error recording marks: {str(e)}"
                
        else:
            if user_details['type'] == "student":
                course_name = analysis.get('course_name')
                
                if course_name:
                    # Query for specific course marks
                    marks_records = conn.execute('''
                        SELECT c.name as course_name, 
                               m.name as assessment_type,
                               m.marks1 as marks
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        WHERE sc.student_id = ? AND c.name = ?
                        ORDER BY m.name
                    ''', (user_details['usn'], course_name)).fetchall()

                    if marks_records:
                        response = f"📊 Your marks for {course_name}:\n\n"
                        for record in marks_records:
                            response += f"📝 {record['assessment_type']}: {record['marks']}\n"
                    else:
                        response = f"No marks found for {course_name}."
                else:
                    # Show all marks if no specific course mentioned
                    marks_records = conn.execute('''
                        SELECT c.name as course_name, 
                               m.name as assessment_type,
                               m.marks1 as marks
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        WHERE sc.student_id = ?
                        ORDER BY c.name, m.name
                    ''', (user_details['usn'],)).fetchall()

                    if marks_records:
                        response = "📊 Your marks:\n\n"
                        current_course = None
                        for record in marks_records:
                            if record['course_name'] != current_course:
                                response += f"📚 {record['course_name']}:\n"
                                current_course = record['course_name']
                            response += f"  📝 {record['assessment_type']}: {record['marks']}\n"
                    else:
                        response = "No marks records found."

            elif user_details['type'] == "teacher":
                student_usn = analysis.get('student_usn')
                course_name = analysis.get('course_name')

                if student_usn and course_name:
                    marks = conn.execute('''
                        SELECT m.name as assessment_type, 
                               m.marks1 as marks
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        WHERE sc.student_id = ? AND c.name = ?
                        ORDER BY m.name
                    ''', (student_usn, course_name)).fetchall()

                    if marks:
                        response = f"📊 Marks for {student_usn} in {course_name}:\n\n"
                        for record in marks:
                            response += f"📝 {record['assessment_type']}: {record['marks']}\n"
                    else:
                        response = f"No marks found for {student_usn} in {course_name}."
                else:
                    response = "Please specify both student USN and course name to view marks."

    finally:
        conn.close()
    
    return response

def handle_timetable_query(user_details, user_message, analysis):
    """Handle timetable-related queries"""
    conn = get_db_connection()
    response = ""
    
    try:
        # Get day from AI analysis
        day = analysis.get('day')
        if day:
            day = day.capitalize()
        
        if user_details['type'] == "student":
            # First get student's class details
            student_class = conn.execute('''
                SELECT s.class_id_id, c.section, c.sem 
                FROM info_student s
                JOIN info_class c ON s.class_id_id = c.id
                WHERE s.USN = ?
            ''', (user_details['usn'],)).fetchone()
            
            if not student_class:
                return "Could not find your class information."
            
            timetable = conn.execute('''
                SELECT at.day, at.period, c.name as course_name, t.name as teacher_name
                FROM info_assigntime at
                JOIN info_assign a ON at.assign_id = a.id
                JOIN info_course c ON a.course_id = c.id
                JOIN info_teacher t ON a.teacher_id = t.id
                JOIN info_class cl ON a.class_id_id = cl.id
                JOIN info_studentcourse sc ON (sc.course_id = c.id AND sc.student_id = ?)
                WHERE cl.id = ?
                AND cl.section = ?
                AND cl.sem = ?
                AND (at.day = ? OR ? IS NULL)
                ORDER BY 
                    CASE at.day 
                        WHEN 'Monday' THEN 1 
                        WHEN 'Tuesday' THEN 2 
                        WHEN 'Wednesday' THEN 3 
                        WHEN 'Thursday' THEN 4 
                        WHEN 'Friday' THEN 5 
                        WHEN 'Saturday' THEN 6 
                        ELSE 7 
                    END,
                    at.period
            ''', (user_details['usn'], student_class['class_id_id'], 
                  student_class['section'], student_class['sem'], 
                  day, day)).fetchall()
            
            if timetable:
                if day:
                    response = f"📅 Your timetable for {day}:\n\n"
                else:
                    response = "📅 Your weekly timetable:\n\n"
                
                current_day = None
                for row in timetable:
                    if row['day'] != current_day:
                        response += f"📌 {row['day']}:\n"
                        current_day = row['day']
                    response += f"⏰ Period {row['period']}: {row['course_name']} (Prof. {row['teacher_name']})\n"
            else:
                response = "No timetable found for the specified day." if day else "No timetable data found."
            
        elif user_details['type'] == "teacher":
            query = '''
                SELECT 
                    at.day, 
                    at.period, 
                    c.name as course_name, 
                    cl.section,
                    cl.sem
                FROM info_assigntime at
                JOIN info_assign a ON at.assign_id = a.id
                JOIN info_course c ON a.course_id = c.id
                JOIN info_class cl ON a.class_id_id = cl.id
                WHERE a.teacher_id = ?
            '''
            params = [user_details['teacher_id']]
            
            if day:
                query += ' AND at.day = ?'
                params.append(day)
            
            query += '''
                ORDER BY 
                    CASE at.day 
                        WHEN 'Monday' THEN 1 
                        WHEN 'Tuesday' THEN 2 
                        WHEN 'Wednesday' THEN 3 
                        WHEN 'Thursday' THEN 4 
                        WHEN 'Friday' THEN 5 
                        WHEN 'Saturday' THEN 6 
                        ELSE 7 
                    END,
                    at.period
            '''
            
            timetable = conn.execute(query, params).fetchall()
            
            if timetable:
                if day:
                    response = f"📅 Your timetable for {day}:\n\n"
                else:
                    response = "📅 Your weekly timetable:\n\n"
                
                current_day = None
                for row in timetable:
                    if row['day'] != current_day:
                        response += f"📌 {row['day']}:\n"
                        current_day = row['day']
                    response += f"⏰ Period {row['period']}: {row['course_name']} for Section {row['section']} (Sem {row['sem']})\n"
            else:
                if day:
                    response = f"No classes scheduled for {day}."
                else:
                    assignments = conn.execute('''
                        SELECT COUNT(*) as count 
                        FROM info_assign 
                        WHERE teacher_id = ?
                    ''', (user_details['teacher_id'],)).fetchone()
                    
                    if assignments['count'] == 0:
                        response = "No courses are currently assigned to you."
                    else:
                        response = "No timetable entries found for your assigned courses."
    finally:
        conn.close()
    
    return response

def extract_day_from_message(message):
    """Extract a day name from the message"""
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    for day in days:
        if day in message.lower():
            return day.capitalize()
    return None

def handle_teacher_other_query(user_context, user_message):
    """Handle other teacher-specific queries"""
    conn = get_db_connection()
    response = ""
    
    try:
        if any(word in user_message.lower() for word in ['class', 'classes', 'students']):
            classes = conn.execute('''
                SELECT DISTINCT c.name as course_name, cl.section, cl.sem
                FROM info_assign a
                JOIN info_course c ON a.course_id = c.id
                JOIN info_class cl ON a.class_id_id = cl.id
                WHERE a.teacher_id = ?
                ORDER BY cl.sem, cl.section, c.name
            ''', (user_context['id'],)).fetchall()
            
            if classes:
                response = "📚 Classes you teach:\n\n"
                for cls in classes:
                    response += f"- {cls['course_name']} (Sem {cls['sem']}, Section {cls['section']})\n"
                
                course_name = extract_entity(conn, user_message, 'info_course', 'name')
                
                if course_name:
                    students = conn.execute('''
                        SELECT s.USN, s.name
                        FROM info_student s
                        JOIN info_class c ON s.class_id_id = c.id
                        JOIN info_assign a ON c.id = a.class_id_id
                        JOIN info_course co ON a.course_id = co.id
                        WHERE co.name = ? AND a.teacher_id = ?
                        ORDER BY s.name
                    ''', (course_name, user_context['id'])).fetchall()
                    
                    if students:
                        response += f"\n👨‍🎓 Students in {course_name}:\n\n"
                        for student in students:
                            response += f"- {student['name']} ({student['USN']})\n"
                    else:
                        response += f"\nNo students found for {course_name}."
            else:
                response = "You are not currently assigned to any classes."
    finally:
        conn.close()
    
    return response

# Helper functions for entity extraction
def extract_entity(conn, message, table, field):
    """Extract entity from message using database lookup"""
    # First try to find exact matches
    entities = conn.execute(f'SELECT {field} FROM {table}').fetchall()
    
    for entity in entities:
        if entity[field].lower() in message.lower():
            return entity[field]
    
    return None

def extract_date_from_message(message):
    """Extract date from message"""
    today = date.today()
    
    if 'today' in message.lower():
        return today.strftime('%Y-%m-%d')
    elif 'tomorrow' in message.lower():
        return (today + timedelta(days=1)).strftime('%Y-%m-%d')
    
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    for i, day in enumerate(days):
        if day in message.lower():
            days_ahead = (i - today.weekday()) % 7
            return (today + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
    
    return None

def extract_number_from_message(message):
    """Extract a number from the message"""
    for word in message.split():
        if word.isdigit():
            return int(word)
    return None

if __name__ == '__main__':
    app.run(debug=True)