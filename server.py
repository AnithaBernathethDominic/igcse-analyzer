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


# ---------- EXTRACT TEXT (PAGE-AWARE) ----------
def extract_text(file):
    """
    Extract text from PDF using PyMuPDF, page by page.
    Returns a list of (page_number, page_text) tuples so we can
    strip lone page-number lines that appear at the top/bottom of each page.
    """
    pages = []
    pdf = fitz.open(stream=file.read(), filetype="pdf")
    for page_index, page in enumerate(pdf):
        text = page.get_text()
        pages.append((page_index + 1, text))   # 1-based page number
    return pages


def pages_to_clean_text(pages):
    """
    Join pages into a single string, removing the lone page-number
    lines that PyMuPDF emits at the top of each page for IGCSE papers.
    These look like a bare integer (2, 3, 4 …) on its own line and are
    the root cause of false question-number matches.
    """
    parts = []
    for page_num, text in pages:
        # Split into lines, drop lines that are ONLY a page number
        lines = text.splitlines()
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            # Drop lines that are purely a small integer (page numbers are
            # typically 1-12 for IGCSE papers).  We also drop the barcode
            # artefact lines that start with * digits *.
            if re.fullmatch(r'\d{1,2}', stripped):
                continue
            if re.match(r'^\*\s*\d{10,}\s*\*', stripped):
                continue
            cleaned_lines.append(line)
        parts.append('\n'.join(cleaned_lines))
    return '\n'.join(parts)


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
def parse_qp(pages):
    """
    Parse the question paper.

    KEY FIX: we work from the page-aware text so that bare integers that
    are page numbers have already been removed before we try to identify
    question numbers.  We then use a tight regex that requires a question
    number to be followed immediately by a sub-question letter in
    parentheses, e.g. '1 (a)' or '1(a)', rather than just any lone digit.
    """
    # Build clean text with page numbers stripped
    text = pages_to_clean_text(pages)
    text = strip_qp_noise(text)
    text = re.sub(r'\s+', ' ', text)

    questions = []

    # -------------------------------------------------------
    # SPLIT ON MAIN QUESTION BOUNDARIES
    # A main question starts with a digit (1-20) that is either:
    #   • at the very start of the string, OR
    #   • preceded by whitespace
    # AND is immediately followed by whitespace then a '(' letter ')'
    # sub-question marker.
    #
    # This prevents bare page numbers (2, 3, 4…) from being treated as
    # question boundaries because a page number is never followed by (a).
    # -------------------------------------------------------
    # Pattern: word boundary, 1-2 digit number (1-20), space(s), then (letter)
    main_pattern = re.compile(
        r'(?<!\d)'               # not preceded by digit
        r'(?<!\w)'               # not preceded by word char
        r'(\d{1,2})'             # capture: the question number
        r'(?=\s+\([a-z]\))'     # lookahead: whitespace then (a)/(b)/...
    )

    # Find all main question positions
    main_matches = list(main_pattern.finditer(text))

    # Filter to only real question numbers (1-20)
    main_matches = [m for m in main_matches if 1 <= int(m.group(1)) <= 20]

    # Deduplicate consecutive matches with the same number
    deduped = []
    seen_nums = set()
    for m in main_matches:
        n = int(m.group(1))
        if n not in seen_nums:
            seen_nums.add(n)
            deduped.append(m)
    main_matches = deduped

    # Slice text into per-main-question blocks
    blocks = []
    for idx, m in enumerate(main_matches):
        start = m.start()
        end = main_matches[idx + 1].start() if idx + 1 < len(main_matches) else len(text)
        blocks.append((m.group(1), text[start:end]))

    # -------------------------------------------------------
    # WITHIN EACH BLOCK, SPLIT ON SUB-QUESTIONS (a), (b), …
    # -------------------------------------------------------
    sub_pattern = re.compile(r'(?=\(([a-z])\))')

    for q_no, block in blocks:
        sub_blocks = sub_pattern.split(block)
        # sub_pattern.split alternates: [prefix, letter, text, letter, text, …]
        # Use finditer instead for cleaner extraction
        sub_matches = list(re.finditer(r'\(([a-z])\)', block))

        for s_idx, s_match in enumerate(sub_matches):
            sub_id = s_match.group(1)
            sub_start = s_match.start()
            sub_end = sub_matches[s_idx + 1].start() if s_idx + 1 < len(sub_matches) else len(block)
            sub_text = block[sub_start:sub_end]

            # Check for sub-sub questions: (i), (ii), (iii), (iv)
            subsub_pattern = re.compile(r'\(([ivx]+)\)')
            subsub_matches = list(subsub_pattern.finditer(sub_text))

            if subsub_matches:
                for ss_idx, ss_match in enumerate(subsub_matches):
                    ss_id = ss_match.group(1)
                    ss_start = ss_match.start()
                    ss_end = subsub_matches[ss_idx + 1].start() if ss_idx + 1 < len(subsub_matches) else len(sub_text)
                    ss_text = sub_text[ss_start:ss_end]

                    qid = f"{q_no}({sub_id})({ss_id})"
                    cleaned = clean_text(ss_text)
                    # Remove MCQ options A/B/C/D
                    cleaned = re.sub(r'\b[A-D]\s+[A-Za-z].*?(?=[A-D]\s|$)', '', cleaned)
                    if len(cleaned) > 10:
                        questions.append({
                            "question": qid,
                            "question_text": cleaned
                        })
            else:
                qid = f"{q_no}({sub_id})"
                cleaned = clean_text(sub_text)
                cleaned = re.sub(r'\b[A-D]\s+[A-Za-z].*?(?=[A-D]\s|$)', '', cleaned)
                if len(cleaned) > 10:
                    questions.append({
                        "question": qid,
                        "question_text": cleaned
                    })

    # Deduplicate – prefer non-empty text
    unique = {}
    for q in questions:
        key = q["question"]
        if key not in unique or (not unique[key]["question_text"] and q["question_text"]):
            unique[key] = q

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
    text = re.sub(r'(?m)^\s*\d{1,2}\s*$', '', text)

    # Remove PDF encoding artifacts
    text = re.sub(r'\(cid:\d+\)', '', text)
    text = re.sub(r'\f', ' ', text)
    return text


# ---------- PARSE MARK SCHEME ----------
def parse_ms(pages):
    """
    Parse the mark scheme PDF into:
    [{ "question": "1(a)", "answer": "..." }, ...]

    KEY FIX: same page-aware approach removes lone page numbers before
    we run the question-label regex, preventing false matches.
    """
    text = pages_to_clean_text(pages)
    text = strip_ms_noise(text)

    # Match question labels: 1(a) or 1(e)(i)
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

    # Deduplicate — prefer non-empty answers
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

        qp_pages = extract_text(qp_file)
        ms_pages = extract_text(ms_file)

        qp_data = parse_qp(qp_pages)
        ms_data = parse_ms(ms_pages)

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
