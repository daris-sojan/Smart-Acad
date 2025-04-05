# ktu_question_generator/__init__.py

import os
import fitz  # PyMuPDF for PDF processing
import re
import nltk
import ollama
from flask import Blueprint, request, render_template, redirect, current_app, flash, session, url_for
from werkzeug.utils import secure_filename
from datetime import datetime

# Download necessary NLTK resources
nltk.download('punkt', quiet=True)
nltk.download('stopwords', quiet=True)

# Create the blueprint
ktu_question_bp = Blueprint('ktu_question', __name__, 
                          template_folder='templates',
                          static_folder='static',
                          url_prefix='/ktu-question')

class KTUQuestionGenerator:
    def __init__(self):
        """Defines the KTU-style question paper format"""
        self.modules = ["Module 1", "Module 2", "Module 3", "Module 4", "Module 5"]

    def extract_text_from_pdf(self, pdf_path):
        """Extracts meaningful text from the uploaded PDF"""
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

    def generate_questions(self, combined_text):
        """Generates a KTU-style university exam question paper"""
        if not combined_text:
            return ["No meaningful content found in the PDFs"]

        system_prompt = """
        You are an AI specialized in generating KTU-style university exam question papers. 
        Follow these guidelines strictly:

        **Required Format:**
        PART A: Short Answer Questions (10 × 3 = 30 Marks)
        - 10 questions covering different topics
        - Each question should be concise and test basic understanding
        - Example: "Explain the concept of [...]" or "Differentiate between [...]"

        PART B: Module 1 Questions (2 × 14 = 28 Marks)
        a) Comprehensive question with subparts (i, ii)
        b) Case study/application-based question

        PART C: Module 2 Questions (2 × 14 = 28 Marks)
        a) Numerical problem with theoretical components
        b) Diagram-based explanation question

        PART D: Module 3 Questions (2 × 14 = 28 Marks)
        a) Comparative analysis question
        b) Scenario-based implementation question

        PART E: Module 4 Questions (2 × 14 = 28 Marks)
        a) Algorithm/procedure explanation
        b) Optimization/improvement analysis

        **Quality Requirements:**
        1. Questions must be unambiguous and academically rigorous
        2. Cover all major topics from the source material
        3. Include appropriate technical terms
        4. Maintain consistent difficulty level
        5. Ensure proper mark distribution as specified
        """

        # Preserve the exact prompt structure that worked well
        user_prompt = f"""Generate a comprehensive KTU question paper from the following content:
        {combined_text[:4000]}

        Follow these steps:
        1. Analyze the content for key topics
        2. Identify fundamental concepts for Part A
        3. Create module-specific application questions
        4. Ensure coverage of all major themes
        5. Maintain strict mark distribution
        """

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
        """Ensures consistent formatting from LLM response"""
        # Convert to list of lines with proper section breaks
        lines = []
        current_section = None
        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('**PART'):
                current_section = line
                lines.append(line)
            elif line and current_section:
                if line.startswith(('- ', 'a)', 'b)')):
                    lines.append(line)
                else:  # Handle paragraph-style questions
                    lines[-1] += " " + line
        return lines or text.split('\n')


@ktu_question_bp.route("/", methods=["GET", "POST"])
def index():
    """Handles multi-PDF upload and question generation"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        flash('Only teachers can access this feature', 'error')
        return redirect(url_for('login'))
    
    if request.method == "POST":
        # Step 1: Handle PDF count selection
        if 'pdf_count' in request.form:
            session['pdf_count'] = min(int(request.form['pdf_count']), 5)
            return render_template("ktu_question/upload.html", 
                                pdf_count=session['pdf_count'])
        
        # Step 2: Handle PDF uploads
        if 'pdfs' in request.files:
            pdf_count = session.get('pdf_count', 1)
            files = request.files.getlist('pdfs')
            
            # Validate files
            valid_files = []
            for file in files[:pdf_count]:
                if file.filename == '':
                    continue
                if file and file.filename.lower().endswith('.pdf'):
                    valid_files.append(file)
            
            if len(valid_files) != pdf_count:
                flash(f'Please upload exactly {pdf_count} PDF files', 'error')
                return redirect(request.url)
            
            # Process files
            upload_folder = os.path.join(current_app.instance_path, 'uploads')
            os.makedirs(upload_folder, exist_ok=True)
            
            combined_text = []
            temp_files = []
            
            try:
                generator = KTUQuestionGenerator()
                for i, file in enumerate(valid_files):
                    filename = secure_filename(f"doc_{i}_{file.filename}")
                    pdf_path = os.path.join(upload_folder, filename)
                    file.save(pdf_path)
                    temp_files.append(pdf_path)
                    
                    # Extract and tag text with source
                    text = generator.extract_text_from_pdf(pdf_path)
                    if text:
                        combined_text.append(f"[PDF {i+1} CONTENT START]\n{text}\n[PDF {i+1} CONTENT END]")
                
                if not combined_text:
                    flash('No extractable text found in PDFs', 'error')
                    return redirect(request.url)
                
                # Generate questions using proven single-PDF method
                questions = generator.generate_questions('\n\n'.join(combined_text))
                current_date = datetime.now().strftime("%B %d, %Y at %H:%M")
                
                return render_template("ktu_question/results.html", 
                                    questions=questions,
                                    current_date=current_date,
                                    pdf_count=pdf_count)
                
            except Exception as e:
                flash(f'Error generating questions: {str(e)}', 'error')
                return render_template("ktu_question/error.html", 
                                    error=str(e))
            finally:
                for path in temp_files:
                    if os.path.exists(path):
                        os.remove(path)
    
    return render_template("ktu_question/select_count.html")