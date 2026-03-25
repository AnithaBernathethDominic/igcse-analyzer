from flask import Flask, render_template, request, jsonify
import pdfplumber
import re
import json
from supabase import create_client

app = Flask(__name__)

# ---------- SUPABASE CONFIG ----------
import os

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------- LOAD TOPICS ----------
with open("topics.json") as f:
    TOPICS = json.load(f)


# ---------- EXTRACT TEXT ----------
def extract_text(file):
    text = ""
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"
    return text


# ---------- PARSE QUESTION PAPER ----------
def parse_qp(text):
    questions = []

    lines = text.split("\n")

    for line in lines:
        match = re.match(r"^\d+\s*\([a-z]\)", line.strip())
        if match:
            questions.append({
                "question": match.group(),
                "question_text": line
            })

    return questions


# ---------- PARSE MARK SCHEME ----------
def parse_ms(text):
    answers = []

    lines = text.split("\n")

    for line in lines:
        match = re.match(r"^\d+\([a-z]\)", line.strip())
        if match:
            answers.append({
                "question": match.group(),
                "answer": line
            })

    return answers


# ---------- MAP TOPIC ----------
def map_topic(text):
    for topic, keywords in TOPICS.items():
        for word in keywords:
            if word.lower() in text.lower():
                return topic
    return "General"


# ---------- MERGE ----------
def merge(qp, ms):
    ms_dict = {item["question"]: item for item in ms}
    result = []

    for q in qp:
        q_no = q["question"]
        ans = ms_dict.get(q_no, {}).get("answer", "")
        topic = map_topic(q["question_text"])

        result.append({
            "question": q_no,
            "question_text": q["question_text"],
            "answer": ans,
            "topic": topic
        })

    return result


# ---------- SAVE TO SUPABASE ----------
def save_to_db(data, paper_name):
    for item in data:
        supabase.table("questions").insert({
            "paper": paper_name,
            "question": item["question"],
            "question_text": item["question_text"],
            "answer": item["answer"],
            "topic": item["topic"]
        }).execute()


# ---------- ROUTES ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    qp_file = request.files["qp"]
    ms_file = request.files["ms"]

    paper_name = qp_file.filename

    qp_text = extract_text(qp_file)
    ms_text = extract_text(ms_file)

    print("QP TEXT:", qp_text[:500])
    print("MS TEXT:", ms_text[:500])
    
    qp_data = parse_qp(qp_text)
    ms_data = parse_ms(ms_text)
  
    print("QP DATA:", qp_data)
    print("MS DATA:", ms_data)
   
    final_data = merge(qp_data, ms_data)

    # SAVE TO CLOUD
    save_to_db(final_data, paper_name)

    return jsonify(final_data)


@app.route("/get_all")
def get_all():
    response = supabase.table("questions").select("*").execute()
    return jsonify(response.data)


@app.route("/topic/<topic>")
def get_by_topic(topic):
    response = supabase.table("questions").select("*").eq("topic", topic).execute()
    return jsonify(response.data)


# ---------- RUN ----------
if __name__ == "__main__":
    app.run(debug=True)
