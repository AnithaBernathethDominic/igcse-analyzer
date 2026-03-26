from flask import Flask, render_template, request, jsonify
from flask import request
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
    current_q = None

    for line in lines:
        line = line.strip()

        # Match main question number (1, 2, 3...)
        if re.match(r"^\d+\s", line):
            current_q = re.match(r"^\d+", line).group()

        # Match sub-question (a), (b), etc.
        elif re.match(r"^\([a-z]\)", line) and current_q:
            sub_q = re.match(r"\([a-z]\)", line).group()
            q_no = f"{current_q}{sub_q}"

            questions.append({
                "question": q_no,
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
    ms_dict = {item["question"].replace(" ", ""): item for item in ms}
    result = []

    for q in qp:
        q_no = q["question"].replace(" ", "")

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
            "question_no": item["question"],   # ✅ FIXED
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
    try:
        qp_file = request.files["qp"]
        ms_file = request.files["ms"]

        paper_name = qp_file.filename

        qp_text = extract_text(qp_file)
        ms_text = extract_text(ms_file)

        qp_data = parse_qp(qp_text)
        ms_data = parse_ms(ms_text)

        print("QP DATA:", qp_data)
        print("MS DATA:", ms_data)

        final_data = merge(qp_data, ms_data)

        save_to_db(final_data, paper_name)

        return jsonify(final_data)

    except Exception as e:
        print("ERROR:", str(e))   
        return jsonify({"error": str(e)}), 500

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

 @app.route("/topics")
def get_topics():
    response = supabase.table("questions").select("topic").execute()

    topics = list(set([item["topic"] for item in response.data]))

    return jsonify(topics)

@app.route("/practice/<topic>")
def practice(topic):
    response = supabase.table("questions").select("*").eq("topic", topic).execute()
    return jsonify(response.data)

@app.route("/practice")
def practice_page():
    return render_template("practice.html")


@app.route("/feedback", methods=["POST"])
def feedback():
    data = request.json

    student = data.get("student", "").lower()
    correct = data.get("correct", "").lower()

    # Extract keywords
    keywords = list(set([word for word in correct.split() if len(word) > 4]))

    matched = [k for k in keywords if k in student]
    missing = [k for k in keywords if k not in student]

    # Mark calculation
    total = len(keywords)
    score = len(matched)
    marks = min(4, round((score / total) * 4)) if total > 0 else 0

    # Highlight student answer
    highlighted = student
    for word in matched:
        highlighted = highlighted.replace(word, f"<span style='color:green;font-weight:bold'>{word}</span>")

    # Feedback comment
    if marks == 4:
        comment = "Excellent answer. Accurate use of key terminology."
    elif marks >= 2:
        comment = "Good attempt. Some key terms missing."
    else:
        comment = "Basic response. Needs improvement."

    return jsonify({
        "marks": f"{marks}/4",
        "matched": matched[:5],
        "missing": missing[:5],
        "comment": comment,
        "highlighted": highlighted,
        "model": correct
    })
