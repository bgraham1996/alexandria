#!/usr/bin/env python3
"""
Research Paper Auto-Importer
=============================
A single script that:
  1. Detects whether a PDF is a research paper
  2. Extracts DOI / arXiv ID / title
  3. Looks up full bibliographic metadata via CrossRef / OpenAlex
  4. Creates a Zotero library item with the metadata
  5. Uploads the PDF as an attachment

Setup:
    pip install pypdf pdfplumber pyzotero requests

    Create a .env file alongside this script (or set env vars):
        ZOTERO_LIBRARY_ID=your_user_id
        ZOTERO_API_KEY=your_api_key

    Get these from: https://www.zotero.org/settings/keys

Usage:
    python paper_importer.py <pdf_file>
    python paper_importer.py <pdf_file> --threshold 45
    python paper_importer.py <pdf_file> --collection "My Papers" --tag "to-read"
    python paper_importer.py <pdf_file> --dry-run
    python paper_importer.py <directory>          # process all PDFs in folder

Exit codes:
    0 = successfully imported (or would import, in dry-run)
    1 = not a paper / no metadata found / import failed
    2 = configuration or file error
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

import requests

from config import load_env, load_settings, expand

# Suppress noisy warnings from pypdf about malformed PDFs
logging.getLogger("pypdf").setLevel(logging.ERROR)

try:
    from pyzotero import zotero as pyzotero_client
except ImportError:
    pyzotero_client = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


# =========================================================================
# PDF text and metadata extraction
# =========================================================================

def extract_text(pdf_path: str, max_pages: int = 5) -> list[str]:
    """Return a list of per-page text strings from the first N pages."""
    pages = []
    if pdfplumber:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages[:max_pages]:
                    pages.append(page.extract_text() or "")
        except Exception:
            pass
    if not pages and PdfReader:
        try:
            reader = PdfReader(pdf_path)
            for page in reader.pages[:max_pages]:
                pages.append(page.extract_text() or "")
        except Exception:
            pass
    return pages


def extract_last_pages(pdf_path: str, n: int = 3) -> list[str]:
    """Return text from the last N pages of a PDF (for references detection)."""
    pages = []
    if pdfplumber:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages[-n:]:
                    pages.append(page.extract_text() or "")
        except Exception:
            pass
    if not pages and PdfReader:
        try:
            reader = PdfReader(pdf_path)
            for page in reader.pages[-n:]:
                pages.append(page.extract_text() or "")
        except Exception:
            pass
    return pages


def get_pdf_metadata(pdf_path: str) -> dict:
    """Extract embedded PDF metadata."""
    try:
        reader = PdfReader(pdf_path)
        meta = reader.metadata or {}
        # Safely convert all values to strings — some PDFs return
        # IndirectObject instead of str, which breaks string operations
        def safe_str(val):
            try:
                return str(val) if val else ""
            except Exception:
                return ""
        return {
            "title": safe_str(meta.get("/Title", "")),
            "author": safe_str(meta.get("/Author", "")),
            "creator": safe_str(meta.get("/Creator", "")),
            "producer": safe_str(meta.get("/Producer", "")),
            "num_pages": len(reader.pages),
        }
    except Exception:
        return {}


# =========================================================================
# Paper detection — heuristic checks
# =========================================================================

DOI_RE = re.compile(r'\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', re.IGNORECASE)
# Also try joining lines to catch DOIs split across line breaks
DOI_MULTILINE_RE = re.compile(r'\b(10\.\d{4,9}/\S+)', re.IGNORECASE)
ARXIV_RE = re.compile(r'arXiv:(\d{4}\.\d{4,5}(?:v\d+)?)', re.IGNORECASE)

SECTION_HEADINGS = [
    "abstract", "introduction", "background", "related work",
    "literature review", "methods", "methodology",
    "materials and methods", "experimental setup", "experiment",
    "results", "discussion", "conclusion", "conclusions",
    "acknowledgements", "acknowledgments", "references",
    "bibliography", "supplementary", "appendix",
    "author contributions", "competing interests",
    "conflicts of interest", "data availability",
]

ACADEMIC_KEYWORDS = [
    "journal", "vol.", "volume", "issue", "pp.", "pages",
    "published", "received", "accepted", "revised",
    "peer-reviewed", "open access", "creative commons",
    "springer", "elsevier", "wiley", "ieee", "acm", "plos",
    "nature", "science", "proceedings", "conference",
    "symposium", "transactions on", "orcid",
    "corresponding author", "manuscript", "preprint",
]


def detect_paper(pdf_path: str, pages: list[str], metadata: dict, last_pages: list[str] | None = None) -> dict:
    """
    Run heuristic checks and return a dict with:
        score, max_possible, confidence (0-100), signals {}
    """
    signals = {}
    score = 0
    max_possible = 0

    def add(name, points, maximum, detail):
        nonlocal score, max_possible
        max_possible += maximum
        score += points
        if detail:
            signals[name] = {"score": points, "max": maximum, "detail": detail}

    full = "\n".join(pages)
    first_two = "\n".join(pages[:2])
    # Also create a version with line breaks removed for DOI matching
    first_two_joined = " ".join(pages[:3]).replace("\n", " ")
    first_lower = (pages[0] if pages else "").lower()
    full_lower = full.lower()

    # DOI (25) — try normal regex first, then joined-line version
    m = DOI_RE.search(first_two)
    if not m:
        m = DOI_MULTILINE_RE.search(first_two_joined)
    add("doi", 25 if m else 0, 25, f"DOI: {m.group(1)}" if m else None)

    # arXiv (20)
    m = ARXIV_RE.search(first_two)
    add("arxiv", 20 if m else 0, 20, f"arXiv: {m.group(1)}" if m else None)

    # Abstract heading (20) — much more forgiving patterns
    first_two_lower = "\n".join(pages[:2]).lower()
    abstract_patterns = [
        r'^\s*abstract\s*$',                    # standalone line
        r'^\s*abstract\s*[:\-—–.]',             # "Abstract:" or "Abstract."
        r'^\s*a\s*b\s*s\s*t\s*r\s*a\s*c\s*t',  # spaced-out letters
        r'\babstract\b.*\n',                     # word "abstract" near start of a line
    ]
    has_abstract = any(
        re.search(pat, first_two_lower, re.MULTILINE)
        for pat in abstract_patterns
    )
    # Also check for an italic/indented abstract block (common in humanities)
    if not has_abstract:
        # Many papers have "Abstract" as inline bold text followed by content
        has_abstract = bool(re.search(r'\babstract\b', first_two_lower))
    add("abstract", 15 if has_abstract else 0, 15,
        "Found 'Abstract' or abstract content" if has_abstract else None)

    # References section (15) — search actual last pages of document
    if last_pages:
        last_text = "\n".join(last_pages).lower()
    else:
        last_text = "\n".join(pages[-3:]).lower() if len(pages) >= 3 else full_lower
    all_text_lower = full_lower + "\n" + (("\n".join(last_pages).lower()) if last_pages else "")
    ref_patterns = [
        r'^\s*references\s*$',                   # standalone
        r'^\s*bibliography\s*$',                  # standalone
        r'^\s*\d*\.?\s*references\s*$',           # "8. References"
        r'^\s*\[1\]\s+\w',                        # numbered [1] style
        r'^\s*references\s*\n',                    # references followed by newline
        r'\breferences\b',                         # word "references" anywhere in last pages
        r'^\s*literature\s+cited\s*$',             # alt heading
        r'^\s*works\s+cited\s*$',                  # alt heading
        r'^\s*endnotes\s*$',                       # endnotes section
    ]
    has_refs = any(
        re.search(pat, last_text, re.MULTILINE)
        for pat in ref_patterns
    )
    # Also check entire text for references-like content
    if not has_refs:
        has_refs = bool(re.search(r'\breferences\b|\bbibliography\b', all_text_lower))
    add("references", 15 if has_refs else 0, 15,
        "Found references/bibliography" if has_refs else None)

    # Section structure (15) — much more flexible heading detection
    # Include last pages in the search (for acknowledgments, references headings)
    all_pages_text = full_lower
    if last_pages:
        all_pages_text += "\n" + "\n".join(last_pages).lower()
    found_headings = set()
    for h in SECTION_HEADINGS:
        patterns = [
            # Standard: "Introduction" or "1. Introduction" on its own line
            rf'^\s*(?:\d+\.?\s*)?{re.escape(h)}\s*$',
            # Number on previous line, heading on next: "1.\n Introduction"
            rf'^\s*{re.escape(h)}\s*$',
            # ALL CAPS version
            rf'^\s*(?:\d+\.?\s*)?{re.escape(h).upper()}\s*$',
            # Heading with trailing content: "Introduction\nSome text..."
            rf'^\s*(?:\d+\.?\s+)?{re.escape(h)}\s*\n',
            # Inline numbered: "1 Introduction" or "1. Introduction"
            rf'^\s*\d+\.?\s+{re.escape(h)}',
            # Two-column merged: "Introduction" at start of line but with trailing text from other column
            rf'^{re.escape(h)}\S',
            # CamelCase merged: "MaterialsandMethods" — no spaces between heading words
            rf'{"".join(re.escape(w) for w in h.split())}',
        ]
        for pat in patterns:
            if re.search(pat, all_pages_text, re.MULTILINE):
                found_headings.add(h)
                break
    # Also look for ALL CAPS headings that might be section titles
    caps_headings = re.findall(r'^\s*\d*\.?\s*([A-Z][A-Z\s]{4,40})\s*$', full, re.MULTILINE)
    for ch in caps_headings:
        ch_lower = ch.strip().lower()
        for h in SECTION_HEADINGS:
            if h in ch_lower:
                found_headings.add(h)
    found_headings = sorted(found_headings)
    n = len(found_headings)
    pts = 15 if n >= 3 else 10 if n == 2 else 5 if n == 1 else 0
    add("sections", pts, 15,
        f"{n} headings: {', '.join(found_headings[:5])}" if n else None)

    # Academic keywords (10) — search first two pages, case-insensitive
    first_two_lower_flat = first_two.lower()
    found_kw = [kw for kw in ACADEMIC_KEYWORDS if kw in first_two_lower_flat]
    n = len(found_kw)
    pts = 10 if n >= 3 else 7 if n == 2 else 4 if n == 1 else 0
    add("keywords", pts, 10,
        f"{n} keywords: {', '.join(found_kw[:4])}" if n else None)

    # PDF metadata — LaTeX / TeX creator (10)
    creator = (metadata.get("creator", "") + " " + metadata.get("producer", "")).lower()
    academic_tools = ["latex", "tex", "dvips", "arxiv"]
    tool_hit = next((t for t in academic_tools if t in creator), None)
    add("creator", 7 if tool_hit else 0, 10,
        f"Creator includes '{tool_hit}'" if tool_hit else None)

    # Page count (5)
    np_ = metadata.get("num_pages", 0)
    pts = 5 if 4 <= np_ <= 60 else 3 if 2 <= np_ <= 3 else 2 if 61 <= np_ <= 100 else 0
    add("pages", pts, 5,
        f"{np_} pages" if pts else (f"{np_} pages (unusual range)" if np_ else None))

    # Two-column layout (8)
    col_pts = 0
    if pdfplumber:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                check = pdf.pages[1:3] if len(pdf.pages) > 1 else pdf.pages[:1]
                for pg in check:
                    words = pg.extract_words()
                    if len(words) < 20:
                        continue
                    mid = float(pg.width) / 2
                    left = sum(1 for w in words if float(w["x1"]) < mid * 0.9)
                    right = sum(1 for w in words if float(w["x0"]) > mid * 1.1)
                    if max(left, right) > 0:
                        balance = min(left, right) / max(left, right)
                        gap = sum(1 for w in words
                                  if float(w["x0"]) < mid * 1.1
                                  and float(w["x1"]) > mid * 0.9)
                        if balance > 0.3 and (gap / len(words)) < 0.15:
                            col_pts = 8
                            break
        except Exception:
            pass
    add("columns", col_pts, 8, "Two-column layout detected" if col_pts else None)

    # Citation patterns (7)
    numbered = re.findall(r'\[(\d{1,3}(?:\s*[,\-–]\s*\d{1,3})*)\]', full)
    author_year = re.findall(
        r'\([A-Z][a-z]+(?:\s+(?:et\s+al\.?|and|&)\s+[A-Z][a-z]+)?,\s*\d{4}\)', full
    )
    # Also catch patterns like "(Smithetal.,2002)" with merged spaces
    author_year_merged = re.findall(
        r'\([A-Z][a-z]+(?:etal\.?|and[A-Z])?,?\s*\d{4}\)', full
    )
    # Also catch "Smith 2004" or "Smith, 2004" style (common in humanities)
    inline_year = re.findall(r'\b[A-Z][a-z]+\s+\d{4}[a-z]?\b', full)
    cites = len(numbered) + len(author_year) + len(author_year_merged)
    # Use inline_year as weaker signal
    if cites < 2 and len(inline_year) >= 5:
        cites = max(cites, len(inline_year) // 2)
    pts = 7 if cites >= 10 else 5 if cites >= 5 else 3 if cites >= 2 else 0
    add("citations", pts, 7, f"{cites} in-text citations" if cites else None)

    confidence = min(100, round(score / max_possible * 100)) if max_possible else 0
    return {
        "score": score,
        "max_possible": max_possible,
        "confidence": confidence,
        "signals": signals,
    }


# =========================================================================
# Identifier and title extraction
# =========================================================================

# DOI prefixes for data repositories (figshare / zenodo / dryad / osf).
# These appear in data-availability sections and must not outrank the article.
DATA_REPO_PREFIXES = ("10.6084/", "10.5281/", "10.5061/", "10.17605/")


def extract_doi(text: str) -> str | None:
    """Return the paper's DOI, choosing the best candidate rather than the
    first match.

    A single PDF commonly contains several DOI-like strings: a truncated
    publisher stub from a wrapped link (e.g. "10.1073/pnas." -> "10.1073/pnas",
    which resolves to the *journal* on CrossRef), the real article DOI repeated
    in every page footer, and dataset DOIs in the data-availability section.
    We score candidates so the article DOI wins:
      1. articles over data-repository DOIs,
      2. a complete suffix (contains a digit) over a publisher stub,
      3. the most frequently printed DOI (the footer article DOI),
      4. then the most specific (longest).
    Trailing punctuation is stripped so lookups don't 404 on a stray ';'/'.'.
    """
    def _clean(doi: str) -> str:
        return re.sub(r'[\s.,;:]+$', '', doi)

    raw = []
    for pat, source in ((DOI_RE, text),
                        (DOI_MULTILINE_RE, text.replace("\n", " ").replace("  ", " "))):
        for m in pat.finditer(source):
            d = _clean(m.group(1))
            if d:
                raw.append(d)
    if not raw:
        return None

    counts = Counter(d.lower() for d in raw)
    first_case = {}
    for d in raw:
        first_case.setdefault(d.lower(), d)

    def score(key: str):
        suffix = key.split("/", 1)[1] if "/" in key else ""
        return (
            0 if key.startswith(DATA_REPO_PREFIXES) else 1,
            1 if any(c.isdigit() for c in suffix) else 0,
            counts[key],
            len(key),
        )

    return first_case[max(counts, key=score)]


def extract_arxiv_id(text: str) -> str | None:
    m = ARXIV_RE.search(text)
    return m.group(1) if m else None


def extract_title(text: str) -> str | None:
    """Heuristic title extraction from the first page."""
    candidates = []
    skip_patterns = [
        "volume", "vol.", "issue", "pp.", "issn", "doi:", "doi ",
        "received", "accepted", "downloaded", "annual review",
        "copyright", "http", "www.", "guest", "ip:",
        "journal of", "the journal", "proceedings", "transactions",
        "articles", "behavioral/", "open access", "creative commons",
        "keywords:", "key words:", "first published",
        "college", "university", "department", "emeritus",
        "arjats", "cls ",
    ]
    for line in text.split("\n")[:30]:
        line = line.strip()
        if not line or len(line) < 10:
            continue
        lower = line.lower()
        if DOI_RE.search(line):
            continue
        if any(kw in lower for kw in skip_patterns):
            continue
        # Skip lines that look like author affiliations
        if "@" in line:
            continue
        # Skip lines that are URLs or reversed URLs
        if re.match(r'^[\w.]+\.(org|edu|com|net|gov)', lower):
            continue
        if '.org' in lower and len(line) < 40:
            continue
        # Skip lines that look like reversed text (common PDF artifact)
        if re.search(r'\.[a-z]{2,4}\.[a-z]', lower) and '/' not in line:
            continue
        # Skip lines that are mostly numbers/punctuation (page headers, IDs)
        alpha_ratio = sum(1 for c in line if c.isalpha()) / max(len(line), 1)
        if alpha_ratio < 0.5:
            continue
        # Skip lines with spaced-out capital letters (journal names like "T HE B IBLE")
        if re.match(r'^[A-Z]\s[A-Z][A-Z\s]+[A-Z]$', line):
            continue
        # Skip lines that look like merged two-column text (very long with no sentence structure)
        if len(line) > 200 and ' ' not in line[:50]:
            continue
        if 10 < len(line) < 300:
            candidates.append(line)
        if len(candidates) >= 3:
            break
    if candidates:
        title = candidates[0]
        # Join continuation lines
        for i in range(1, len(candidates)):
            next_line = candidates[i]
            # Join if: next starts with lowercase, current ends with preposition/conjunction,
            # current line is short (likely a multi-line title), or next is short continuation
            ends_with_connector = title.rstrip().split()[-1].lower() in [
                'a', 'an', 'the', 'of', 'in', 'on', 'for', 'and', 'or', 'to',
                'with', 'from', 'by', 'how', 'why', 'that', 'which', 'its',
            ]
            next_starts_lower = next_line and next_line[0].islower()
            title_short = len(title) < 60
            # Don't join if next line looks like an author name
            # Author lines typically have: "FirstName LastName," or "FirstName M. LastName"
            # or contain digits (affiliations) or are very short single names
            looks_like_author = bool(re.match(
                r'^[A-Z][a-z]+\s+[A-Z]\.?\s+[A-Z][a-z]', next_line  # "Thomas C. Chou"
            )) or bool(re.match(
                r'^[A-Z][a-z]+\s+[A-Z][a-z]+\s*,', next_line  # "Lauren Ross,"
            )) or bool(re.match(
                r'^[A-Z][a-z]+\s+[A-Z]\.\s', next_line  # "John A. Smith"
            ))
            if (next_starts_lower or ends_with_connector or title_short) and not looks_like_author:
                title = title.rstrip() + " " + next_line
            else:
                break
        return title
    return None


# =========================================================================
# Metadata lookup — CrossRef and OpenAlex
# =========================================================================

HEADERS = {"User-Agent": "PaperAutoImporter/1.0 (research-pipeline)"}


def lookup_crossref_doi(doi: str) -> dict | None:
    try:
        r = requests.get(f"https://api.crossref.org/works/{doi}",
                         headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json().get("message")
    except Exception:
        pass
    return None


def lookup_crossref_title(title: str) -> dict | None:
    try:
        r = requests.get("https://api.crossref.org/works",
                         params={"query.title": title, "rows": 3},
                         headers=HEADERS, timeout=15)
        if r.status_code == 200:
            items = r.json().get("message", {}).get("items", [])
            if items:
                best_title = " ".join(items[0].get("title", []))
                words_a = set(title.lower().split())
                words_b = set(best_title.lower().split())
                overlap = len(words_a & words_b) / max(len(words_a), len(words_b), 1)
                if overlap > 0.6:
                    return items[0]
    except Exception:
        pass
    return None


def lookup_openalex_arxiv(arxiv_id: str) -> dict | None:
    try:
        r = requests.get(f"https://api.openalex.org/works/https://arxiv.org/abs/{arxiv_id}",
                         headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


# =========================================================================
# Metadata → Zotero item conversion
# =========================================================================

def crossref_to_zotero(cr: dict) -> dict:
    type_map = {
        "journal-article": "journalArticle",
        "proceedings-article": "conferencePaper",
        "book-chapter": "bookSection",
        "book": "book",
        "posted-content": "preprint",
        "report": "report",
    }
    item_type = type_map.get(cr.get("type", ""), "journalArticle")

    creators = []
    for a in cr.get("author", []):
        given, family = a.get("given", ""), a.get("family", "")
        if given and family:
            creators.append({"creatorType": "author", "firstName": given, "lastName": family})
        elif family:
            creators.append({"creatorType": "author", "name": family})

    date_parts = cr.get("published", cr.get("issued", {})).get("date-parts", [[]])
    date_str = ""
    if date_parts and date_parts[0]:
        p = date_parts[0]
        date_str = f"{p[0]}" + (f"-{p[1]:02d}" if len(p) > 1 else "") + (f"-{p[2]:02d}" if len(p) > 2 else "")

    # CrossRef titles/abstracts often carry JATS/HTML markup (<i>, <sub>, ...)
    # and embedded newlines; strip tags and collapse whitespace.
    def _clean_text(val: str) -> str:
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', val or '')).strip()

    abstract = _clean_text(cr.get("abstract", ""))

    item = {
        "itemType": item_type,
        "title": _clean_text(" ".join(cr.get("title", ["Untitled"]))) or "Untitled",
        "creators": creators,
        "date": date_str,
        "DOI": cr.get("DOI", ""),
        "url": cr.get("URL", ""),
        "abstractNote": abstract,
    }

    container = cr.get("container-title", [])
    if item_type == "journalArticle":
        item["publicationTitle"] = container[0] if container else ""
        item["volume"] = cr.get("volume", "")
        item["issue"] = cr.get("issue", "")
        item["pages"] = cr.get("page", "")
        item["ISSN"] = ", ".join(cr.get("ISSN", []))
    elif item_type == "conferencePaper":
        item["proceedingsTitle"] = container[0] if container else ""
        item["conferenceName"] = cr.get("event", {}).get("name", "")
    elif item_type == "bookSection":
        item["bookTitle"] = container[0] if container else ""
        item["publisher"] = cr.get("publisher", "")

    return item


def openalex_to_zotero(oa: dict) -> dict:
    creators = []
    for auth in oa.get("authorships", []):
        name = auth.get("author", {}).get("display_name", "")
        if name:
            parts = name.rsplit(" ", 1)
            if len(parts) == 2:
                creators.append({"creatorType": "author", "firstName": parts[0], "lastName": parts[1]})
            else:
                creators.append({"creatorType": "author", "name": name})

    source = (oa.get("primary_location") or {}).get("source") or {}
    biblio = oa.get("biblio") or {}

    return {
        "itemType": "journalArticle",
        "title": oa.get("display_name", "Untitled"),
        "creators": creators,
        "date": oa.get("publication_date", ""),
        "DOI": (oa.get("doi") or "").replace("https://doi.org/", ""),
        "url": oa.get("doi", "") or oa.get("id", ""),
        "publicationTitle": source.get("display_name", ""),
        "volume": str(biblio.get("volume", "") or ""),
        "issue": str(biblio.get("issue", "") or ""),
        "pages": str(biblio.get("first_page", "") or ""),
    }


# =========================================================================
# Zotero API interaction
# =========================================================================

def load_config() -> dict:
    load_env()
    settings = load_settings()

    lib_id = os.environ.get("ZOTERO_LIBRARY_ID", "")
    api_key = os.environ.get("ZOTERO_API_KEY", "")
    if not lib_id or not api_key:
        return None
    return {
        "library_id": lib_id,
        "library_type": settings.get("zotero", {}).get("library_type", "user"),
        "api_key": api_key,
    }


def find_or_create_collection(zot, name: str) -> str | None:
    for c in zot.everything(zot.collections()):
        if c["data"]["name"].lower() == name.lower():
            return c["data"]["key"]
    try:
        result = zot.create_collections([{"name": name}])
        if result and "successful" in result:
            return result["successful"]["0"]["data"]["key"]
    except Exception:
        pass
    return None


# =========================================================================
# Main pipeline
# =========================================================================

def _pdf_stored(zot, item_key: str) -> bool:
    """True if the item has a PDF attachment with real stored file content
    (a non-empty md5) — i.e. the upload actually landed, not a link/placeholder.
    """
    try:
        for child in zot.children(item_key):
            d = child.get("data", {})
            if (d.get("itemType") == "attachment"
                    and d.get("linkMode") == "imported_file"
                    and d.get("md5")):
                return True
    except Exception:
        pass
    return False


def process_pdf(
    pdf_path: str,
    threshold: int = 45,
    collection: str | None = None,
    tags: list[str] | None = None,
    output_dir: str | None = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """
    Process a single PDF through the full pipeline.

    Returns a result dict:
        status: "imported" | "not_paper" | "no_metadata" | "failed" | "error"
        confidence: int (0-100)
        title: str or None
        message: str
    """
    filename = os.path.basename(pdf_path)
    log = print if verbose else (lambda *a, **k: None)

    result = {
        "file": filename,
        "status": "error",
        "confidence": 0,
        "title": None,
        "message": "",
    }

    # --- Extract text ---
    log(f"\n{'='*60}")
    log(f"  {filename}")
    log(f"{'='*60}")

    pages = extract_text(pdf_path)
    if not pages or all(not p.strip() for p in pages):
        result["message"] = "No extractable text (scanned PDF without OCR?)"
        log(f"  Error: {result['message']}")
        return result

    last_pages = extract_last_pages(pdf_path, n=5)
    pdf_meta = get_pdf_metadata(pdf_path)

    # --- Detect paper ---
    detection = detect_paper(pdf_path, pages, pdf_meta, last_pages)
    result["confidence"] = detection["confidence"]

    log(f"  Confidence: {detection['confidence']}%")
    if detection["signals"]:
        for name, info in detection["signals"].items():
            log(f"    [{info['score']:>2}/{info['max']:>2}] {name}: {info['detail']}")

    if detection["confidence"] < threshold:
        result["status"] = "not_paper"
        result["message"] = f"Confidence {detection['confidence']}% below threshold {threshold}%"
        log(f"  Verdict: NOT A PAPER")
        return result

    log(f"  Verdict: PAPER — proceeding to metadata lookup")

    # --- Extract identifiers ---
    full_text = "\n".join(pages[:3])
    doi = extract_doi(full_text)

    # Fallback: try to find DOI in PDF metadata
    if not doi:
        meta_str = " ".join(str(v) for v in pdf_meta.values() if isinstance(v, str))
        doi = extract_doi(meta_str)

    # Fallback: try to extract DOI from filename
    if not doi:
        doi_from_name = DOI_RE.search(filename.replace("_", "/"))
        if doi_from_name:
            doi = doi_from_name.group(1).rstrip(".")

    # Clean up truncated DOIs (trailing hyphens, spaces)
    if doi:
        doi = doi.rstrip("-").rstrip()

    arxiv_id = extract_arxiv_id(full_text)
    title = extract_title(pages[0] if pages else "")

    log(f"  DOI:   {doi or 'not found'}")
    log(f"  arXiv: {arxiv_id or 'not found'}")
    log(f"  Title: {(title[:55] + '...') if title and len(title) > 55 else title or 'not found'}")

    # --- Look up metadata ---
    zotero_item = None
    source = None

    if doi:
        log(f"  Looking up DOI on CrossRef...")
        cr = lookup_crossref_doi(doi)
        container_types = ("journal", "journal-issue", "journal-volume",
                           "book-series", "book-set")
        if cr and cr.get("type") in container_types:
            log(f"  DOI resolved to a {cr.get('type')} (container), not an "
                f"article — ignoring and trying title/arXiv")
            cr = None
        if cr:
            zotero_item = crossref_to_zotero(cr)
            source = "CrossRef (DOI)"

    if not zotero_item and arxiv_id:
        log(f"  Looking up arXiv on OpenAlex...")
        oa = lookup_openalex_arxiv(arxiv_id)
        if oa:
            zotero_item = openalex_to_zotero(oa)
            source = "OpenAlex (arXiv)"

    if not zotero_item and title:
        log(f"  Searching CrossRef by title...")
        cr = lookup_crossref_title(title)
        if cr:
            zotero_item = crossref_to_zotero(cr)
            source = "CrossRef (title)"

    if not zotero_item:
        result["status"] = "no_metadata"
        result["message"] = "Paper detected but no metadata found"
        log(f"  No metadata found — manual import needed")
        return result

    result["title"] = zotero_item.get("title", "")
    log(f"  Metadata via {source}:")
    log(f"    Title:   {zotero_item['title'][:65]}")
    authors = ", ".join(
        c.get("lastName", c.get("name", "?"))
        for c in zotero_item.get("creators", [])[:4]
    )
    log(f"    Authors: {authors}")
    log(f"    Date:    {zotero_item.get('date', 'N/A')}")

    # --- Dry run stops here ---
    if dry_run:
        result["status"] = "imported"
        result["message"] = "Dry run — metadata found, would import"
        log(f"  [DRY RUN] Would create item and upload PDF")
        return result

    # --- Connect to Zotero ---
    if not pyzotero_client:
        result["message"] = "pyzotero not installed"
        log(f"  Error: pyzotero not installed. Run: pip install pyzotero")
        return result

    config = load_config()
    if not config:
        result["message"] = "Zotero API credentials not configured"
        log(f"  Error: Set ZOTERO_LIBRARY_ID and ZOTERO_API_KEY")
        log(f"  Get these from: https://www.zotero.org/settings/keys")
        return result

    zot = pyzotero_client.Zotero(
        config["library_id"], config["library_type"], config["api_key"]
    )

    # Tags
    if tags:
        zotero_item["tags"] = [{"tag": t} for t in tags]

    # Collection
    if collection:
        log(f"  Finding/creating collection '{collection}'...")
        coll_key = find_or_create_collection(zot, collection)
        if coll_key:
            zotero_item["collections"] = [coll_key]

    # Duplicate check
    if zotero_item.get("DOI"):
        existing = zot.items(q=zotero_item["DOI"], qmode="everything")
        for item in existing:
            if item["data"].get("DOI", "").lower() == zotero_item["DOI"].lower():
                result["status"] = "failed"
                result["message"] = "Duplicate — DOI already in library"
                log(f"  Skipping: item with this DOI already exists")
                return result

    # Create item
    log(f"  Creating Zotero item...")
    try:
        resp = zot.create_items([zotero_item])
    except Exception as e:
        result["message"] = f"API error: {e}"
        log(f"  Error creating item: {e}")
        return result

    if not resp or "successful" not in resp or not resp["successful"]:
        result["status"] = "failed"
        result["message"] = f"Item creation failed: {resp.get('failed', 'unknown')}"
        log(f"  Error: {result['message']}")
        return result

    item_key = resp["successful"]["0"]["key"]
    log(f"  Created item: {item_key}")

    # Attach PDF — then VERIFY the file actually stored. A silent upload
    # failure (e.g. Zotero storage quota) otherwise leaves a useless
    # "link only" item with no offline / annotatable PDF.
    log(f"  Uploading PDF...")
    upload_ok = False
    attach_err = ""
    try:
        zot.attachment_simple([pdf_path], item_key)
        upload_ok = _pdf_stored(zot, item_key)
        if not upload_ok:
            attach_err = "no stored file found after upload"
    except Exception as e:
        attach_err = ("Zotero storage quota exceeded"
                      if "quota" in str(e).lower() else str(e))

    if not upload_ok:
        # Item exists but is link-only. Leave the PDF where it is so a re-run
        # retries it, and flag this loudly rather than reporting a false success.
        result["status"] = "no_file"
        result["message"] = (f"Item {item_key} created but PDF NOT stored "
                             f"({attach_err}); PDF left in place for retry")
        log(f"  ERROR: PDF not stored ({attach_err}).")
        log(f"  Item is link-only — PDF left at {pdf_path} for retry.")
        return result

    result["status"] = "imported"
    result["message"] = f"Imported as {item_key} (PDF stored)"
    log(f"  PDF attached and verified")
    log(f"  Done!")

    # --- Rename and move PDF if output_dir is set ---
    if output_dir:
        try:
            out = Path(output_dir).expanduser()
            out.mkdir(parents=True, exist_ok=True)

            # Build a clean filename from the paper title
            paper_title = zotero_item.get("title", "")
            if paper_title:
                # Sanitise: remove characters that are invalid in filenames
                clean = re.sub(r'[<>:"/\\|?*]', '', paper_title)
                clean = clean.strip('. ')
                # Truncate to a reasonable length
                if len(clean) > 150:
                    clean = clean[:150].rsplit(' ', 1)[0]
                new_name = f"{clean}.pdf"
            else:
                new_name = os.path.basename(pdf_path)

            dest = out / new_name
            # Handle name collisions
            if dest.exists():
                stem = dest.stem
                for i in range(2, 100):
                    dest = out / f"{stem} ({i}).pdf"
                    if not dest.exists():
                        break

            shutil.move(pdf_path, str(dest))
            log(f"  Moved → {dest}")
            result["moved_to"] = str(dest)
        except Exception as e:
            log(f"  Warning: Could not move PDF: {e}")

    return result


# =========================================================================
# Entry point
# =========================================================================

def main():
    # Non-secret defaults come from configs/config.yaml; CLI flags override.
    imp = load_settings().get("import", {})

    parser = argparse.ArgumentParser(
        description="Detect research papers and import them to Zotero.",
    )
    parser.add_argument("path", nargs="?", default=None,
                        help="PDF file or directory of PDFs "
                             "(default: import.source_dir from config.yaml)")
    parser.add_argument("--threshold", type=int, default=imp.get("threshold", 45),
                        help="Detection confidence threshold 0-100")
    parser.add_argument("--collection", "-c",
                        help="Zotero collection to add papers to")
    parser.add_argument("--tag", "-t", action="append", dest="tags",
                        help="Tag to add (repeatable); "
                             "defaults to import.tag from config.yaml")
    parser.add_argument("--output-dir", "-o",
                        default=expand(imp.get("output_dir")) or None,
                        help="Move imported PDFs here, renamed to paper title "
                             "(default: import.output_dir from config.yaml)")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Detect and look up metadata but don't import")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Suppress progress output (use with --json)")
    args = parser.parse_args()

    # Fall back to config.yaml for the scan path and tag when not given.
    if args.path is None:
        args.path = expand(imp.get("source_dir"))
    if not args.path:
        print("Error: no path given and import.source_dir is not set in config.yaml")
        sys.exit(2)
    if not args.tags:
        default_tag = imp.get("tag")
        args.tags = [default_tag] if default_tag else None

    target = Path(args.path)

    if target.is_file():
        pdfs = [target]
    elif target.is_dir():
        pdfs = sorted(target.glob("*.pdf"))
        if not pdfs:
            print(f"No PDFs found in {target}")
            sys.exit(2)
    else:
        print(f"Error: {args.path} not found")
        sys.exit(2)

    results = []
    for pdf in pdfs:
        r = process_pdf(
            pdf_path=str(pdf),
            threshold=args.threshold,
            collection=args.collection,
            tags=args.tags,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
            verbose=not args.quiet,
        )
        results.append(r)

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))

    # Summary for directory processing
    if len(results) > 1 and not args.quiet:
        imported = sum(1 for r in results if r["status"] == "imported")
        not_paper = sum(1 for r in results if r["status"] == "not_paper")
        no_meta = sum(1 for r in results if r["status"] == "no_metadata")
        no_file = sum(1 for r in results if r["status"] == "no_file")
        failed = sum(1 for r in results if r["status"] in ("failed", "error"))
        print(f"\nSummary: {imported} imported, {not_paper} not papers, "
              f"{no_meta} no metadata, {no_file} created-but-no-PDF, {failed} failed")
        if no_file:
            print(f"WARNING: {no_file} item(s) were created WITHOUT a stored PDF "
                  f"(link-only); their PDFs were left in place for retry.")

    # Exit code: non-zero if anything is link-only or nothing imported cleanly.
    if any(r["status"] == "no_file" for r in results):
        sys.exit(1)
    elif any(r["status"] == "imported" for r in results):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
