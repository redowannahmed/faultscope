"""
structure.py — Repository structure building and utilities (§3).

Ported from:
  - get_repo_structure/get_repo_structure.py   (parse_python_file, create_structure)
  - preprocess_data.py                         (filter_none_python, filter_out_test_files,
                                                get_full_file_paths_and_classes_and_functions,
                                                show_project_structure, correct_file_paths)

KEY DEVIATION from the original (§3.2):
  The original create_structure() uses os.path.basename(directory_path) as a synthetic
  top-level key, making every path look like "<repo_folder_name>/sub/dir/file.py".
  This causes a path-mismatch bug in the downstream reasoning stage.
  We fix it by using a single consistent convention everywhere:
    every file path is POSIX-style, relative to repo/'s own root, with NO synthetic prefix.
  So repo/astropy/io/ascii/html.py => "astropy/io/ascii/html.py" everywhere.
"""

from __future__ import annotations

import ast
import logging
import os
from typing import Any

from config import MAX_FILE_BYTES, MAX_FILES, SKIP_DIRS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# §3.1 — Per-file AST parsing
# ---------------------------------------------------------------------------

def parse_python_file(
    file_path: str, file_content: str
) -> tuple[list[dict], list[dict], list[str]]:
    """
    Parse a single Python file and extract class, function, and line information.

    Returns:
        (class_info, function_names, lines)
        class_info:      list of {name, start_line, end_line, text: [lines], methods: [...]}
        function_names:  list of {name, start_line, end_line, text: [lines]}
                         — top-level FunctionDef nodes NOT already counted as class methods.
                         — AsyncFunctionDef nodes are intentionally excluded (matches original).
        lines:           file_content.splitlines()

    On any ast.parse failure: logs a warning and returns ([], [], file_content.splitlines()).
    This is a DEVIATION from the original (which returns an empty string on failure — a bug).
    """
    # Try to parse the file into an AST. This can fail on syntax errors,
    # encoding issues, or non-Python files masquerading as .py.
    try:
        parsed_data = ast.parse(file_content)
    except Exception as exc:
        logger.warning("AST parse failed for %s: %s", file_path, exc)
        # DEVIATION from original: return real line content instead of empty string.
        return [], [], file_content.splitlines()

    class_info: list[dict] = []
    function_names: list[dict] = []
    class_methods: set[str] = set()  # Track method names to avoid double-counting

    # Walk the AST to find all ClassDef and FunctionDef nodes.
    for node in ast.walk(parsed_data):
        if isinstance(node, ast.ClassDef):
            # For each class, extract its methods (FunctionDef nodes in its body).
            methods: list[dict] = []
            for n in node.body:
                if isinstance(n, ast.FunctionDef):
                    methods.append({
                        "name": n.name,
                        "start_line": n.lineno,
                        "end_line": n.end_lineno,
                        "text": file_content.splitlines()[n.lineno - 1 : n.end_lineno],
                    })
                    # Record method names so we don't count them as top-level functions.
                    class_methods.add(n.name)
            class_info.append({
                "name": node.name,
                "start_line": node.lineno,
                "end_line": node.end_lineno,
                "text": file_content.splitlines()[node.lineno - 1 : node.end_lineno],
                "methods": methods,
            })
        elif isinstance(node, ast.FunctionDef) and not isinstance(
            node, ast.AsyncFunctionDef
        ):
            # Exclude AsyncFunctionDef — matches original behaviour explicitly.
            # Only count top-level functions (not class methods).
            if node.name not in class_methods:
                function_names.append({
                    "name": node.name,
                    "start_line": node.lineno,
                    "end_line": node.end_lineno,
                    "text": file_content.splitlines()[node.lineno - 1 : node.end_lineno],
                })

    return class_info, function_names, file_content.splitlines()


# ---------------------------------------------------------------------------
# §3.2 — Directory walk → nested structure dict (DEVIATION applied)
# ---------------------------------------------------------------------------

def create_structure(
    repo_root: str,
    max_files: int = MAX_FILES,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> dict:
    """
    Walk repo_root and return a nested dict keyed by path segments RELATIVE TO repo_root.

    DEVIATION: no synthetic top-level basename key (see module docstring).

    Each .py file maps to {"classes": [...], "functions": [...], "text": [...]}.
    Each non-.py file maps to {} (present in tree, no parsed content).
    """
    structure: dict = {}
    file_count = 0

    # Walk the entire directory tree. os.walk yields (root, dirs, files) for
    # each directory. We prune SKIP_DIRS in-place so os.walk won't descend.
    for root, dirs, files in os.walk(repo_root):
        # Prune skipped dirs in-place so os.walk does not descend into them.
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)

        # Build the nested dict structure by following the path hierarchy.
        # relative_root is the path from repo_root to the current directory.
        relative_root = os.path.relpath(root, repo_root)
        curr = structure
        # Navigate into nested dicts for each path segment.
        if relative_root != ".":
            for part in relative_root.split(os.sep):
                curr = curr.setdefault(part, {})

        for file_name in sorted(files):
            if file_count >= max_files:
                logger.warning(
                    "MAX_FILES (%d) reached; skipping remaining files from %s",
                    max_files,
                    root,
                )
                continue

            full_path = os.path.join(root, file_name)

            if file_name.endswith(".py"):
                try:
                    size = os.path.getsize(full_path)
                    if size > max_file_bytes:
                        logger.warning(
                            "Skipping content of large file (%d bytes): %s",
                            size,
                            full_path,
                        )
                        curr[file_name] = {"classes": [], "functions": [], "text": []}
                    else:
                        try:
                            with open(
                                full_path, "r", encoding="utf-8", errors="replace"
                            ) as fh:
                                content = fh.read()
                        except Exception as read_exc:
                            logger.warning(
                                "Could not read %s: %s", full_path, read_exc
                            )
                            curr[file_name] = {"classes": [], "functions": [], "text": []}
                            file_count += 1
                            continue

                        classes, functions, lines = parse_python_file(full_path, content)
                        curr[file_name] = {
                            "classes": classes,
                            "functions": functions,
                            "text": lines,
                        }
                except Exception as exc:
                    logger.warning(
                        "Unexpected error processing %s: %s", full_path, exc
                    )
                    curr[file_name] = {"classes": [], "functions": [], "text": []}

                file_count += 1
            else:
                curr[file_name] = {}

    return structure


# ---------------------------------------------------------------------------
# §3.3 — Basic filtering (verbatim from preprocess_data.py)
# ---------------------------------------------------------------------------

def filter_none_python(structure: dict) -> None:
    """
    Recursively drop dict entries that are neither a parsed .py file nor a real folder.
    Mutates structure in-place.

    This filter removes non-.py files (like images, configs) that don't have
    the {classes, functions, text} shape of a parsed Python file.
    """
    for key in list(structure.keys()):
        value = structure[key]
        # A parsed file has exactly 3 keys: classes, functions, text
        is_parsed_file = (
            isinstance(value, dict)
            and "functions" in value
            and "classes" in value
            and "text" in value
            and len(value.keys()) == 3
        )
        if not is_parsed_file:
            # Recurse into subdirectories or non-parsed entries.
            filter_none_python(value)
            # Remove empty directories that have no remaining children.
            if structure[key] == {}:
                del structure[key]
        else:
            # Remove parsed files that don't have .py extension (shouldn't exist,
            # but defensive check).
            if not key.endswith(".py"):
                del structure[key]


def filter_out_test_files(structure: dict) -> None:
    """
    Recursively drop any key (file or folder) whose name starts with 'test'.
    Mutates structure in-place.

    Known quirk (inherited from original, intentional): this deletes ANY name
    starting with the literal substring 'test' at every level, including
    legitimately-named non-test files/folders like 'testing_utils/'.
    Do not fix this — it is the original's behaviour.
    """
    for key in list(structure.keys()):
        if key.startswith("test"):
            del structure[key]
        elif isinstance(structure[key], dict):
            filter_out_test_files(structure[key])


# ---------------------------------------------------------------------------
# §3.4 — Flattening + pretty-printing (verbatim from preprocess_data.py)
# ---------------------------------------------------------------------------

def get_full_file_paths_and_classes_and_functions(
    structure: dict, current_path: str = ""
) -> tuple[list, list[dict], list[dict]]:
    """
    Flatten the nested structure dict into three parallel lists.

    Returns:
        files:     list of (path, list_of_lines) tuples for every .py file,
                   plus bare path strings for non-.py files (matches original).
        classes:   list of {file, name, start_line, end_line, methods} dicts.
        functions: list of {file, name, start_line, end_line, text} dicts.
    """
    files: list = []
    classes: list[dict] = []
    functions: list[dict] = []

    for name, content in structure.items():
        if isinstance(content, dict):
            is_parsed_file = (
                "functions" in content
                and "classes" in content
                and "text" in content
                and len(content.keys()) == 3
            )
            next_path = f"{current_path}/{name}" if current_path else name

            if not is_parsed_file:
                sub_files, sub_classes, sub_functions = (
                    get_full_file_paths_and_classes_and_functions(content, next_path)
                )
                files.extend(sub_files)
                classes.extend(sub_classes)
                functions.extend(sub_functions)
            else:
                files.append((next_path, content["text"]))
                for clazz in content["classes"]:
                    classes.append({
                        "file": next_path,
                        "name": clazz["name"],
                        "start_line": clazz["start_line"],
                        "end_line": clazz["end_line"],
                        "methods": [
                            {
                                "name": m["name"],
                                "start_line": m["start_line"],
                                "end_line": m["end_line"],
                            }
                            for m in clazz.get("methods", [])
                        ],
                    })
                for function in content["functions"]:
                    function = dict(function)
                    function["file"] = next_path
                    functions.append(function)
        else:
            next_path = f"{current_path}/{name}" if current_path else name
            files.append(next_path)

    return files, classes, functions


def show_project_structure(structure: dict, spacing: int = 0) -> str:
    """
    Pretty-print the repo tree as indented text for use inside LLM prompts.
    Skips non-.py files that have extensions (matches original behaviour).
    """
    pp_string = ""
    for key, value in structure.items():
        if "." in key and not key.endswith(".py"):
            continue  # skip non-python files with extensions — matches original
        if "." in key:
            pp_string += " " * spacing + str(key) + "\n"
        else:
            pp_string += " " * spacing + str(key) + "/" + "\n"
        if isinstance(value, dict) and "classes" not in value:
            pp_string += show_project_structure(value, spacing + 4)
    return pp_string


def correct_file_paths(model_found_files: list[str], files: list) -> list[str]:
    """
    Keep only model-proposed paths that exactly match a real file path.
    Preserves the model's proposed order.
    """
    found_files: list[str] = []
    if model_found_files:
        # Fresh structures contain ``(path, lines)`` tuples.  Persisted
        # sessions are JSON, however, so those tuples are restored as lists
        # after a server restart.  Both forms describe the same file entry.
        real_paths = {
            f[0] if isinstance(f, (tuple, list)) else f
            for f in files
        }
        for model_file in model_found_files:
            if model_file in real_paths:
                found_files.append(model_file)
    return found_files
