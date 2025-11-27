# llm_agent.py
import logging
from typing import Optional, Dict, Any
from utils import parse_question_text

logger = logging.getLogger("llm_agent")

def ask_llm_for_action(page_text: str, pre_text: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Local heuristic 'agent' that returns a structured action dict.
    This replaces external LLM calls and uses parse_question_text from utils.py.

    The returned dict should be JSON-serializable and contain fields like:
      - action (sum, count, max, mean, pdf_read, chart, download_return_file, return_text)
      - column (optional)
      - page (optional)
      - cutoff (optional)
    """
    try:
        spec = parse_question_text(page_text, pre_text)
        # parse_question_text already returns a dict in the expected shape.
        logger.debug("Local agent produced spec: %s", spec)
        return spec
    except Exception as e:
        logger.exception("Local agent failed: %s", e)
        return None
