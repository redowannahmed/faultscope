"""Stage browser-uploaded .py files into a local source directory.

This is the static-ingestion counterpart to ``github_fetch.py``.  Both modules
exist for the same reason: the RGFL pipeline only ever consumes a local
directory path, so every ingestion mode's job is to produce one and then get
out of the way.  Nothing downstream of ``create_structure()`` knows or cares
which module produced the directory it is walking.

Unlike the GitHub path, the candidate set here is not nominated by an LLM —
the user has already chosen the files, so Stage 1 (§4) is bypassed and the
uploaded paths become the candidate list directly.
"""

from __future__ import annotations

import os
import shutil
import tempfile

from config import (
    MAX_FILE_BYTES,
    MAX_UPLOAD_FILES,
    MAX_UPLOAD_TOTAL_BYTES,
)


def sanitize_relative_path(raw_path: str) -> str:
    """Reduce an untrusted client-supplied path to a safe relative POSIX path.

    Upload paths come from a browser, so they are untrusted input: they may be
    absolute, Windows-style, or contain traversal components.  Anything that
    would escape the staging directory is stripped rather than rejected, so a
    file named ``../../etc/passwd.py`` simply lands as ``passwd.py``.
    """
    # Normalize to POSIX separators and strip whitespace.
    path = (raw_path or "").replace("\\", "/").strip()
    # Remove empty segments, current-dir (.) and parent-dir (..) components,
    # and Windows drive letters (e.g. "C:").
    segments = [
        segment
        for segment in path.split("/")
        if segment not in ("", ".", "..") and not segment.endswith(":")
    ]
    return "/".join(segments)


def stage_uploaded_files(files: list[tuple[str, bytes]]) -> tuple[str, list[str]]:
    """Write uploaded files to a temp directory and return it with their paths.

    Args:
        files: ``(client_path, content_bytes)`` pairs, in the caller's order.

    Returns:
        ``(staging_root, relative_paths)`` where ``relative_paths`` are
        POSIX-style paths relative to ``staging_root``, order preserved.

    Raises:
        ValueError: on an empty set, a non-``.py`` file, or a size/count limit.
            The staging directory is removed before the error propagates.
    """
    # ── Input validation ──────────────────────────────────────────────
    if not files:
        raise ValueError("No files were uploaded.")
    if len(files) > MAX_UPLOAD_FILES:
        raise ValueError(
            f"Too many files: {len(files)}. This tool accepts at most "
            f"{MAX_UPLOAD_FILES} uploaded files per project."
        )

    # Create a temp directory that will hold the staged source tree.
    staging_root = tempfile.mkdtemp(prefix="rgfl_mini_upload_")
    relative_paths: list[str] = []
    total_bytes = 0

    try:
        for client_path, content in files:
            # Sanitize the client-supplied path to prevent directory traversal.
            relative_path = sanitize_relative_path(client_path)
            if not relative_path:
                raise ValueError(f"Could not derive a usable filename from {client_path!r}.")
            # Only Python source files are accepted for static ingestion.
            if not relative_path.endswith(".py"):
                raise ValueError(
                    f"{relative_path!r} is not a .py file. Static ingestion accepts "
                    "Python source files only."
                )
            # Enforce per-file size limit to prevent abuse.
            if len(content) > MAX_FILE_BYTES:
                raise ValueError(
                    f"{relative_path!r} is {len(content) / 1e3:.0f} KB, over the "
                    f"{MAX_FILE_BYTES / 1e3:.0f} KB per-file limit."
                )

            # Track cumulative upload size against the total budget.
            total_bytes += len(content)
            if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
                raise ValueError(
                    f"Uploaded files exceed the {MAX_UPLOAD_TOTAL_BYTES / 1e6:.0f} MB total limit."
                )

            # Write the file to disk, creating subdirectories as needed.
            destination = os.path.join(staging_root, *relative_path.split("/"))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "wb") as handle:
                handle.write(content)

            # Deduplicate: only record each path once (first occurrence wins).
            if relative_path not in relative_paths:
                relative_paths.append(relative_path)
    except Exception:
        # Clean up the staging directory on any error — never leave orphaned files.
        shutil.rmtree(staging_root, ignore_errors=True)
        raise

    return staging_root, relative_paths
