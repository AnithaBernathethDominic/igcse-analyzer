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


# ---------- PDF TEXT EXTRACTION ----------
def extract_text_pages(file_storage, crop=True):
    """
    Extract text page-by-page using PyMuPDF.

    When crop=True, trims page margins to avoid barcode/footer/margin noise such as:
    - DO NOT WRITE IN THIS MARGIN
    - page numbers
    - turn over/footer strings
    - barcode junk / symbol garbage
    """
    pdf_bytes = file_storage.read()
    pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []

    for page in pdf:
        rect = page.rect

        if crop:
            # Keep the main content area only.
            # This removes most header/footer/margin garbage from Cambridge PDFs.
            clip = fitz.Rect(
                rect.width * 0.07,   # left
                rect.height * 0.06,  # top
                rect.width * 0.93,   # right
                rect.height * 0.90   # bottom
            )
            text = page.get_text("text", clip=clip)
        else:
            text = page.get_text("text")

        pages.append(text)

    return pages


def extract_text(file_storage):
    """Backwards-compatible raw text extractor."""
    return "\n".join(extract_text_pages(file_storage))


# ---------- COMMON CLEANING ----------
def normalize_unicode(text):
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("−", "-")
    text = text.replace("™", "")
    return text


def remove_binary_garbage(text):
    """Remove barcode / OCR / encoding junk lines."""
    cleaned_lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            cleaned_lines.append("")
            continue

        # Cambridge barcode / page artifact junk
        if re.fullmatch(r"\*\s*[0-9 ]+\s*\*", s):
            continue
        if re.search(r"[¬Ĭĥ¥ÕõąċČÛÙÀú¾´íÈÏĪÅ]+", s):
            continue
        if re.search(r"[,`´~^_]{2,}", s):
            continue
        if re.search(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", s):
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


# ---------- STRIP QP NOISE ----------
def strip_qp_noise(text):
    """Remove Cambridge boilerplate and repeating junk from question paper text."""
    text = normalize_unicode(text)
    text = remove_binary_garbage(text)

    # Header / footer / metadata
    patterns = [
        r'This document (?:consists of|has) \d+ (?:printed )?pages\.?',
        r'Cambridge IGCSE[^\n]*',
        r'COMPUTER SCIENCE\s*0478/12[^\n]*',
        r'Paper 1[^\n]*',
        r'1 hour 45 minutes',
        r'INSTRUCTIONS',
        r'INFORMATION',
        r'You must answer on the question paper\.?',
        r'No additional materials are needed\.?',
        r'Answer all questions\.?',
        r'Use a black or dark blue pen[^\n]*',
        r'Write your name[^\n]*',
        r'Write your answer[^\n]*',
        r'Do not use an erasable pen[^\n]*',
        r'Do not write on any bar codes\.?',
        r'Calculators must not be used in this paper\.?',
        r'The total mark for this paper is \d+\.?',
        r'The number of marks for each question[^\n]*',
        r'No marks will be awarded for using brand names[^\n]*',
        r'DC \([^)]+\)\s*\d+(?:/\d+)?',
        r'©\s*UCLES\s*202\d',
        r'\[Turn over\]',
        r'0478/12/M/J/25',
        r'Permission to reproduce items[^\n]*',
        r'Cambridge Assessment International Education[^\n]*',
        r'Local Examinations Syndicate \(UCLES\)[^\n]*',
        r'DO NOT WRITE IN THIS MARGIN(?:\s+DO NOT WRITE IN THIS MARGIN)*',
        r'Working space',
    ]
    for pat in patterns:
        text = re.sub(pat, ' ', text, flags=re.IGNORECASE)

    # Remove isolated page numbers
    text = re.sub(r'(?m)^\s*\d{1,2}\s*$', ' ', text)

    # Remove MCQ decorative letters if they appear alone on lines
    text = re.sub(r'(?m)^\s*[A-D]\s*$', ' ', text)

    # Remove leftover non-ascii junk that slipped through
    text = re.sub(r'[^\x09\x0A\x0D\x20-\x7E]', ' ', text)

    # Collapse excessive blank lines, but keep some structure for parsing
    text = re.sub(r'\n[ \t]+', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ---------- CLEAN QUESTION TEXT ----------
def clean_question_text(text):
    """Clean extracted question text without destroying math / binary values."""
    text = normalize_unicode(text)

    # Remove mark brackets like [2]
    text = re.sub(r'\[\d+\]', ' ', text)

    # Remove long answer lines / dot leaders
    text = re.sub(r'\.{3,}', ' ', text)

    # Remove leftover repeated margin/footer fragments
    text = re.sub(r'DO NOT WRITE IN THIS MARGIN(?:\s+DO NOT WRITE IN THIS MARGIN)*', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\bWorking space\b', ' ', text, flags=re.IGNORECASE)

    # Remove stray isolated page number at start/end
    text = re.sub(r'^\s*\d{1,2}\s+', '', text)
    text = re.sub(r'\s+\d{1,2}\s*$', '', text)

    # Remove obvious barcode remnants and odd junk tokens
    text = re.sub(r'\*\s*[0-9 ]+\s*\*', ' ', text)
    text = re.sub(r'[^\x09\x0A\x0D\x20-\x7E]', ' ', text)

    # Normalise whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{2,}', '\n', text)

    return text.strip()


def remove_trailing_mcq_options(text):
    """
    For MCQ-style stems like 2(a), remove trailing option lines from the question text.
    Example:
      A input
      B output
      C process
      D storage
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    kept = []
    for line in lines:
        if re.fullmatch(r'[A-D]\s+.+', line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


# ---------- PARSE QUESTION PAPER ----------
def parse_qp(raw_text):
    """
    Parse Cambridge question paper into:
    [{"question": "1(a)", "question_text": "..."}, ...]

    Fixes garbage attachment by:
    1. removing margin/header/footer/barcode noise aggressively
    2. cutting content before obvious page-end junk
    3. trimming option blocks and answer-space artifacts
    """
    text = strip_qp_noise(raw_text)
    questions = []

    # Main questions start at line beginning: 1, 2, 3 ...
    main_pattern = re.compile(r'(?m)^\s*(\d{1,2})\s+')
    main_matches = list(main_pattern.finditer(text))

    for i, m in enumerate(main_matches):
        q_num = int(m.group(1))
        if not (1 <= q_num <= 40):
            continue

        start = m.start()
        end = main_matches[i + 1].start() if i + 1 < len(main_matches) else len(text)
        block = text[start:end].strip()

        # Skip any preamble-like block without subparts
        sub_matches = list(re.finditer(r'\(([a-z])\)', block))
        if not sub_matches:
            continue

        for s_idx, s_match in enumerate(sub_matches):
            sub_id = s_match.group(1)
            sub_start = s_match.start()
            sub_end = sub_matches[s_idx + 1].start() if s_idx + 1 < len(sub_matches) else len(block)
            sub_text = block[sub_start:sub_end].strip()

            # Split sub-subparts such as (i), (ii), (iii)
            ss_matches = list(re.finditer(r'\(((?:ix)|(?:iv)|(?:v?i{1,3})|x)\)', sub_text))

            if ss_matches:
                for ss_idx, ss_match in enumerate(ss_matches):
                    ss_id = ss_match.group(1)
                    ss_start = ss_match.start()
                    ss_end = ss_matches[ss_idx + 1].start() if ss_idx + 1 < len(ss_matches) else len(sub_text)
                    ss_text = sub_text[ss_start:ss_end].strip()

                    cleaned = clean_question_text(ss_text)
                    cleaned = remove_trailing_mcq_options(cleaned)
                    qid = f"{q_num}({sub_id})({ss_id})"

                    if len(cleaned) > 10:
                        questions.append({
                            "question": qid,
                            "question_text": cleaned
                        })
            else:
                cleaned = clean_question_text(sub_text)
                cleaned = remove_trailing_mcq_options(cleaned)
                qid = f"{q_num}({sub_id})"

                if len(cleaned) > 10:
                    questions.append({
                        "question": qid,
                        "question_text": cleaned
                    })

    # Deduplicate - first clean occurrence wins
    unique = {}
    for q in questions:
        if q["question"] not in unique:
            unique[q["question"]] = q

    return list(unique.values())


# ---------- STRIP MS NOISE ----------
def strip_ms_noise(text):
    """Remove mark-scheme headers and mark-count columns."""
    text = normalize_unicode(text)
    text = remove_binary_garbage(text)

    patterns = [
        r'0478/12[^\n]*',
        r'Cambridge IGCSE[^\n]*Mark Scheme[^\n]*',
        r'PUBLISHED',
        r'May/June 2025',
        r'© Cambridge University Press[^\n]*',
        r'Page \d+ of \d+',
        r'Generic Marking Principles[\s\S]*?Question\s+Answer\s+Marks',
        r'Annotations guidance for centres[\s\S]*?Question\s+Answer\s+Marks',
        r'Question\s+Answer\s+Marks',
        r'Annotation Meaning[^\n]*',
        r'Annotations',
        r'Examples:',
    ]
    for pat in patterns:
        text = re.sub(pat, ' ', text, flags=re.IGNORECASE)

    # Standalone marks column numbers
    text = re.sub(r'(?m)^\s*\d{1,2}\s*$', ' ', text)

    # Remove leftover non-ascii junk
    text = re.sub(r'[^\x09\x0A\x0D\x20-\x7E]', ' ', text)
    text = re.sub(r'\n[ \t]+', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ---------- PARSE MARK SCHEME ----------
def parse_ms(raw_text):
    """
    Parse the mark scheme PDF into:
    [{"question": "1(a)", "answer": "..."}, ...]
    """
    text = strip_ms_noise(raw_text)

    q_pattern = re.compile(r'(?<!\w)(\d+\([a-z]\)(?:\([ivx]+\))?)(?!\w)')
    matches = list(q_pattern.finditer(text))
    answers = []

    for i, m in enumerate(matches):
        q_label = m.group(1).strip()
        ans_start = m.end()
        ans_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw_answer = text[ans_start:ans_end]

        answer = raw_answer.strip()
        answer = re.sub(r'\s+', ' ', answer)

        # Remove trailing lone mark-count residue if present
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
            child_answers = [
                ms_ans for ms_key, ms_ans in ms_dict.items()
                if ms_key.startswith(q_key + "(")
            ]
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

        qp_pages = extract_text_pages(qp_file, crop=True)
        ms_pages = extract_text_pages(ms_file, crop=True)

        qp_text = "\n".join(qp_pages)
        ms_text = "\n".join(ms_pages)

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
