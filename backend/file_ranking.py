"""
file_ranking.py — Stage 3: File-level reasoning-guided ranking (§6).

Ported verbatim from rgfl/fl/file_ranking.py.

One single LLM call given ALL of Stage 2's reasoning at once.
The parser uses eval() first (matching the original research code exactly),
with a regex fallback.

The prompt text is copied verbatim from the real RGFL research codebase.
"""

from __future__ import annotations

import json
import logging
import re

from llm import call_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template — verbatim from file_ranking.py (§6). Do NOT alter.
# ---------------------------------------------------------------------------
_FILE_RANKING_PROMPT_TEMPLATE = """\
Below is a list of files from a repository and the reasonings behind the codes of these files:

{file_reasoning}

Can you rank the files based on the similarity of their reasoning to the bug report:

{bug_report}

Please just return the list of ranked files.
"""


# ---------------------------------------------------------------------------
# Response parser — verbatim from file_ranking.extract_file_list (§6)
# ---------------------------------------------------------------------------

def extract_file_list(llm_output: str) -> list[str]:
    """
    Parse the LLM's ranking response into an ordered list of file path strings.

    Strategy (verbatim from original):
    1. If the stripped output looks like a Python list literal, eval() it.
       NOTE: eval() on LLM output is an accepted, documented risk for this
       research reimplementation — it matches the original exactly.
       If eval() succeeds but does not return a list, discard and fall through.
    2. Otherwise, extract any token matching [\\w\\-/]+\\.py via regex.

    Args:
        llm_output: Raw response text from the ranking LLM call.

    Returns:
        Ordered list of file path strings (may include invented paths —
        caller must cross-reference against the real candidate set).
    """
    stripped = llm_output.strip()
    # First, try to eval as a Python list literal — the original code does this.
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            result = eval(stripped)  # noqa: S307 — intentional, matches original
            if isinstance(result, list):
                return result
            # eval succeeded but not a list — fall through to regex
        except Exception:
            pass
    # Fallback: extract all tokens matching Python file path patterns.
    return re.findall(r"[\w\-/]+\.py", llm_output)


# ---------------------------------------------------------------------------
# Hit@k evaluation helper (§6 optional evaluation)
# ---------------------------------------------------------------------------

def hit_at_k(
    ranked_list: list[str],
    ground_truth_file: str | None,
) -> dict | None:
    """
    Compute rank position and Hit@k for a known ground-truth file.

    Mirrors the paper's Hit@k / MRR metrics (§4.2), applied to one instance.

    Args:
        ranked_list:       The final ranked list of file paths.
        ground_truth_file: The known correct file path, or None.

    Returns:
        None if no ground truth was provided.
        Otherwise a dict:
          {
            "rank_position": int | None,   # 1-indexed; None if not in ranked list
            "hit_at_k": {"1": bool, "2": bool, "3": bool, "5": bool}
          }
    """
    if not ground_truth_file:
        return None

    # Find the 1-indexed position of the ground truth in the ranked list.
    rank_position: int | None = None
    if ground_truth_file in ranked_list:
        rank_position = ranked_list.index(ground_truth_file) + 1

    # Check if the ground truth appears in the top-k positions.
    return {
        "rank_position": rank_position,
        "hit_at_k": {
            "1": ground_truth_file in ranked_list[:1],
            "2": ground_truth_file in ranked_list[:2],
            "3": ground_truth_file in ranked_list[:3],
            "5": ground_truth_file in ranked_list[:5],
        },
    }


# ---------------------------------------------------------------------------
# Main stage function
# ---------------------------------------------------------------------------

def rank_files(
    file_reasoning_dict: dict[str, str],
    problem_statement: str,
    candidates: list[str],
    model: str,
    backend: str,
) -> list[str]:
    """
    Run Stage 3: one LLM call to rank all candidate files by reasoning relevance.

    The raw LLM output is parsed, then cross-referenced against the real
    candidate set — any invented path is dropped, and the original relative
    order is preserved for valid entries.

    Args:
        file_reasoning_dict: {file_path: reasoning_text} from Stage 2.
        problem_statement:   Bug report text.
        candidates:          The validated candidate list from Stage 1 (used for filtering).
        model:               LLM model identifier.
        backend:             LLM backend name.

    Returns:
        Ordered list of file paths (subset of candidates) in ranked order.

    Raises:
        RuntimeError on LLM call failure (caller handles).
    """
    # Serialize reasoning dict as JSON (verbatim from original) for the prompt.
    file_reasoning_json = json.dumps(file_reasoning_dict, indent=2)

    prompt = _FILE_RANKING_PROMPT_TEMPLATE.format(
        file_reasoning=file_reasoning_json,
        bug_report=problem_statement,
    )

    logger.info("Stage 3: calling LLM (%s/%s) for file ranking", backend, model)
    raw_output = call_llm(prompt, model=model, backend=backend, temperature=0.0)

    # Parse the LLM's proposed ranking — may contain invented paths.
    proposed = extract_file_list(raw_output)
    logger.debug("Stage 3: LLM proposed order: %s", proposed)

    # Cross-reference: keep only real candidates, preserve LLM's relative order.
    # The LLM may invent paths or omit some candidates.
    candidate_set = set(candidates)
    seen: set[str] = set()
    ranked: list[str] = []
    for path in proposed:
        if path in candidate_set and path not in seen:
            ranked.append(path)
            seen.add(path)

    # Append any candidates the LLM forgot to mention (preserve completeness).
    # The pipeline expects a ranking of ALL candidates, not just a subset.
    for path in candidates:
        if path not in seen:
            ranked.append(path)
            seen.add(path)

    logger.info("Stage 3: final ranked list has %d files", len(ranked))
    return ranked
