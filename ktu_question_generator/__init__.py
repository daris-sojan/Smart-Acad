import os
import fitz
import re
import ollama
from flask import Blueprint, request, render_template, redirect, current_app, flash, session, url_for
from werkzeug.utils import secure_filename
from datetime import datetime

ktu_question_bp = Blueprint('ktu_question', __name__,
                          template_folder='templates',
                          static_folder='static',
                          url_prefix='/ktu-question')

class KTUQuestionGenerator:
    def __init__(self):
        self.modules = ["Module 1", "Module 2", "Module 3", "Module 4", "Module 5"]

    def extract_text_from_pdf(self, pdf_path):
        try:
            with fitz.open(pdf_path) as doc:
                full_text = []
                for page in doc:
                    text = page.get_text("text")
                    text = re.sub(r'\s+', ' ', text).strip()
                    full_text.append(text)
                return ' '.join(full_text)
        except Exception as e:
            print(f"PDF extraction error: {e}")
            return ""

    def extract_questions_from_past_papers(self, pdf_paths):
        past_questions = []
        for path in pdf_paths:
            text = self.extract_text_from_pdf(path)
            questions = re.findall(
                r'(?:Q\s*\d+\.?|Question\s*\d*:?|Part\s*[A-E]\s*\(.*?\):?)(.*?)(?=(?:Q\s*\d+|Question\s*\d+|Part\s*[A-E]\s*\()|$)',
                text,
                re.DOTALL | re.IGNORECASE
            )
            past_questions.extend([self._clean_question(q) for q in questions if q.strip()])
        return past_questions

    def _clean_question(self, question):
        question = re.sub(r'\[.*?\]', '', question)
        question = re.sub(r'\s+', ' ', question).strip()
        return question[:500]

    def generate_questions(self, combined_text, past_questions=None):
        if not combined_text:
            return ["No meaningful content found in the PDFs"]

        past_questions_section = ""
        if past_questions:
            sample_questions = '\n'.join(f'- {q}' for q in past_questions[:15])
            past_questions_section = f"""
**Avoidance Requirements:**
Do not create questions similar to these past questions:
{sample_questions}

**Additional Guidelines:**
1. Ensure new questions are distinct in both content and phrasing
2. Cover similar concepts but with different approaches
3. Vary question types and formats
"""

        system_prompt = f"""
You are an AI specialized in generating KTU-style university exam question papers.
Follow these guidelines strictly:

**Required Format:**
PART A: Short Answer Questions (10 × 3 = 30 Marks)
PART B: Module 1 Questions (2 × 14 = 28 Marks)
PART C: Module 2 Questions (2 × 14 = 28 Marks)
PART D: Module 3 Questions (2 × 14 = 28 Marks)
PART E: Module 4 Questions (2 × 14 = 28 Marks)

**Formatting Rules:**
1. Start each section with **PART X:**
2. List questions with bullet points (-)
3. For sub-questions use a) and b)
4. Explicitly include marks allocation in brackets [2 Marks]
5. Keep questions concise but unambiguous

**Quality Requirements:**
1. Questions must be unambiguous and academically rigorous
2. Cover all major topics from the source material
3. Include appropriate technical terms
4. Maintain consistent difficulty level
{past_questions_section}
"""

        user_prompt = f"Generate a comprehensive KTU question paper from: {combined_text[:4000]}"

        try:
            response = ollama.chat(
                model="mistral",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return self._format_response(response["message"]["content"])
        except Exception as e:
            return [f"Error generating questions: {str(e)}"]

    def _format_response(self, text):
        """Convert raw AI response into properly formatted KTU question paper format"""
        formatted = []
        current_section = None
        
        for line in text.split('\n'):
            line = line.strip()
            
            # Section headers
            if line.startswith('**PART'):
                current_section = line
                formatted.append(line)
            # Questions (various formats)
            elif line and current_section:
                if (line.startswith('- ') or  # List items
                    line.startswith('a)') or  # Sub-questions
                    line.startswith('b)') or
                    line[0].isdigit()):       # Numbered questions
                    formatted.append(line)
                elif formatted:
                    # Continue previous question if not a new item
                    formatted[-1] += " " + line
        
        # Fallback if no formatting detected
        if not any(line.startswith('**PART') for line in formatted):
            return text.split('\n')
        
        return formatted

def save_uploaded_files(files, file_type):
    upload_folder = os.path.join(current_app.instance_path, 'uploads', file_type)
    os.makedirs(upload_folder, exist_ok=True)
    
    saved_paths = []
    for i, file in enumerate(files):
        if file and file.filename:
            if file.filename.lower().endswith('.pdf'):
                filename = secure_filename(f"{file_type}_{i}_{file.filename}")
                pdf_path = os.path.join(upload_folder, filename)
                file.save(pdf_path)
                saved_paths.append(pdf_path)
            else:
                flash('Only PDF files are allowed', 'error')
                return None
    
    return saved_paths if saved_paths else None

@ktu_question_bp.route("/", methods=["GET", "POST"])
def index():
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        flash('Only teachers can access this feature', 'error')
        return redirect(url_for('login'))
    
    if request.method == "POST":
        if 'has_past_papers' in request.form:
            session['has_past_papers'] = request.form['has_past_papers'] == 'yes'
            session['pdf_count'] = min(int(request.form.get('pdf_count', 1)), 5)
            if session['has_past_papers']:
                return render_template("ktu_question/upload.html",
                                    step="past_papers")
            return render_template("ktu_question/upload.html",
                                step="study_materials",
                                pdf_count=session['pdf_count'])
        
        if 'step' in request.form and request.form['step'] == 'past_papers':
            files = [f for f in request.files.getlist('pdfs') if f.filename]
            if not files:
                flash('Please upload at least one past paper', 'error')
                return redirect(request.url)
            
            past_papers = save_uploaded_files(files, "past_papers")
            if not past_papers:
                return redirect(request.url)
                
            session['past_papers'] = past_papers
            return render_template("ktu_question/upload.html",
                                step="study_materials",
                                pdf_count=session['pdf_count'])
        
        if 'step' in request.form and request.form['step'] == 'study_materials':
            files = request.files.getlist('pdfs')
            if len(files) != session['pdf_count']:
                flash(f'Please upload exactly {session["pdf_count"]} study material PDFs', 'error')
                return redirect(request.url)
            
            study_materials = save_uploaded_files(files, "study_materials")
            if not study_materials:
                return redirect(request.url)
            
            generator = KTUQuestionGenerator()
            combined_text = []
            
            for path in study_materials:
                text = generator.extract_text_from_pdf(path)
                if text:
                    combined_text.append(text)
            
            if not combined_text:
                flash('No extractable text found in PDFs', 'error')
                return redirect(request.url)
            
            past_questions = None
            if session.get('has_past_papers'):
                past_questions = generator.extract_questions_from_past_papers(session['past_papers'])
            
            questions = generator.generate_questions('\n\n'.join(combined_text), past_questions)
            
            # Cleanup files
            for path in study_materials + session.get('past_papers', []):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except:
                    pass
            
            session.pop('past_papers', None)
            session.pop('has_past_papers', None)
            
            return render_template("ktu_question/results.html",
                                questions=questions,
                                current_date=datetime.now().strftime("%B %d, %Y at %H:%M"),
                                pdf_count=session['pdf_count'])
    
    return render_template("ktu_question/select_count.html")