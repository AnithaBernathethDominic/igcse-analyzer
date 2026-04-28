from flask import Flask, request, jsonify, render_template

import re

import json

import os

import fitz  # PyMuPDF

import anthropic

from supabase import create_client

app = Flask(__name__)

# ---------- SUPABASE CONFIG ----------

SUPABASE_URL = os.getenv("SUPABASE_URL")

SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------- RULE-BASED TOPIC CLASSIFIER ----------
# Built from CS-Paper1-topics.docx.
# This removes the need for topics.json and gives better subtopic-level mapping.

TOPIC_RULES = [
    {"topic": "1. Data Representation", "subtopic": "1.1 Number Systems",
     "keywords": ["binary","denary","hexadecimal","hex","convert","conversion","overflow","shift","logical shift","left shift","right shift","two's complement","twos complement","8-bit","12-bit","register","add binary","binary addition","negative denary","largest denary","largest hexadecimal"]},
    {"topic": "1. Data Representation", "subtopic": "1.2 Text, Sound and Images",
     "keywords": ["sample rate","sampling rate","sample resolution","sampling resolution","resolution","colour depth","color depth","ascii","unicode","character set","pixel","pixels","image","sound","text"]},
    {"topic": "1. Data Representation", "subtopic": "1.3 Data Storage and Compression",
     "keywords": ["file size","compression","compress","compressed","rle","run length encoding","lossy","lossless","bit","byte","nibble","kib","mib","gib","tib","storage units"]},
    {"topic": "2. Data Transmission", "subtopic": "2.1 Transmission Methods",
     "keywords": ["packet","packets","header","payload","trailer","packet switching","transmission method","serial","parallel","simplex","half-duplex","full-duplex","duplex","usb"]},
    {"topic": "2. Data Transmission", "subtopic": "2.2 Error Detection",
     "keywords": ["parity","parity check","checksum","echo check","check digit","isbn","barcode","bar code","arq","automatic repeat request","error detection","acknowledgement","timeout"]},
    {"topic": "2. Data Transmission", "subtopic": "2.3 Encryption",
     "keywords": ["encryption","encrypt","encrypted","symmetric","asymmetric","public key","private key","plain text","cipher text","key"]},
    {"topic": "3. Hardware", "subtopic": "3.1 Computer Architecture",
     "keywords": ["cpu","microprocessor","alu","cu","control unit","arithmetic logic unit","register","pc","mar","mdr","cir","acc","accumulator","program counter","fde","fetch","decode","execute","fetch-decode-execute","clock speed","cache","core","cores","embedded system","von neumann"]},
    {"topic": "3. Hardware", "subtopic": "3.2 Input and Output Devices",
     "keywords": ["input device","output device","keyboard","scanner","barcode scanner","qr code scanner","digital camera","microphone","mouse","touch screen","printer","speaker","projector","screen","sensor","temperature sensor","pressure sensor","light sensor","humidity sensor","gas sensor","proximity sensor"]},
    {"topic": "3. Hardware", "subtopic": "3.3 Data Storage",
     "keywords": ["ram","rom","primary storage","secondary storage","magnetic","optical","solid-state","solid state","ssd","hdd","virtual memory","cloud storage","tracks","sectors","pits","lands","nand","nor","floating gate","control gate"]},
    {"topic": "3. Hardware", "subtopic": "3.4 Network Hardware",
     "keywords": ["nic","network interface card","mac","mac address","ip address","ipv4","ipv6","router"]},
    {"topic": "4. Software", "subtopic": "4.1 System and Application Software",
     "keywords": ["system software","application software","operating system","os","memory management","file management","security","interrupt","hardware interrupt","software interrupt","interrupt service routine","isr","queue"]},
    {"topic": "4. Software", "subtopic": "4.2 Programming Languages and Translators",
     "keywords": ["high-level","high level","low-level","low level","assembly language","assembler","compiler","interpreter","translator","ide","integrated development environment","machine code","mnemonic","executable file","line by line","whole code"]},
    {"topic": "5. Internet and Its Uses", "subtopic": "5.1 Internet Basics",
     "keywords": ["internet","world wide web","www","url","http","https","web browser","browser","website","web page"]},
    {"topic": "5. Internet and Its Uses", "subtopic": "5.2 Digital Currency",
     "keywords": ["digital currency","cryptocurrency","blockchain","bitcoin","electronic money","e-wallet","wallet"]},
    {"topic": "5. Internet and Its Uses", "subtopic": "5.3 Cyber Security",
     "keywords": ["malware","phishing","firewall","anti-malware","virus","hacking","password","authentication","biometric","fingerprint","social engineering","spyware","brute force"]},
    {"topic": "6. Automated and Emerging Technologies", "subtopic": "Automated Systems, Robotics and AI",
     "keywords": ["automated system","automation","robotics","robot","ai","artificial intelligence","expert system","machine learning","actuator","microprocessor","sensor"]},
    {"topic": "7. Algorithm Design", "subtopic": "Algorithm Design and Problem Solving",
     "keywords": ["algorithm","decomposition","flowchart","pseudocode","validation","verification","test data","normal data","abnormal data","extreme data","trace table","dry run","logic error","syntax error"]},
    {"topic": "8. Programming", "subtopic": "Programming Concepts",
     "keywords": ["variable","constant","sequence","selection","iteration","loop","for loop","while loop","repeat until","array","file handling","read file","write file","procedure","function","parameter"]},
    {"topic": "9. Databases", "subtopic": "Databases",
     "keywords": ["database","table","field","record","primary key","foreign key","query","sql","data type","form","report"]},
    {"topic": "10. Boolean Logic", "subtopic": "Logic Gates, Truth Tables and Logic Circuits",
     "keywords": ["logic gate","and gate","or gate","not gate","nand","nor","xor","truth table","logic circuit","logic expression","boolean","problem statement"]},
]

def _normalise_for_topic(text):
    text = text.lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()

def classify_topic(question_text, top_n=3):
    q = _normalise_for_topic(question_text)
    scored = []

    for rule in TOPIC_RULES:
        score = 0
        matched = []

        for keyword in rule["keywords"]:
            kw = _normalise_for_topic(keyword)
            if kw in q:
                score += 4 if " " in kw else 2
                matched.append(keyword)

        if re.search(r"\b[01]{4,16}\b", q) and rule["subtopic"] == "1.1 Number Systems":
            score += 3
            matched.append("binary number pattern")

        if re.search(r"\b[0-9a-f]{2,4}\b", q) and ("hex" in q or "hexadecimal" in q) and rule["subtopic"] == "1.1 Number Systems":
            score += 3
            matched.append("hexadecimal pattern")

        if "tick" in q and ("input" in q or "output" in q or "storage" in q) and rule["subtopic"] == "3.2 Input and Output Devices":
            score += 3
            matched.append("input/output/storage tick-box pattern")

        if "explain" in q and "why" in q and "embedded" in q and rule["subtopic"] == "3.1 Computer Architecture":
            score += 3
            matched.append("embedded system explanation pattern")

        if score > 0:
            scored.append({
                "topic": rule["topic"],
                "subtopic": rule["subtopic"],
                "score": score,
                "matched_keywords": sorted(set(matched))
            })

    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:top_n]

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

# ---------- CONTEXT DETECTION (for pseudocode / table questions) ----------

# Signals that a sub-question refers to content shown in the preamble
CONTEXT_REF_PATTERNS = [
    r'\bidentif\w+\b.*\b(error|line|mistake|bug|correct)\b',
    r'\b(error|errors)\b.*\b(pseudocode|algorithm|program|code)\b',
    r'\bcomplete\b.*\b(pseudocode|algorithm|table|program|procedure)\b',
    r'\b(trace|dry.?run)\b.*\b(algorithm|pseudocode|table)\b',
    r'\bwrite\b.*\b(pseudocode|algorithm|program|code|procedure)\b',
    r'\busing\b.*\b(diagram|table|pseudocode|algorithm)\b',
    r'\bbelow\b',
    r'\babove\b',
    r'\bgiven\b.*\b(algorithm|pseudocode|program|table)\b',
    r'\bthis\b.*\b(algorithm|pseudocode|program|procedure)\b',
    r'\bthe (algorithm|pseudocode|program|procedure|table|diagram)\b',
    r'\bfollowing\b',
    r'\bshown\b',
    r'\bdescribe\b.*\b(algorithm|pseudocode|program)\b',
]

# Signals that a preamble block contains structured content worth preserving
PREAMBLE_CONTENT_PATTERNS = [
    r'\d{2}\s+[A-Z]',                     # line-numbered code: "01 DECLARE"
    r'DECLARE\s+\w+\s*:',                 # pseudocode declarations
    r'\bIF\b.*\bTHEN\b',                 # IF/THEN
    r'\bFOR\b.*\bTO\b',                  # FOR loops
    r'\bWHILE\b|\bREPEAT\b|\bUNTIL\b',# loop keywords
    r'\bINPUT\b|\bOUTPUT\b',             # I/O statements
    r'\bPROCEDURE\b|\bFUNCTION\b',      # procedure/function
    r'\|\s*\w+.*\|',                     # table rows |col|col|
    r'<-|:=',                                # assignment operators
]

def has_context_reference(text):
    """Return True if sub-question text references external context."""
    tl = text.lower()
    for pat in CONTEXT_REF_PATTERNS:
        if re.search(pat, tl, re.IGNORECASE):
            return True
    return False

def preamble_is_meaningful(preamble):
    """Return True if preamble contains structured content (code, table)."""
    if len(preamble.strip()) < 20:
        return False
    for pat in PREAMBLE_CONTENT_PATTERNS:
        if re.search(pat, preamble, re.IGNORECASE):
            return True
    return False

def format_context_block(preamble, q_num):
    """
    Format preamble text as a readable context block.
    Restores line breaks before line-numbered code lines.
    """
    ctx = preamble.strip()
    # Restore newlines before line-numbered pseudocode (e.g. "01 DECLARE")
    ctx = re.sub(r'\s+(\d{2}\s+[A-Z])', r'\n\1', ctx)
    # Collapse excessive whitespace but keep single newlines
    ctx = re.sub(r'[ \t]+', ' ', ctx)
    ctx = re.sub(r'\n{3,}', '\n\n', ctx)
    return f"[Question {q_num} context — read before answering]\n{ctx.strip()}\n\n"

# ---------- PARSE QUESTION PAPER ----------

def parse_qp(raw_text):
    """
    Parse IGCSE question paper PDF text into structured question objects.

    Question numbers appear as:  \n<digit(s)> \n
    (digit + trailing space — distinguishes from lone page numbers).

    Key improvement: when a question block has a preamble containing
    pseudocode, tables, or diagrams, that context is automatically
    prepended to any sub-question that references it. This means
    questions like "Identify errors in the pseudocode" carry the
    actual pseudocode with them into the database.

    Sub-questions:     (a)-(h)
    Sub-sub-questions: roman numerals (i), (ii), (iii) ...
    """
    main_pattern = re.compile(r'\n(\d{1,2}) \n')
    parts = main_pattern.split(raw_text)

    if len(parts) <= 3:
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

        # ---- Extract preamble (text before first sub-question) ----
        first_sub = re.search(r'\(([a-h])\)', block)
        if first_sub:
            raw_preamble = block[:first_sub.start()]
            preamble_cleaned = clean_qtext(raw_preamble)
        else:
            raw_preamble = ""
            preamble_cleaned = ""

        # Decide if preamble has structured content to attach to sub-questions
        preamble_has_code = preamble_is_meaningful(raw_preamble)
        context_prefix = (format_context_block(preamble_cleaned, q_num)
                          if preamble_has_code else "")

        # ---- No sub-questions: store whole block ----
        sub_matches = list(re.finditer(r'\(([a-h])\)', block))
        if not sub_matches:
            cleaned = clean_qtext(block)
            if len(cleaned) > 15:
                questions.append({"question": str(q_num), "question_text": cleaned})
            continue

        # ---- Process each sub-question ----
        for s_idx, s_match in enumerate(sub_matches):
            sub_id    = s_match.group(1)
            sub_start = s_match.start()
            sub_end   = (sub_matches[s_idx + 1].start()
                         if s_idx + 1 < len(sub_matches) else len(block))
            sub_text  = block[sub_start:sub_end]

            # ---- Sub-sub-questions (roman numerals) ----
            ss_matches = list(re.finditer(
                r'\((i{1,3}|iv|vi{0,3}|ix|xi{0,3})\)', sub_text))

            if ss_matches:
                for ss_idx, ss_match in enumerate(ss_matches):
                    ss_id    = ss_match.group(1)
                    ss_start = ss_match.start()
                    ss_end   = (ss_matches[ss_idx + 1].start()
                                if ss_idx + 1 < len(ss_matches) else len(sub_text))
                    ss_text  = sub_text[ss_start:ss_end]
                    qid      = f"{q_num}({sub_id})({ss_id})"
                    cleaned  = clean_qtext(ss_text)
                    if len(cleaned) > 15:
                        # Prepend context if sub-question references external content
                        if context_prefix and has_context_reference(cleaned):
                            cleaned = context_prefix + cleaned
                        questions.append({"question": qid, "question_text": cleaned})
            else:
                qid     = f"{q_num}({sub_id})"
                cleaned = clean_qtext(sub_text)
                if len(cleaned) > 15:
                    # Prepend context if sub-question references external content
                    if context_prefix and has_context_reference(cleaned):
                        cleaned = context_prefix + cleaned
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
    """
    Return one clean topic string for database storage.
    Format: Topic - Subtopic
    """
    matches = classify_topic(text, top_n=1)
    if not matches:
        return "General"
    best = matches[0]
    return f"{best['topic']} - {best['subtopic']}"

def map_topic_details(text):
    """
    Return detailed classifier output for API/debugging.
    """
    matches = classify_topic(text, top_n=3)
    if not matches:
        return {
            "topic": "General",
            "main_topic": "General",
            "subtopic": "",
            "score": 0,
            "matched_keywords": [],
            "alternatives": []
        }
    best = matches[0]
    return {
        "topic": f"{best['topic']} - {best['subtopic']}",
        "main_topic": best["topic"],
        "subtopic": best["subtopic"],
        "score": best["score"],
        "matched_keywords": best["matched_keywords"],
        "alternatives": matches[1:]
    }

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

        topic_info = map_topic_details(q["question_text"])

        result.append({

            "question": q_key,

            "question_text": q["question_text"],

            "answer": ans,

            "topic": topic_info["topic"],

            "main_topic": topic_info.get("main_topic", "General"),

            "subtopic": topic_info.get("subtopic", ""),

            "topic_score": topic_info.get("score", 0),

            "matched_keywords": topic_info.get("matched_keywords", []),

            "topic_alternatives": topic_info.get("alternatives", [])

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

    AI-powered feedback using Claude (claude-haiku-4-5-20251001).

    Claude acts as an IGCSE examiner, reads the mark scheme,

    and returns structured JSON with marks, matched/missing points,

    a highlighted answer, and an examiner comment.

    Falls back to keyword heuristic if the API call fails.

    """

    import html as html_lib

    data_in = request.json

    student  = data_in.get("student", "").strip()

    correct  = data_in.get("correct", "").strip()

    question = data_in.get("question_text", "").strip()   # optional, sent by frontend

    # ------------------------------------------------------------------ #

    #  AI EVALUATION                                                       #

    # ------------------------------------------------------------------ #

    def ai_evaluate(student_ans, mark_scheme, q_text):

        """Call Claude to evaluate the student answer against the mark scheme."""

        system_prompt = """You are a strict but fair IGCSE Computer Science examiner (Cambridge 0478).

Your job is to mark a student's answer against the official mark scheme.

Rules you MUST follow:

1. A single vague word like "data" or "yes" is NEVER worth a mark on its own — the student must demonstrate understanding.

2. Award 1 mark per distinct correct point that matches a mark scheme concept.

3. The student does not need to use exact wording — credit correct ideas expressed in their own words.

4. Do NOT award marks for restating the question or for irrelevant/incorrect statements.

5. Infer the maximum marks from the mark scheme preamble (e.g. "Any one from:" = 1 mark, "Two from:" = 2 marks). Default to 4 if unclear.

You MUST respond with ONLY a valid JSON object — no explanation, no markdown, no extra text.

JSON schema:

{

  "marks_awarded": <integer>,

  "max_marks": <integer>,

  "comment": "<one sentence examiner feedback>",

  "matched_points": ["<point the student correctly made>", ...],

  "missing_points": ["<key concept the student missed>", ...],

  "highlighted_answer": "<student answer with <mark> tags around correct parts>"

}

For highlighted_answer: wrap each correct phrase/word in the student's answer with <mark class='hit'>...</mark>.

Leave incorrect or irrelevant parts as plain text.

"""

        user_prompt = f"""Question: {q_text if q_text else '(not provided)'}

Mark Scheme:

{mark_scheme}

Student Answer:

{student_ans}

Mark this answer strictly and return only the JSON."""

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        message = client.messages.create(

            model="claude-haiku-4-5-20251001",

            max_tokens=512,

            messages=[{"role": "user", "content": user_prompt}],

            system=system_prompt

        )

        raw = message.content[0].text.strip()

        # Strip any accidental markdown fences

        raw = re.sub(r'^```(?:json)?\s*', '', raw)

        raw = re.sub(r'\s*```$', '', raw)

        return json.loads(raw)

    # ------------------------------------------------------------------ #

    #  FALLBACK heuristic (if AI unavailable)                             #

    # ------------------------------------------------------------------ #

    def heuristic_evaluate(student_ans, mark_scheme):

        """
        Improved deterministic marker.
        Handles "Any two from" mark schemes, one-word list answers,
        plurals, and small spelling mistakes such as acutator -> actuator.
        """

        import difflib

        STOPWORDS = {
            "the","and","that","this","with","from","have","which","will","when",
            "what","where","there","their","they","than","then","each","such",
            "into","uses","using","would","could","should","about","after",
            "before","other","also","more","some","been","were","being",
            "because","while","any","give","state","explain","describe",
            "must","not","can","per","are","has","had","used","for","its",
            "example","examples","cambridge","igcse","mark","scheme","published",
            "may","june","november","october","paper","answer","marks"
        }

        SYNONYMS = {
            "microprocessor": ["microprocessors", "processor", "processors"],
            "microprocessors": ["microprocessor", "processor", "processors"],
            "actuator": ["actuators", "acutator", "actutor", "actuater"],
            "actuators": ["actuator", "acutator", "actutor", "actuater"],
            "sensor": ["sensors"],
            "sensors": ["sensor"],
        }

        def norm(txt):
            txt = (txt or "").lower()
            txt = txt.replace("’", "'").replace("–", "-").replace("—", "-")
            txt = re.sub(r"[^a-z0-9\s,/;:-]", " ", txt)
            return re.sub(r"\s+", " ", txt).strip()

        def singular(w):
            if len(w) > 4 and w.endswith("ies"):
                return w[:-3] + "y"
            if len(w) > 3 and w.endswith("s"):
                return w[:-1]
            return w

        def tokens(txt):
            return [w for w in re.findall(r"[a-z0-9]+", norm(txt)) if len(w) > 2 and w not in STOPWORDS]

        def extract_max_marks(ans):
            txt = norm(ans)
            nums = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'1':1,'2':2,'3':3,'4':4,'5':5,'6':6}
            m = re.search(r'\bany\s+(one|two|three|four|five|six|[1-6])\s+from\b', txt)
            if m: return nums.get(m.group(1), 4)
            m = re.search(r'\b(one|two|three|four|five|six|[1-6])\s+from\b', txt)
            if m: return nums.get(m.group(1), 4)
            m = re.search(r'\bgive\s+(one|two|three|four|five|six|[1-6])\b', norm(question))
            if m: return nums.get(m.group(1), 4)
            return 4

        def clean_ms(ans):
            txt = norm(ans)
            txt = re.sub(r'\bcambridge igcse mark scheme\b.*$', ' ', txt)
            txt = re.sub(r'\bpublished\b.*$', ' ', txt)
            txt = re.sub(r'\bany\s+(one|two|three|four|five|six|[1-6])\s+from\b[:\s]*', ' ', txt)
            txt = re.sub(r'\b(one|two|three|four|five|six|[1-6])\s+from\b[:\s]*', ' ', txt)
            txt = re.sub(r'\bexamples?\b[:\s]*', ' ', txt)
            return re.sub(r'\s+', ' ', txt).strip()

        def extract_points(ans):
            txt = clean_ms(ans)
            # Split common MS separators, but also handle compact lists: Sensors Microprocessors Actuators
            parts = re.split(r'\s*//\s*|\s*;\s*|\s*\|\s*|,\s*', txt)
            points = []
            for part in parts:
                ws = tokens(part)
                if len(ws) == 1:
                    points.append(ws[0])
                elif 2 <= len(ws) <= 6:
                    points.extend(ws)
                elif ws:
                    points.append(' '.join(ws))
            if len(points) <= 1:
                points = tokens(txt)
            seen, final = set(), []
            for point in points:
                key = singular(point)
                if key not in seen and key not in STOPWORDS:
                    seen.add(key); final.append(point)
            return final

        def term_matches(term, student_words):
            base = singular(term)
            candidates = {base, term}
            for syn in SYNONYMS.get(base, []) + SYNONYMS.get(term, []):
                candidates.add(syn); candidates.add(singular(syn))
            if candidates & student_words:
                return True
            # spelling tolerance for technical words
            for sw in student_words:
                if len(base) >= 6 and difflib.SequenceMatcher(None, base, sw).ratio() >= 0.80:
                    return True
            return False

        student_words = {singular(w) for w in tokens(student_ans)} | set(tokens(student_ans))
        max_marks = extract_max_marks(mark_scheme)
        points = extract_points(mark_scheme)

        matched, missing = [], []
        for point in points:
            ws = tokens(point)
            ok = False
            if len(ws) == 1:
                ok = term_matches(ws[0], student_words)
            elif ws:
                hits = [w for w in ws if term_matches(w, student_words)]
                ok = len(hits) >= max(1, min(2, len(ws)))
            if ok:
                matched.append(point)
                if len(matched) >= max_marks:
                    break
            else:
                missing.append(point)

        marks = min(max_marks, len(matched))

        highlighted = html_lib.escape(student_ans)
        for point in sorted(matched, key=len, reverse=True):
            for w in tokens(point):
                highlighted = re.sub(r'\b' + re.escape(w) + r's?\b',
                                     lambda m: f"<mark class='hit'>{m.group(0)}</mark>",
                                     highlighted, flags=re.IGNORECASE)
            if singular(point) == 'actuator':
                highlighted = re.sub(r'\bacutator\b', "<mark class='hit'>acutator</mark>", highlighted, flags=re.IGNORECASE)

        r = marks/max_marks if max_marks else 0
        comment = ("Excellent answer." if r >= 1.0 else
                   "Good attempt — some points missing." if r >= 0.5 else
                   "Partially correct — expand your answer." if marks > 0 else
                   "Insufficient — review the mark scheme.")

        return {
            "marks_awarded": marks,
            "max_marks": max_marks,
            "comment": comment,
            "matched_points": matched[:6],
            "missing_points": missing[:6],
            "highlighted_answer": highlighted
        }

    # ------------------------------------------------------------------ #

    #  RUN                                                                 #

    # ------------------------------------------------------------------ #

    heuristic_result = heuristic_evaluate(student, correct)

    try:

        ai_result = ai_evaluate(student, correct, question)

        # Safety net: if deterministic marking finds more valid points,
        # use it. This fixes cases where AI misses exact/fuzzy list answers.
        if heuristic_result.get("marks_awarded", 0) > ai_result.get("marks_awarded", 0):
            result = heuristic_result
        else:
            result = ai_result

    except Exception as e:

        print(f"[AI feedback error] {e} — falling back to heuristic")

        result = heuristic_result

    marks_awarded = result.get("marks_awarded", 0)

    max_marks     = result.get("max_marks", 4)

    # Convert <mark class='hit'> tags to styled spans for the browser

    highlighted = result.get("highlighted_answer", html_lib.escape(student))

    highlighted = highlighted.replace(

        "<mark class='hit'>",

        "<span style='color:green;font-weight:bold;background:#d1fae5;padding:0 2px;border-radius:3px'>"

    ).replace("</mark>", "</span>")

    return jsonify({

        "marks":       f"{marks_awarded}/{max_marks}",

        "matched":     result.get("matched_points", [])[:6],

        "missing":     result.get("missing_points", [])[:6],

        "comment":     result.get("comment", ""),

        "highlighted": highlighted,

        "model":       correct

    })

if __name__ == "__main__":

    app.run(debug=True)
