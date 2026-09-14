"""
candidates.py — Stage 1: Candidate file selection (§4).

Ported verbatim from rgfl/fl/FL.py, LLMFL.localize.
This is Agentless's own file-level LLM localization step — a single LLM call
that reads the bug report + repo file tree and proposes up to N candidate files.

The prompt text is copied verbatim from the real RGFL research codebase.
The only parameterisation is substituting top_n_candidates into one sentence.
"""

from __future__ import annotations

import logging

from config import DEFAULT_TOP_N_CANDIDATES
from llm import call_llm
from structure import correct_file_paths, show_project_structure

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template — verbatim from FL.py (§4). Do NOT alter.
# The only text substitution allowed: {top_n_candidates} in the "at most N files" line.
# ---------------------------------------------------------------------------
_CANDIDATE_PROMPT_TEMPLATE = """\
Please look through the following GitHub problem description and Repository structure and provide a list of files that one would need to edit to fix the problem.

### GitHub Problem Description ###
{problem_statement}

###

### Repository Structure ###
{structure}

###

Please only provide the full path and return at most {top_n_candidates} files.
The returned files should be separated by new lines ordered by most to least important and wrapped with ```
For example:
```
file1.py
file2.py
```
"""


def build_candidate_prompt(
    problem_statement: str,
    structure: dict,
    top_n_candidates: int = DEFAULT_TOP_N_CANDIDATES,
) -> str:
    """
    Assemble the exact candidate-selection prompt.

    Args:
        problem_statement: Raw bug report text.
        structure:         The nested structure dict (post-filter).
        top_n_candidates:  How many files to request; substituted into the prompt.

    Returns:
        The fully-formatted prompt string ready to send to the LLM.
    """
    # Convert the nested structure dict into indented tree text for the prompt.
    tree_text = show_project_structure(structure).strip()
    return _CANDIDATE_PROMPT_TEMPLATE.format(
        problem_statement=problem_statement,
        structure=tree_text,
        top_n_candidates=top_n_candidates,
    )


def parse_candidate_files(raw_llm_output: str, files: list) -> list[str]:
    """
    Extract valid file paths from the LLM's raw response.

    Ported verbatim from FL._parse_model_return_lines + correct_file_paths.
    Split on newlines; the triple-backtick fence lines the LLM typically emits
    simply won't match any real file path and are silently dropped by
    correct_file_paths — no special backtick-stripping needed (matches original).

    Args:
        raw_llm_output: The model's full response text.
        files:          The flat files list from get_full_file_paths_and_classes_and_functions.

    Returns:
        Ordered list of validated real file paths.
    """
    # Split the response into lines — each line should be one file path.
    lines = raw_llm_output.strip().split("\n") if raw_llm_output else []
    # correct_file_paths filters to only real file paths in the repo.
    return correct_file_paths(lines, files)


def select_candidates(
    problem_statement: str,
    structure: dict,
    files_flat: list,
    model: str,
    backend: str,
    top_n_candidates: int = DEFAULT_TOP_N_CANDIDATES,
) -> tuple[list[str], str]:
    """
    Run Stage 1: call the LLM once to select candidate files.

    Args:
        problem_statement: The bug report text.
        structure:         Nested structure dict (post-filter).
        files_flat:        Flat file list from get_full_file_paths_and_classes_and_functions.
        model:             LLM model identifier.
        backend:           LLM backend name ("anthropic" | "openai" | "gemini").
        top_n_candidates:  Max number of candidate files to request.

    Returns:
        (candidates, raw_llm_output)
        candidates:      Ordered list of validated real file paths (may be empty).
        raw_llm_output:  The model's full response, kept for debugging/display.

    Raises:
        RuntimeError if the LLM call itself fails (callers handle this).
    """
    prompt = build_candidate_prompt(problem_statement, structure, top_n_candidates)
    logger.info(
        "Stage 1: calling LLM (%s/%s) for candidate selection (top_n=%d)",
        backend,
        model,
        top_n_candidates,
    )
    # temperature=0 per spec (§4) — deterministic output for reproducibility.
    raw_output = call_llm(prompt, model=model, backend=backend, temperature=0.0)
    # Parse and validate the LLM's proposed paths against the real file list.
    candidates = parse_candidate_files(raw_output, files_flat)
    logger.info("Stage 1: LLM proposed %d valid candidates", len(candidates))
    return candidates, raw_output
