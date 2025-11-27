# solver.py  (synchronous, requests + BeautifulSoup; no external LLM)
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
)
from llm_agent import ask_llm_for_action

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("solver")


# ----------------- entrypoint -----------------

def solve_quiz_with_deadline(url: str, email: str, secret: str,
                             start_time: float, max_seconds: int) -> None:
    """
    Top-level entry, called from app.py in a background thread.
    """
    deadline = start_time + max_seconds
    remaining = deadline - time.time()
    if remaining <= 3:
        logger.warning("Not enough time left to start solver.")
        return
    try:
        _solve_quiz_chain(url, email, secret, deadline)
    except Exception as e:
        logger.exception("Solver error: %s", e)


# ----------------- main loop -----------------

def _solve_quiz_chain(initial_url: str, email: str, secret: str, deadline: float) -> None:
    def time_left() -> int:
        return max(0, int(deadline - time.time()))

    current_url = initial_url
    visited = set()
    logger.info("Starting solver chain at %s", initial_url)

    while current_url and time_left() > 6:
        if current_url in visited:
            logger.warning("Loop detected; stopping.")
            break
        visited.add(current_url)

        logger.info("Loading %s (time left %d)", current_url, time_left())
        try:
            resp = requests.get(current_url, timeout=45)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Page load failed: %s", e)
            break

        html = resp.text or ""
        soup = BeautifulSoup(html, "lxml")

        # Visible text
        visible_text = soup.get_text(separator="\n")

        # <pre> blocks (often contain JSON instructions)
        pre_blocks = [tag.get_text("\n") for tag in soup.find_all("pre")]
        pre_text = "\n\n".join(pre_blocks)

        # <script> blocks (for e.g. atob(...) encoded text)
        script_texts = [tag.get_text() for tag in soup.find_all("script")]
        script_text = "\n\n".join(script_texts)

        # Try to decode atob(`...`) style base64 strings if present
        decoded_chunks = []
        for m in re.finditer(r"atob\(`([^`]+)`\)", script_text):
            b64 = m.group(1).replace("\n", "")
            try:
                decoded = base64.b64decode(b64).decode("utf-8", errors="ignore")
                decoded_chunks.append(decoded)
            except Exception:
                pass

        decoded_text = "\n\n".join(decoded_chunks)
        if decoded_text:
            pre_text = (pre_text + "\n\n" + decoded_text).strip()

        page_text = visible_text

        logger.info("Page snippet: %s",
                    (page_text[:300].replace("\n", " ") if page_text else "") )

        # 1) detect submit URL
        submit_url = detect_submit_url(page_text, pre_text, current_url)
        if not submit_url:
            logger.error("Submit URL not found. Stopping.")
            break
        logger.info("Submit URL: %s", submit_url)

        # 2) detect data file
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

        # 3) detect scrape instruction (secondary page)
        scrape_url = detect_scrape_url(page_text, current_url)
        secret_code = None
        if scrape_url:
            logger.info("Found scrape instruction -> visiting %s", scrape_url)
            secret_code = scrape_secondary_page(scrape_url)

        # 4) detect audio URL (we do not support transcription here; just detect)
        audio_url = detect_audio_url(page_text, pre_text)
        audio_transcript = None
        if audio_url:
            logger.info("Detected audio URL: %s (no transcription available in this local build)", audio_url)
            audio_transcript = None  # transcription not available without external STT

        # 5) decide action (heuristics + optional local agent)
        question_spec = parse_question_text(page_text, pre_text)
        if question_spec.get("action") == "return_text" or question_spec.get("action") is None:
            llm_spec = None
            try:
                llm_spec = ask_llm_for_action(page_text, pre_text)
            except Exception:
                llm_spec = None
            if llm_spec:
                question_spec.update(llm_spec)

        # 6) compute answer
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
                # purely text-based question
                act = question_spec.get("action")
                if act == "count":
                    answer = len([ln for ln in page_text.splitlines() if ln.strip()])
                elif act == "chart":
                    try:
                        dfs = pd.read_html(page_text)
                        if dfs:
                            answer = df_to_chart_data_uri(dfs[0])
                        else:
                            answer = "no-table"
                    except Exception:
                        answer = "no-table"
                else:
                    answer = page_text.strip()[:800]
        except Exception as e:
            logger.exception("Error computing answer: %s", e)
            answer = page_text.strip()[:600] if page_text else ""

        # 7) submit payload
        payload = {"email": email, "secret": secret, "url": current_url, "answer": answer}
        payload = enforce_payload_limit(payload)

        next_url = None
        try:
            resp = requests.post(submit_url, json=payload, timeout=25)
            logger.info("Submit HTTP %s", resp.status_code)
            logger.info("Submit text: %s", resp.text[:1000])
            if resp.ok:
                try:
                    jr = resp.json()
                    next_url = jr.get("url")
                except Exception:
                    pass
            else:
                logger.warning("Submit returned non-200.")
        except Exception as e:
            logger.error("Submit error: %s", e)
            break

        if next_url:
            logger.info("Next URL -> %s", next_url)
            current_url = next_url
        else:
            logger.info("No next; finishing.")
            break

    logger.info("Solver completed; time left: %d", time_left())


# ----------------- helpers (adapted to requests/BeautifulSoup) -----------------

def detect_submit_url(page_text: str, pre_text: str, current_url: str) -> Optional[str]:
    content = (pre_text or "") + "\n" + (page_text or "")
    m = re.search(r"https?://[^\s'\"<>]+/submit[^\s'\"<>]*", content, flags=re.I)
    if m:
        return m.group(0)
    m = re.search(r"https?://[^\s'\"<>]+/(submit|post|answer)[^\s'\"<>]*", content, flags=re.I)
    if m:
        return m.group(0)
    # relative /submit
    m = re.search(r"(^|[^A-Za-z])(\/submit[^\s'\"<>]*)", content, flags=re.I)
    if m:
        from urllib.parse import urljoin
        return urljoin(current_url, m.group(2))
    m = re.search(r"post\s+back\s+to\s+(\/submit[^\s'\"<>]*)", content, flags=re.I)
    if m:
        from urllib.parse import urljoin
        return urljoin(current_url, m.group(1))
    return None


def detect_file_url(page_text: str, pre_text: str) -> Optional[str]:
    content = (pre_text or "") + "\n" + (page_text or "")
    m = re.search(r"https?://[^\s'\"<>]+\.(csv|pdf|xlsx|xls|json|wav|mp3|m4a|ogg)", content, flags=re.I)
    return m.group(0) if m else None


def detect_scrape_url(page_text: str, current_url: str) -> Optional[str]:
    m = re.search(r"scrape\s+([\/][^\s'\"<>]+)", page_text, flags=re.I)
    if m:
        from urllib.parse import urljoin
        return urljoin(current_url, m.group(1))
    return None


def scrape_secondary_page(scrape_url: str) -> Optional[str]:
    try:
        resp = requests.get(scrape_url, timeout=30)
        resp.raise_for_status()
        html = resp.text or ""
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(separator="\n")
    except Exception as e:
        logger.error("Secondary scrape failed: %s", e)
        return None

    m = re.search(r"secret\s*code\s*[:\-]?\s*([A-Za-z0-9_-]+)", text, flags=re.I)
    if m:
        return m.group(1)
    return text.strip()[:300]


def detect_audio_url(page_text: str, pre_text: str) -> Optional[str]:
    content = (pre_text or "") + "\n" + (page_text or "")
    m = re.search(r"https?://[^\s'\"<>]+\.(mp3|wav|m4a|ogg)", content, flags=re.I)
    return m.group(0) if m else None


def transcribe_audio(audio_url: str) -> Optional[str]:
    """
    Transcription is disabled in this local build (no external STT).
    If you later add an STT provider, implement it here.
    """
    logger.info("transcribe_audio called but no STT provider configured.")
    return None


def enforce_payload_limit(payload: dict) -> dict:
    try:
        b = json.dumps(payload).encode("utf-8")
        if len(b) > 900_000 and isinstance(payload.get("answer"), str):
            payload["answer"] = payload["answer"][:200000]
    except Exception:
        pass
    return payload
