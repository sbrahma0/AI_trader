"""
portfolio_parser.py — Parse brokerage statements (PDF / CSV) into holdings.

Supports:
  - Robinhood PDF account statements
  - Generic brokerage CSV exports
  - Claude vision fallback for any PDF layout
"""

import re
import csv
import json
import io
from pathlib import Path
from typing import Optional

# ── PDF PARSING ───────────────────────────────────────────────────────────────

def parse_pdf(file_bytes: bytes) -> list[dict]:
    """
    Main entry point: parse a PDF brokerage statement.
    Returns a list of holdings: [{ticker, shares, avg_cost, company_name}, ...]
    Tries pdfplumber first, falls back to Claude vision.
    """
    holdings = _parse_with_pdfplumber(file_bytes)
    if holdings:
        return holdings

    # Fallback: Claude vision API
    holdings = _parse_with_claude_vision(file_bytes)
    return holdings


def _parse_with_pdfplumber(file_bytes: bytes) -> list[dict]:
    """Extract holdings from PDF using pdfplumber table detection."""
    try:
        import pdfplumber
    except ImportError:
        return []

    holdings = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            # Try table extraction from all pages
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    parsed = _parse_table(table)
                    holdings.extend(parsed)

            # If tables gave nothing, fall back to text parsing
            if not holdings:
                holdings = _parse_text(full_text)
    except Exception as e:
        print(f"pdfplumber error: {e}")
        return []

    return _dedupe_holdings(holdings)


def _parse_table(table: list[list]) -> list[dict]:
    """Parse a table extracted by pdfplumber into holdings."""
    if not table or len(table) < 2:
        return []

    holdings = []
    # Normalize header row
    header = [str(cell or "").lower().strip() for cell in table[0]]

    # Map column names to indices
    col_map = {}
    for i, h in enumerate(header):
        if any(k in h for k in ["symbol", "ticker", "stock"]):
            col_map["ticker"] = i
        elif any(k in h for k in ["shares", "quantity", "qty"]):
            col_map["shares"] = i
        elif any(k in h for k in ["avg cost", "average cost", "cost basis", "avg price", "purchase price"]):
            col_map["avg_cost"] = i
        elif any(k in h for k in ["name", "security", "description"]):
            col_map["company_name"] = i

    if "ticker" not in col_map:
        return []

    for row in table[1:]:
        try:
            ticker = str(row[col_map["ticker"]] or "").strip().upper()
            if not ticker or not re.match(r'^[A-Z]{1,5}$', ticker):
                continue
            shares = _parse_number(row[col_map.get("shares", -1)])
            avg_cost = _parse_number(row[col_map.get("avg_cost", -1)])
            company_name = str(row[col_map.get("company_name", -1)] or "").strip()
            if shares is not None and shares > 0:
                holdings.append({
                    "ticker": ticker,
                    "shares": shares,
                    "avg_cost": avg_cost,
                    "company_name": company_name or None,
                })
        except Exception:
            continue

    return holdings


def _parse_text(text: str) -> list[dict]:
    """
    Regex-based text parser for common Robinhood / brokerage statement formats.
    Handles patterns like:  NVDA  25 shares  $489.23 avg cost
    """
    holdings = []

    # Robinhood pattern: TICKER  N shares  $X.XX avg cost
    pattern_rh = re.compile(
        r'\b([A-Z]{1,5})\b[^\n]*?(\d[\d,]*\.?\d*)\s*(?:shares?|shs?)'
        r'[^\n]*?\$?([\d,]+\.?\d*)\s*(?:avg|average|cost)',
        re.IGNORECASE
    )
    for m in pattern_rh.finditer(text):
        ticker = m.group(1).upper()
        shares = _parse_number(m.group(2))
        avg_cost = _parse_number(m.group(3))
        if shares and shares > 0:
            holdings.append({"ticker": ticker, "shares": shares, "avg_cost": avg_cost, "company_name": None})

    # Generic: lines with TICKER followed by numbers
    if not holdings:
        pattern_generic = re.compile(
            r'\b([A-Z]{2,5})\s+(\d[\d,]*\.?\d*)\s+\$?([\d,]+\.?\d*)',
            re.MULTILINE
        )
        for m in pattern_generic.finditer(text):
            ticker = m.group(1)
            # Skip common non-ticker uppercase words
            skip = {"USD","ETF","IRA","PDF","LLC","INC","THE","AND","FOR","NET"}
            if ticker in skip:
                continue
            shares = _parse_number(m.group(2))
            avg_cost = _parse_number(m.group(3))
            if shares and 0 < shares < 1_000_000:
                holdings.append({"ticker": ticker, "shares": shares, "avg_cost": avg_cost, "company_name": None})

    return holdings


def _parse_with_claude_vision(file_bytes: bytes) -> list[dict]:
    """
    Fallback: send PDF pages as images to Claude and ask it to extract holdings.
    Used when pdfplumber cannot find structured tables.
    """
    try:
        import anthropic
        import base64
        import os

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            print("⚠️  ANTHROPIC_API_KEY not set — Claude vision fallback unavailable.")
            return []

        client = anthropic.Anthropic(api_key=api_key)

        # Convert first few PDF pages to images using pdf2image if available
        try:
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(file_bytes, dpi=150, first_page=1, last_page=5)
        except ImportError:
            print("pdf2image not installed — install with: pip install pdf2image")
            return []

        image_blocks = []
        for img in images[:3]:  # limit to 3 pages
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.standard_b64encode(buf.getvalue()).decode()
            image_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64}
            })

        image_blocks.append({
            "type": "text",
            "text": (
                "This is a brokerage account statement. Extract all stock holdings. "
                "For each holding return a JSON array with objects: "
                "{\"ticker\": \"NVDA\", \"shares\": 25, \"avg_cost\": 489.23, \"company_name\": \"NVIDIA\"} "
                "Return ONLY the JSON array, no other text."
            )
        })

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": image_blocks}]
        )

        raw = response.content[0].text.strip()
        # Extract JSON array from response
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            holdings = []
            for item in data:
                t = str(item.get("ticker", "")).upper().strip()
                s = item.get("shares")
                c = item.get("avg_cost")
                n = item.get("company_name")
                if t and s:
                    holdings.append({"ticker": t, "shares": float(s), "avg_cost": float(c or 0) or None, "company_name": n})
            return holdings

    except Exception as e:
        print(f"Claude vision parsing error: {e}")

    return []


# ── CSV PARSING ───────────────────────────────────────────────────────────────

def parse_csv(file_bytes: bytes) -> list[dict]:
    """
    Parse a brokerage CSV export into holdings.
    Auto-detects column layout from header row.
    """
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    holdings = []

    col_aliases = {
        "ticker":       ["symbol", "ticker", "stock symbol", "instrument"],
        "shares":       ["shares", "quantity", "qty", "units"],
        "avg_cost":     ["average cost", "avg cost", "cost basis/share", "purchase price", "avg price"],
        "company_name": ["name", "description", "security name", "stock name"],
    }

    header = [h.lower().strip() for h in (reader.fieldnames or [])]
    col_map = {}
    for field, aliases in col_aliases.items():
        for alias in aliases:
            if alias in header:
                col_map[field] = alias
                break

    if "ticker" not in col_map:
        # Try positional — first column often ticker
        if reader.fieldnames:
            col_map["ticker"] = reader.fieldnames[0]

    for row in reader:
        try:
            ticker = str(row.get(col_map.get("ticker", ""), "") or "").strip().upper()
            if not ticker or not re.match(r'^[A-Z]{1,5}$', ticker):
                continue
            shares = _parse_number(row.get(col_map.get("shares", ""), ""))
            avg_cost = _parse_number(row.get(col_map.get("avg_cost", ""), ""))
            company_name = str(row.get(col_map.get("company_name", ""), "") or "").strip()
            if shares and shares > 0:
                holdings.append({
                    "ticker": ticker,
                    "shares": shares,
                    "avg_cost": avg_cost,
                    "company_name": company_name or None,
                })
        except Exception:
            continue

    return _dedupe_holdings(holdings)


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _parse_number(val) -> Optional[float]:
    if val is None:
        return None
    s = str(val).replace("$", "").replace(",", "").replace(" ", "").strip()
    if not s or s in ["-", "N/A", "n/a"]:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _dedupe_holdings(holdings: list[dict]) -> list[dict]:
    """Merge duplicate tickers by keeping highest share count."""
    seen = {}
    for h in holdings:
        t = h["ticker"]
        if t not in seen or (h["shares"] or 0) > (seen[t]["shares"] or 0):
            seen[t] = h
    return list(seen.values())


def parse_file(filename: str, file_bytes: bytes) -> list[dict]:
    """Auto-detect file type and parse."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return parse_pdf(file_bytes)
    elif ext in [".csv", ".tsv", ".txt"]:
        return parse_csv(file_bytes)
    else:
        raise ValueError(f"Unsupported file type: {ext}. Upload a PDF or CSV.")
