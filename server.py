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
# Matches IGCSE CS 0478 syllabus topics exactly.
# No topics.json needed — but topics.json is kept in sync for reference.

TOPIC_RULES = [
    {"topic": "1. Data Representation", "subtopic": "1.1 Number Systems",
     "keywords": ["binary","denary","hexadecimal","hex","convert","conversion",
                  "overflow","shift","logical shift","left shift","right shift",
                  "two's complement","twos complement","8-bit","12-bit",
                  "add binary","binary addition","negative denary",
                  "largest denary","largest hexadecimal"]},
    {"topic": "1. Data Representation", "subtopic": "1.2 Text, Sound and Images",
     "keywords": ["sample rate","sampling rate","sample resolution","sampling resolution",
                  "resolution","colour depth","color depth","ascii","unicode",
                  "character set","pixel","pixels","image","sound"]},
    {"topic": "1. Data Representation", "subtopic": "1.3 Data Storage and Compression",
     "keywords": ["file size","compression","compress","compressed","rle",
                  "run length encoding","lossy","lossless","nibble",
                  "kib","mib","gib","tib","storage units"]},
    {"topic": "2. Data Transmission", "subtopic": "2.1 Transmission Methods",
     "keywords": ["packet","packets","header","payload","trailer","packet switching",
                  "transmission method","serial","parallel","simplex",
                  "half-duplex","full-duplex","duplex","usb"]},
    {"topic": "2. Data Transmission", "subtopic": "2.2 Error Detection",
     "keywords": ["parity","parity check","checksum","echo check","check digit",
                  "isbn","barcode","bar code","arq","automatic repeat request",
                  "error detection","acknowledgement","timeout"]},
    {"topic": "2. Data Transmission", "subtopic": "2.3 Encryption",
     "keywords": ["encryption","encrypt","encrypted","symmetric","asymmetric",
                  "public key","private key","plain text","cipher text",
                  "ssl","secure socket","https","certificate"]},
    {"topic": "3. Hardware", "subtopic": "3.1 Computer Architecture",
     "keywords": ["cpu","microprocessor","alu","cu","control unit",
                  "arithmetic logic unit","program counter","mar","mdr","cir",
                  "accumulator","fde","fetch","decode","execute",
                  "fetch-decode-execute","clock speed","cache","cores",
                  "embedded system","von neumann","general purpose"]},
    {"topic": "3. Hardware", "subtopic": "3.2 Input and Output Devices",
     "keywords": ["input device","output device","keyboard","scanner",
                  "barcode scanner","qr code scanner","digital camera",
                  "microphone","mouse","touch screen","printer","speaker",
                  "projector","screen","temperature sensor","pressure sensor",
                  "light sensor","humidity sensor","gas sensor","proximity sensor"]},
    {"topic": "3. Hardware", "subtopic": "3.3 Data Storage",
     "keywords": ["ram","rom","primary storage","secondary storage","magnetic",
                  "optical","solid-state","solid state","ssd","hdd",
                  "virtual memory","cloud storage","tracks","sectors",
                  "pits","lands","floating gate","control gate",
                  "volatile","non-volatile"]},
    {"topic": "3. Hardware", "subtopic": "3.4 Network Hardware",
     "keywords": ["nic","network interface card","mac address",
                  "ip address","ipv4","ipv6","router","switch","hub","modem"]},
    {"topic": "4. Software", "subtopic": "4.1 System and Application Software",
     "keywords": ["system software","application software","operating system",
                  "memory management","file management",
                  "interrupt","hardware interrupt","software interrupt",
                  "interrupt service routine","isr","utility software","queue"]},
    {"topic": "4. Software", "subtopic": "4.2 Programming Languages and Translators",
     "keywords": ["high-level","high level","low-level","low level",
                  "assembly language","assembler","compiler","interpreter",
                  "translator","ide","integrated development environment",
                  "machine code","mnemonic","executable file",
                  "line by line","whole code","source code"]},
    {"topic": "5. Internet and Its Uses", "subtopic": "5.1 Internet Basics",
     "keywords": ["internet","world wide web","www","url","http",
                  "web browser","browser","website","web page","dns",
                  "domain name","webpage"]},
    {"topic": "5. Internet and Its Uses", "subtopic": "5.2 Digital Currency",
     "keywords": ["digital currency","cryptocurrency","blockchain","bitcoin",
                  "electronic money","e-wallet","wallet"]},
    {"topic": "5. Internet and Its Uses", "subtopic": "5.3 Cyber Security",
     "keywords": ["malware","phishing","firewall","anti-malware","virus",
                  "hacking","password","authentication","biometric",
                  "fingerprint","social engineering","spyware","brute force",
                  "ransomware","ddos","trojan"]},
    {"topic": "6. Automated and Emerging Technologies",
     "subtopic": "Automated Systems, Robotics and AI",
     "keywords": ["automated system","automation","robotics","robot",
                  "artificial intelligence","expert system","machine learning",
                  "actuator","inference engine","knowledge base","rule base"]},
    {"topic": "7. Algorithm Design",
     "subtopic": "Algorithm Design and Problem Solving",
     "keywords": ["algorithm","decomposition","flowchart","pseudocode",
                  "validation","verification","test data","normal data",
                  "abnormal data","extreme data","trace table","dry run",
                  "logic error","syntax error","runtime error"]},
    {"topic": "8. Programming", "subtopic": "Programming Concepts",
     "keywords": ["variable","constant","sequence","selection","iteration",
                  "for loop","while loop","repeat until","array",
                  "file handling","read file","write file","procedure",
                  "function","parameter","subroutine","declare",
                  "integer","string","boolean","real"]},
    {"topic": "9. Databases", "subtopic": "Databases",
     "keywords": ["database","table","field","record","primary key",
                  "foreign key","query","sql","data type","form","report",
                  "entity","relationship"]},
    {"topic": "10. Boolean Logic",
     "subtopic": "Logic Gates, Truth Tables and Logic Circuits",
     "keywords": ["logic gate","and gate","or gate","not gate","nand gate",
                  "nor gate","xor gate","truth table","logic circuit",
                  "logic expression","boolean","logic diagram"]},
]

def _normalise_for_topic(text):
    text = text.lower()
    text = text.replace("\u2019", "'").replace("\u2013", "-").replace("\u2014", "-")
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
        # Pattern boosts
        if re.search(r"\b[01]{4,16}\b", q) and rule["subtopic"] == "1.1 Number Systems":
            score += 3; matched.append("binary number pattern")
        if (re.search(r"\b[0-9a-f]{2,4}\b", q) and ("hex" in q or "hexadecimal" in q)
                and rule["subtopic"] == "1.1 Number Systems"):
            score += 3; matched.append("hexadecimal pattern")
        if ("tick" in q and any(w in q for w in ("input","output","storage"))
                and rule["subtopic"] == "3.2 Input and Output Devices"):
            score += 3; matched.append("tick-box pattern")
        if ("explain" in q and "why" in q and "embedded" in q
                and rule["subtopic"] == "3.1 Computer Architecture"):
            score += 3; matched.append("embedded system pattern")
        if any(kw in q for kw in ("compiler","interpreter","assembler","translator","machine code")):
            if rule["subtopic"] == "4.2 Programming Languages and Translators":
                score += 4
        if ("ide" in q and any(w in q for w in ("function","feature","tool","role","found"))):
            if rule["subtopic"] == "4.2 Programming Languages and Translators":
                score += 4
        if (("complete" in q or "write" in q) and
                any(w in q for w in ("procedure","function","program"))):
            if rule["topic"] == "8. Programming": score += 4
        if any(kw in q for kw in ("ssl","https","secure connection","certificate")):
            if rule["subtopic"] == "2.3 Encryption": score += 4
        if score > 0:
            scored.append({"topic": rule["topic"], "subtopic": rule["subtopic"],
                           "score": score, "matched_keywords": sorted(set(matched))})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]

def map_topic(text):
    """Return 'Topic - Subtopic' string for DB storage."""
    matches = classify_topic(text, top_n=1)
    if not matches:
        return "General"
    b = matches[0]
    return f"{b['topic']} - {b['subtopic']}"

def map_topic_details(text):
    matches = classify_topic(text, top_n=3)
    if not matches:
        return {"topic": "General", "main_topic": "General", "subtopic": "",
                "score": 0, "matched_keywords": [], "alternatives": []}
    b = matches[0]
    return {"topic": f"{b['topic']} - {b['subtopic']}", "main_topic": b["topic"],
            "subtopic": b["subtopic"], "score": b["score"],
            "matched_keywords": b["matched_keywords"], "alternatives": matches[1:]}

# ---------- EXTRACT TEXT ----------
def extract_text(file):
    text = ""
    pdf = fitz.open(stream=file.read(), filetype="pdf")
    for page in pdf:
        text += page.get_text() + "\n"
    return text

# ---------- STRIP ALL NOISE ----------
def strip_all_noise(text):
    text = re.sub(r'\* \d{13} \*', ' ', text)
    text = re.sub(r',[\x00-\x1F\t]+,', ' ', text)
    text = re.sub(r'[^\x20-\x7E\n\'\"\\-]+', ' ', text)
    text = re.sub(r'0478/\d+/[A-Z]/[A-Z]/\d+', ' ', text)
    text = re.sub(r'0478/\d+', ' ', text)
    text = re.sub(r'UCLES 202\d', ' ', text)
    text = re.sub(r'Cambridge IGCSE[^\n]*', ' ', text)
    text = re.sub(r'Cambridge Assessment[^\n]*', ' ', text)
    text = re.sub(r'Local Examinations[^\n]*', ' ', text)
    text = re.sub(r'\[Turn over\]?', ' ', text)
    text = re.sub(r'DC \([A-Z/]+\) \d+/\d+', ' ', text)
    text = re.sub(r'DO NOT WRITE IN THIS MARGIN', ' ', text)
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
    text = re.sub(r'Permission to reproduce.*', ' ', text, flags=re.DOTALL)
    text = re.sub(r'(?m)^\s*\d{1,3}\s*$', ' ', text)
    text = re.sub(r'\.{5,}', '', text)
    text = re.sub(r'\[\d+\]', '', text)
    text = re.sub(r'(?m)^\s*Working space\s*$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'(?m)^\s*[12]\s*$', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

# ---------- CLEAN QUESTION TEXT ----------
def clean_qtext(raw):
    text = strip_all_noise(raw)
    text = re.sub(r'\b[A-D]\s+(input|output|process|storage|compil\w*|interpret\w*)\b[^\n]*',
                  '', text, flags=re.IGNORECASE)
    text = re.sub(r"URL input into\s*patient.?s computer", '', text)
    text = re.sub(r"Patient.?s\s*computer", '', text)
    text = re.sub(r'www\.[a-zA-Z0-9.\-]+\.com\b', '', text)
    text = re.sub(r'\bComponent\s+Description\b', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ---------- CONTEXT DETECTION ----------
CONTEXT_REF_PATTERNS = [
    r'\bidentif\w+\b.*\b(error|line|mistake|bug|correct)\b',
    r'\b(error|errors)\b.*\b(pseudocode|algorithm|program|code)\b',
    r'\bcomplete\b.*\b(pseudocode|algorithm|table|program|procedure)\b',
    r'\b(trace|dry.?run)\b.*\b(algorithm|pseudocode|table)\b',
    r'\bwrite\b.*\b(pseudocode|algorithm|program|code|procedure)\b',
    r'\busing\b.*\b(diagram|table|pseudocode|algorithm)\b',
    r'\bbelow\b', r'\babove\b',
    r'\bgiven\b.*\b(algorithm|pseudocode|program|table)\b',
    r'\bthis\b.*\b(algorithm|pseudocode|program|procedure)\b',
    r'\bthe (algorithm|pseudocode|program|procedure|table|diagram)\b',
    r'\bfollowing\b', r'\bshown\b',
    r'\bdescribe\b.*\b(algorithm|pseudocode|program)\b',
]
PREAMBLE_CONTENT_PATTERNS = [
    r'\d{2}\s+[A-Z]', r'DECLARE\s+\w+\s*:', r'\bIF\b.*\bTHEN\b',
    r'\bFOR\b.*\bTO\b', r'\bWHILE\b|\bREPEAT\b|\bUNTIL\b',
    r'\bINPUT\b|\bOUTPUT\b', r'\bPROCEDURE\b|\bFUNCTION\b',
    r'\|\s*\w+.*\|', r'<-|:=',
]

def has_context_reference(text):
    tl = text.lower()
    for pat in CONTEXT_REF_PATTERNS:
        if re.search(pat, tl, re.IGNORECASE):
            return True
    return False

def preamble_is_meaningful(preamble):
    if len(preamble.strip()) < 20:
        return False
    for pat in PREAMBLE_CONTENT_PATTERNS:
        if re.search(pat, preamble, re.IGNORECASE):
            return True
    return False

def format_context_block(preamble, q_num):
    ctx = preamble.strip()
    ctx = re.sub(r'\s+(\d{2}\s+[A-Z])', r'\n\1', ctx)
    ctx = re.sub(r'[ \t]+', ' ', ctx)
    ctx = re.sub(r'\n{3,}', '\n\n', ctx)
    return f"[Question {q_num} context — read before answering]\n{ctx.strip()}\n\n"

# ---------- PARSE QUESTION PAPER ----------
def parse_qp(raw_text):
    main_pattern = re.compile(r'\n(\d{1,2}) \n')
    parts = main_pattern.split(raw_text)
    if len(parts) <= 3:
        main_pattern = re.compile(r'\n\s*(\d{1,2})\s*\n')
        parts = main_pattern.split(raw_text)

    print(f"[DEBUG] parse_qp: {len(parts)} parts, Qs: {[parts[i] for i in range(1,len(parts),2)]}")
    questions = []

    for i in range(1, len(parts), 2):
        try:
            q_num = int(parts[i].strip())
        except ValueError:
            continue
        if not (1 <= q_num <= 20):
            continue
        block = parts[i + 1] if i + 1 < len(parts) else ""

        first_sub = re.search(r'\(([a-h])\)', block)
        if first_sub:
            raw_preamble    = block[:first_sub.start()]
            preamble_cleaned = clean_qtext(raw_preamble)
        else:
            raw_preamble = preamble_cleaned = ""

        preamble_has_code = preamble_is_meaningful(raw_preamble)
        context_prefix = (format_context_block(preamble_cleaned, q_num)
                          if preamble_has_code else "")

        sub_matches = list(re.finditer(r'\(([a-h])\)', block))
        if not sub_matches:
            cleaned = clean_qtext(block)
            if len(cleaned) > 15:
                questions.append({"question": str(q_num), "question_text": cleaned})
            continue

        for s_idx, s_match in enumerate(sub_matches):
            sub_id    = s_match.group(1)
            sub_start = s_match.start()
            sub_end   = (sub_matches[s_idx+1].start()
                         if s_idx+1 < len(sub_matches) else len(block))
            sub_text  = block[sub_start:sub_end]

            ss_matches = list(re.finditer(
                r'\((i{1,3}|iv|vi{0,3}|ix|xi{0,3})\)', sub_text))

            if ss_matches:
                for ss_idx, ss_match in enumerate(ss_matches):
                    ss_id    = ss_match.group(1)
                    ss_start = ss_match.start()
                    ss_end   = (ss_matches[ss_idx+1].start()
                                if ss_idx+1 < len(ss_matches) else len(sub_text))
                    ss_text  = sub_text[ss_start:ss_end]
                    qid      = f"{q_num}({sub_id})({ss_id})"
                    cleaned  = clean_qtext(ss_text)
                    if len(cleaned) > 15:
                        if context_prefix and has_context_reference(cleaned):
                            cleaned = context_prefix + cleaned
                        questions.append({"question": qid, "question_text": cleaned})
            else:
                qid     = f"{q_num}({sub_id})"
                cleaned = clean_qtext(sub_text)
                if len(cleaned) > 15:
                    if context_prefix and has_context_reference(cleaned):
                        cleaned = context_prefix + cleaned
                    questions.append({"question": qid, "question_text": cleaned})

    unique = {}
    for q in questions:
        if q["question"] not in unique:
            unique[q["question"]] = q
    print(f"[DEBUG] parse_qp: {len(unique)} unique questions")
    return list(unique.values())

# ---------- STRIP MS NOISE ----------
def strip_ms_noise(text):
    text = re.sub(r'0478/\d+[^\n]*', ' ', text)
    text = re.sub(r'Cambridge IGCSE[^\n]*PUBLISHED[^\n]*', ' ', text)
    text = re.sub(r'Cambridge University Press[^\n]*', ' ', text)
    text = re.sub(r'Cambridge Assessment[^\n]*', ' ', text)
    text = re.sub(r'(?i)(january|february|march|april|may|june|july|august|'
                  r'september|october|november|december)\s*/?\s*\d{4}\s*', ' ', text)
    text = re.sub(r'Page \d+ of \d+\s*', ' ', text)
    text = re.sub(r'Question\s+Answer\s+Marks', ' ', text)
    text = re.sub(r'\bAnswer\b\s+\bMarks\b', ' ', text)
    text = re.sub(r'\(cid:\d+\)', ' ', text)
    text = re.sub(r'\f', ' ', text)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)
    return text

# ---------- PARSE MARK SCHEME ----------
def _clean_ms_answer(raw):
    raw = re.sub(r'\s+', ' ', raw).strip()
    raw = re.sub(r'\s+\d{1,2}$', '', raw).strip()
    raw = re.sub(r'^[\s;:,/\\|]+', '', raw).strip()
    raw = re.sub(r'[\s;:,/\\|]+$', '', raw).strip()
    return raw

def parse_ms(raw_text):
    text = strip_ms_noise(raw_text)
    label_pat = re.compile(
        r'(?m)^\s*(\d{1,2}(?:\([a-z]\)(?:\([ivx]+\))?)?)\s*$'
    )
    matches = []
    for m in label_pat.finditer(text):
        label = m.group(1).strip()
        next_start = m.end()
        next_match = label_pat.search(text, next_start)
        next_end = next_match.start() if next_match else len(text)
        preview = _clean_ms_answer(text[next_start:next_end])

        # Mark and page numbers can appear as standalone digit lines in PDF text.
        # Keep standalone main-question labels only when they introduce real text.
        if "(" not in label and len(preview) < 20:
            continue
        matches.append(m)

    print(f"[DEBUG] parse_ms: {len(matches)} labels: {[m.group(1) for m in matches]}")
    answers = []
    for i, m in enumerate(matches):
        q_label   = m.group(1).strip()
        ans_start = m.end()
        ans_end   = matches[i+1].start() if i+1 < len(matches) else len(text)
        raw       = _clean_ms_answer(text[ans_start:ans_end])
        if not raw:
            continue
        answers.append({"question": q_label, "answer": raw})
    unique = {}
    for a in answers:
        k = a["question"]
        if k not in unique or (not unique[k]["answer"] and a["answer"]):
            unique[k] = a
    print(f"[DEBUG] parse_ms: {len(unique)} answers")
    return list(unique.values())

# ---------- MERGE ----------
def merge(qp, ms):
    ms_dict = {item["question"]: item["answer"] for item in ms}
    result  = []
    for q in qp:
        q_key = q["question"]
        ans   = ms_dict.get(q_key, "")
        if not ans:
            child = [v for k, v in ms_dict.items() if k.startswith(q_key + "(")]
            if child:
                ans = " | ".join(child)
        td = map_topic_details(q["question_text"])
        result.append({
            "question":          q_key,
            "question_text":     q["question_text"],
            "answer":            ans,
            "topic":             td["topic"],
            "main_topic":        td["main_topic"],
            "subtopic":          td["subtopic"],
            "topic_score":       td["score"],
            "matched_keywords":  td["matched_keywords"],
            "topic_alternatives": td["alternatives"],
        })
    return result

# ---------- SAVE ----------
def save_to_db(data, paper_name):
    for item in data:
        supabase.table("questions").insert({
            "paper":         paper_name,
            "question_no":   item["question"],
            "question_text": item["question_text"],
            "answer":        item.get("answer", ""),
            "topic":         item.get("topic", "General"),
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
        qp_file    = request.files["qp"]
        ms_file    = request.files["ms"]
        paper_name = qp_file.filename
        qp_text    = extract_text(qp_file)
        ms_text    = extract_text(ms_file)
        qp_data    = parse_qp(qp_text)
        ms_data    = parse_ms(ms_text)
        print("QP DATA:", qp_data)
        print("MS DATA:", ms_data)
        final_data = merge(qp_data, ms_data)
        save_to_db(final_data, paper_name)
        return jsonify(final_data)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/topics")
def get_topics():
    """Return sorted unique topic strings."""
    resp   = supabase.table("questions").select("topic").execute()
    topics = sorted(set(item["topic"] for item in resp.data if item["topic"]))
    return jsonify(topics)

@app.route("/topics/with-counts")
def get_topics_with_counts():
    """Return [{topic, count}] for sidebar badges."""
    resp = supabase.table("questions").select("topic").execute()
    counts = {}
    for item in resp.data:
        t = item["topic"] or "General"
        counts[t] = counts.get(t, 0) + 1
    result = [{"topic": t, "count": c} for t, c in sorted(counts.items())]
    return jsonify(result)

@app.route("/practice/<path:topic>")
def practice(topic):
    resp = supabase.table("questions").select("*").eq("topic", topic).execute()
    return jsonify(resp.data)

@app.route("/feedback", methods=["POST"])
def feedback():
    import html as html_lib

    data_in  = request.json
    student  = data_in.get("student", "").strip()
    correct  = data_in.get("correct", "").strip()
    question = data_in.get("question_text", "").strip()

    def ai_evaluate(student_ans, mark_scheme, q_text):
        system_prompt = """You are a strict but fair IGCSE Computer Science examiner (Cambridge 0478).
Your task: mark the student answer against the official mark scheme and give formative feedback.

MARKING RULES:
1. A vague fragment like "data", "yes", "it changes", "data changes" is NEVER worth a mark.
2. Award 1 mark per distinct, complete point matching a mark scheme concept.
3. Credit correct ideas in own words — exact wording not required.
4. Do NOT credit: restating the question, partial fragments, circular reasoning.
5. Infer max_marks from preamble: "Any one from:" = 1, "Two from:" = 2. Default 4 if unclear.

FORMATIVE FEEDBACK:
- examiner_comment: 1-2 sentences specific to this answer.
- how_to_improve: concrete advice with example phrasing.
- what_was_good: what was creditworthy, or "No creditworthy points were made."

Return ONLY valid JSON — no markdown, no extra text.

{
  "marks_awarded": <int>,
  "max_marks": <int>,
  "examiner_comment": "<string>",
  "how_to_improve": "<string>",
  "what_was_good": "<string>",
  "matched_points": ["<string>"],
  "missing_points": ["<string>"],
  "highlighted_answer": "<student answer with <mark class='hit'>correct</mark> and <mark class='miss'>wrong/vague</mark> tags>"
}"""
        user_prompt = f"""Question: {q_text or '(not provided)'}

Mark Scheme:
{mark_scheme}

Student Answer:
{student_ans}

Apply marking rules strictly. Return only JSON."""

        client  = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)

    def _score_exact_objective(student_ans, mark_scheme, q_text):
        q_lower = q_text.lower()
        s_compact = re.sub(r'[\s,;:_-]+', '', student_ans).upper()
        s_binary = s_compact.replace("O", "0")

        def strip_leading_zeroes(value):
            value = value.lstrip("0")
            return value or "0"

        def binary_candidates(text):
            cleaned = text.upper().replace("O", "0")
            cleaned = re.sub(r'\(([01]{1,8})\)\s*([01]{2,32})',
                             lambda m: m.group(1) + m.group(2), cleaned)
            tokens = re.findall(r'(?<![A-Z0-9])[01]{2,32}(?![A-Z0-9])', cleaned)
            out = set()
            for token in tokens:
                out.add(token)
                out.add(strip_leading_zeroes(token))
            return out

        def number_candidates(text):
            return set(re.findall(r'(?<![A-Z0-9])\d+(?![A-Z0-9])', text.upper()))

        is_binary_q = any(term in q_lower for term in ("binary", "base 2", "base two"))
        is_hex_q = any(term in q_lower for term in ("hex", "hexadecimal", "base 16"))
        is_denary_q = any(term in q_lower for term in ("denary", "decimal", "base 10"))

        if is_binary_q:
            expected = binary_candidates(mark_scheme)
            student_value = strip_leading_zeroes(s_binary)
            if expected and student_value in expected:
                return {
                    "marks_awarded": 1, "max_marks": 1,
                    "examiner_comment": "Correct. The denary value has been converted to the correct binary number.",
                    "how_to_improve": "For binary conversion questions, leading zeroes are usually optional unless the question asks for a fixed number of bits.",
                    "what_was_good": "You gave the correct binary value.",
                    "matched_points": [student_ans],
                    "missing_points": [],
                    "highlighted_answer": f"<mark class='hit'>{html_lib.escape(student_ans)}</mark>"
                }
            if expected:
                model = sorted(expected, key=len)[0]
                return {
                    "marks_awarded": 0, "max_marks": 1,
                    "examiner_comment": "The answer does not match the required binary value.",
                    "how_to_improve": f"Convert the denary number using place values 16, 8, 4, 2, 1. The expected binary value is {model}.",
                    "what_was_good": "You attempted the answer in binary form." if re.fullmatch(r'[01O\s]+', student_ans.upper()) else "No creditworthy value was given.",
                    "matched_points": [],
                    "missing_points": [model],
                    "highlighted_answer": html_lib.escape(student_ans)
                }

        if is_denary_q or is_hex_q:
            expected = number_candidates(mark_scheme)
            if expected and s_compact in expected:
                label = "hexadecimal" if is_hex_q else "denary"
                return {
                    "marks_awarded": 1, "max_marks": 1,
                    "examiner_comment": f"Correct. You gave the required {label} value.",
                    "how_to_improve": "Keep setting out conversions clearly so small place-value mistakes are easier to spot.",
                    "what_was_good": "Your final value matches the mark scheme.",
                    "matched_points": [student_ans],
                    "missing_points": [],
                    "highlighted_answer": f"<mark class='hit'>{html_lib.escape(student_ans)}</mark>"
                }

        return None

    def heuristic_evaluate(student_ans, mark_scheme, q_text):
        STOPWORDS = {
            "the","and","that","this","with","from","have","which","will","when",
            "what","where","there","their","they","than","then","each","such",
            "into","uses","using","would","could","should","about","after",
            "before","other","also","more","some","been","were","being",
            "because","while","any","give","state","explain","describe",
            "must","not","can","per","are","has","had","used","for","its"
        }
        def extract_max(ans):
            nums = {'one':1,'two':2,'three':3,'four':4,'five':5,
                    '1':1,'2':2,'3':3,'4':4,'5':5}
            if re.search(r'(?i)\bone\s+mark\b', ans):
                return 1
            m = re.match(r'(?i)any\s+(\w+)\s+from', ans)
            if m: return nums.get(m.group(1).lower(), 4)
            m = re.match(r'(?i)(\w+)\s+from[:\s]', ans)
            if m: return nums.get(m.group(1).lower(), 4)
            if re.match(r'(?i)\s*(give|state|identify|write)\b', q_text.strip()):
                return 1
            return 4
        def split_concepts(ans):
            clean = re.sub(r'(?i)^(any|one|two|three|four|five)\s+(mark[s]?\s+)?(from|for\s+each)[:\s]*','',ans)
            parts = re.split(r'\s*//\s*|\s*;\s*', clean)
            if len(parts)==1: parts = re.split(r'(?<=[a-z]{3})\.\s+(?=[A-Z])', parts[0])
            return [p.strip() for p in parts if len(p.strip())>6]
        sl = student_ans.lower()
        mm = extract_max(mark_scheme.lower())
        concepts = split_concepts(mark_scheme.lower()) or [mark_scheme.lower()]
        mc, hit, miss = 0, [], []
        for c in concepts:
            kws = list(set([w for w in re.findall(r'[a-z]+', c)
                            if len(w)>3 and w not in STOPWORDS]))
            if not kws: continue
            matched = [w for w in kws if re.search(r'\b'+re.escape(w)+r'\b', sl)]
            if len(matched)/len(kws)>=0.35 and (len(matched)>=2 or any(len(w)>6 for w in matched)):
                mc += 1; hit.extend(matched)
            else:
                miss.extend([w for w in kws if w not in matched])
        marks = min(mm, mc)
        hit   = list(set(hit)); miss = list(set(miss)-set(hit))
        hi    = html_lib.escape(student_ans)
        for w in sorted(hit, key=len, reverse=True):
            hi = re.sub(r'\b'+re.escape(w)+r'\b', f"<mark class='hit'>{w}</mark>", hi)
        r = marks/mm if mm else 0
        return {
            "marks_awarded": marks, "max_marks": mm,
            "examiner_comment": ("Good answer." if r>=1.0 else
                                 "Partially correct — missing detail." if marks>0 else
                                 "Answer too vague — no creditworthy points."),
            "how_to_improve": "Review the model answer and use precise terminology.",
            "what_was_good":  ", ".join(hit) if hit else "No creditworthy points were made.",
            "matched_points": hit[:6], "missing_points": miss[:6],
            "highlighted_answer": hi
        }

    try:
        result = _score_exact_objective(student, correct, question)
        if result is None:
            result = ai_evaluate(student, correct, question)
    except Exception as e:
        print(f"[AI feedback error] {e} — fallback")
        result = heuristic_evaluate(student, correct, question)

    ma  = result.get("marks_awarded", 0)
    mm  = result.get("max_marks", 4)
    hi  = result.get("highlighted_answer", html_lib.escape(student))
    hi  = hi.replace("<mark class='hit'>",
            "<span style='color:#065f46;font-weight:600;background:#d1fae5;padding:1px 3px;border-radius:3px'>"
          ).replace("<mark class='miss'>",
            "<span style='color:#991b1b;background:#fee2e2;padding:1px 3px;border-radius:3px;text-decoration:line-through'>"
          ).replace("</mark>", "</span>")

    return jsonify({
        "marks":            f"{ma}/{mm}",
        "matched":          result.get("matched_points", [])[:6],
        "missing":          result.get("missing_points", [])[:6],
        "examiner_comment": result.get("examiner_comment", result.get("comment", "")),
        "how_to_improve":   result.get("how_to_improve", ""),
        "what_was_good":    result.get("what_was_good", ""),
        "highlighted":      hi,
        "model":            correct,
        "feedback_version":  "2026-06-28-objective-v2",
    })

if __name__ == "__main__":
    app.run(debug=True)
