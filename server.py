from flask import Flask, request, jsonify, render_template
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


# ---------- EXTRACT TEXT ----------
def extract_text(file):
    """Extract text from PDF using PyMuPDF (fitz), reading each page in natural order."""
    text = ""
    pdf = fitz.open(stream=file.read(), filetype="pdf")
    for page in pdf:
        text += page.get_text()
    return text


# ---------- CLEAN TEXT ----------
def clean_text(text):
    """Remove noise from extracted question text."""
    # Remove answer lines (dots) and mark brackets
    text = re.sub(r'\.{3,}', '', text)
    text = re.sub(r'\[\d+\]', '', text)
    # Remove boilerplate
    text = re.sub(r'©.*?202\d', '', text)
    text = re.sub(r'UCLES\s*202\d.*', '', text)
    text = re.sub(r'\[Turn\s*over\]?', '', text)
    text = re.sub(r'DO NOT WRITE.*?MARGIN', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Working\s*space', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Cambridge IGCSE.*', '', text)
    text = re.sub(r'0478/12.*', '', text)
    # Remove PDF encoding artifacts
    text = re.sub(r'\(cid:\d+\)', '', text)
    text = re.sub(r'\*\s*\d{10,}\s*\*', '', text)
    text = re.sub(r'\bDFD\b', '', text)
    # Normalise whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ---------- STRIP QP NOISE ----------
def strip_qp_noise(text):
    """Pre-process raw QP text before question splitting."""
    text = re.sub(r'\(cid:\d+\)', ' ', text)
    text = re.sub(r'\*\s*\d{10,}\s*\*', ' ', text)
    text = re.sub(r'DO NOT WRITE\s+IN THIS MARGIN', ' ', text)
    text = re.sub(r'Working\s+space', ' ', text)
    text = re.sub(r'\[Turn\s*over\]?', ' ', text)
    text = re.sub(r'©\s*UCLES\s*202\d', ' ', text)
    text = re.sub(r'0478/12/O/N/25', ' ', text)
    text = re.sub(r'\bDFD\b', ' ', text)
    text = re.sub(r'Cambridge IGCSE[^\n]*', ' ', text)
    text = re.sub(r'\f\d*\s*', ' ', text)   # form-feed + optional page number
    text = re.sub(r'\f', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text


# ---------- PARSE QUESTION PAPER ----------
def parse_qp(text):
    import re

    questions = []

    # ===============================
    # CLEAN TEXT
    # ===============================
    text = re.sub(r"\s+", " ", text)

    text = re.sub(r"©.*?UCLES.*?", " ", text)
    text = re.sub(r"Turn over", " ", text)
    text = re.sub(r"DO NOT WRITE.*?", " ", text)
    text = re.sub(r"Working space.*?", " ", text)
    text = re.sub(r"\*.*?\*", " ", text)

    # remove encoding junk
    text = re.sub(r"[^\x00-\x7F]+", " ", text)

    # remove binary / hex noise
    text = re.sub(r"\b[01]{8,}\b", " ", text)
    text = re.sub(r"\b[0-9A-F]{6,}\b", " ", text)

    # ===============================
    # SPLIT BLOCKS
    # ===============================
    parts = re.split(r"(?=\b\d{1,2}\s)", text)

    valid_blocks = []

    for part in parts:
        match = re.match(r"(\d{1,2})", part)
        if not match:
            continue

        num = int(match.group(1))

        # ✅ ACCEPT ONLY REAL QUESTION NUMBERS
        if 1 <= num <= 20:
            valid_blocks.append(part)

    # ===============================
    # PROCESS BLOCKS
    # ===============================
    for block in valid_blocks:

        q_no = re.match(r"(\d{1,2})", block).group(1)

        sub_blocks = re.split(r"(?=\([a-z]\))", block)

        for sub in sub_blocks:

            sub_match = re.match(r"\(([a-z])\)", sub)
            if not sub_match:
                continue

            sub_id = sub_match.group(1)

            subsub_blocks = re.split(r"(?=\([ivx]+\))", sub)

            if len(subsub_blocks) > 1:
                for s in subsub_blocks:

                    ss_match = re.match(r"\(([ivx]+)\)", s)
                    if not ss_match:
                        continue

                    qid = f"{q_no}({ss_match.group(1)})"

                    cleaned = clean_text(s)

                    # remove MCQ options
                    cleaned = re.sub(r"\b[A-D]\s+[A-Za-z].*?(?=[A-D]\s|$)", "", cleaned)

                    if len(cleaned) > 10:
                        questions.append({
                            "question": qid,
                            "question_text": cleaned
                        })

            else:
                qid = f"{q_no}({sub_id})"

                cleaned = clean_text(sub)

                cleaned = re.sub(r"\b[A-D]\s+[A-Za-z].*?(?=[A-D]\s|$)", "", cleaned)

                if len(cleaned) > 10:
                    questions.append({
                        "question": qid,
                        "question_text": cleaned
                    })

    # ===============================
    # REMOVE DUPLICATES
    # ===============================
    unique = {}
    for q in questions:
        unique[q["question"]] = q

    return list(unique.values())
# ---------- STRIP MS NOISE ----------
def strip_ms_noise(text):
    """Pre-process raw mark scheme text to remove headers and mark columns."""
    # Remove page headers
    text = re.sub(
        r'0478/12\s*Cambridge IGCSE[^\n]*\n[^\n]*PUBLISHED[^\n]*\n',
        '\n', text
    )
    text = re.sub(r'Cambridge IGCSE[^\n]*PUBLISHED[^\n]*', '', text)
    text = re.sub(r'© Cambridge University Press[^\n]*', '', text)
    text = re.sub(r'October/November 2025\s*', '', text)
    text = re.sub(r'0478/12\s*', '', text)
    text = re.sub(r'Page \d+ of \d+\s*', '', text)
    # Remove column headers
    text = re.sub(r'Question\s+Answer\s+Marks', '', text)
    text = re.sub(r'\bAnswer\b\s+\bMarks\b', '', text)
    # Remove standalone mark-count numbers on their own line (e.g. "\n 4 \n")
    # These are the "Marks" column values in the table
    text = re.sub(r'(?m)^\s*\d{1,2}\s*$', '', text)
    # Remove PDF encoding artifacts
    text = re.sub(r'\(cid:\d+\)', '', text)
    text = re.sub(r'\f', ' ', text)
    return text


# ---------- PARSE MARK SCHEME ----------
def parse_ms(raw_text):
    """
    Parse the mark scheme PDF into:
      [{ "question": "1(a)", "answer": "..." }, ...]

    The mark scheme uses labels like:
      1(a)  1(b)  1(e)(i)  2(a)  5(c)(i)  7(f)(ii)
    """

    text = strip_ms_noise(raw_text)

    # Match question labels: 1(a)  or  1(e)(i)
    q_pattern = re.compile(
        r'(?<!\w)'
        r'(\d+\([a-z]\)(?:\([ivx]+\))?)'
        r'(?!\w)'
    )

    matches = list(q_pattern.finditer(text))

    answers = []
    for i, m in enumerate(matches):
        q_label = m.group(1).strip()
        ans_start = m.end()
        ans_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        raw_answer = text[ans_start:ans_end]

        # Clean the answer block
        raw_answer = re.sub(r'\s+', ' ', raw_answer)
        answer = raw_answer.strip()

        # Remove trailing lone digit (residual mark count)
        answer = re.sub(r'\s+\d{1,2}$', '', answer).strip()

        answers.append({
            "question": q_label,
            "answer": answer
        })

    # Deduplicate -- prefer non-empty answers
    unique = {}
    for a in answers:
        key = a["question"]
        if key not in unique or (not unique[key]["answer"] and a["answer"]):
            unique[key] = a

    return list(unique.values())


# ---------- MAP TOPIC ----------
def map_topic(text):
    for topic, keywords in TOPICS.items():
        for word in keywords:
            if word.lower() in text.lower():
                return topic
    return "General"


# ---------- MERGE ----------
def merge(qp, ms):
    ms_dict = {item["question"]: item["answer"] for item in ms}

    result = []
    for q in qp:
        q_key = q["question"]
        ans = ms_dict.get(q_key, "")

        # Fallback: if key like "1(e)" has no direct match,
        # collect answers from all its children e.g. "1(e)(i)", "1(e)(ii)"
        if not ans:
            child_answers = []
            for ms_key, ms_ans in ms_dict.items():
                if ms_key.startswith(q_key + "("):
                    child_answers.append(ms_ans)
            if child_answers:
                ans = " | ".join(child_answers)

        result.append({
            "question": q_key,
            "question_text": q["question_text"],
            "answer": ans,
            "topic": map_topic(q["question_text"])
        })

    return result


# ---------- SAVE ----------
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


@app.route("/topics")
def get_topics():
    response = supabase.table("questions").select("topic").execute()
    topics = list(set([item["topic"] for item in response.data]))
    return jsonify(topics)


@app.route("/practice/<topic>")
def practice(topic):
    response = supabase.table("questions").select("*").eq("topic", topic).execute()
    return jsonify(response.data)


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

    highlighted = student
    for word in matched:
        highlighted = highlighted.replace(
            word,
            f"<span style='color:green;font-weight:bold'>{word}</span>"
        )

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


if __name__ == "__main__":
    app.run(debug=True)
