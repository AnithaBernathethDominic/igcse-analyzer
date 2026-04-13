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
    """Extract full raw text from PDF using PyMuPDF."""
    text = ""
    pdf = fitz.open(stream=file.read(), filetype="pdf")
    for page in pdf:
        text += page.get_text() + "\n"
    return text

# ---------- STRIP QP NOISE ----------

def strip_qp_noise(text):
    """Remove IGCSE boilerplate from question paper text."""
    # Paper header / footer lines
    text = re.sub(r'0478/\d+[^\n]*', ' ', text)
    text = re.sub(r'© UCLES 202\d[^\n]*', ' ', text)
    text = re.sub(r'\[Turn over\]?', ' ', text)
    text = re.sub(r'DC \(JP/CGW\).*', ' ', text)
    text = re.sub(r'\* \d[\d ]+\d \*', ' ', text)
    text = re.sub(r'Working\s+space', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'Cambridge IGCSE[^\n]*', ' ', text)

    # Margin watermark — repeated "DO NOT WRITE IN THIS MARGIN" blocks
    text = re.sub(r'(?:DO NOT WRITE IN THIS MARGIN\s*)+', ' ', text, flags=re.IGNORECASE)

    # Copyright / permission paragraph (multi-line, appears at end of papers)
    text = re.sub(
        r'Permission to reproduce items.*?(?=\n\s*\n|\Z)',
        ' ', text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r'Every reasonable effort has been made.*?(?=\n\s*\n|\Z)',
        ' ', text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r'To avoid the issue of disclosure.*?(?=\n\s*\n|\Z)',
        ' ', text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r'Cambridge Assessment International Education.*?(?=\n\s*\n|\Z)',
        ' ', text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r'Cambridge Assessment is the brand name.*?(?=\n\s*\n|\Z)',
        ' ', text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r'This is produced for each series of examinations.*?(?=\n\s*\n|\Z)',
        ' ', text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r'www\.cambridgeinternational\.org[^\n]*', ' ', text)
    text = re.sub(r'BLANK\s+PAGE', ' ', text, flags=re.IGNORECASE)

    # Encoding artifacts
    text = re.sub(r'\(cid:\d+\)', ' ', text)
    text = re.sub(r'\bDFD\b', ' ', text)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)  # strip non-ASCII artifacts

    return text

# ---------- CLEAN QUESTION TEXT ----------

def clean_text(text):
    """Remove dot-lines, mark brackets and normalise whitespace."""
    text = re.sub(r'\.{3,}', '', text)
    text = re.sub(r'\[\d+\]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# ---------- PARSE QUESTION PAPER ----------

def parse_qp(raw_text):
    """
    Parse the IGCSE question paper into structured question objects.

    Key insight: PyMuPDF emits question numbers as '\\n1 \\n', '\\n2 \\n'
    (digit + trailing space + newline), whereas page numbers appear as
    '\\n2\\n' (digit, NO trailing space). We split on the question-number
    pattern only, so page numbers are never mistaken for question numbers.

    Sub-questions use letters (a-h) and sub-sub-questions use roman
    numerals. We deliberately avoid [a-z] for sub-letters because (i),
    (ii) etc. would collide with roman-numeral sub-sub-question markers.
    """
    text = strip_qp_noise(raw_text)

    questions = []

    # Split on main question numbers: \n<digits><space>\n
    main_pattern = re.compile(r'\n\s*(\d{1,2}) \n')
    parts = main_pattern.split(text)

    # parts = [preamble, "1", block1, "2", block2, ...]
    for i in range(1, len(parts), 2):
        q_num = int(parts[i])
        if not (1 <= q_num <= 20):
            continue
        block = parts[i + 1] if i + 1 < len(parts) else ""

        # Sub-questions: letters a-h only (avoids collision with roman i,v,x)
        sub_matches = list(re.finditer(r'\(([a-h])\)', block))

        for s_idx, s_match in enumerate(sub_matches):
            sub_id = s_match.group(1)
            sub_start = s_match.start()
            sub_end = (sub_matches[s_idx + 1].start()
                       if s_idx + 1 < len(sub_matches) else len(block))
            sub_text = block[sub_start:sub_end]

            # Sub-sub-questions: roman numerals (i), (ii), (iii), (iv)...
            ss_matches = list(re.finditer(
                r'\((i{1,3}|iv|vi{0,3}|ix|xi{0,3})\)', sub_text))

            if ss_matches:
                for ss_idx, ss_match in enumerate(ss_matches):
                    ss_id = ss_match.group(1)
                    ss_start = ss_match.start()
                    ss_end = (ss_matches[ss_idx + 1].start()
                               if ss_idx + 1 < len(ss_matches) else len(sub_text))
                    ss_text = sub_text[ss_start:ss_end]
                    qid = f"{q_num}({sub_id})({ss_id})"
                    cleaned = clean_text(ss_text)
                    if len(cleaned) > 10:
                        questions.append({"question": qid,
                                          "question_text": cleaned})
            else:
                qid = f"{q_num}({sub_id})"
                cleaned = clean_text(sub_text)
                # Remove MCQ option lines (A ... B ... C ... D ...)
                cleaned = re.sub(r'\b[A-D] [A-Za-z].*', '', cleaned)
                if len(cleaned) > 10:
                    questions.append({"question": qid,
                                      "question_text": cleaned})

    # Deduplicate - first occurrence wins
    unique = {}
    for q in questions:
        if q["question"] not in unique:
            unique[q["question"]] = q

    return list(unique.values())

# ---------- STRIP MS NOISE ----------

def strip_ms_noise(text):
    """Remove mark-scheme headers and mark-count columns."""
    text = re.sub(r'0478/\d+[^\n]*', ' ', text)
    text = re.sub(r'Cambridge IGCSE[^\n]*PUBLISHED[^\n]*', ' ', text)
    text = re.sub(r'© Cambridge University Press[^\n]*', ' ', text)
    text = re.sub(r'October/November 2025\s*', ' ', text)
    text = re.sub(r'Page \d+ of \d+\s*', ' ', text)
    text = re.sub(r'Question\s+Answer\s+Marks', ' ', text)
    text = re.sub(r'\bAnswer\b\s+\bMarks\b', ' ', text)

    # Margin watermark
    text = re.sub(r'(?:DO NOT WRITE IN THIS MARGIN\s*)+', ' ', text, flags=re.IGNORECASE)

    # Copyright / permission paragraph
    text = re.sub(
        r'Permission to reproduce items.*?(?=\n\s*\n|\Z)',
        ' ', text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r'Cambridge Assessment International Education.*?(?=\n\s*\n|\Z)',
        ' ', text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r'www\.cambridgeinternational\.org[^\n]*', ' ', text)
    text = re.sub(r'BLANK\s+PAGE', ' ', text, flags=re.IGNORECASE)

    # Standalone mark-count numbers on their own line
    text = re.sub(r'(?m)^\s*\d{1,2}\s*$', ' ', text)
    text = re.sub(r'\(cid:\d+\)', ' ', text)
    text = re.sub(r'\f', ' ', text)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)

    return text

# ---------- PARSE MARK SCHEME ----------

def parse_ms(raw_text):
    """
    Parse the mark scheme PDF into:
    [{ "question": "1(a)", "answer": "..." }, ...]
    """
    text = strip_ms_noise(raw_text)

    # Match labels like 1(a) or 1(e)(i)
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
        raw_answer = re.sub(r'\s+', ' ', raw_answer)
        answer = raw_answer.strip()

        # Remove trailing lone digit (residual mark count)
        answer = re.sub(r'\s+\d{1,2}$', '', answer).strip()
        answers.append({"question": q_label, "answer": answer})

    # Deduplicate - prefer non-empty answers
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

        # Fallback: collect answers from child questions e.g. 1(e)(i), 1(e)(ii)
        if not ans:
            child_answers = [ms_ans for ms_key, ms_ans in ms_dict.items()
                             if ms_key.startswith(q_key + "(")]
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
