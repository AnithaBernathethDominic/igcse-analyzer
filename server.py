from flask import Flask, render_template, request, jsonify
import re
import json
import os
import fitz  # PyMuPDF
from supabase import create_client

app = Flask(__name__)

# ---------- SUPABASE CONFIG ----------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------- LOAD TOPICS ----------
with open("topics.json") as f:
    TOPICS = json.load(f)


# ---------- EXTRACT TEXT FROM PDF ----------
def extract_text(file):
    text = ""
    pdf = fitz.open(stream=file.read(), filetype="pdf")

    for page in pdf:
        text += page.get_text()

    return text


# ---------- PARSE QUESTION PAPER ----------
def parse_qp(text):
    questions = []

    lines = text.split("\n")
    current_q = None
    current_sub = None
    current_text = ""

    for line in lines:
        line = line.strip()

        # 🚫 skip empty
        if line == "":
            continue

        # 🚫 skip noise
        if (
            "DO NOT WRITE" in line or
            "Working space" in line or
            re.match(r"^\.+$", line) or
            re.match(r"^\[\d+\]$", line)
        ):
            continue

        # 🚫 skip paper codes (0478 etc)
        if re.match(r"^0\d{3}", line):
            continue

        # 🚫 skip binary numbers
        if re.match(r"^[01]{6,}$", line):
            continue

        # 🚫 skip hex-only lines
        if re.match(r"^[0-9A-F]+$", line):
            continue

        # 🚫 skip garbage characters
        if any(char in line for char in ["*", "¬", "Ĭ", "ĥ", "¥"]):
            continue

        # ✅ detect REAL question number (FIXED)
        match_q = re.match(r"^(\d+)\s+[A-Za-z]", line)

        if match_q:
           q_num = match_q.group(1)

           # ✅ only update if new question
           if q_num != current_q:
              current_q = q_num

        # ✅ detect sub-question
        elif re.match(r"^\([a-z]\)", line):

            if current_q and current_sub and current_text:
                questions.append({
                    "question": f"{current_q}{current_sub}",
                    "question_text": clean_text(current_text)
                })

            current_sub = re.match(r"\([a-z]\)", line).group()
            current_text = line

        # ✅ continue same question
        elif current_sub:

            # 🔥 detect (i), (ii), (iii)
            if re.match(r"^\([ivx]+\)", line):

                # save previous
                if current_text:
                    questions.append({
                        "question": f"{current_q}{current_sub}",
                        "question_text": clean_text(current_text)
                    })

               # start new sub-sub question
               current_sub = re.match(r"\([ivx]+\)", line).group()
               current_text = line

          else:
              current_text += " " + line

    # ✅ last question
    if current_q and current_sub and current_text:
        questions.append({
            "question": f"{current_q}{current_sub}",
            "question_text": clean_text(current_text)
        })

    return questions
    
#ADD CLEANING FUNCTION
def clean_text(text):
    # remove UCLES / footer junk
    text = re.sub(r"©.*?2025", "", text)
    text = re.sub(r"\[Turn over\]", "", text)

    # remove marks like [2]
    text = re.sub(r"\[\d+\]", "", text)

    # remove dots
    text = re.sub(r"\.+", "", text)

    # remove extra spaces
    text = re.sub(r"\s+", " ", text)

    return text.strip()

# ---------- PARSE MARK SCHEME ----------
def parse_ms(text):
    answers = []

    pattern = r"\d+\([a-z]\)"
    matches = list(re.finditer(pattern, text))

    for i in range(len(matches)):
        start = matches[i].start()
        end = matches[i+1].start() if i+1 < len(matches) else len(text)

        block = text[start:end]
        q_no = matches[i].group()

        # Clean answer text
        answer = re.sub(r"\s+", " ", block).strip()

        answers.append({
            "question": q_no,
            "answer": answer
        })

    return answers


# ---------- MAP TOPIC ----------
def map_topic(text):
    for topic, keywords in TOPICS.items():
        for word in keywords:
            if word.lower() in text.lower():
                return topic
    return "General"


# ---------- MERGE QP + MS ----------
def merge(qp, ms):
    ms_dict = {item["question"]: item for item in ms}

    result = []

    for q in qp:
        q_no = q["question"]

        ans = ms_dict.get(q_no, {}).get("answer", "")

        result.append({
            "question": q_no,
            "question_text": q["question_text"],
            "answer": ans,
            "topic": map_topic(q["question_text"])   # ✅ ADD THIS
       })

    return result

# ---------- SAVE TO SUPABASE ----------
def save_to_db(data, paper_name):
    for item in data:
        supabase.table("questions").insert({
            "paper": paper_name,
            "question_no": item["question"],
            "question_text": item["question_text"],
            "answer": item.get("answer", ""),
            "topic": item.get("topic", "General")
        }).execute()


# ---------- ROUTES ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/practice")
def practice_page():
    return render_template("practice.html")


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


# ---------- GET TOPICS ----------
@app.route("/topics")
def get_topics():
    response = supabase.table("questions").select("topic").execute()
    topics = list(set([item["topic"] for item in response.data]))
    return jsonify(topics)


# ---------- PRACTICE ----------
@app.route("/practice/<topic>")
def practice(topic):
    response = supabase.table("questions").select("*").eq("topic", topic).execute()
    return jsonify(response.data)


# ---------- AI FEEDBACK (IGCSE STYLE) ----------
@app.route("/feedback", methods=["POST"])
def feedback():
    data = request.json

    student = data.get("student", "").lower()
    correct = data.get("correct", "").lower()

    keywords = list(set([word for word in correct.split() if len(word) > 4]))

    matched = [k for k in keywords if k in student]
    missing = [k for k in keywords if k not in student]

    total = len(keywords)
    score = len(matched)

    marks = min(4, round((score / total) * 4)) if total > 0 else 0

    # Highlight matched words
    highlighted = student
    for word in matched:
        highlighted = highlighted.replace(
            word,
            f"<span style='color:green;font-weight:bold'>{word}</span>"
        )

    # Comment
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


# ---------- RUN ----------
if __name__ == "__main__":
    app.run(debug=True)
