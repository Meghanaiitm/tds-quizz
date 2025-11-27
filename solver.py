# solver.py  (requests + BeautifulSoup; no external LLM)
import time
import logging
import re
import json
import base64
import os
from typing import Optional

import requests
import pandas as pd
from bs4 import BeautifulSoup

from utils import (
    parse_question_text,
    compute_answer_from_csv_bytes,
    compute_answer_from_excel_bytes,
    compute_answer_from_pdf_bytes,
    file_bytes_to_data_uri,
    df_to_chart_data_uri,
    safe_json_parse,
    enforce_payload_limit,   # <-- FIX ADDED
)
from llm_agent import ask_llm_for_action

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("solver")


# ----------------- entrypoint -----------------

def solve_quiz_with_deadline(url: str, email: str, secret: str,
                             start_time: float, max_seconds: int) -> None:

    deadline = start_time + max_seconds
    if deadline - time.time() <= 3:
        logger.warning("Not enough time left to start solver.")
        return

    try:
        _solve_quiz_chain(url, email, secret, deadline)
    except Exception as e:
        logger.exception("Solver error: %s", e)


# ----------------- main loop -----------------

def _solve_quiz_chain(initial_url: str, email: str, secret: str, deadline: float):

    def time_left():
        return max(0, int(deadline - time.time()))

    current_url = initial_url
    visited = set()

    logger.info("Starting solver chain at %s", initial_url)

    while current_url and time_left() > 6:

        if current_url in visited:
            logger.warning("Loop detected.")
            break
        visited.add(current_url)

        # Fetch page
        try:
            resp = requests.get(current_url, timeout=45)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Page load failed: %s", e)
            break

        html = resp.text or ""
        soup = BeautifulSoup(html, "lxml")

        page_text = soup.get_text(separator="\n")
        pre_text = "\n\n".join(tag.get_text("\n") for tag in soup.find_all("pre"))
        script_text = "\n\n".join(tag.get_text() for tag in soup.find_all("script"))

        # extract atob base64 strings
        decoded_chunks = []
        for m in re.finditer(r"atob\(`([^`]+)`\)", script_text):
            try:
                chunk = base64.b64decode(m.group(1).replace("\n", "")).decode("utf-8", errors="ignore")
                decoded_chunks.append(chunk)
            except Exception:
                pass

        if decoded_chunks:
            pre_text = (pre_text + "\n\n" + "\n\n".join(decoded_chunks)).strip()

        # Show page snippet
        logger.info("Page snippet: %s",
                    (page_text[:300].replace("\n", " ") if page_text else "")
                    )

        # 1) Submit URL
        submit_url = detect_submit_url(page_text, pre_text, current_url)
        if not submit_url:
            logger.error("No submit URL found.")
            break
        logger.info("Submit URL: %s", submit_url)

        # 2) File URL
        file_url = detect_file_url(page_text, pre_text)
        file_bytes = None
        file_ext = None
        if file_url:
            try:
                r = requests.get(file_url, timeout=30)
                if r.ok:
                    file_bytes = r.content
                    file_ext = file_url.rsplit(".", 1)[-1].lower()
                    logger.info("Downloaded file: %s (%d bytes)", file_ext, len(file_bytes))
            except Exception as e:
                logger.error("File download error: %s", e)

        # 3) Scrape page
        scrape_url = detect_scrape_url(page_text, current_url)
        secret_code = None
        if scrape_url:
            secret_code = scrape_secondary_page(scrape_url)

        # 4) Audio detection (disabled STT)
        audio_url = detect_audio_url(page_text, pre_text)
        audio_transcript = None

        # 5) Decide action
        question_spec = parse_question_text(page_text, pre_text)

        if question_spec.get("action") == "return_text":
            llm_spec = ask_llm_for_action(page_text, pre_text)
            if llm_spec:
                question_spec.update(llm_spec)

        # 6) Compute answer
        answer = None

        try:
            if secret_code:
                answer = secret_code

            elif audio_transcript:
                answer = audio_transcript.strip()

            elif file_bytes and file_ext:

                if file_ext == "csv":
                    answer = compute_answer_from_csv_bytes(file_bytes, question_spec)

                elif file_ext in ("xls", "xlsx"):
                    answer = compute_answer_from_excel_bytes(file_bytes, question_spec)

                elif file_ext == "pdf":
                    val = compute_answer_from_pdf_bytes(file_bytes, question_spec)
                    answer = val if val is not None else file_bytes_to_data_uri(file_bytes, "pdf")

                elif file_ext == "json":
                    parsed = safe_json_parse(file_bytes.decode("utf-8", errors="ignore"))
                    answer = parsed if parsed else file_bytes.decode("utf-8", errors="ignore")[:1000]

                else:
                    answer = file_bytes_to_data_uri(file_bytes, file_ext)

            else:
                act = question_spec.get("action")

                if act == "count":
                    answer = len([ln for ln in page_text.splitlines() if ln.strip()])

                elif act == "chart":
                    try:
                        dfs = pd.read_html(page_text)
                        answer = df_to_chart_data_uri(dfs[0]) if dfs else "no-table"
                    except Exception:
                        answer = "no-table"

                else:
                    answer = page_text.strip()[:800]

        except Exception as e:
            logger.exception("Error computing answer: %s", e)
            answer = page_text.strip()[:600] if page_text else ""

        # 7) Submit
        payload = {"email": email, "secret": secret, "url": current_url, "answer": answer}
        payload = enforce_payload_limit(payload)

        next_url = None
        try:
            resp = requests.post(submit_url, json=payload, timeout=25)
            logger.info("Submit HTTP %s", resp.status_code)
            logger.info("Submit text: %s", resp.text[:1000])

            if resp.ok:
                try:
                    next_url = resp.json().get("url")
                except Exception:
                    pass

        except Exception as e:
            logger.error("Submit error: %s", e)
            break

        if next_url:
            current_url = next_url
        else:
            logger.info("Quiz ended.")
            break

    logger.info("Solver finished (time left %d)", time_left())


# ----------------- helpers -----------------

def detect_submit_url(page_text: str, pre_text: str, current_url: str) -> Optional[str]:
    content = (pre_text or "") + "\n" + (page_text or "")

    # 1. Absolute URLs containing /submit
    m = re.search(r"https?://[^\s'\"<>]+/submit[^\s'\"<>]*", content, flags=re.I)
    if m:
        return m.group(0)

    # 2. Absolute URLs with patterns submit/post/answer
    m = re.search(r"https?://[^\s'\"<>]+/(submit|post|answer|api)[^\s'\"<>]*", content, flags=re.I)
    if m:
        return m.group(0)

    # 3. Relative /submit
    m = re.search(r"(['\"])(\/submit[^\s'\"<>]*)\1", content)
    if m:
        from urllib.parse import urljoin
        return urljoin(current_url, m.group(2))

    # 4. JSON-like hidden fields
    m = re.search(r'"submit_url"\s*:\s*"([^"]+)"', content, flags=re.I)
    if m:
        from urllib.parse import urljoin
        return urljoin(current_url, m.group(1))

    # 5. URL inside JS variables
    m = re.search(r"var\s+submitUrl\s*=\s*['\"]([^'\"]+)['\"]", content)
    if m:
        from urllib.parse import urljoin
        return urljoin(current_url, m.group(1))

    # 6. Fallback: ANY URL on same domain containing keyword "submit"
    domain = current_url.split("/")[2]
    m = re.findall(r"https?://" + re.escape(domain) + r"[^\s'\"<>]+", content)
    for url in m:
        if "submit" in url.lower():
            return url

    return None



def detect_file_url(page_text, pre_text):
    content = (pre_text or "") + "\n" + (page_text or "")
    m = re.search(r"https?://[^\s'\"<>]+\.(csv|pdf|xlsx|xls|json|wav|mp3|m4a|ogg)", content, flags=re.I)
    return m.group(0) if m else None


def detect_scrape_url(page_text, current_url):
    m = re.search(r"scrape\s+([\/][^\s'\"<>]+)", page_text, flags=re.I)
    if m:
        from urllib.parse import urljoin
        return urljoin(current_url, m.group(1))
    return None


def scrape_secondary_page(url):
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        text = soup.get_text("\n")

        m = re.search(r"secret\s*code\s*[:\-]?\s*([A-Za-z0-9_-]+)", text, flags=re.I)
        return m.group(1) if m else text.strip()[:300]

    except Exception as e:
        logger.error("Scrape failed: %s", e)
        return None


def detect_audio_url(page_text, pre_text):
    content = (pre_text or "") + "\n" + (page_text or "")
    m = re.search(r"https?://[^\s'\"<>]+\.(mp3|wav|m4a|ogg)", content, flags=re.I)
    return m.group(0) if m else None
