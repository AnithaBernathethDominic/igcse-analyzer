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
    text = re.sub(r'0478/\d+[^\n]*', ' ', text)
    text = re.sub(r'© UCLES 202\d[^\n]*', ' ', text)
    text = re.sub(r'\[Turn over\]?', ' ', text)
    text = re.sub(r'DC \(.*?\).*', ' ', text)
    text = re.sub(r'\* \d[\d ]+\d \*', ' ', text)
    text = re.sub(r'Working\s+space', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'Cambridge IGCSE[^\n]*', ' ', text)
    text = re.sub(r'\(cid:\d+\)', ' ', text)
    text = re.sub(r'\bDFD\b', ' ', text)
    text = re.sub(r'(?m)^\s*\d{1,3}\s*$', ' ', text)
    text = re.sub(r'(?i)(january|february|march|april|may|june|july|august|september|october|november|december)\s*/?\s*\d{4}[^\n]*', ' ', text)
    text = re.sub(r"For\s+Examiner[''s]*\s+Use", ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'BLANK PAGE[^\n]*', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'Do not write[^\n]*', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)
    return text

# ---------- CLEAN QUESTION TEXT ----------
def clean_text(text):
    text = re.sub(r'\.{3,}', '', text)
    text = re.sub(r'\[\d+\]', '', text)
    text = re.sub(r'\(\d+\)', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# ---------- DEBUG ----------
def debug_qp_structure(text):
    print("\n=== RAW TEXT SAMPLE (first 3000 chars after noise strip) ===")
    print(repr(text[:3000]))
    print("=== END SAMPLE ===\n")
    for q in range(1, 11):
        for m in re.finditer(rf'(.{{0,30}})(\b{q}\b)(.{{0,30}})', text):
            print(f"  [DEBUG] Q{q} context: {repr(m.group(0))}")
            break

# ---------- PARSE QUESTION PAPER ----------
def parse_qp(raw_text):
    text = strip_qp_noise(raw_text)
    debug_qp_structure(text)

    questions = []
    parts = []

    # Try strategies from most to least strict
    strategies = [
        (r'\n\s*(\d{1,2}) \n',                         "digit+space+newline"),
        (r'\n\s*(\d{1,2})\s*\n',                        "digit+newline"),
        (r'\n{2,}\s*(\d{1,2})[ \t]*\n',                 "double-newline+digit"),
        (r'(?m)^[ \t]*(\d{1,2})[ \t]+(?=[A-Z(])',       "line-start digit before uppercase"),
        (r'(?<!\d)[ \t]*\n[ \t]*(\d{1,2})[ \t]*\n[ \t]*(?!\d)', "loose newline-digit-newline"),
    ]

    for pattern, name in strategies:
        p = re.compile(pattern)
        parts = p.split(text)
        print(f"[DEBUG] Strategy '{name}': {len(parts)} parts")
        if len(parts) > 3:
            print(f"[DEBUG] Using strategy: {name}")
            break

    if len(parts) <= 3:
        print("[ERROR] No strategy matched. Dumping full text for inspection:")
        print(repr(text[:5000]))
        return []

    for i in range(1, len(parts), 2):
        try:
            q_num = int(parts[i].strip())
        except ValueError:
            continue

        if not (1 <= q_num <= 20):
            continue

        block = parts[i + 1] if i + 1 < len(parts) else ""

        sub_matches = list(re.finditer(r'\(([a-h])\)', block))

        if not sub_matches:
            qid = f"{q_num}"
            cleaned = clean_text(block)
            cleaned = re.sub(r'\b[A-D] [A-Za-z].*', '', cleaned)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()
            if len(cleaned) > 10:
                questions.append({"question": qid, "question_text": cleaned})
            continue

        for s_idx, s_match in enumerate(sub_matches):
            sub_id = s_match.group(1)
            sub_start = s_match.start()
            sub_end = (sub_matches[s_idx + 1].start()
                       if s_idx + 1 < len(sub_matches) else len(block))
            sub_text = block[sub_start:sub_end]

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
                        questions.append({"question": qid, "question_text": cleaned})
            else:
                qid = f"{q_num}({sub_id})"
                cleaned = clean_text(sub_text)
                cleaned = re.sub(r'\b[A-D] [A-Za-z].*', '', cleaned)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                if len(cleaned) > 10:
                    questions.append({"question": qid, "question_text": cleaned})

    unique = {}
    for q in questions:
        if q["question"] not in unique:
            unique[q["question"]] = q

    print(f"[DEBUG] Total questions parsed: {len(unique)}")
    return list(unique.values())

# ---------- STRIP MS NOISE ----------
def strip_ms_noise(text):
    text = re.sub(r'0478/\d+[^\n]*', ' ', text)
    text = re.sub(r'Cambridge IGCSE[^\n]*PUBLISHED[^\n]*', ' ', text)
    text = re.sub(r'© Cambridge University Press[^\n]*', ' ', text)
    text = re.sub(r'(?i)(january|february|march|april|may|june|july|august|september|october|november|december)\s*/?\s*\d{4}\s*', ' ', text)
    text = re.sub(r'Page \d+ of \d+\s*', ' ', text)
    text = re.sub(r'Question\s+Answer\s+Marks', ' ', text)
    text = re.sub(r'\bAnswer\b\s+\bMarks\b', ' ', text)
    text = re.sub(r'(?m)^\s*\d{1,2}\s*$', ' ', text)
    text = re.sub(r'\(cid:\d+\)', ' ', text)
    text = re.sub(r'\f', ' ', text)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)
    return text

# ---------- PARSE MARK SCHEME ----------
def parse_ms(raw_text):
    text = strip_ms_noise(raw_text)

    print("\n=== MS RAW TEXT SAMPLE (first 2000 chars) ===")
    print(repr(text[:2000]))
    print("=== END MS SAMPLE ===\n")

    q_pattern = re.compile(
        r'(?<!\w)'
        r'(\d+\([a-z]\)(?:\([ivx]+\))?)'
        r'(?!\w)'
    )

    matches = list(q_pattern.finditer(text))
    print(f"[DEBUG] MS labels found: {[m.group(1) for m in matches]}")

    answers = []
    for i, m in enumerate(matches):
        q_label = m.group(1).strip()
        ans_start = m.end()
        ans_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw_answer = text[ans_start:ans_end]
        raw_answer = re.sub(r'\s+', ' ', raw_answer)
        answer = raw_answer.strip()
        answer = re.sub(r'\s+\d{1,2}$', '', answer).strip()
        answer = re.sub(r'^[\s;:,/\\|]+', '', answer).strip()
        answer = re.sub(r'[\s;:,/\\|]+$', '', answer).strip()
        answers.append({"question": q_label, "answer": answer})

    unique = {}
    for a in answers:
        key = a["question"]
        if key not in unique or (not unique[key]["answer"] and a["answer"]):
            unique[key] = a

    print(f"[DEBUG] Total MS answers parsed: {len(unique)}")
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
        import traceback
        traceback.print_exc()
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
    STOPWORDS = {
        "the", "and", "that", "this", "with", "from", "have", "which",
        "will", "when", "what", "where", "there", "their", "they", "than",
        "then", "each", "such", "into", "used", "uses", "using", "would",
        "could", "should", "about", "after", "before", "other", "also",
        "more", "some", "been", "were", "being", "because", "while"
    }
    data = request.json
    student = data.get("student", "").lower()
    correct = data.get("correct", "").lower()

    keywords = list(set([
        word for word in re.findall(r'[a-z]+', correct)
        if len(word) > 4 and word not in STOPWORDS
    ]))

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
