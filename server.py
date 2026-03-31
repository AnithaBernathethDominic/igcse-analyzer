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


# ---------- EXTRACT TEXT ----------
def extract_text(file):
    text = ""
    pdf = fitz.open(stream=file.read(), filetype="pdf")
    for page in pdf:
        text += page.get_text()
    return text


# ---------- CLEAN TEXT ----------
def clean_text(text):
    text = re.sub(r"©.*?2025", "", text)
    text = re.sub(r"UCLES.*", "", text)
    text = re.sub(r"\[Turn over\]", "", text)
    text = re.sub(r"[^\x00-\x7F]+", " ", text)
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"\.+", "", text)
    text = re.sub(r"\s+", " ", text)
    # return text.strip()
    return re.sub(r"\s+", " ", text).strip()
    


# ---------- PARSE QUESTION PAPER ----------
def parse_qp(text):
    questions = []

    lines = text.split("\n")
    current_q = None
    current_sub = None
    current_text = ""

    for line in lines:
        line = line.strip()

        if not line:
            continue

        # 🔥 NORMALIZE SPACES
        line = re.sub(r"\s+", " ", line)

        # ===============================
        # 🚫 REMOVE FOOTER / GARBAGE
        # ===============================
        if (
            "DO NOT WRITE" in line or
            "Working space" in line or
            "Turn over" in line or
            "UCLES" in line or
            "0478/" in line or
            re.match(r"^\*.*\*$", line) or
            re.match(r"^\.+$", line) or
            re.match(r"^\[\d+\]$", line)
        ):
            continue

        # skip binary
        if re.match(r"^[01]{6,}$", line):
            continue

        # skip hex only
        if re.match(r"^[0-9A-F]{3,}$", line):
            continue

        # ===============================
        # ✅ STEP 1: QUESTION DETECTION FIRST (FIXED)
        # ===============================

        # Case 1: 2(a)
        match_inline = re.match(r"^(\d+)\(([a-z])\)", line)
        if match_inline:

            if current_q and current_sub and current_text:
                questions.append({
                    "question": f"{current_q}{current_sub}",
                    "question_text": clean_text(current_text)
                })

            current_q = match_inline.group(1)
            current_sub = f"({match_inline.group(2)})"
            current_text = line
            continue

        # Case 2: "2"
        match_q_only = re.match(r"^(\d+)$", line)
        if match_q_only:
            q_num = int(match_q_only.group(1))

            if 1 <= q_num <= 20:
                current_q = str(q_num)

            continue

        # Case 3: "2 A computer..." ✅ FIXED LOGIC
        match_q = re.match(r"^(\d+)\s+[A-Za-z]", line)
        if match_q:
            new_q = int(match_q.group(1))

            # 🔥 ONLY allow proper sequence (1 → 2 → 3)
            if current_q is None or new_q == int(current_q) + 1:
                current_q = str(new_q)
                current_sub = None
                current_text = ""

            continue

        # ===============================
        # ✅ (a)(b)(c)
        # ===============================
        if re.match(r"^\([a-z]\)", line):

            if current_q and current_sub and current_text:
                questions.append({
                    "question": f"{current_q}{current_sub}",
                    "question_text": clean_text(current_text)
                })

            current_sub = re.match(r"\([a-z]\)", line).group()
            current_text = line
            continue

        # ===============================
        # ✅ (i)(ii)(iii)
        # ===============================
        if current_sub:

            if re.match(r"^\([ivx]+\)", line):

                # 🔥 DO NOT CHANGE current_q HERE (important fix)
                if current_q and current_sub and current_text:
                    questions.append({
                        "question": f"{current_q}{current_sub}",
                        "question_text": clean_text(current_text)
                    })

                current_sub = re.match(r"\([ivx]+\)", line).group()
                current_text = line

            else:
                current_text += " " + line

    # ===============================
    # ✅ LAST QUESTION
    # ===============================
    if current_q and current_sub and current_text:
        questions.append({
            "question": f"{current_q}{current_sub}",
            "question_text": clean_text(current_text)
        })

    # ===============================
    # ✅ REMOVE DUPLICATES
    # ===============================
    unique = {}
    for q in questions:
        unique[q["question"]] = q

    return list(unique.values())
# ---------- PARSE MARK SCHEME ----------

def parse_ms(text):
    answers = []

    # 🔥 FIX 1: support nested (1(e)(i)) also
    pattern = r"\d+\([a-z]\)(?:\([ivx]+\))?"
    matches = list(re.finditer(pattern, text))

    for i in range(len(matches)):
        start = matches[i].start()
        end = matches[i+1].start() if i+1 < len(matches) else len(text)

        block = text[start:end]
        q_no = matches[i].group()

        # ===============================
        # 🔥 FIX 2: NORMALIZE QUESTION KEY
        # ===============================
        # convert 1(e)(iv) → 1(iv)
        nested = re.match(r"(\d+)\([a-z]\)\(([ivx]+)\)", q_no)
        if nested:
            q_no = f"{nested.group(1)}({nested.group(2)})"

        # ===============================
        # 🔥 FIX 3: REMOVE GARBAGE TEXT
        # ===============================
        block = re.sub(r"Cambridge.*", "", block)
        block = re.sub(r"UCLES.*", "", block)
        block = re.sub(r"Page \d+.*", "", block)
        block = re.sub(r"Question Answer Marks.*", "", block)

        # ===============================
        # 🔥 FIX 4: REMOVE QUESTION NUMBER FROM ANSWER
        # ===============================
        block = block.replace(matches[i].group(), "")

      
        answer = re.sub(r"\s+", " ", block).strip()

        # remove trailing marks (e.g., "1", "2")
        answer = re.sub(r"\s\d+$", "", answer)

        answers.append({
            "question": q_no,
            "answer": answer
        })

    # ===============================
    # 🔥 FIX 6: REMOVE DUPLICATES
    # ===============================
    unique = {}
    for a in answers:
        unique[a["question"]] = a

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
    # ✅ clean mapping: question → answer
    ms_dict = {item["question"]: item["answer"] for item in ms}

    result = []

    for q in qp:
        q_no = q["question"]
        ans = ""

        # ===============================
        # ✅ 1. EXACT MATCH (MOST IMPORTANT)
        # ===============================
        if q_no in ms_dict:
            ans = ms_dict[q_no]

        # ===============================
        # ✅ 2. SAFE FALLBACK (ONLY IF NEEDED)
        # ===============================
        else:
            # handle case like 1(e) vs 1(e)(i)
            for key in ms_dict:
                if key.startswith(q_no + "("):   # 🔥 safer condition
                    ans = ms_dict[key]
                    break

        # ===============================
        # ✅ ADD TO RESULT
        # ===============================
        result.append({
            "question": q_no,
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
