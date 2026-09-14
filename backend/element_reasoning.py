"""
element_reasoning.py — Stage 5: Element-level reasoning (§8).

Ported verbatim from rgfl/fl/element_reasoning.py, process_file.

For each code element extracted in Stage 4 (within a single file), calls the
LLM once to explain what that element does in the context of the bug report.
Calls are parallelised within a file using ThreadPoolExecutor(max_workers=4).

The prompt text is copied verbatim from the real RGFL research codebase.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import MAX_ELEMENT_WORKERS
from elements import element_key, extract_code_elements_from_file, get_source_code
from llm import call_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template — verbatim from element_reasoning.py (§8). Do NOT alter.
# ---------------------------------------------------------------------------
_ELEMENT_REASONING_PROMPT_TEMPLATE = """\
A user is trying to fix a bug described in the following report:

{bug_report}

Below is a code element (a function, a class, or a global variable) in a file in a repository:

{element_code}

Explain the purpose and functionality of this code element in the context of the bug report. Focus on what this element does and whether it may be related to the bug.
"""


def _reason_single_element(
    element: tuple[str, str, int, int],
    file_source: str,
    problem_statement: str,
    model: str,
    backend: str,
) -> tuple[str, str]:
    """
    Produce reasoning for a single code element.

    Returns:
        (element_key_str, reasoning_text_or_error_string)
    """
    kind, name, _, _ = element
    key = element_key(kind, name)
    # Extract the element's source code from the full file text.
    element_code = get_source_code(element, file_source)

    prompt = _ELEMENT_REASONING_PROMPT_TEMPLATE.format(
        bug_report=problem_statement,
        element_code=element_code,
    )

    try:
        reasoning = call_llm(prompt, model=model, backend=backend, temperature=0.0)
    except Exception as exc:
        # Store the error as the reasoning value — the ranking stage needs
        # an entry for every element, so we can't just skip failures.
        error_msg = f"Error during reasoning: {exc}"
        logger.warning("LLM call failed for element %s: %s", key, exc)
        return key, error_msg

    return key, reasoning


def generate_element_reasoning(
    file_path: str,
    repo_root: str,
    problem_statement: str,
    model: str,
    backend: str,
    max_workers: int = MAX_ELEMENT_WORKERS,
) -> dict[str, str] | None:
    """
    Run Stage 5 for a single file: extract elements (Stage 4) then reason about
    each one in parallel (Stage 5).

    Naming convention: each element's key is f"{kind}: {name}"
    e.g. "function: write", "class: HTML", "global: DEFAULT_TIMEOUT"

    Args:
        file_path:         Repo-relative path to the file.
        repo_root:         Absolute path to the repo/ folder.
        problem_statement: Bug report text.
        model:             LLM model identifier.
        backend:           LLM backend name.
        max_workers:       Thread-pool size per file (default MAX_ELEMENT_WORKERS = 4).

    Returns:
        Dict {element_key: reasoning_text} for this file,
        or None if ast.parse fails (caller logs warning and skips the file).
    """
    abs_path = os.path.join(repo_root, file_path)

    # Read the file content from disk.
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            file_content = fh.read()
    except Exception as exc:
        logger.warning("Could not read file for element extraction %s: %s", abs_path, exc)
        return None

    # Parse the file to extract all code elements (functions, classes, globals).
    try:
        elements, file_source = extract_code_elements_from_file(file_content)
    except Exception as exc:
        # If ast.parse fails, skip this file (per §7: "log a warning, do not crash")
        logger.warning(
            "AST parse failed during element extraction for %s: %s", file_path, exc
        )
        return None

    if not elements:
        logger.info("No elements found in %s", file_path)
        return {}

    logger.info(
        "Stage 5: reasoning about %d elements in %s (workers=%d)",
        len(elements),
        file_path,
        max_workers,
    )

    results: dict[str, str] = {}

    # Submit all elements to the thread pool for parallel LLM calls.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {
            executor.submit(
                _reason_single_element,
                elem,
                file_source,
                problem_statement,
                model,
                backend,
            ): element_key(elem[0], elem[1])
            for elem in elements
        }
        for future in as_completed(future_to_key):
            key, reasoning = future.result()
            results[key] = reasoning

    return results


def generate_all_element_reasoning(
    ranked_files: list[str],
    repo_root: str,
    problem_statement: str,
    model: str,
    backend: str,
    top_k: int,
    max_workers: int = MAX_ELEMENT_WORKERS,
) -> dict[str, dict[str, str]]:
    """
    Run Stages 4+5 on the top_k files from the file ranking.

    Returns a dict keyed by "file1_elements_reasoning", "file2_elements_reasoning", ...
    matching the naming convention in the original source and the spec (§8).

    Files where element extraction fails are skipped (None returned by
    generate_element_reasoning) — they produce no key in the output dict.
    """
    # Only process the top-k files from the ranking.
    top_files = ranked_files[:top_k]
    output: dict[str, dict[str, str]] = {}

    for idx, file_path in enumerate(top_files, start=1):
        # Key naming convention: "file1_elements_reasoning", "file2_elements_reasoning", ...
        key = f"file{idx}_elements_reasoning"
        logger.info(
            "Stages 4+5: processing file %d/%d: %s", idx, len(top_files), file_path
        )
        reasoning = generate_element_reasoning(
            file_path=file_path,
            repo_root=repo_root,
            problem_statement=problem_statement,
            model=model,
            backend=backend,
            max_workers=max_workers,
        )
        if reasoning is not None:
            output[key] = reasoning
        else:
            # Extraction failed — skip this file entirely.
            logger.warning("Skipping element reasoning for %s (extraction failed)", file_path)

    return output
