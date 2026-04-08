from flask import Flask, render_template, request, jsonify
import re, json, os
import fitz  # PyMuPDF
from supabase import create_client

app = Flask(__name__)

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

with open("topics.json") as f:
    TOPICS = json.load(f)


# ── PDF text extraction ───────────────────────────────────────────────────────
def extract_text(file):
    pdf = fitz.open(stream=file.read(), filetype="pdf")
    return "\n".join(page.get_text() for page in pdf)


# ── Shared noise filter ───────────────────────────────────────────────────────
_NOISE_RE = re.compile(
    r"DO NOT WRITE|Working space|UCLES|Turn over|Cambridge|"
    r"0478/12|DC \(JP|This document|Page \d+ of|"
    r"COMPUTER SCIENCE|Paper 1|October/November|"
    r"INSTRUCTIONS|INFORMATION|mark scheme|Published|"
    r"Generic Marking|GENERIC MARKING"
)

# Roman numerals that are sub-sub-question markers.
# Must be checked BEFORE the general letter-sub pattern because (i), (ii),
# (iii), (iv) also match \([a-z]\) and would be wrongly treated as letter subs.
_ROMAN_RE = re.compile(
    r"^\((i{1,3}|iv|vi{0,3}|vii|viii|ix|x)\)\s*(.*)", re.IGNORECASE
)

# Letter sub-question — only match AFTER we know the line is NOT a roman numeral
_LETTER_RE = re.compile(r"^\(([a-z])\)\s*(.*)")

# Inline "2(a) text" — only if the digit is followed by a letter-sub and text
_INLINE_RE = re.compile(r"^(\d+)\(([a-z])\)\s*(.*)")


def _roman_order(r):
    tbl = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
           "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
    return tbl.get((r or "").lower(), 0)


def _basic_clean(raw_lines):
    """Remove noise, garbage, and control chars. Return clean list of strings."""
    out = []
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        if sum(1 for c in line if ord(c) > 127) > 3:   # barcode/encoding garbage
            continue
        if _NOISE_RE.search(line):
            continue
        if re.match(r"^[.\s]+$", line):                 # dot answer-blank lines
            continue
        if re.match(r"^\*[\s\d]+\*$", line):            # "* 000000002 *" barcodes
            continue
        line = re.sub(r"[\x00-\x1F\x7F]+", " ", line).strip()
        line = re.sub(r"\s+", " ", line)
        if line:
            out.append(line)
    return out


def _clean_fragment(text):
    """Final clean on an extracted question / answer block."""
    text = re.sub(r"[^\x00-\x7F]+", " ", text)          # non-ASCII artefacts
    text = re.sub(r"[\x00-\x1F\x7F]+", " ", text)       # control chars
    text = re.sub(r"\[\d+\]", "", text)                  # [1] [4] mark allocations
    text = re.sub(r"\.{3,}", "", text)                   # dot-blank lines
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── Distinguish page numbers from question numbers ────────────────────────────
def _real_question_number_indices(raw_lines):
    """
    Both page numbers and question numbers appear as bare integers in the raw
    PDF text.  They are distinguished by the *next* non-empty line:

      • Page number  → next non-empty line starts with ",\\x01…" or is ","
      • Question num → next non-empty line starts with "\\x01…"  (right-margin)
                       or is readable question text (fallback)

    Returns the set of raw-line indices that are genuine question numbers.
    """
    real = set()
    for i, raw in enumerate(raw_lines):
        s = raw.strip()
        if not re.match(r"^\d+$", s):
            continue
        num = int(s)
        if not (1 <= num <= 20):
            continue
        for j in range(i + 1, min(i + 6, len(raw_lines))):
            nxt = raw_lines[j]
            if not nxt.strip():
                continue
            if nxt.strip().startswith(",\x01") or nxt.strip() == ",":
                break        # it's a page number — skip
            if nxt.strip().startswith("\x01"):
                real.add(i)  # confirmed question number
                break
            real.add(i)      # fallback: next text is readable content
            break
    return real


# ── QP Parser ─────────────────────────────────────────────────────────────────
def parse_qp(text):
    """
    Parse a Cambridge IGCSE question paper (PyMuPDF text).

    Returns [{"question": "1(a)", "question_text": "…"}, …]

    The canonical key format is:
      • "1(a)"       for top-level letter subs
      • "1(e)(i)"    for roman-numeral children  ← includes the parent letter

    Bugs fixed vs original
    ──────────────────────
    1. Page numbers are filtered so they cannot reset current_q.

    2. Roman-numeral markers (i)/(ii)/… are checked BEFORE the general
       letter-sub pattern ^\([a-z]\), because (i), (ii), (iii) also match
       that pattern and were previously being treated as letter subs,
       producing wrong keys like "1(i)" instead of "1(e)(i)".

    3. When a roman child fires, current_sub (the parent letter, e.g. "(e)")
       is PRESERVED — it is never overwritten by roman handling.  The child
       key is built as  current_q + current_sub + "(roman)".

    4. The preamble text of the letter-sub (context for (e)/(c)/(f)) is saved
       and prepended to each roman child so question_text is self-contained.

    5. Fragments are sorted after collection to fix column-reordering artefacts
       (the PDF sometimes emits "(c)" before "(b)" due to multi-column layout).
    """
    raw_lines = text.split("\n")
    real_q_raw_indices = _real_question_number_indices(raw_lines)

    # Build cleaned line list while tracking which ones are real Q numbers
    cleaned: list[str] = []
    is_q_num: list[bool] = []
    for i, raw in enumerate(raw_lines):
        line = raw.strip()
        if not line:
            continue
        if sum(1 for c in line if ord(c) > 127) > 3:
            continue
        if _NOISE_RE.search(line):
            continue
        if re.match(r"^[.\s]+$", line):
            continue
        if re.match(r"^\*[\s\d]+\*$", line):
            continue
        clean = re.sub(r"[\x00-\x1F\x7F]+", " ", line).strip()
        clean = re.sub(r"\s+", " ", clean)
        if clean:
            is_q_num.append(i in real_q_raw_indices)
            cleaned.append(clean)

    # ── accumulator ──────────────────────────────────────────────────────────
    fragments: list[dict] = []
    current_q:     str | None = None   # "1", "2" …
    current_sub:   str | None = None   # ALWAYS the letter-level sub: "(e)", "(c)"
    current_roman: str | None = None   # "i", "ii", "iii" … or None
    current_text:  str        = ""
    parent_preamble: str      = ""     # body text of current_sub before first roman

    def flush():
        nonlocal current_text
        q, sub, rom, txt = current_q, current_sub, current_roman, current_text
        if q and sub and txt.strip():
            # Key: "1(e)(i)"  or  "1(a)" when no roman
            key     = f"{q}{sub}" + (f"({rom})" if rom else "")
            sub_ch  = sub.strip("()")
            r_order = _roman_order(rom)
            fragments.append({
                "key":  key,
                "text": _clean_fragment(txt),
                "sort": (int(q), sub_ch, r_order),
            })
        current_text = ""

    for idx, line in enumerate(cleaned):

        # ── genuine question number (1 … 7) ──────────────────────────────
        if is_q_num[idx]:
            flush()
            current_q     = str(int(line.strip()))
            current_sub   = None
            current_roman = None
            parent_preamble = ""
            continue

        # ── bare digit that is NOT a real question number → skip ─────────
        #    (page numbers, mark allocations like "2" or "4")
        if re.match(r"^\d+$", line.strip()):
            continue

        # ── inline "2(a) question text…" ─────────────────────────────────
        m = _INLINE_RE.match(line)
        if m:
            flush()
            current_q     = m.group(1)
            current_sub   = f"({m.group(2)})"
            current_roman = None
            current_text  = m.group(3).strip()
            parent_preamble = ""
            continue

        # ── ROMAN SUB  (i), (ii), (iii), (iv) … ─────────────────────────
        # Must be checked BEFORE letter-sub because (i) also matches \([a-z]\)
        m = _ROMAN_RE.match(line)
        if m:
            # On first roman child: save letter-sub body as context preamble
            if current_roman is None and current_text.strip():
                parent_preamble = current_text.strip()
            flush()
            # *** current_sub stays as the parent letter sub (e.g. "(e)") ***
            current_roman = m.group(1).lower()
            rest          = m.group(2).strip()
            current_text  = (
                (parent_preamble + " " + rest).strip()
                if parent_preamble else rest
            )
            continue

        # ── LETTER SUB  (a), (b), (c), (d), (e), (f) … ──────────────────
        m = _LETTER_RE.match(line)
        if m:
            flush()
            current_sub     = f"({m.group(1)})"
            current_roman   = None
            current_text    = m.group(2).strip()
            parent_preamble = ""
            continue

        # ── accumulate body text ──────────────────────────────────────────
        current_text += (" " if current_text else "") + line

    flush()  # flush the last fragment

    # Sort by (q_num, sub_letter, roman_order) to fix column-reordering
    fragments.sort(key=lambda x: x["sort"])

    # Deduplicate — last write wins (handles rare duplicate extractions)
    seen: dict[str, dict] = {}
    for f in fragments:
        seen[f["key"]] = f

    return [{"question": v["key"], "question_text": v["text"]}
            for v in seen.values()]


# ── MS Parser ─────────────────────────────────────────────────────────────────
def parse_ms(text):
    """
    Parse the mark scheme PDF and return {question_key: answer_text}.

    Uses the same canonical key format as parse_qp so keys always match:
      "1(a)", "1(e)(i)", "5(c)(iii)" …

    Bug fixed: original code stripped the middle letter from nested keys
    (converting "1(e)(i)" → "1(i)"), causing all roman-sub answers to be lost.
    The KEY_RE below captures the full three-part key when present.
    """
    clean_lines = _basic_clean(text.split("\n"))
    ms_text     = "\n".join(clean_lines)

    # Matches "1(a)", "1(e)(i)", "5(c)(iii)" etc.
    KEY_RE = re.compile(
        r"(?<!\w)(\d+)\(([a-z])\)(?:\(([ivx]+)\))?(?=\s|$)",
        re.IGNORECASE,
    )
    matches = list(KEY_RE.finditer(ms_text))

    ms_dict: dict[str, str] = {}
    for idx, m in enumerate(matches):
        q_num = m.group(1)
        sub   = f"({m.group(2)})"
        roman = m.group(3).lower() if m.group(3) else None
        key   = f"{q_num}{sub}" + (f"({roman})" if roman else "")

        start = m.end()
        end   = matches[idx + 1].start() if idx + 1 < len(matches) else len(ms_text)
        block = ms_text[start:end].strip()

        # Remove trailing lone mark-count digit (e.g. "\n4\n" at end of block)
        block = re.sub(r"\n\s*\d+\s*$", "", block).strip()
        block = re.sub(r"\s+", " ", block).strip()

        if key in ms_dict:
            ms_dict[key] += " " + block
        else:
            ms_dict[key] = block

    return ms_dict


# ── Topic mapping ─────────────────────────────────────────────────────────────
def map_topic(question_text):
    qt = question_text.lower()
    for topic, keywords in TOPICS.items():
        if any(kw.lower() in qt for kw in keywords):
            return topic
    return "General"


# ── Merge QP + MS ─────────────────────────────────────────────────────────────
def merge(qp_data, ms_data):
    result = []
    for q in qp_data:
        key = q["question"]
        ans = ms_data.get(key, "")

        # Fallback 1: parent letter key  "1(e)(i)" → try "1(e)"
        if not ans:
            m = re.match(r"(\d+\([a-z]\))\(", key)
            if m:
                ans = ms_data.get(m.group(1), "")

        # Fallback 2: strip middle letter  "1(e)(i)" → try "1(i)"
        if not ans:
            m = re.match(r"(\d+)\([a-z]\)\(([ivx]+)\)$", key)
            if m:
                ans = ms_data.get(f"{m.group(1)}({m.group(2)})", "")

        result.append({
            "question":      key,
            "question_text": q["question_text"],
            "answer":        ans,
            "topic":         map_topic(q["question_text"]),
        })
    return result


# ── DB save ───────────────────────────────────────────────────────────────────
def save_to_db(data, paper_name):
    for item in data:
        supabase.table("questions").insert({
            "paper":         paper_name,
            "question_no":   item["question"],
            "question_text": item["question_text"],
            "answer":        item.get("answer", ""),
            "topic":         item.get("topic", "General"),
        }).execute()


# ── Routes ────────────────────────────────────────────────────────────────────
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
    topics = list({item["topic"] for item in response.data})
    return jsonify(topics)


@app.route("/practice/<topic>")
def practice(topic):
    response = (supabase.table("questions")
                .select("*").eq("topic", topic).execute())
    return jsonify(response.data)


@app.route("/feedback", methods=["POST"])
def feedback():
    data    = request.json
    student = data.get("student", "").lower()
    correct = data.get("correct", "").lower()

    keywords = list({w for w in correct.split() if len(w) > 4})
    matched  = [k for k in keywords if k in student]
    missing  = [k for k in keywords if k not in student]
    total    = len(keywords)
    score    = len(matched)
    marks    = min(4, round((score / total) * 4)) if total > 0 else 0

    highlighted = student
    for word in matched:
        highlighted = highlighted.replace(
            word, f"<span style='color:green;font-weight:bold'>{word}</span>"
        )

    if marks == 4:
        comment = "Excellent answer. Accurate use of key terminology."
    elif marks >= 2:
        comment = "Good attempt. Some key terms missing."
    else:
        comment = "Basic response. Needs improvement."

    return jsonify({
        "marks":       f"{marks}/4",
        "matched":     matched[:5],
        "missing":     missing[:5],
        "comment":     comment,
        "highlighted": highlighted,
        "model":       correct,
    })


if __name__ == "__main__":
    app.run(debug=True)
