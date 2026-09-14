"""
elements.py — Stage 4: Code element extraction (§7).

Ported verbatim from rgfl/fl/element_reasoning.py.

Extracts every function, class, and module-level global variable from a Python
file using ast.  The visitor recurses into all children, so it captures:
  - Every FunctionDef anywhere in the file (including methods and nested defs),
    recorded as bare ("function", name, ...) without class qualification.
  - Every ClassDef anywhere in the file.
  - Module-level Assign nodes only (where the immediate parent is ast.Module).

Naming convention for downstream use: f"{kind}: {name}"
  e.g. "function: write", "class: HTML", "global: DEFAULT_TIMEOUT"
This is exactly the key format Stages 5 and 6 use, and the format expected in
ground_truth_elements (§2).
"""

from __future__ import annotations

import ast
import logging

logger = logging.getLogger(__name__)


def extract_code_elements_from_file(
    file_content: str,
) -> tuple[list[tuple[str, str, int, int]], str]:
    """
    Extract all code elements from a Python file's source text.

    IMPORTANT: because generic_visit recurses, this captures every FunctionDef
    and ClassDef at any nesting depth — including methods inside classes and
    inner functions.  Globals are restricted to Assign nodes at Module level.

    Args:
        file_content: Full source text of the file.

    Returns:
        (elements, file_content)
        elements: list of (kind, name, start_line, end_line) tuples
                  kind in {"function", "class", "global"}
        file_content: the same source string (returned for convenience so
                      callers can pass it straight to get_source_code).

    Raises:
        SyntaxError / any ast.parse exception — callers catch and skip the file.
    """
    tree = ast.parse(file_content)
    elements: list[tuple[str, str, int, int]] = []

    # Annotate every node with its parent so visit_Assign can check node.parent.
    # This is a post-hoc attribute assignment — ast doesn't provide parent links
    # by default, but the walk guarantees we visit parents before children.
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node  # type: ignore[attr-defined]

    class CodeVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            # Record every function/method as a standalone element.
            # Class methods are NOT qualified with the class name — matching
            # the original RGFL extraction convention.
            elements.append(("function", node.name, node.lineno, node.end_lineno))
            self.generic_visit(node)

        # AsyncFunctionDef intentionally NOT overridden — matches original.
        # Async functions are not captured as elements in this reimplementation.

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            elements.append(("class", node.name, node.lineno, node.end_lineno))
            self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign) -> None:
            # Only capture module-level assignments (parent is the Module node).
            # This filters out assignments inside functions/classes.
            parent = getattr(node, "parent", None)
            if isinstance(parent, ast.Module):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        elements.append(
                            ("global", target.id, node.lineno, node.end_lineno)
                        )
            self.generic_visit(node)

    CodeVisitor().visit(tree)
    return elements, file_content


def get_source_code(
    element: tuple[str, str, int, int],
    file_source: str,
) -> str:
    """
    Slice the source lines for a single element.

    Args:
        element:     (kind, name, start_line, end_line) — 1-indexed line numbers.
        file_source: Full source text of the file.

    Returns:
        The element's source code as a single string.
    """
    lines = file_source.splitlines()
    kind, name, start_line, end_line = element
    # Line numbers are 1-indexed; Python slicing is 0-indexed, so start_line - 1.
    return "\n".join(lines[start_line - 1 : end_line])


def element_key(kind: str, name: str) -> str:
    """Return the canonical string key for an element: '<kind>: <name>'."""
    return f"{kind}: {name}"
