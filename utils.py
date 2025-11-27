# utils.py
import re
import io
import base64
import json
import logging

# IMPORTANT: Use Agg backend so matplotlib works on Render (no display)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import pandas as pd
from typing import Any, Optional

logger = logging.getLogger("utils")


def text_lower(s):
    return s.lower() if s else s


def parse_question_text(page_text: str, pre_text: Optional[str] = None) -> dict:
    text = (pre_text or "") + "\n" + (page_text or "")
    txt = text.lower()

    # detect cutoff patterns (e.g., "Cutoff: 38636")
    m_cut = re.search(r"cutoff[:\s]+([0-9]+)", txt)
    cutoff = int(m_cut.group(1)) if m_cut else None

    # sum of column
    if "sum of the" in txt and "column" in txt:
        m = re.search(r"sum of the\s+['\"]?([a-z0-9 _-]+)['\"]?\s+column", txt)
        col = m.group(1) if m else None
        return {"action": "sum", "column": col, "cutoff": cutoff}

    # count rows
    if "count" in txt and "rows" in txt:
        return {"action": "count", "cutoff": cutoff}

    # max/min
    if ("max" in txt or "maximum" in txt) and "column" in txt:
        m = re.search(r"(?:max(?:imum)? of the|maximum of the)\s+['\"]?([a-z0-9 _-]+)['\"]?\s+column", txt)
        col = m.group(1) if m else None
        return {"action": "max", "column": col, "cutoff": cutoff}

    if "mean" in txt or "average" in txt:
        m = re.search(r"(?:mean|average) of the\s+['\"]?([a-z0-9 _-]+)['\"]?\s+column", txt)
        col = m.group(1) if m else None
        return {"action": "mean", "column": col, "cutoff": cutoff}

    # pdf page mention
    if "page" in txt and "pdf" in txt:
        m = re.search(r"page\s+([0-9]+)", txt)
        page = int(m.group(1)) if m else None
        return {"action": "pdf_read", "page": page}

    if "chart" in txt or "plot" in txt:
        return {"action": "chart", "chart": True}

    if "download" in txt or "file" in txt or "csv" in txt:
        return {"action": "download_return_file", "cutoff": cutoff}

    return {"action": "return_text"}


def compute_answer_from_csv_bytes(bytes_data: bytes, action: dict) -> Any:
    try:
        df = pd.read_csv(io.BytesIO(bytes_data))
    except Exception:
        df = pd.read_csv(io.BytesIO(bytes_data), sep=None, engine="python")

    return compute_answer_from_df(df, action)


def compute_answer_from_excel_bytes(bytes_data: bytes, action: dict) -> Any:
    df = pd.read_excel(io.BytesIO(bytes_data))
    return compute_answer_from_df(df, action)


def compute_answer_from_df(df: pd.DataFrame, action: dict) -> Any:
    act = action.get("action")
    col = action.get("column")
    cutoff = action.get("cutoff")

    # normalize column names
    cols = [c.lower() for c in df.columns]

    if col:
        matches = [c for c in df.columns if c.lower() == col.lower()]
        if matches:
            colname = matches[0]
        else:
            candidate_cols = ["value", "amount", "price", "score", "count"]
            colname = None
            for cand in candidate_cols:
                if cand in cols:
                    colname = df.columns[cols.index(cand)]
                    break
    else:
        num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        colname = num_cols[0] if num_cols else None

    # count
    if act == "count":
        if cutoff is not None and colname:
            return int((pd.to_numeric(df[colname], errors="coerce") > cutoff).sum())
        return int(len(df))

    # numeric ops
    if colname:
        series = pd.to_numeric(df[colname], errors="coerce").dropna()
        if cutoff is not None:
            series = series[series > cutoff]

        if act == "sum":
            return float(series.sum())
        if act in ("mean", "average"):
            return float(series.mean())
        if act == "max":
            return float(series.max())
        if act == "min":
            return float(series.min())

    # fallback: try common numeric columns
    for candidate in ["value", "amount", "price", "score"]:
        if candidate in cols:
            s = pd.to_numeric(df[candidate], errors="coerce").dropna()
            if cutoff is not None:
                s = s[s > cutoff]
            if act == "sum":
                return float(s.sum())

    return int(len(df))


def compute_answer_from_pdf_bytes(bytes_data: bytes, action: dict) -> Any:
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(bytes_data)) as pdf:
            page_num = action.get("page")

            if page_num:
                idx = max(0, page_num - 1)
                if idx < len(pdf.pages):
                    page = pdf.pages[idx]
                    text = page.extract_text() or ""
                    tables = page.extract_tables()

                    if tables:
                        rows = tables[0]
                        if len(rows) >= 2:
                            df = pd.DataFrame(rows[1:], columns=rows[0])
                            return compute_answer_from_df(df, action)

                    if action.get("action") == "sum":
                        nums = [float(x) for x in re.findall(r"[-+]?\d*\.\d+|\d+", text)]
                        return float(sum(nums))

                    return text.strip()

            # process entire PDF
            full_text = "\n".join((p.extract_text() or "") for p in pdf.pages)
            if action.get("action") == "sum":
                nums = [float(x) for x in re.findall(r"[-+]?\d*\.\d+|\d+", full_text)]
                return float(sum(nums))

            return full_text.strip()

    except Exception as e:
        logger.exception("PDF parse error: %s", e)
        return None


def file_bytes_to_data_uri(bytes_data: bytes, ext: str) -> str:
    mime = "application/octet-stream"
    if ext in ("png", "jpg", "jpeg"):
        mime = f"image/{ext}"
    elif ext == "pdf":
        mime = "application/pdf"
    elif ext == "csv":
        mime = "text/csv"

    b64 = base64.b64encode(bytes_data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def df_to_chart_data_uri(df, x=None, y=None, kind="bar") -> str:
    buf = io.BytesIO()
    plt.figure(figsize=(6, 4))

    if x and y and x in df.columns and y in df.columns:
        df.plot(kind=kind, x=x, y=y)
    else:
        df.plot(kind=kind)

    plt.tight_layout()
    plt.savefig(buf, format="png")
    plt.close()

    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def safe_json_parse(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None


def enforce_payload_limit(payload: dict) -> dict:
    """Ensures final payload stays under 1MB."""
    try:
        data = json.dumps(payload).encode("utf-8")
        if len(data) > 900_000:
            if isinstance(payload.get("answer"), str):
                payload["answer"] = payload["answer"][:200000]
    except Exception:
        pass
    return payload
