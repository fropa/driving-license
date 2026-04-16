#!/usr/bin/env python3
"""
Scrapes driving license exam tickets from emsi.ge via its JSON API.
Tickets are grouped by themes/chapters.

Usage:
    python scrape_tickets.py                          # B/B1, Georgian, → tickets.json
    python scrape_tickets.py --category "A,A1"        # different category
    python scrape_tickets.py --language eng            # English
    python scrape_tickets.py --output out             # output basename (no extension)
    python scrape_tickets.py --format csv             # CSV instead of JSON
    python scrape_tickets.py --format both            # both JSON and CSV

Requirements:
    pip install requests

API endpoints (public, no auth needed):
    GET /c/exam/cats?main_cat=B,%20B1&noCache=0
        → {"cats": [{id, title, quantity, ...}, ...]}

    GET /c/exam/newTickets?language=geo&quantity=500&main_cat=B,%20B1&chapters=1&page=1
        → {"paginationData": {"data": [{...ticket...}], "last_page": N, ...}}

Ticket fields:
    id, question, ans1–ans4, cat_id, description (hint/explanation),
    img_own (image filename; full URL: IMAGE_BASE + img_own)

Note on correct answers:
    ans1 is always the correct answer in the raw API data.
    The website shuffles answer order before display.
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests

BASE       = "https://emsi.ge"
CATS_URL   = f"{BASE}/c/exam/cats"
TICKETS_URL= f"{BASE}/c/exam/newTickets"
IMAGE_BASE = f"{BASE}/admin/upload/img/tickets/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://emsi.ge/",
}


# ──────────────────────────────────────────────
# API helpers
# ──────────────────────────────────────────────

def get_chapters(category: str) -> list:
    """Return list of chapter dicts from the /cats endpoint."""
    r = requests.get(
        CATS_URL,
        params={"main_cat": category, "noCache": 0},
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["cats"]


def get_tickets_for_chapter(chapter_id: int, category: str, language: str) -> list:
    """
    Fetch all tickets for a given chapter, handling pagination.
    Returns a list of normalised ticket dicts.
    """
    all_tickets = []
    page = 1

    while True:
        r = requests.get(
            TICKETS_URL,
            params={
                "language": language,
                "quantity": 500,        # large batch to minimise page count
                "main_cat": category,
                "chapters": chapter_id,
                "page": page,
            },
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()

        pg = r.json()["paginationData"]
        raw_tickets = pg["data"]

        for t in raw_tickets:
            ticket = _normalise(t)
            if ticket:
                all_tickets.append(ticket)

        if page >= pg["last_page"]:
            break
        page += 1
        time.sleep(0.15)   # be polite

    return all_tickets


def _normalise(raw: dict) -> dict:
    """Convert a raw API ticket into a clean canonical shape."""
    # Build answers list; ans1 is always the correct answer in the raw data
    answers = []
    for i in range(1, 5):
        text = (raw.get(f"ans{i}") or "").strip()
        if text:
            answers.append({"text": text, "correct": i == 1})

    # Skip "ამოღებული" (removed) tickets — not on the real exam
    if raw.get("inactive", 0) != 0:
        return None

    img_own = (raw.get("img_own") or "").strip()
    return {
        "id":          raw.get("id"),
        "question":    (raw.get("question") or "").strip(),
        "image_url":   (IMAGE_BASE + img_own) if img_own else None,
        "answers":     answers,
        "hint":        (raw.get("description") or "").strip() or None,
        "chapter_id":  raw.get("cat_id"),
    }


# ──────────────────────────────────────────────
# Output helpers
# ──────────────────────────────────────────────

def save_json(data: list, path: str):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] Saved JSON → {path}")


def save_csv(data: list, path: str):
    rows = []
    for chapter in data:
        for ticket in chapter["tickets"]:
            for i, ans in enumerate(ticket["answers"]):
                rows.append({
                    "chapter_id":   chapter["chapter_id"],
                    "chapter_title":chapter["chapter_title"],
                    "ticket_id":    ticket["id"],
                    "question":     ticket["question"],
                    "image_url":    ticket["image_url"] or "",
                    "answer_index": i + 1,
                    "answer_text":  ans["text"],
                    "is_correct":   ans["correct"],
                    "hint":         ticket["hint"] or "",
                })

    if not rows:
        print("[-] No rows to write.")
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"[+] Saved CSV  → {path}")


def print_summary(data: list):
    total = sum(len(ch["tickets"]) for ch in data)
    print(f"\n{'═'*55}")
    print(f"  Chapters : {len(data)}")
    print(f"  Tickets  : {total}")
    print(f"{'═'*55}")
    for ch in data:
        print(f"  [{ch['chapter_id']:>3}] {ch['chapter_title']:<40} {len(ch['tickets'])} tickets")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape emsi.ge driving exam tickets")
    parser.add_argument("--category", default="B, B1",  help="Vehicle category (default: 'B, B1')")
    parser.add_argument("--language", default="geo",    help="Language: geo | eng | rus  (default: geo)")
    parser.add_argument("--output",   default="tickets",help="Output filename without extension")
    parser.add_argument("--format",   default="json",   choices=["json", "csv", "both"])
    args = parser.parse_args()

    print(f"[*] Category : {args.category}")
    print(f"[*] Language : {args.language}")

    # 1. Fetch chapter list
    print("[*] Fetching chapter list …")
    chapters_meta = get_chapters(args.category)
    print(f"[*] Found {len(chapters_meta)} chapters")

    # 2. Fetch tickets per chapter
    results = []
    for ch in chapters_meta:
        cid   = ch["id"]
        title = ch.get("title") or ch.get("inEng") or f"Chapter {cid}"
        qty   = ch.get("quantity", "?")
        print(f"  -> [{cid:>3}] {title} ({qty} active) … ", end="", flush=True)

        tickets = get_tickets_for_chapter(cid, args.category, args.language)
        print(f"got {len(tickets)}")

        results.append({
            "chapter_id":    cid,
            "chapter_title": title,
            "tickets":       tickets,
        })

    # 3. Print summary
    print_summary(results)

    # 4. Save
    if args.format in ("json", "both"):
        save_json(results, f"{args.output}.json")
    if args.format in ("csv", "both"):
        save_csv(results, f"{args.output}.csv")


if __name__ == "__main__":
    main()
