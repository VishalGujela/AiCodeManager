"""Optional LLM-backed code fix suggestions (OpenAI-compatible HTTP API)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Union

import httpx

from app.core.config import Settings, get_settings
from app.models.request_models import AISuggestionPayload, FeedbackSummary
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Basic guardrails: block obvious injection / RCE patterns in generated Python.
_UNSAFE_PATTERNS = re.compile(
    r"(?is)"
    r"\bos\.system\s*\(|"
    r"\bsubprocess\.|"
    r"\beval\s*\(|"
    r"\bexec\s*\(|"
    r"\bcompile\s*\(|"
    r"__import__\s*\(|"
    r"`\s*;|\|\s*sh\b"
)


def sanitize_fixed_code(raw: str) -> str:
    """Strip markdown fences and trim; does not guarantee safety."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:python|py)?\s*", "", text, count=1, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text, count=1)
    return text.strip()


def _is_likely_unsafe(code: str) -> bool:
    return bool(_UNSAFE_PATTERNS.search(code))


def _feedback_to_text(feedback: Optional[Union[FeedbackSummary, Dict[str, Any]]]) -> str:
    if feedback is None:
        return "(none)"
    if isinstance(feedback, FeedbackSummary):
        return feedback.model_dump_json()
    try:
        return json.dumps(feedback, default=str, indent=2)[:12000]
    except TypeError:
        return str(feedback)[:12000]


def _build_prompt(code: str, tests: str, issues_text: str) -> str:
    return f"""You are a senior software engineer.
The following code failed tests and validation.

Code:
{code}

Tests:
{tests}

Issues (lint, types, test feedback as JSON):
{issues_text}

Fix the code so that all tests pass and issues are addressed.
Respond with a single JSON object ONLY, with keys:
  "fixed_code": string (complete corrected Python source, no markdown fences),
  "explanation": string (brief rationale),
  "confidence": number between 0 and 1 (your confidence the fix is correct).
"""


async def generate_fix(
    *,
    code: str,
    tests: str,
    feedback: Optional[Union[FeedbackSummary, Dict[str, Any]]] = None,
    settings: Optional[Settings] = None,
) -> AISuggestionPayload:
    s = settings or get_settings()
    if not s.ai_enabled:
        return AISuggestionPayload(
            fixed_code="",
            explanation="",
            confidence=0.0,
            error="AI suggestions are disabled (set AI_ENABLED=true and OPENAI_API_KEY)",
        )
    if not (s.openai_api_key or "").strip():
        return AISuggestionPayload(
            fixed_code="",
            explanation="",
            confidence=0.0,
            error="OPENAI_API_KEY is not configured",
        )

    issues_text = _feedback_to_text(feedback)
    prompt = _build_prompt(code, tests, issues_text)
    url = f"{s.openai_api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {s.openai_api_key}",
        "Content-Type": "application/json",
    }
    body: Dict[str, Any] = {
        "model": s.openai_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    if s.openai_json_mode:
        body["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=s.ai_timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.warning("ai_http_error: %s", e)
        return AISuggestionPayload(
            fixed_code="",
            explanation="",
            confidence=0.0,
            error=f"AI request failed: {e!s}"[:2000],
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("ai_unexpected_error")
        return AISuggestionPayload(
            fixed_code="",
            explanation="",
            confidence=0.0,
            error=str(e)[:2000],
        )

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return AISuggestionPayload(
            fixed_code="",
            explanation="",
            confidence=0.0,
            error=f"Unexpected AI response shape: {e!s}",
        )

    fixed_code = ""
    explanation = ""
    confidence = 0.0
    try:
        parsed = json.loads(content)
        fixed_code = str(parsed.get("fixed_code", "") or "")
        explanation = str(parsed.get("explanation", "") or "")
        conf = parsed.get("confidence", 0.5)
        confidence = float(conf) if conf is not None else 0.0
        confidence = max(0.0, min(1.0, confidence))
    except (json.JSONDecodeError, TypeError, ValueError):
        fixed_code = sanitize_fixed_code(content)
        explanation = "Model did not return JSON; used raw content as code."
        confidence = 0.3

    fixed_code = sanitize_fixed_code(fixed_code)
    if not fixed_code:
        return AISuggestionPayload(
            fixed_code="",
            explanation=explanation or "Empty fixed_code from model",
            confidence=0.0,
            error="Model returned empty code",
        )

    if _is_likely_unsafe(fixed_code):
        return AISuggestionPayload(
            fixed_code="",
            explanation=explanation,
            confidence=0.0,
            error="Generated code rejected by safety filter (suspicious patterns)",
        )

    return AISuggestionPayload(
        fixed_code=fixed_code,
        explanation=explanation,
        confidence=confidence,
        error=None,
    )
