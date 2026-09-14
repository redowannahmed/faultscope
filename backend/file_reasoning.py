"""
file_reasoning.py — Stage 2: File-level reasoning (§5).

Ported verbatim from rgfl/fl/file_reasoning.py.

For every candidate file, calls the LLM once (in parallel) to explain what the
file does in the context of the bug report.  Results form the input to Stage 3.

The prompt text is copied verbatim from the real RGFL research codebase.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import MAX_REASONING_FILE_CHARS, MAX_REASONING_WORKERS
from llm import call_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template — verbatim from file_reasoning.py (§5). Do NOT alter.
# ---------------------------------------------------------------------------
_FILE_REASONING_PROMPT_TEMPLATE = """\
A user is trying to fix a bug described in the following report:

{bug_report}

Below is a code file from a repository:

{file_content}

Explain the purpose and functionality of this code in the context of the bug report. Focus on what this file does and whether it may be related to the bug.
"""


def _reason_single_file(
    candidate_path: str,
    problem_statement: str,
    repo_root: str,
    model: str,
    backend: str,
    max_chars: int,
) -> tuple[str, str]:
    """
    Produce reasoning for a single candidate file.

    Reads the file fresh from disk (not from the structure dict — matches original).
    Truncates content if it exceeds max_chars, appending a marker.

    Returns:
        (candidate_path, reasoning_text_or_error_string)
    """
    abs_path = os.path.join(repo_root, candidate_path)
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            file_content = fh.read()
    except Exception as exc:
        # Store the error as the reasoning value — Stage 3 expects a dict
        # entry for every candidate, so we can't just skip failures.
        error_msg = f"Error during reasoning: {exc}"
        logger.warning("Could not read file %s: %s", abs_path, exc)
        return candidate_path, error_msg

    # Truncate excessively long files to stay within LLM context budgets.
    if len(file_content) > max_chars:
        file_content = file_content[:max_chars] + "\n... [truncated] ..."

    prompt = _FILE_REASONING_PROMPT_TEMPLATE.format(
        bug_report=problem_statement,
        file_content=file_content,
    )

    try:
        reasoning = call_llm(prompt, model=model, backend=backend, temperature=0.0)
    except Exception as exc:
        # Store the error as the reasoning value (same as file-read failures).
        error_msg = f"Error during reasoning: {exc}"
        logger.warning(
            "LLM call failed for file %s: %s", candidate_path, exc
        )
        return candidate_path, error_msg

    return candidate_path, reasoning


def generate_file_reasoning(
    candidates: list[str],
    problem_statement: str,
    repo_root: str,
    model: str,
    backend: str,
    max_workers: int = MAX_REASONING_WORKERS,
    max_chars: int = MAX_REASONING_FILE_CHARS,
) -> dict[str, str]:
    """
    Run Stage 2: generate one reasoning paragraph per candidate file, in parallel.

    Uses ThreadPoolExecutor(max_workers) matching the original's concurrency level.
    If an individual file's LLM call fails, stores the error string as the value
    (matches original) — Stage 3 needs an entry for every candidate.

    Args:
        candidates:        Ordered list of validated file paths (from Stage 1).
        problem_statement: Bug report text.
        repo_root:         Absolute path to the repo/ folder.
        model:             LLM model identifier.
        backend:           LLM backend name.
        max_workers:       Thread-pool size (default MAX_REASONING_WORKERS = 5).
        max_chars:         Character budget per file before truncation.

    Returns:
        Dict mapping each candidate file path to its reasoning text (or error string).
    """
    logger.info(
        "Stage 2: generating file reasoning for %d candidates (workers=%d)",
        len(candidates),
        max_workers,
    )
    results: dict[str, str] = {}

    # Submit all files to the thread pool; as_completed yields futures as they finish.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_path = {
            executor.submit(
                _reason_single_file,
                path,
                problem_statement,
                repo_root,
                model,
                backend,
                max_chars,
            ): path
            for path in candidates
        }
        for future in as_completed(future_to_path):
            path, reasoning = future.result()
            results[path] = reasoning
            logger.debug("Stage 2: finished reasoning for %s", path)

    # Preserve the original candidate order in the returned dict.
    # ThreadPoolExecutor returns results in completion order, not submission order.
    ordered: dict[str, str] = {path: results[path] for path in candidates if path in results}
    return ordered
