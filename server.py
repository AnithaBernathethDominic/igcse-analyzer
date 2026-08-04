from flask import Flask, request, jsonify, render_template, session
from functools import wraps
import hmac
import re
import json
import os
import uuid
import io
import urllib.error
import urllib.parse
import urllib.request
import fitz  # PyMuPDF
import anthropic
from PIL import Image
from supabase import create_client
import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

app = Flask(__name__)
CLASS_ACCESS_PASSWORD = os.getenv("CLASS_ACCESS_PASSWORD", "")
app.secret_key = os.getenv("FLASK_SECRET_KEY") or CLASS_ACCESS_PASSWORD or os.urandom(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")),
)

# ---------- FIREBASE AUTH CONFIG ----------
FIREBASE_AUTH_DISABLED = os.getenv("FIREBASE_AUTH_DISABLED", "").lower() in ("1", "true", "yes")
FIREBASE_CONFIG = {
    "apiKey": os.getenv("FIREBASE_API_KEY", ""),
    "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", ""),
    "projectId": os.getenv("FIREBASE_PROJECT_ID", ""),
    "appId": os.getenv("FIREBASE_APP_ID", ""),
}

def init_firebase_admin():
    if firebase_admin._apps:
        return True
    try:
        service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        if service_account_json:
            cred = credentials.Certificate(json.loads(service_account_json))
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app()
        return True
    except Exception as e:
        print(f"[Firebase auth disabled until configured] {e}")
        return False

FIREBASE_ADMIN_READY = init_firebase_admin()

def firebase_auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if FIREBASE_AUTH_DISABLED:
            return fn(*args, **kwargs)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Firebase login required"}), 401

        if not FIREBASE_ADMIN_READY:
            return jsonify({"error": "Firebase Admin is not configured on the server"}), 503

        token = auth_header.split(" ", 1)[1].strip()
        try:
            request.firebase_user = firebase_auth.verify_id_token(token)
        except Exception:
            return jsonify({"error": "Invalid or expired Firebase token"}), 401

        return fn(*args, **kwargs)
    return wrapper

def require_auth(fn):
    @wraps(fn)
    @firebase_auth_required
    def wrapper(*args, **kwargs):
        if FIREBASE_AUTH_DISABLED:
            return fn(*args, **kwargs)
        if not CLASS_ACCESS_PASSWORD:
            return jsonify({"error": "CLASS_ACCESS_PASSWORD is not configured on the server"}), 503
        if session.get("firebase_uid") != request.firebase_user.get("uid"):
            return jsonify({"error": "Class password required", "code": "class_access_required"}), 403
        return fn(*args, **kwargs)
    return wrapper

# ---------- SUPABASE CONFIG ----------
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().strip("'\"")
# Prefer a server-only service-role key when one is configured.  Keep the old
# variable as a fallback so existing deployments continue to work.
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
    or ""
).strip().strip("'\"")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def supabase_origin():
    raw = (SUPABASE_URL or "").strip().strip("'\"")
    if raw and not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"

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

def extract_pdf_pages(file_bytes):
    pages = []
    pdf = fitz.open(stream=file_bytes, filetype="pdf")
    for index, page in enumerate(pdf):
        pages.append({"page_no": index + 1, "text": page.get_text(), "images": page.get_images(full=True)})
    return pages

def clean_extracted_text(text):
    return (
        text.replace("\r", "\n")
        .replace("\t", " ")
        .replace("\xa0", " ")
    )

def safe_storage_name(value):
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_") or "question"

def upload_question_image(image_bytes, ext, paper_name, question_no):
    if not image_bytes:
        return {"url": None, "error": "No image bytes were created from the PDF page."}
    safe_paper = safe_storage_name(os.path.splitext(paper_name)[0])
    safe_question = safe_storage_name(question_no)
    file_ext = (ext or "png").lower().lstrip(".")
    content_type = "image/jpeg" if file_ext in ("jpg", "jpeg") else f"image/{file_ext}"
    file_name = f"{safe_paper}/{safe_question}_{uuid.uuid4().hex}.{file_ext}"
    try:
        encoded_name = urllib.parse.quote(file_name, safe="/")
        base_url = supabase_origin()
        storage_url = f"{base_url}/storage/v1/object/question-images/{encoded_name}"
        request_obj = urllib.request.Request(
            storage_url,
            data=image_bytes,
            method="POST",
            headers={
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "apikey": SUPABASE_KEY,
                "Content-Type": content_type,
                "x-upsert": "true",
            },
        )
        with urllib.request.urlopen(request_obj, timeout=30):
            pass

        public_url = f"{base_url}/storage/v1/object/public/question-images/{encoded_name}"
        return {"url": public_url, "error": None}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        error = f"Storage upload failed: HTTP {e.code} {detail}"
        print(f"[Storage image upload skipped] {error}")
        return {"url": None, "error": error}
    except urllib.error.URLError as e:
        parsed = urllib.parse.urlparse(supabase_origin())
        error = (
            f"Storage upload failed: could not reach Supabase host "
            f"'{parsed.netloc or parsed.path}'. Check SUPABASE_URL in Render."
        )
        print(f"[Storage image upload skipped] {error}: {e}")
        return {"url": None, "error": error}
    except Exception as e:
        error = f"Storage upload failed: {e}"
        print(f"[Storage image upload skipped] {error}")
        return {"url": None, "error": error}

DIAGRAM_KEYWORDS = {"diagram", "annotate", "sketch", "figure"}
COMPLETE_VISUAL_KEYWORDS = {"diagram", "figure", "table", "circuit", "flowchart"}

def is_diagram_question(question_text):
    text = (question_text or "").lower()
    if any(keyword in text for keyword in DIAGRAM_KEYWORDS):
        return True
    if "complete" in text and any(keyword in text for keyword in COMPLETE_VISUAL_KEYWORDS):
        return True
    return bool(re.search(r"\b(label|shown|below)\b.{0,80}\b(diagram|figure|table|circuit|flowchart)\b", text))

def text_mentions_diagram(text):
    return is_diagram_question(text)

ROMAN_PART = r"i{1,3}|iv|vi{0,3}|ix|xi{0,3}"
MAIN_TOKEN_RE = re.compile(rf"^(\d{{1,2}})(?:\(([a-h])\))?(?:\(({ROMAN_PART})\))?$", re.IGNORECASE)
SUB_TOKEN_RE = re.compile(rf"^\(([a-h])\)(?:\(({ROMAN_PART})\))?$", re.IGNORECASE)
SUBSUB_TOKEN_RE = re.compile(rf"^\(({ROMAN_PART})\)$", re.IGNORECASE)

class QuestionTracker:
    def __init__(self):
        self.main = None
        self.sub = None
        self.subsub = None

    def update_word(self, word, x0, page_width):
        clean = word.strip().strip(".,;:")
        if not clean:
            return None

        if m := MAIN_TOKEN_RE.fullmatch(clean):
            value = int(m.group(1))
            if 1 <= value <= 20 and x0 <= page_width * 0.35:
                self.main = m.group(1)
                self.sub = m.group(2).lower() if m.group(2) else None
                self.subsub = m.group(3).lower() if m.group(3) else None
                return self.label()
        if m := SUB_TOKEN_RE.fullmatch(clean):
            if self.main and x0 <= page_width * 0.50:
                self.sub = m.group(1).lower()
                self.subsub = m.group(2).lower() if m.group(2) else None
                return self.label()
        if m := SUBSUB_TOKEN_RE.fullmatch(clean):
            if self.main and self.sub and x0 <= page_width * 0.55:
                self.subsub = m.group(1).lower()
                return self.label()
        return None

    def label(self):
        if not self.main:
            return None
        parts = [self.main]
        if self.sub:
            parts.append(f"({self.sub})")
        if self.subsub:
            parts.append(f"({self.subsub})")
        return "".join(parts)

def find_label_positions(pdf, labels_in_order):
    wanted = set(labels_in_order)
    found = {}
    tracker = QuestionTracker()
    for page_index, page in enumerate(pdf):
        words = page.get_text("words")
        words.sort(key=lambda word: (word[1], word[0]))
        for word in words:
            label = tracker.update_word(word[4], word[0], page.rect.width)
            if label in wanted and label not in found:
                found[label] = {
                    "label": label,
                    "page": page_index,
                    "y0": word[1],
                    "x0": word[0],
                }
    return [
        found.get(label, {"label": label, "page": None, "y0": None, "x0": None})
        for label in labels_in_order
    ]

def render_page_region(page, y0, y1, dpi=200):
    rect = fitz.Rect(page.rect.x0, y0, page.rect.x1, y1)
    rect = rect + (0, -5, 0, 5)
    rect = rect & page.rect
    if rect.height < 30 or rect.width < 30:
        return None
    zoom = dpi / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
    return pixmap.tobytes("png")

def crop_question_to_next(pdf, page_num, y0, next_page_num=None, next_y0=None, dpi=200):
    images = []
    if page_num is None:
        return images

    end_page = next_page_num if next_page_num is not None else page_num
    if end_page < page_num:
        end_page = page_num

    for current_page_num in range(page_num, end_page + 1):
        page = pdf[current_page_num]
        start_y = y0 if current_page_num == page_num else page.rect.y0
        end_y = page.rect.y1
        if current_page_num == end_page and next_y0 is not None:
            end_y = next_y0
        image = render_page_region(page, start_y, end_y, dpi=dpi)
        if image:
            images.append(image)
    return images

def stitch_vertically(png_bytes_list):
    images = [Image.open(io.BytesIO(image)).convert("RGB") for image in png_bytes_list]
    if not images:
        return None

    width = max(image.width for image in images)
    total_height = sum(image.height for image in images)
    canvas = Image.new("RGB", (width, total_height), "white")

    y_offset = 0
    for image in images:
        canvas.paste(image, (0, y_offset))
        y_offset += image.height

    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()

def render_full_page(pdf, page_num, dpi=180):
    if page_num is None or page_num < 0 or page_num >= len(pdf):
        return None
    page = pdf[page_num]
    pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    return pixmap.tobytes("png")

def first_available_page(positions, index):
    for pos in positions[index::-1]:
        if pos.get("page") is not None:
            return pos["page"]
    for pos in positions[index + 1:]:
        if pos.get("page") is not None:
            return pos["page"]
    return 0

def next_located_position(positions, index):
    for pos in positions[index + 1:]:
        if pos.get("page") is not None and pos.get("y0") is not None:
            return pos
    return None

def get_question_image(pdf, positions, index):
    pos = positions[index]
    next_pos = next_located_position(positions, index)
    if pos.get("y0") is not None and pos.get("page") is not None:
        parts = crop_question_to_next(
            pdf,
            pos["page"],
            pos["y0"],
            next_pos["page"] if next_pos else pos["page"],
            next_pos["y0"] if next_pos else None,
        )
        if parts:
            return stitch_vertically(parts) if len(parts) > 1 else parts[0]

    return render_full_page(pdf, first_available_page(positions, index))

def extract_and_upload_question_images(file_bytes, paper_name, questions=None):
    image_by_question = {}
    errors_by_question = {}
    pdf = fitz.open(stream=file_bytes, filetype="pdf")
    ordered_questions = questions or []
    labels_in_order = [
        question.get("question") or question.get("question_no")
        for question in ordered_questions
        if question.get("question") or question.get("question_no")
    ]
    positions = find_label_positions(pdf, labels_in_order)
    position_index = {pos["label"]: index for index, pos in enumerate(positions)}

    for question in ordered_questions:
        question_key = question.get("question") or question.get("question_no")
        if not question_key or not is_diagram_question(question.get("question_text")):
            continue

        try:
            image_bytes = get_question_image(pdf, positions, position_index[question_key])
            upload_result = upload_question_image(image_bytes, "png", paper_name, question_key)
            if upload_result["url"]:
                image_by_question[question_key] = upload_result["url"]
            else:
                errors_by_question[question_key] = upload_result["error"] or "Image upload failed."
        except Exception as e:
            errors_by_question[question_key] = f"Image extraction failed: {e}"

    return image_by_question, errors_by_question

# ---------- STRIP ALL NOISE ----------
def strip_all_noise(text):
    text = clean_extracted_text(text)
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
    text = re.sub(r'(?m)^\s*Working space\s*$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'(?m)^\s*[12]\s*$', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

# ---------- CLEAN QUESTION TEXT ----------
def clean_qtext(raw):
    text = strip_all_noise(raw)
    text = re.sub(r"URL input into\s*patient.?s computer", '', text)
    text = re.sub(r"Patient.?s\s*computer", '', text)
    text = re.sub(r'www\.[a-zA-Z0-9.\-]+\.com\b', '', text)
    text = re.sub(r'\bComponent\s+Description\b', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_options(text):
    match = re.search(
        r'(?:^|\s)A\s+(.+?)\s+B\s+(.+?)\s+C\s+(.+?)\s+D\s+(.+?)(?:\s*\[\d+\]\s*)?$',
        text,
        re.DOTALL,
    )
    if not match:
        return None
    return {
        "question_text": text[:match.start()].strip(),
        "option_a": match.group(1).strip(),
        "option_b": match.group(2).strip(),
        "option_c": match.group(3).strip(),
        "option_d": match.group(4).strip(),
    }

def parse_question_details(question_no, raw_question, cleaned_question_text):
    cleaned_question_text = re.sub(
        rf'^\s*\((?:[a-z]|{ROMAN_PART})\)(?:\s+|$)',
        '',
        cleaned_question_text,
        flags=re.IGNORECASE,
    ).strip()

    marks = None
    mark_tail = r'\[(\d+)\]\s*[,.;:\s]*$'
    marks_match = re.search(mark_tail, raw_question.strip())
    if not marks_match:
        marks_match = re.search(mark_tail, cleaned_question_text.strip())
    if marks_match:
        marks = int(marks_match.group(1))
        cleaned_question_text = re.sub(mark_tail, '', cleaned_question_text).strip()

    options = parse_options(cleaned_question_text)
    if options:
        cleaned_question_text = options["question_text"]

    needs_review = (
        not question_no
        or len(cleaned_question_text) < 10
        or "undefined" in cleaned_question_text.lower()
        or len(cleaned_question_text.split()) < 3
    )

    return {
        "question": question_no,
        "question_no": question_no,
        "question_text": cleaned_question_text,
        "option_a": options["option_a"] if options else None,
        "option_b": options["option_b"] if options else None,
        "option_c": options["option_c"] if options else None,
        "option_d": options["option_d"] if options else None,
        "marks": marks,
        "image_url": None,
        "raw_text": raw_question,
        "needs_review": needs_review,
        "extraction_confidence": "poor" if needs_review else "good",
    }

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
                questions.append(parse_question_details(str(q_num), block, cleaned))
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
                        questions.append(parse_question_details(qid, ss_text, cleaned))
            else:
                qid     = f"{q_num}({sub_id})"
                cleaned = clean_qtext(sub_text)
                if len(cleaned) > 15:
                    if context_prefix and has_context_reference(cleaned):
                        cleaned = context_prefix + cleaned
                    questions.append(parse_question_details(qid, sub_text, cleaned))

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
            "question_no":       q.get("question_no", q_key),
            "question_text":     q["question_text"],
            "option_a":          q.get("option_a"),
            "option_b":          q.get("option_b"),
            "option_c":          q.get("option_c"),
            "option_d":          q.get("option_d"),
            "marks":             q.get("marks"),
            "image_url":         q.get("image_url"),
            "image_error":       q.get("image_error"),
            "raw_text":          q.get("raw_text", q["question_text"]),
            "needs_review":      q.get("needs_review", False),
            "extraction_confidence": q.get("extraction_confidence", "good"),
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
    rows = []
    for item in data:
        question_text = item.get("question_text", "")
        td = map_topic_details(question_text)
        rows.append({
            "paper":         paper_name,
            "question_no":   item.get("question_no") or item.get("question"),
            "question_text": question_text,
            "option_a":      item.get("option_a"),
            "option_b":      item.get("option_b"),
            "option_c":      item.get("option_c"),
            "option_d":      item.get("option_d"),
            "marks":         item.get("marks"),
            "image_url":     item.get("image_url"),
            "raw_text":      item.get("raw_text"),
            "needs_review":  item.get("needs_review", False),
            "extraction_confidence": item.get("extraction_confidence", "good"),
            "answer":        item.get("answer", ""),
            "topic":         item.get("topic") or td["topic"],
        })
    if not rows:
        return []
    return supabase.table("questions").insert(rows).execute().data

# ---------- ROUTES ----------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/practice")
def practice_page():
    return render_template("practice.html")

@app.route("/firebase-config")
def firebase_config():
    return jsonify(FIREBASE_CONFIG)

@app.route("/class-access/status")
@firebase_auth_required
def class_access_status():
    if FIREBASE_AUTH_DISABLED:
        return jsonify({"granted": True})
    return jsonify({
        "granted": session.get("firebase_uid") == request.firebase_user.get("uid")
    })

@app.route("/class-access/verify", methods=["POST"])
@firebase_auth_required
def verify_class_access():
    if not CLASS_ACCESS_PASSWORD:
        return jsonify({"error": "CLASS_ACCESS_PASSWORD is not configured on the server"}), 503
    supplied = str((request.get_json(silent=True) or {}).get("password", ""))
    if not hmac.compare_digest(supplied, CLASS_ACCESS_PASSWORD):
        return jsonify({"error": "Incorrect class password"}), 403
    session.clear()
    session["firebase_uid"] = request.firebase_user.get("uid")
    return jsonify({"granted": True})

@app.route("/class-access/logout", methods=["POST"])
def clear_class_access():
    session.clear()
    return jsonify({"ok": True})

@app.route("/upload", methods=["POST"])
def upload():
    try:
        qp_file    = request.files["qp"]
        ms_file    = request.files["ms"]
        paper_name = qp_file.filename
        qp_bytes   = qp_file.read()
        ms_bytes   = ms_file.read()
        qp_pages   = extract_pdf_pages(qp_bytes)
        ms_pages   = extract_pdf_pages(ms_bytes)
        qp_text    = "\n".join(page["text"] for page in qp_pages)
        ms_text    = "\n".join(page["text"] for page in ms_pages)
        qp_data    = parse_qp(qp_text)
        ms_data    = parse_ms(ms_text)
        image_map, image_errors = extract_and_upload_question_images(qp_bytes, paper_name, qp_data)
        question_counts = {}
        for question in qp_data:
            main_q = re.match(r"^(\d{1,2})", question.get("question", ""))
            if main_q:
                question_counts[main_q.group(1)] = question_counts.get(main_q.group(1), 0) + 1
        for question in qp_data:
            main_q = re.match(r"^(\d{1,2})", question.get("question", ""))
            if main_q:
                main_key = main_q.group(1)
                if question_counts.get(main_key, 0) == 1 or text_mentions_diagram(question.get("question_text")):
                    question["image_url"] = (
                        image_map.get(question.get("question"))
                        or image_map.get(main_key)
                    )
                    question["image_error"] = (
                        image_errors.get(question.get("question"))
                        or image_errors.get(main_key)
                    )
            if text_mentions_diagram(question.get("question_text")) and not question.get("image_url"):
                question["needs_review"] = True
                question["extraction_confidence"] = "poor"
                question["image_error"] = question.get("image_error") or "Diagram mentioned, but no image URL was produced."
        print("QP DATA:", qp_data)
        print("MS DATA:", ms_data)
        final_data = merge(qp_data, ms_data)
        return jsonify({"paper_name": paper_name, "questions": final_data})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/save-questions", methods=["POST"])
def save_questions():
    try:
        payload = request.get_json(force=True)
        paper_name = payload.get("paper_name") or "Uploaded paper"
        questions = payload.get("questions") or []
        saved = save_to_db(questions, paper_name)
        return jsonify({"saved": len(saved), "questions": saved})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/topics")
@require_auth
def get_topics():
    """Return sorted unique topic strings."""
    try:
        resp = supabase.table("questions").select("topic").execute()
        topics = sorted(set(item["topic"] for item in resp.data if item.get("topic")))
        return jsonify(topics)
    except Exception as e:
        app.logger.exception("Could not load topics from Supabase")
        return jsonify({"error": f"Could not load topics: {e}"}), 500

@app.route("/topics/with-counts")
@require_auth
def get_topics_with_counts():
    """Return [{topic, count}] for sidebar badges."""
    try:
        resp = supabase.table("questions").select("topic").execute()
        counts = {}
        for item in resp.data:
            t = item.get("topic") or "General"
            counts[t] = counts.get(t, 0) + 1
        result = [{"topic": t, "count": c} for t, c in sorted(counts.items())]
        return jsonify(result)
    except Exception as e:
        app.logger.exception("Could not load topic counts from Supabase")
        return jsonify({"error": f"Could not load topics: {e}"}), 500

@app.route("/practice/<path:topic>")
@require_auth
def practice(topic):
    try:
        resp = supabase.table("questions").select("*").eq("topic", topic).execute()
        return jsonify(resp.data)
    except Exception as e:
        app.logger.exception("Could not load questions from Supabase")
        return jsonify({"error": f"Could not load questions: {e}"}), 500

@app.route("/feedback", methods=["POST"])
@require_auth
def feedback():
    import html as html_lib

    data_in  = request.json
    student  = data_in.get("student", "").strip()
    correct  = data_in.get("correct", "").strip()
    question = data_in.get("question_text", "").strip()

    def _score_mcq(student_ans, mark_scheme, q_text):
        q_clean = re.sub(r'\s+', ' ', q_text).strip()
        if not re.search(r'(?i)\b(tick|circle|choose|select)\b.*\b(A|B|C|D)\b', q_clean):
            return None

        def normalise_option_text(value):
            value = value.lower()
            value = re.sub(r'\([^)]*\)', ' ', value)
            value = re.sub(r'[^a-z0-9]+', ' ', value)
            return re.sub(r'\s+', ' ', value).strip()

        option_matches = re.findall(
            r'(?:^|\s)([A-D])\s+(.+?)(?=\s+[A-D]\s+|$)', q_clean)
        options = {
            letter.upper(): text.strip(" .;,")
            for letter, text in option_matches
            if text.strip()
        }
        if len(options) < 2:
            return None

        cleaned_ms = re.sub(r'(?i)\b(one|two|three|four)\s+mark[s]?\b', ' ', mark_scheme)
        cleaned_ms = re.sub(r'(?i)\b(mark|answer|correct|box|tick)\b', ' ', cleaned_ms)
        letters = re.findall(r'(?<![A-Z])([A-D])(?![A-Z])', cleaned_ms.upper())
        if not letters:
            compact = re.sub(r'[^A-D]', '', cleaned_ms.upper())
            if len(compact) == 1:
                letters = [compact]
        if not letters:
            return None

        expected = letters[0]
        expected_text = options.get(expected, "")
        expected_display = f"{expected} - {expected_text}" if expected_text else expected
        s_raw = student_ans.strip()
        s_letter = s_raw.upper()
        s_text = normalise_option_text(s_raw)
        correct_match = (
            s_letter == expected or
            (expected_text and s_text == normalise_option_text(expected_text))
        )
        selected_display = (
            f"{s_letter} - {options[s_letter]}" if s_letter in options else s_raw
        )
        return {
            "marks_awarded": 1 if correct_match else 0,
            "max_marks": 1,
            "examiner_comment": (
                f"Correct. {expected_display} is the answer."
                if correct_match else
                f"{selected_display} is not correct. The correct answer is {expected_display}."
            ),
            "how_to_improve": (
                "" if correct_match else
                "For multiple-choice questions, compare each option with the keyword in the question before choosing."
            ),
            "what_was_good": (
                "You selected the correct option."
                if correct_match else
                "You selected an option, but it did not match the mark scheme."
            ),
            "matched_points": [expected_display] if correct_match else [],
            "missing_points": [] if correct_match else [expected_display],
            "highlighted_answer": (
                f"<mark class='hit'>{html_lib.escape(s_raw)}</mark>"
                if correct_match else html_lib.escape(s_raw)
            )
        }

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
        result = _score_mcq(student, correct, question)
        if result is None:
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
