"""
store.py — In-memory (+ optional on-disk JSON) project/session store (§11).

Keyed by project_id (uuid4 string).  No database is used — this is sufficient
for a demo / research reimplementation context.

If PERSIST_SESSIONS is True (configurable), each session is serialized to
  sessions/{project_id}.json  after every stage update.  This allows the server
to survive a restart while retaining completed pipeline state.
"""
#demo\
#demo2
from __future__ import annotations

import json
import logging
import os
import uuid
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Set PERSIST_SESSIONS = True to write each session to disk after every update.
PERSIST_SESSIONS: bool = True
_SESSIONS_DIR: str = "sessions"  # relative to the cwd when uvicorn is started


# ---------------------------------------------------------------------------
# In-memory store — a simple dict mapping project_id -> session dict.
# No database needed for a demo/research tool.
# ---------------------------------------------------------------------------
_store: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Helpers — disk persistence for surviving server restarts
# ---------------------------------------------------------------------------

def _sessions_path(project_id: str) -> str:
    """Return the file path for a session's JSON file on disk."""
    return os.path.join(_SESSIONS_DIR, f"{project_id}.json")


def _persist(project_id: str) -> None:
    """Write a session to disk (best-effort; never raises).

    This ensures that if the server restarts, we can recover completed
    pipeline stages from disk rather than forcing the user to re-run them.
    """
    if not PERSIST_SESSIONS:
        return
    try:
        os.makedirs(_SESSIONS_DIR, exist_ok=True)
        session = _store.get(project_id)
        if session is None:
            return
        path = _sessions_path(project_id)
        # structure dict contains lists of lines — perfectly JSON-serialisable.
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(session, fh, indent=2, default=str)
    except Exception as exc:
        logger.warning("Could not persist session %s: %s", project_id, exc)


def _load_from_disk(project_id: str) -> dict[str, Any] | None:
    """Try to load a session from disk (used at startup or on cache miss).

    This allows the server to recover sessions after a restart by reading
    the JSON files we wrote during _persist().
    """
    path = _sessions_path(project_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("Could not load session %s from disk: %s", project_id, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_session(
    repo_root: str,
    problem_statement: str,
    model: str,
    backend: str,
    ground_truth_file: str | None = None,
    ground_truth_elements: list[str] | None = None,
    source_type: str | None = None,
    repo_url: str | None = None,
    owner: str | None = None,
    repo_name: str | None = None,
    resolved_ref: str | None = None,
    resolved_commit_sha: str | None = None,
    download_dir: str | None = None,
) -> str:
    """
    Create a new project session and return its project_id.

    The full session shape matches §11 exactly.
    """
    project_id = str(uuid.uuid4())
    session: dict[str, Any] = {
        "project_id": project_id,
        "repo_root": repo_root,
        "problem_statement": problem_statement,
        "ground_truth_file": ground_truth_file,
        "ground_truth_elements": ground_truth_elements,
        "model": model,
        "backend": backend,
        # Source metadata.  Local projects deliberately retain None values for
        # GitHub-specific fields so their existing behavior and session shape
        # remain compatible.
        "source_type": source_type,
        "repo_url": repo_url,
        "owner": owner,
        "repo_name": repo_name,
        "resolved_ref": resolved_ref,
        "resolved_commit_sha": resolved_commit_sha,
        "download_dir": download_dir,
        # Pipeline state — all None until the corresponding stage runs.
        "structure": None,
        "files_flat": None,
        "candidates": None,
        "candidates_raw_output": None,
        "file_reasoning": None,
        "file_ranking": None,
        "file_ranking_eval": None,
        "element_reasoning": None,
        "element_ranking": None,
        "element_ranking_eval": None,
    }
    _store[project_id] = session
    _persist(project_id)
    logger.info("Created session %s", project_id)
    return project_id


def get_session(project_id: str) -> dict[str, Any] | None:
    """
    Return the session dict for project_id, or None if not found.
    Falls back to disk if missing from in-memory cache (e.g. after restart).
    """
    if project_id in _store:
        return _store[project_id]
    # Cache miss — try disk.
    session = _load_from_disk(project_id)
    if session is not None:
        _store[project_id] = session
    return session


def update_session(project_id: str, **fields: Any) -> None:
    """
    Update one or more fields in the session dict and persist.

    Raises KeyError if project_id is not found.
    """
    session = get_session(project_id)
    if session is None:
        raise KeyError(f"No session found for project_id={project_id!r}")
    session.update(fields)
    _store[project_id] = session
    _persist(project_id)


def get_session_copy(project_id: str) -> dict[str, Any] | None:
    """Return a deep copy of the session (safe for returning to the API caller)."""
    session = get_session(project_id)
    if session is None:
        return None
    return deepcopy(session)


def list_sessions() -> list[str]:
    """Return all known project_ids (in-memory only)."""
    return list(_store.keys())


def delete_session(project_id: str) -> None:
    """Remove a session from memory and its optional persisted JSON file."""
    _store.pop(project_id, None)
    if not PERSIST_SESSIONS:
        return
    try:
        path = _sessions_path(project_id)
        if os.path.exists(path):
            os.remove(path)
    except Exception as exc:
        logger.warning("Could not delete persisted session %s: %s", project_id, exc)
