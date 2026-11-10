"""Strip company identity from announcement text before an LLM sees it.

Why: the model was trained on news up to its cutoff and may "remember" how a company's story
ended. Removing names and tickers reduces (doesn't eliminate) that look-ahead. What is replaced:
- every known name of the company (current, former, name at filing time), with and without its
  legal suffix ("Inc.", "Corp" ...), and its distinctive first word ("Apple" from "Apple Inc.");
- its tickers when written as a ticker ("NASDAQ: AAPL", "(AAPL)", or a bare ticker of 3+ letters);
- web addresses and e-mail addresses (domains usually name the company).
Product names, places and people stay; `leak check` in the pilot measures how often the model can
still guess the company."""
from __future__ import annotations

import re

COMPANY = "[COMPANY]"
TICKER = "[TICKER]"

_SUFFIXES = r"(?:,?\s+(?:inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|lp|l\.p|n\.v|s\.a|ag|se|holdings?|group|trust|bancorp|bancshares|technologies|international)\.?)+$"
# first words too common to stand for one company on their own
_COMMON = {
    "american", "first", "national", "united", "general", "international", "global", "bank",
    "capital", "new", "north", "south", "east", "west", "western", "eastern", "central", "pacific",
    "atlantic", "southern", "northern", "community", "financial", "energy", "health", "healthcare",
    "medical", "royal", "great", "standard", "southwest", "midwest", "texas", "california",
    "florida", "ohio", "the", "citizens", "peoples", "home", "federal", "security", "pioneer",
    "advanced", "applied", "digital", "data", "power", "life", "world", "universal", "allied",
    "premier", "summit", "liberty", "patriot", "heritage", "investors", "trust", "union", "old",
    "us", "u.s.", "china", "golden", "silver", "green", "blue", "red", "black", "white",
    "smart", "net", "one", "bio", "pharma", "therapeutics", "systems", "solutions", "services",
    "industries", "resources", "partners", "realty", "properties", "insurance", "mutual",
}
_URL = re.compile(r"\b(?:https?://|www\.)\S+|\b[\w.-]+\.(?:com|net|org|io|co|us|biz)\b(?:/\S*)?", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
_EIN = re.compile(r"\b\d{2}-\d{7}\b|\b[0-3]-\d{4,5}\b")      # IRS employer id, SEC file number
_ITEM = re.compile(r"\bItem\s+\d\.\d\d\b", re.IGNORECASE)
_FILENAME = re.compile(r"\b[\w.-]+\.(?:htm|html|txt|pdf|jpg|gif|png)\b", re.IGNORECASE)
_EXCH = r"(?:NASDAQ|Nasdaq|NYSE(?:\s+American|\s+MKT|\s+Arca)?|AMEX|OTC(?:QB|QX)?|TSX|Nasdaq\s*(?:GS|GM|CM))"


def name_variants(names: list[str]) -> list[str]:
    """All the strings that should be replaced for these company names, longest first."""
    out: set[str] = set()
    for n in names:
        n = re.sub(r"\s+", " ", (n or "").strip())
        if not n:
            continue
        n = re.sub(r"\s*/[A-Z]{2,}/?$", "", n)          # EDGAR state tags: "ACME CORP /DE/"
        out.add(n)
        core = re.sub(_SUFFIXES, "", n, flags=re.IGNORECASE).strip(" ,.")
        if len(core) >= 3:
            out.add(core)
        words = core.split()
        first = words[0].strip(",.").lower() if words else ""
        if len(words) > 1 and len(first) >= 4 and first not in _COMMON and first.isalpha():
            out.add(words[0].strip(",."))
    return sorted(out, key=len, reverse=True)


def acronyms(names: list[str]) -> set[str]:
    """Initials a release may use for the company: "Universal Electronics Inc" -> UEI, UE."""
    out = set()
    for n in names:
        n = re.sub(r"\s*/[A-Z]{2,}/?$", "", (n or "").strip())
        words = [w for w in re.findall(r"[A-Za-z][\w']*", n) if w.lower() not in {"the", "of", "and"}]
        core = [w for w in re.findall(r"[A-Za-z][\w']*", re.sub(_SUFFIXES, "", n, flags=re.IGNORECASE))
                if w.lower() not in {"the", "of", "and"}]
        for ws in (words, core):
            a = "".join(w[0] for w in ws).upper()
            if 3 <= len(a) <= 6:
                out.add(a)
    return out


def strip_cover_page(text: str) -> str:
    """When the document is the 8-K form itself, its cover page (registrant address, phone, IRS and
    file numbers) identifies the company: keep the text from the first "Item x.xx" onwards."""
    head = text[:6000]
    if "registrant" in head.lower():
        m = _ITEM.search(text)
        if m:
            return text[m.start():]
    return text


def anonymise(text: str, names: list[str], tickers: list[str]) -> str:
    text = strip_cover_page(text)
    text = _FILENAME.sub("[FILE]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE.sub("[PHONE]", text)
    text = _EIN.sub("[ID]", text)
    text = _URL.sub("[URL]", text)
    for v in name_variants(names):
        # possessives and all-caps headings match too; word boundaries so "Apple" leaves "Applebee".
        # One-word names match in any case but must start with a capital, so "MEDALLION" also catches
        # "Medallion" and "CoStar", while "Target" spares "our sales target".
        if " " in v:   # "SHERWIN WILLIAMS CO" must also catch "Sherwin-Williams"
            pat, flags = r"[\s\-]+".join(re.escape(w) for w in v.split(" ")), re.IGNORECASE
        else:
            pat, flags = rf"(?=[A-Z])(?i:{re.escape(v)})", 0
        tail = r"(?:,?\s+(?:Inc|Incorporated|Corp|Corporation|Co|Company|Ltd|Limited|plc|PLC|LLC|L\.P|Holdings|INC|CORP|CORPORATION|COMPANY|HOLDINGS)\.?)*"
        text = re.sub(rf"(?<![\w-]){pat}{tail}(?:'s|’s)?(?![\w-])", COMPANY, text, flags=flags)
    # any ticker written with its exchange, known or not (old tickers aren't in our lists): "Nasdaq:VICL"
    text = re.sub(rf"(\b{_EXCH}\s*[:\-]\s*)[A-Z][A-Z.]{{0,5}}\b", rf"\1{TICKER}", text)
    for a in sorted(acronyms(names), key=len, reverse=True):    # exact capitals only
        text = re.sub(rf"(?<![\w-]){re.escape(a)}(?:'s|’s)?(?![\w-])", COMPANY, text)
    for t in sorted({t.strip().upper() for t in tickers if t and t.strip()}, key=len, reverse=True):
        q = re.escape(t)
        text = re.sub(rf"({_EXCH}\s*[:\-]?\s*){q}\b", rf"\1{TICKER}", text)   # "NASDAQ: AAPL"
        text = re.sub(rf"\(\s*{q}\s*\)", f"({TICKER})", text)                  # "(AAPL)"
        if len(t) >= 3:                                                        # bare "AAPL"; 1-2 letters too ambiguous
            # also wire-service tags: "--AAPL (", "(AAPL-F)", "AAPLF"
            text = re.sub(rf"(?<![\w$]){q}(?:-[A-Z]{{1,3}}|[A-Z])?(?![\w])", TICKER, text)
    return text
