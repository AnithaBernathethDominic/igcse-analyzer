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

# ---------- STRIP ALL NOISE ----------
def strip_all_noise(text):
    """
    Single comprehensive noise stripper for IGCSE Cambridge PDFs.
    Handles page boundary artifacts, barcodes, margin text, legal boilerplate,
    answer dot-leaders, and mark brackets.
    """
    # Barcode / security strings
    text = re.sub(r'\* \d{13} \*', ' ', text)
    text = re.sub(r',[\x00-\x1F\t]+,', ' ', text)

    # Strip all non-printable / non-ASCII characters (barcodes, special chars)
    # Keep standard ASCII printable + newline + hyphen/dash variants
    text = re.sub(r'[^\x20-\x7E\n\'\"\-]+', ' ', text)

    # Cambridge headers & footers
    text = re.sub(r'0478/\d+/[A-Z]/[A-Z]/\d+', ' ', text)  # 0478/12/M/J/25
    text = re.sub(r'0478/\d+', ' ', text)
    text = re.sub(r'UCLES 202\d', ' ', text)
    text = re.sub(r'Cambridge IGCSE[^\n]*', ' ', text)
    text = re.sub(r'Cambridge Assessment[^\n]*', ' ', text)
    text = re.sub(r'Local Examinations[^\n]*', ' ', text)
    text = re.sub(r'\[Turn over\]?', ' ', text)
    text = re.sub(r'DC \([A-Z/]+\) \d+/\d+', ' ', text)

    # Margin text
    text = re.sub(r'DO NOT WRITE IN THIS MARGIN', ' ', text)

    # Page intro boilerplate (cover page)
    text = re.sub(r'(?m)^\s*This document has \d+ pages\.\s*$', ' ', text)
    text = re.sub(r'(?m)^\s*\d+ hour[^\n]*$', ' ', text)
    text = re.sub(r'(?m)^\s*(You must answer|No additional|INSTRUCTIONS|INFORMATION)[^\n]*$', ' ', text)
    text = re.sub(r'Answer all questions\.[^\n]*', ' ', text)
    text = re.sub(r'Use a black or dark[^\n]*', ' ', text)
    text = re.sub(r'Write your name[^\n]*', ' ', text)
    text = re.sub(r'Do not use an erasable[^\n]*', ' ', text)
    text = re.sub(r'Do not write on any[^\n]*', ' ', text)
    text = re.sub(r'Calculators must not[^\n]*', ' ', text)
    text = re.sub(r'The total mark[^\n]*', ' ', text)
    text = re.sub(r'The number of marks[^\n]*', ' ', text)
    text = re.sub(r'No marks will be awarded[^\n]*', ' ', text)

    # Legal boilerplate (last page)
    text = re.sub(r'Permission to reproduce.*', ' ', text, flags=re.DOTALL)

    # Standalone page numbers (a lone 1–3 digit number on its own line)
    text = re.sub(r'(?m)^\s*\d{1,3}\s*$', ' ', text)

    # Answer spaces
    text = re.sub(r'\.{5,}', '', text)        # dot leaders
    text = re.sub(r'\[\d+\]', '', text)        # mark allocations [2]
    text = re.sub(r'(?m)^\s*Working space\s*$', '', text, flags=re.IGNORECASE)

    # Standalone answer-blank numbered lines like "1  " "2  " (robot component blanks)
    text = re.sub(r'(?m)^\s*[12]\s*$', ' ', text)

    # Normalise whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)

    return text.strip()

# ---------- CLEAN QUESTION TEXT ----------
def clean_qtext(raw):
    """
    Apply noise stripping + question-specific cleanup to a raw question block.
    """
    text = strip_all_noise(raw)

    # Remove MCQ option lines:  A input  /  B output  etc.
    text = re.sub(r'\b[A-D]\s+(input|output|process|storage|compil\w*|interpret\w*)\b[^\n]*', '', text, flags=re.IGNORECASE)

    # Remove diagram labels that are not part of the question text
    text = re.sub(r"URL input into\s*patient.?s computer", '', text)
    text = re.sub(r"Patient.?s\s*computer", '', text)
    text = re.sub(r'www\.[a-zA-Z0-9.\-]+\.com\b', '', text)
    text = re.sub(r'\bComponent\s+Description\b', '', text)   # table header

    # Collapse remaining whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ---------- PARSE QUESTION PAPER ----------
def parse_qp(raw_text):
    """
    Parse IGCSE question paper PDF text into structured question objects.

    Question numbers in this PDF appear as:  \\n<digit(s)> \\n
    (digit + trailing space before newline — distinguishes from lone page numbers).

    Sub-questions:  (a)-(h)
    Sub-sub-questions: roman numerals (i), (ii), (iii) ...
    """
    # Split on main question numbers
    main_pattern = re.compile(r'\n(\d{1,2}) \n')
    parts = main_pattern.split(raw_text)

    if len(parts) <= 3:
        # Fallback: try without the trailing space requirement
        main_pattern = re.compile(r'\n\s*(\d{1,2})\s*\n')
        parts = main_pattern.split(raw_text)

    print(f"[DEBUG] parse_qp: {len(parts)} parts, questions: {[parts[i] for i in range(1, len(parts), 2)]}")

    questions = []

    for i in range(1, len(parts), 2):
        try:
            q_num = int(parts[i].strip())
        except ValueError:
            continue
        if not (1 <= q_num <= 20):
            continue

        block = parts[i + 1] if i + 1 < len(parts) else ""

        # Find sub-questions (a)-(h)
        sub_matches = list(re.finditer(r'\(([a-h])\)', block))

        if not sub_matches:
            # No sub-questions — store whole block as top-level
            cleaned = clean_qtext(block)
            if len(cleaned) > 15:
                questions.append({"question": str(q_num), "question_text": cleaned})
            continue

        for s_idx, s_match in enumerate(sub_matches):
            sub_id = s_match.group(1)
            sub_start = s_match.start()
            sub_end = (sub_matches[s_idx + 1].start()
                       if s_idx + 1 < len(sub_matches) else len(block))
            sub_text = block[sub_start:sub_end]

            # Find sub-sub-questions (roman numerals)
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
                    cleaned = clean_qtext(ss_text)
                    if len(cleaned) > 15:
                        questions.append({"question": qid, "question_text": cleaned})
            else:
                qid = f"{q_num}({sub_id})"
                cleaned = clean_qtext(sub_text)
                if len(cleaned) > 15:
                    questions.append({"question": qid, "question_text": cleaned})

    # Deduplicate — first occurrence wins
    unique = {}
    for q in questions:
        if q["question"] not in unique:
            unique[q["question"]] = q

    print(f"[DEBUG] parse_qp: {len(unique)} unique questions extracted")
    return list(unique.values())

# ---------- STRIP MS NOISE ----------
def strip_ms_noise(text):
    """Remove mark-scheme specific headers and formatting."""
    text = re.sub(r'0478/\d+[^\n]*', ' ', text)
    text = re.sub(r'Cambridge IGCSE[^\n]*PUBLISHED[^\n]*', ' ', text)
    text = re.sub(r'Cambridge University Press[^\n]*', ' ', text)
    text = re.sub(r'Cambridge Assessment[^\n]*', ' ', text)
    text = re.sub(r'(?i)(january|february|march|april|may|june|july|august|'
                  r'september|october|november|december)\s*/?\s*\d{4}\s*', ' ', text)
    text = re.sub(r'Page \d+ of \d+\s*', ' ', text)
    text = re.sub(r'Question\s+Answer\s+Marks', ' ', text)
    text = re.sub(r'\bAnswer\b\s+\bMarks\b', ' ', text)
    text = re.sub(r'(?m)^\s*\d{1,2}\s*$', ' ', text)  # standalone mark counts
    text = re.sub(r'\(cid:\d+\)', ' ', text)
    text = re.sub(r'\f', ' ', text)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)
    return text

# ---------- PARSE MARK SCHEME ----------
def parse_ms(raw_text):
    """
    Parse mark scheme PDF into:
      [{ "question": "1(a)", "answer": "..." }, ...]
    """
    text = strip_ms_noise(raw_text)

    q_pattern = re.compile(
        r'(?<!\w)'
        r'(\d+\([a-z]\)(?:\([ivx]+\))?)'
        r'(?!\w)'
    )
    matches = list(q_pattern.finditer(text))
    print(f"[DEBUG] parse_ms: {len(matches)} labels found: {[m.group(1) for m in matches]}")

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

    # Deduplicate — prefer non-empty answers
    unique = {}
    for a in answers:
        key = a["question"]
        if key not in unique or (not unique[key]["answer"] and a["answer"]):
            unique[key] = a

    print(f"[DEBUG] parse_ms: {len(unique)} answers extracted")
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
    """
    Concept-based scoring:
    - Splits mark-scheme answer into individual marking concepts
      (separated by // ; or sentence boundaries).
    - A concept is "matched" if the student hits >= 25% of its keywords.
    - Each matched concept = 1 mark, capped at inferred max marks.
    - Correctly handles "Any one from:" / "Two from:" preambles.
    """

    STOPWORDS = {
        "the", "and", "that", "this", "with", "from", "have", "which",
        "will", "when", "what", "where", "there", "their", "they", "than",
        "then", "each", "such", "into", "uses", "using", "would",
        "could", "should", "about", "after", "before", "other", "also",
        "more", "some", "been", "were", "being", "because", "while",
        "any", "give", "state", "explain", "describe", "must", "not",
        "can", "per", "are", "has", "had", "used", "for", "its"
    }

    def split_into_concepts(answer):
        """Split MS answer into individual marking point strings."""
        clean = re.sub(
            r'(?i)^(any|one|two|three|four|five)\s+(mark[s]?\s+)?(from|for\s+each)[:\s]*',
            '', answer.strip())
        # Primary split: explicit // or ;
        parts = re.split(r'\s*//\s*|\s*;\s*', clean)
        # If only 1 part, try sentence-boundary split
        if len(parts) == 1:
            parts = re.split(r'(?<=[a-z]{3})\.\s+(?=[A-Z])', parts[0])
        return [p.strip() for p in parts if len(p.strip()) > 6]

    def extract_max_marks(answer):
        """Infer max marks from preamble like 'Any two from:' or 'Two from:'."""
        nums = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
                '1': 1, '2': 2, '3': 3, '4': 4, '5': 5}
        m = re.match(r'(?i)any\s+(\w+)\s+from', answer.strip())
        if m:
            return nums.get(m.group(1).lower(), 4)
        m = re.match(r'(?i)(\w+)\s+from[:\s]', answer.strip())
        if m:
            return nums.get(m.group(1).lower(), 4)
        m = re.match(r'(?i)(\w+)\s+mark', answer.strip())
        if m:
            return nums.get(m.group(1).lower(), 4)
        return 4   # default

    data_in  = request.json
    student  = data_in.get("student", "").lower()
    correct  = data_in.get("correct", "")
    correct_l = correct.lower()

    max_marks = extract_max_marks(correct_l)
    concepts  = split_into_concepts(correct_l)
    if not concepts:
        concepts = [correct_l]

    THRESHOLD = 0.25   # fraction of concept keywords student must match

    matched_concepts = 0
    all_matched_kws  = []
    all_missing_kws  = []

    for concept in concepts:
        kws = list(set([
            w for w in re.findall(r'[a-z]+', concept)
            if len(w) > 3 and w not in STOPWORDS
        ]))
        if not kws:
            continue
        matched = [w for w in kws
                   if re.search(r'\b' + re.escape(w) + r'\b', student)]
        ratio = len(matched) / len(kws)
        if ratio >= THRESHOLD:
            matched_concepts += 1
            all_matched_kws.extend(matched)
        else:
            all_missing_kws.extend([w for w in kws if w not in matched])

    marks = min(max_marks, matched_concepts)
    all_matched_kws = list(set(all_matched_kws))
    all_missing_kws = list(set(all_missing_kws) - set(all_matched_kws))

    # Build highlighted student answer (word-boundary safe)
    highlighted = student
    for word in sorted(all_matched_kws, key=len, reverse=True):
        highlighted = re.sub(
            r'\b' + re.escape(word) + r'\b',
            f"<span style='color:green;font-weight:bold'>{word}</span>",
            highlighted
        )

    ratio_overall = marks / max_marks if max_marks > 0 else 0
    if ratio_overall >= 1.0:
        comment = "Excellent answer. Accurate use of key terminology."
    elif ratio_overall >= 0.5:
        comment = "Good attempt. Some key points missing."
    elif marks > 0:
        comment = "Partial credit. Expand your answer with more detail."
    else:
        comment = "Basic response. Review the model answer and try again."

    return jsonify({
        "marks":       f"{marks}/{max_marks}",
        "matched":     all_matched_kws[:6],
        "missing":     all_missing_kws[:6],
        "comment":     comment,
        "highlighted": highlighted,
        "model":       correct
    })

if __name__ == "__main__":
    app.run(debug=True)
