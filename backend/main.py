"""
main.py — FastAPI application + all API routes (§12).

Routes are staged (not one giant "run everything" call) to match RGFL's own
pipeline design, keep each step independently testable, and be ready for a
future frontend to wire onto these exact stage boundaries.

Stage gate rule: each POST route validates its prerequisite stage has run
for that project_id (returns HTTP 409 with a clear message if not).

All routes:
  GET  /meta                              —      server defaults (model, stage sizes)
  POST /projects                          — §3:  build structure, apply filters
  POST /projects/upload                   — §3:  same, from uploaded .py files
                                                 (adopts them as candidates, skipping §4)
  POST /projects/{project_id}/candidates  — §4:  candidate file selection
  POST /projects/{project_id}/file-reasoning  — §5: file-level reasoning
  POST /projects/{project_id}/file-ranking    — §6: file-level ranking
  POST /projects/{project_id}/element-reasoning — §7+8: element extraction + reasoning
  POST /projects/{project_id}/element-ranking   — §9: element-level ranking
  GET  /projects/{project_id}/candidate-meta  — line/byte counts for candidates
  GET  /projects/{project_id}/element-source  — source slice for one element
  GET  /projects/{project_id}             — return full session state (debug / frontend hydration)
  DELETE /projects/{project_id}           — drop a session, clean up its temp checkout
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

# Import all pipeline modules — each handles one stage of the RGFL pipeline.
import store as session_store
from candidates import select_candidates
from config import (
    DEFAULT_BACKEND,
    DEFAULT_MODEL,
    DEFAULT_TOP_K_FILES,
    DEFAULT_TOP_N_CANDIDATES,
    MAX_ELEMENT_WORKERS,
    MAX_REASONING_WORKERS,
)
from element_ranking import element_hit_at_k, rank_all_files_elements
from element_reasoning import generate_all_element_reasoning
from elements import extract_code_elements_from_file, get_source_code
from file_ranking import hit_at_k, rank_files
from file_reasoning import generate_file_reasoning
from github_fetch import get_repo_source
from structure import (
    create_structure,
    filter_none_python,
    filter_out_test_files,
    get_full_file_paths_and_classes_and_functions,
    show_project_structure,
)
from uploads import stage_uploaded_files

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="RGFL-Mini Backend",
    description=(
        "A faithful, standalone reimplementation of RGFL's core file-level + "
        "element-level localization mechanism. Backend only — no frontend, no "
        "patch generation, no line-level localization."
    ),
    version="0.1.0",
)

# Allow all origins for development / local frontend wiring.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _require_session(project_id: str) -> dict[str, Any]:
    """Return the session or raise HTTP 404."""
    session = session_store.get_session(project_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project '{project_id}' not found.",
        )
    return session


def _parse_json_list(raw: str | None, field_name: str) -> list[str] | None:
    """Parse an optional multipart form field that carries a JSON array.

    Multipart forms have no native array-of-strings type, so list-valued
    fields arrive as a JSON string.  A bare comma-separated string is also
    accepted, since that is what a user typing into a text field produces.
    """
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [item.strip() for item in raw.split(",") if item.strip()] or None
    if not isinstance(parsed, list):
        raise HTTPException(
            status_code=422, detail=f"{field_name} must be a JSON array of strings."
        )
    return [str(item).strip() for item in parsed if str(item).strip()] or None


def _build_structure(repo_root: str, empty_detail: str | None = None) -> tuple[dict, list, int, str]:
    """Run Stage 0 (§3) over a local directory, whatever produced it.

    Shared by every ingestion mode — local path, GitHub download, and staged
    uploads — so all three enter the pipeline through identical code.

    Args:
        repo_root:    Directory to walk.
        empty_detail: Message to raise when the filters leave nothing behind.
                      Callers override it because the default talks about
                      `repo_root`, which means nothing to someone who uploaded
                      files through a browser.

    Returns:
        ``(structure, files_flat, num_python_files, tree_preview)``.
    """
    # §3.2 — Walk and parse: build nested dict from the filesystem.
    structure = create_structure(repo_root)

    # §3.3 — Apply both filters (in order matching localize.py).
    # First remove non-.py files, then remove test files.
    filter_none_python(structure)
    filter_out_test_files(structure)

    # §3.4 — Flatten the nested dict into parallel lists for downstream stages.
    files_flat, _classes_flat, _functions_flat = (
        get_full_file_paths_and_classes_and_functions(structure)
    )

    # Count only .py files (tuples in files_flat have (path, lines) format).
    num_python_files = sum(1 for f in files_flat if isinstance(f, tuple))

    if num_python_files == 0:
        raise HTTPException(
            status_code=422,
            detail=empty_detail
            or (
                "No Python (.py) files found in the repository after filtering. "
                "Check that repo_root points to the correct directory."
            ),
        )

    return structure, files_flat, num_python_files, show_project_structure(structure).strip()


def _real_file_paths(files_flat: list) -> list[str]:
    """Return every real .py path in a flattened file list.

    Entries are ``(path, lines)`` when fresh and ``[path, lines]`` once a
    session has been round-tripped through JSON, so both are accepted.
    """
    paths: list[str] = []
    for entry in files_flat or []:
        if isinstance(entry, (tuple, list)) and entry:
            paths.append(entry[0])
    return paths


def _read_repo_file(repo_root: str, relative_path: str) -> str:
    """Read one repo-relative file from disk, refusing paths that escape the root."""
    root = os.path.abspath(repo_root)
    absolute = os.path.abspath(os.path.join(root, *relative_path.split("/")))
    if os.path.commonpath([root, absolute]) != root:
        raise HTTPException(status_code=400, detail="Path must stay inside the repository.")
    if not os.path.isfile(absolute):
        raise HTTPException(status_code=404, detail=f"File not found in repo: {relative_path!r}")
    with open(absolute, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _require_stage(session: dict[str, Any], field: str, stage_name: str) -> None:
    """
    Raise HTTP 409 if a prerequisite pipeline field is still None.

    This is the stage gate described in §12: each POST validates that its
    prerequisite stage has already run.
    """
    if session.get(field) is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Prerequisite stage '{stage_name}' has not completed for this project. "
                f"Run that stage first before calling this endpoint."
            ),
        )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class CreateProjectRequest(BaseModel):
    repo_root: str | None = Field(
        default=None,
        description="Absolute path on the server to the repo/ folder to analyse.",
    )
    repo_url: str | None = Field(
        default=None,
        description="Public GitHub repository URL or owner/repo shorthand.",
    )
    ref: str | None = Field(
        default=None,
        description="Optional GitHub branch, tag, or commit SHA.",
    )
    subdir: str | None = Field(
        default="",
        description="Optional path inside a GitHub repository to use as its source root.",
    )
    github_token: str | None = Field(
        default=None,
        description="Optional token for this GitHub request; overrides GITHUB_TOKEN.",
    )
    problem_statement: str = Field(
        ...,
        description="The full natural-language bug report text.",
    )
    ground_truth_file: str | None = Field(
        default=None,
        description=(
            "Optional. POSIX-style path relative to repo/ root, e.g. "
            "'astropy/io/ascii/html.py'. Used only for Hit@k evaluation, "
            "never fed to the LLM."
        ),
    )
    ground_truth_elements: list[str] | None = Field(
        default=None,
        description=(
            "Optional. List of '<kind>: <name>' strings, e.g. "
            "['function: write', 'class: HTML']. Used only for element-level "
            "evaluation, never fed to the LLM."
        ),
    )
    model: str = Field(
        default=DEFAULT_MODEL,
        description="LLM model identifier, e.g. 'gpt-4o', 'claude-sonnet-4-5', 'gemini-2.5-pro'.",
    )
    backend: str = Field(
        default=DEFAULT_BACKEND,
        description="LLM backend: 'anthropic', 'openai', or 'gemini'.",
    )

    @model_validator(mode="after")
    def check_exactly_one_source(self) -> "CreateProjectRequest":
        has_root = bool(self.repo_root)
        has_url = bool(self.repo_url)
        if has_root == has_url:
            raise ValueError(
                "Provide exactly one of `repo_root` (a local path) or `repo_url` "
                "(a GitHub URL), not both or neither."
            )
        return self


class CreateProjectResponse(BaseModel):
    project_id: str
    num_python_files: int
    tree_preview: str


class CandidatesRequest(BaseModel):
    top_n_candidates: int = Field(
        default=DEFAULT_TOP_N_CANDIDATES,
        ge=1,
        le=20,
        description="How many candidate files to request from the LLM (default 5).",
    )


class CandidatesResponse(BaseModel):
    candidates: list[str]
    raw_llm_output: str


class FileReasoningResponse(BaseModel):
    file_reasoning: dict[str, str]


class FileRankingResponse(BaseModel):
    ranked_files: list[str]
    ground_truth_rank: int | None
    hit_at_k: dict[str, bool] | None


class ElementReasoningRequest(BaseModel):
    top_k_files: int = Field(
        default=DEFAULT_TOP_K_FILES,
        ge=1,
        le=10,
        description=(
            "How many top-ranked files to extract elements from (default 3, "
            "matching the paper's own choice — §4.3/RQ1.1)."
        ),
    )


class ElementReasoningResponse(BaseModel):
    element_reasoning: dict[str, dict[str, str]]


class ElementRankingResponse(BaseModel):
    element_ranking: dict[str, list[str]]
    element_ranking_eval: dict[str, Any] | None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/meta")
def get_meta() -> dict[str, Any]:
    """Report the server's configured defaults.

    The frontend reads its engine label and stage defaults from here rather
    than hard-coding them, so the UI can never claim a different model than
    the one the pipeline actually calls.
    """
    return {
        "default_model": DEFAULT_MODEL,
        "default_backend": DEFAULT_BACKEND,
        "default_top_n_candidates": DEFAULT_TOP_N_CANDIDATES,
        "default_top_k_files": DEFAULT_TOP_K_FILES,
    }


@app.post(
    "/projects/upload",
    response_model=CreateProjectResponse,
    status_code=201,
)
async def create_project_from_upload(
    files: list[UploadFile] = File(..., description="Candidate .py files."),
    problem_statement: str = Form(...),
    relative_paths: str | None = Form(
        default=None,
        description="Optional JSON array of per-file paths, aligned with `files` by index.",
    ),
    ground_truth_file: str | None = Form(default=None),
    ground_truth_elements: str | None = Form(
        default=None,
        description="Optional JSON array of '<kind>: <name>' strings.",
    ),
    model: str = Form(default=DEFAULT_MODEL),
    backend: str = Form(default=DEFAULT_BACKEND),
) -> CreateProjectResponse:
    """Stage 0 for static ingestion — build a project from uploaded .py files.

    The uploaded files are staged to a temp directory, run through the same
    Stage 0 as every other source, and then adopted as the candidate set
    directly.  Stage 1 (§4, LLM candidate selection) is deliberately skipped:
    the user has already chosen the candidates, so there is nothing to
    nominate.  Stages 2-6 then run unchanged.
    """
    if backend not in ("anthropic", "openai", "gemini"):
        raise HTTPException(
            status_code=422,
            detail=f"backend must be one of 'anthropic', 'openai', 'gemini'. Got: {backend!r}",
        )
    if not problem_statement.strip():
        raise HTTPException(status_code=422, detail="problem_statement must be non-empty.")

    parsed_paths = _parse_json_list(relative_paths, "relative_paths")
    parsed_elements = _parse_json_list(ground_truth_elements, "ground_truth_elements")

    payload: list[tuple[str, bytes]] = []
    for index, upload in enumerate(files):
        client_path = (
            parsed_paths[index]
            if parsed_paths and index < len(parsed_paths) and parsed_paths[index]
            else (upload.filename or "")
        )
        payload.append((client_path, await upload.read()))

    try:
        staging_root, staged_paths = stage_uploaded_files(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    logger.info("POST /projects/upload — %d files staged at %s", len(staged_paths), staging_root)

    try:
        structure, files_flat, num_python_files, tree_preview = _build_structure(
            staging_root,
            empty_detail=(
                "Every uploaded file was removed by the Stage 0 filters. Files whose name "
                "starts with 'test' are dropped by design (§3.3), as are files that are not "
                "valid Python. Upload at least one other .py file."
            ),
        )

        # Stage 0's filters can legitimately remove an uploaded file — most
        # notably filter_out_test_files, which drops any name starting with
        # "test" (§3.3's documented quirk).  Candidates must only ever contain
        # paths the rest of the pipeline can actually see.
        visible = set(_real_file_paths(files_flat))
        candidates = [path for path in staged_paths if path in visible]
        dropped = [path for path in staged_paths if path not in visible]
        if dropped:
            logger.warning("Uploaded files removed by Stage 0 filters: %s", dropped)
        if not candidates:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Every uploaded file was removed by the Stage 0 filters. Files whose "
                    "name starts with 'test' are dropped by design (§3.3). Upload at least "
                    "one other .py file."
                ),
            )
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise

    project_id = session_store.create_session(
        repo_root=staging_root,
        problem_statement=problem_statement,
        model=model,
        backend=backend,
        ground_truth_file=ground_truth_file or None,
        ground_truth_elements=parsed_elements,
        source_type="static",
        # download_dir drives DELETE /projects/{id} cleanup, so the staging
        # directory is recorded there for the same reason a GitHub checkout is.
        download_dir=staging_root,
    )
    session_store.update_session(
        project_id,
        structure=structure,
        files_flat=files_flat,
        candidates=candidates,
        candidates_raw_output=(
            "Static upload: candidate selection (§4) skipped — the uploaded files "
            "are the candidate set."
        ),
    )

    logger.info(
        "Project %s created from upload — %d candidates, %d Python files",
        project_id,
        len(candidates),
        num_python_files,
    )

    return CreateProjectResponse(
        project_id=project_id,
        num_python_files=num_python_files,
        tree_preview=tree_preview,
    )


@app.post("/projects", response_model=CreateProjectResponse, status_code=201)
def create_project(req: CreateProjectRequest) -> CreateProjectResponse:
    """
    Stage 0 — Build the repository structure object (§3).

    Validates the repo_root exists, walks the directory, parses Python files
    with AST, applies both filters (filter_none_python, filter_out_test_files),
    flattens the structure, and stores everything in a new session.

    Returns the project_id plus a tree preview and the Python file count.
    """
    # Validate the backend is one of our supported providers.
    if req.backend not in ("anthropic", "openai", "gemini"):
        raise HTTPException(
            status_code=422,
            detail=f"backend must be one of 'anthropic', 'openai', 'gemini'. Got: {req.backend!r}",
        )

    # The bug report must be non-empty — it's the core input to the pipeline.
    if not req.problem_statement.strip():
        raise HTTPException(
            status_code=422,
            detail="problem_statement must be non-empty.",
        )

    # Handle GitHub URL vs local path — they're mutually exclusive (enforced by model_validator).
    github_meta: dict[str, str] | None = None
    if req.repo_url:
        # Download and extract the GitHub repository to a temp directory.
        try:
            github_meta = get_repo_source(
                repo_url=req.repo_url,
                ref=req.ref,
                subdir=req.subdir,
                github_token=req.github_token,
            )
        except Exception as exc:
            logger.info("GitHub repository fetch failed for %r: %s", req.repo_url, exc)
            raise HTTPException(status_code=400, detail=f"Failed to fetch GitHub repo: {exc}")
        repo_root = github_meta["effective_root"]
    else:
        # The model validator guarantees this is non-None for local projects.
        repo_root = os.path.abspath(req.repo_root or "")
        if not os.path.isdir(repo_root):
            raise HTTPException(
                status_code=400,
                detail=f"repo_root does not exist or is not a directory: {repo_root!r}",
            )

    logger.info("POST /projects — repo_root=%s", repo_root)

    try:
        structure, files_flat, num_python_files, tree_preview = _build_structure(repo_root)
    except Exception:
        # A failed Stage 0 never creates a session, so it must not leave a
        # temporary GitHub checkout behind.
        if github_meta:
            shutil.rmtree(github_meta["download_dir"], ignore_errors=True)
        raise

    # Create session
    project_id = session_store.create_session(
        repo_root=repo_root,
        problem_statement=req.problem_statement,
        model=req.model,
        backend=req.backend,
        ground_truth_file=req.ground_truth_file,
        ground_truth_elements=req.ground_truth_elements,
        source_type="github" if github_meta else "local",
        repo_url=req.repo_url if github_meta else None,
        owner=github_meta["owner"] if github_meta else None,
        repo_name=github_meta["repo"] if github_meta else None,
        resolved_ref=github_meta["resolved_ref"] if github_meta else None,
        resolved_commit_sha=github_meta["resolved_commit_sha"] if github_meta else None,
        download_dir=github_meta["download_dir"] if github_meta else None,
    )
    session_store.update_session(
        project_id,
        structure=structure,
        files_flat=files_flat,
    )

    logger.info(
        "Project %s created — %d Python files, repo_root=%s",
        project_id,
        num_python_files,
        repo_root,
    )

    return CreateProjectResponse(
        project_id=project_id,
        num_python_files=num_python_files,
        tree_preview=tree_preview,
    )


@app.post(
    "/projects/{project_id}/candidates",
    response_model=CandidatesResponse,
)
def run_candidates(project_id: str, req: CandidatesRequest) -> CandidatesResponse:
    """
    Stage 1 — Candidate file selection (§4).

    Single LLM call: reads the bug report + file tree and proposes up to
    top_n_candidates files.  Returns the validated candidate list and raw LLM output.

    Prerequisite: POST /projects (structure must be built).
    """
    # Verify the project exists and has completed Stage 0.
    session = _require_session(project_id)
    _require_stage(session, "structure", "POST /projects")
    _require_stage(session, "files_flat", "POST /projects")

    logger.info("POST /projects/%s/candidates — top_n=%d", project_id, req.top_n_candidates)

    try:
        # Call the LLM to nominate candidate files based on the bug report.
        candidates, raw_output = select_candidates(
            problem_statement=session["problem_statement"],
            structure=session["structure"],
            files_flat=session["files_flat"],
            model=session["model"],
            backend=session["backend"],
            top_n_candidates=req.top_n_candidates,
        )
    except Exception as exc:
        logger.error("Candidate selection failed for %s: %s", project_id, exc)
        raise HTTPException(
            status_code=502,
            detail=f"LLM call failed during candidate selection: {exc}",
        )

    # The LLM might return paths that don't exist in the repo — we must reject
    # an empty candidate set since the pipeline can't continue without files.
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail=(
                "The LLM did not propose any file paths that match real files in "
                "the repository. The pipeline cannot continue with an empty candidate set."
            ),
        )

    # Persist the candidates and raw LLM output for the next stage.
    session_store.update_session(
        project_id,
        candidates=candidates,
        candidates_raw_output=raw_output,
    )

    logger.info(
        "Stage 1 done for %s — %d candidates: %s",
        project_id,
        len(candidates),
        candidates,
    )
    return CandidatesResponse(candidates=candidates, raw_llm_output=raw_output)


@app.post(
    "/projects/{project_id}/file-reasoning",
    response_model=FileReasoningResponse,
)
def run_file_reasoning(project_id: str) -> FileReasoningResponse:
    """
    Stage 2 — File-level reasoning (§5).

    One LLM call per candidate file, run in parallel (ThreadPoolExecutor, 5 workers).
    Reads each file fresh from disk.  Failed individual calls are stored as error
    strings (never dropped) so Stage 3 has a complete dict.

    Prerequisite: POST /projects/{project_id}/candidates
    """
    session = _require_session(project_id)
    _require_stage(session, "candidates", "POST /projects/{project_id}/candidates")

    logger.info("POST /projects/%s/file-reasoning", project_id)

    try:
        # Run parallel LLM calls — one per candidate file explaining its
        # relationship to the bug report.
        file_reasoning = generate_file_reasoning(
            candidates=session["candidates"],
            problem_statement=session["problem_statement"],
            repo_root=session["repo_root"],
            model=session["model"],
            backend=session["backend"],
            max_workers=MAX_REASONING_WORKERS,
        )
    except Exception as exc:
        logger.error("File reasoning failed for %s: %s", project_id, exc)
        raise HTTPException(
            status_code=502,
            detail=f"File reasoning stage failed: {exc}",
        )

    # Store the reasoning dict — Stage 3 will use it to rank files.
    session_store.update_session(project_id, file_reasoning=file_reasoning)

    logger.info(
        "Stage 2 done for %s — reasoned about %d files", project_id, len(file_reasoning)
    )
    return FileReasoningResponse(file_reasoning=file_reasoning)


@app.post(
    "/projects/{project_id}/file-ranking",
    response_model=FileRankingResponse,
)
def run_file_ranking(project_id: str) -> FileRankingResponse:
    """
    Stage 3 — File-level reasoning-guided ranking (§6).

    One LLM call given ALL of Stage 2's reasoning at once.
    Optionally computes Hit@k / rank_position if ground_truth_file was supplied.

    Prerequisite: POST /projects/{project_id}/file-reasoning
    """
    session = _require_session(project_id)
    _require_stage(session, "file_reasoning", "POST /projects/{project_id}/file-reasoning")

    logger.info("POST /projects/%s/file-ranking", project_id)

    try:
        # Single LLM call: present all file reasoning and ask for a ranking.
        ranked_files = rank_files(
            file_reasoning_dict=session["file_reasoning"],
            problem_statement=session["problem_statement"],
            candidates=session["candidates"],
            model=session["model"],
            backend=session["backend"],
        )
    except Exception as exc:
        logger.error("File ranking failed for %s: %s", project_id, exc)
        raise HTTPException(
            status_code=502,
            detail=f"File ranking LLM call failed: {exc}",
        )

    # Optionally evaluate against ground truth if provided.
    eval_result = hit_at_k(ranked_files, session.get("ground_truth_file"))

    # Store the ranked list and evaluation results.
    session_store.update_session(
        project_id,
        file_ranking=ranked_files,
        file_ranking_eval=eval_result,
    )

    logger.info(
        "Stage 3 done for %s — ranked order: %s", project_id, ranked_files
    )

    # Extract ground truth rank and Hit@k results for the API response.
    ground_truth_rank: int | None = None
    hit_at_k_result: dict[str, bool] | None = None
    if eval_result:
        ground_truth_rank = eval_result.get("rank_position")
        raw_hak = eval_result.get("hit_at_k", {})
        # Convert int keys to str for JSON serialisation compatibility.
        hit_at_k_result = {str(k): v for k, v in raw_hak.items()}

    return FileRankingResponse(
        ranked_files=ranked_files,
        ground_truth_rank=ground_truth_rank,
        hit_at_k=hit_at_k_result,
    )


@app.post(
    "/projects/{project_id}/element-reasoning",
    response_model=ElementReasoningResponse,
)
def run_element_reasoning(
    project_id: str, req: ElementReasoningRequest
) -> ElementReasoningResponse:
    """
    Stages 4 + 5 — Element extraction and element-level reasoning (§7, §8).

    For each of the top_k_files from Stage 3's ranking:
      - Extract code elements with ast (Stage 4).
      - Call the LLM once per element in parallel (Stage 5).

    Returns dict keyed by "file1_elements_reasoning", "file2_elements_reasoning", ...

    Prerequisite: POST /projects/{project_id}/file-ranking
    """
    session = _require_session(project_id)
    _require_stage(session, "file_ranking", "POST /projects/{project_id}/file-ranking")

    logger.info(
        "POST /projects/%s/element-reasoning — top_k=%d", project_id, req.top_k_files
    )

    try:
        # For each of the top-K ranked files, extract elements via AST and
        # run parallel LLM calls to explain each element's relevance to the bug.
        element_reasoning = generate_all_element_reasoning(
            ranked_files=session["file_ranking"],
            repo_root=session["repo_root"],
            problem_statement=session["problem_statement"],
            model=session["model"],
            backend=session["backend"],
            top_k=req.top_k_files,
            max_workers=MAX_ELEMENT_WORKERS,
        )
    except Exception as exc:
        logger.error("Element reasoning failed for %s: %s", project_id, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Element reasoning stage failed: {exc}",
        )

    # Store element reasoning — Stage 6 will use it to rank elements.
    session_store.update_session(project_id, element_reasoning=element_reasoning)

    logger.info(
        "Stages 4+5 done for %s — processed %d files",
        project_id,
        len(element_reasoning),
    )
    return ElementReasoningResponse(element_reasoning=element_reasoning)


@app.post(
    "/projects/{project_id}/element-ranking",
    response_model=ElementRankingResponse,
)
def run_element_ranking(project_id: str) -> ElementRankingResponse:
    """
    Stage 6 — Element-level ranking (§9).

    For each of the top-K files' element-reasoning dicts (from Stage 5), one LLM
    call ranks which elements are most likely related to the bug.
    Optionally evaluates against ground_truth_elements.

    Returns dict keyed by "similar_elements_file1", "similar_elements_file2", ...

    Prerequisite: POST /projects/{project_id}/element-reasoning
    """
    session = _require_session(project_id)
    _require_stage(
        session, "element_reasoning", "POST /projects/{project_id}/element-reasoning"
    )

    logger.info("POST /projects/%s/element-ranking", project_id)

    try:
        # For each file's element reasoning, make one LLM call to rank
        # which elements are most likely buggy.
        element_ranking = rank_all_files_elements(
            element_reasoning=session["element_reasoning"],
            problem_statement=session["problem_statement"],
            model=session["model"],
            backend=session["backend"],
        )
    except Exception as exc:
        logger.error("Element ranking failed for %s: %s", project_id, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Element ranking stage failed: {exc}",
        )

    # Optionally evaluate against ground truth elements if provided.
    eval_result = element_hit_at_k(
        element_ranking, session.get("ground_truth_elements")
    )

    # Store the ranked elements and evaluation results.
    session_store.update_session(
        project_id,
        element_ranking=element_ranking,
        element_ranking_eval=eval_result,
    )

    logger.info("Stage 6 done for %s", project_id)
    return ElementRankingResponse(
        element_ranking=element_ranking,
        element_ranking_eval=eval_result,
    )


@app.get("/projects/{project_id}/candidate-meta")
def get_candidate_meta(project_id: str) -> dict[str, Any]:
    """Return size metadata for the stored candidate files.

    Exists so the UI can show line counts next to each candidate without
    pulling whole-file contents (or the entire session) over the wire.
    """
    session = _require_session(project_id)
    _require_stage(session, "candidates", "POST /projects/{project_id}/candidates")

    repo_root = session["repo_root"]
    entries: list[dict[str, Any]] = []
    for path in session["candidates"]:
        absolute = os.path.join(repo_root, *path.split("/"))
        try:
            with open(absolute, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read()
            entries.append(
                {
                    "path": path,
                    "line_count": len(content.splitlines()),
                    "size_bytes": os.path.getsize(absolute),
                    "available": True,
                }
            )
        except Exception as exc:
            logger.warning("Could not stat candidate %s: %s", path, exc)
            entries.append(
                {"path": path, "line_count": None, "size_bytes": None, "available": False}
            )

    return {"candidate_meta": entries}


@app.get("/projects/{project_id}/element-source")
def get_element_source(project_id: str, path: str, element: str) -> dict[str, Any]:
    """Return the source slice for one '<kind>: <name>' element in a file.

    Stage 5 (§7) discards line numbers once it has produced reasoning text, so
    the inspector re-extracts them here with the same ast walk rather than
    guessing.  Element keys are not unique within a file — a name can appear on
    several methods — so `match_count` reports how many were found and the
    first is returned.
    """
    session = _require_session(project_id)

    if ": " not in element:
        raise HTTPException(
            status_code=422,
            detail=f"element must be '<kind>: <name>', e.g. 'function: write'. Got: {element!r}",
        )
    kind, _, name = element.partition(": ")

    source = _read_repo_file(session["repo_root"], path)
    try:
        elements, _ = extract_code_elements_from_file(source)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse {path!r}: {exc}")

    matches = [e for e in elements if e[0] == kind and e[1] == name]
    if not matches:
        raise HTTPException(
            status_code=404, detail=f"Element {element!r} not found in {path!r}."
        )

    match_kind, match_name, start_line, end_line = matches[0]
    return {
        "path": path,
        "element": element,
        "kind": match_kind,
        "name": match_name,
        "start_line": start_line,
        "end_line": end_line,
        "match_count": len(matches),
        "code": get_source_code(matches[0], source),
    }


@app.get("/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    """
    Return the full stored session state for a project.

    Useful for debugging each stage and for a future frontend to hydrate from.
    The 'structure' field is intentionally included (may be large) — callers
    can choose to ignore it.
    """
    session = _require_session(project_id)
    # Return a copy so callers can't mutate the store through this dict.
    return session_store.get_session_copy(project_id)


@app.delete("/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, str]:
    """Delete a project session and clean up its GitHub download, if any."""
    session = _require_session(project_id)
    download_dir = session.get("download_dir")
    if download_dir and os.path.isdir(download_dir):
        shutil.rmtree(download_dir, ignore_errors=True)
    session_store.delete_session(project_id)
    return {"deleted": project_id}
