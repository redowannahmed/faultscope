/**
 * Stage 01 — Manifest & Code Ingestion
 *
 * The entry point of the FaultScope pipeline. This stage collects:
 *   1. A bug report (free-text problem statement).
 *   2. Source code — either from a public GitHub repository or a direct file
 *      upload of Python files.
 *   3. Optional ground-truth metadata used only for evaluation scoring.
 *
 * Once submitted the pipeline resolves the candidate set and kicks off the
 * reasoning stages downstream.
 */
import React, { useRef, useState } from "react";
import { useProject } from "../../context/ProjectContext";
import Badge from "../common/Badge";
import Button from "../common/Button";
import SectionHeading from "../common/SectionHeading";
import StageStatus from "../common/StageStatus";
import { formatBytes } from "../../lib/pipeline";

/**
 * Walk a dropped directory entry, collecting .py files with their paths intact.
 * Drag-and-drop reports only a basename per file, so folder structure has to be
 * reconstructed from the FileSystemEntry tree or it is lost.
 */
async function readEntry(entry, prefix = "") {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    return file.name.endsWith(".py") ? [{ file, path: `${prefix}${file.name}` }] : [];
  }

  if (entry.isDirectory) {
    const reader = entry.createReader();
    const entries = [];
    // readEntries returns at most 100 at a time; keep reading until it is empty.
    for (;;) {
      const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
      if (!batch.length) break;
      entries.push(...batch);
    }
    const nested = await Promise.all(
      entries.map((child) => readEntry(child, `${prefix}${entry.name}/`)),
    );
    return nested.flat();
  }

  return [];
}

/**
 * Extract Python files from a drag-and-drop DataTransfer.
 *
 * The WebKit entries API preserves directory structure so we can reconstruct
 * full relative paths. Browsers that don't support it fall back to a flat
 * file list — directory nesting is lost but the upload still works.
 */
async function filesFromDrop(dataTransfer) {
  const items = Array.from(dataTransfer.items || []);
  const entries = items
    .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
    .filter(Boolean);

  if (entries.length) {
    const collected = await Promise.all(entries.map((entry) => readEntry(entry)));
    return collected.flat();
  }

  // Fallback for browsers without the entries API.
  return Array.from(dataTransfer.files || [])
    .filter((file) => file.name.endsWith(".py"))
    .map((file) => ({ file, path: file.webkitRelativePath || file.name }));
}

/**
 * ModeToggle — Switches between GitHub ingestion and direct file upload.
 *
 * Rendered as tab-like buttons with a single accent underline on the active
 * choice. Only one mode can be active; selecting the other swaps the form
 * fields rendered below.
 */
function ModeToggle({ value, onChange, disabled }) {
  const modes = [
    { id: "github", label: "Public GitHub Repository" },
    { id: "static", label: "Upload Static Files" },
  ];

  return (
    <div className="flex items-center gap-8 border-b border-hairline pb-3">
      {modes.map((mode) => (
        <button
          key={mode.id}
          type="button"
          disabled={disabled}
          onClick={() => onChange(mode.id)}
          className={`relative pb-3 font-mono text-xs uppercase tracking-[0.12em] transition-colors
            disabled:cursor-not-allowed ${
              value === mode.id ? "text-accent" : "text-muted hover:text-secondary"
            }`}
        >
          {mode.label}
          {value === mode.id && <span className="absolute -bottom-[13px] left-0 h-px w-full bg-accent" />}
        </button>
      ))}
    </div>
  );
}

/**
 * Field — A labelled form field wrapper.
 *
 * Renders the `.label-meta` label on top, an optional "required"/"optional"
 * hint aligned to the right, and the input/textarea element passed as
 * children. Keeps field layout consistent across the form.
 */
function Field({ label, hint, children }) {
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-4">
        <label className="label-meta">{label}</label>
        {hint && <span className="font-mono text-[11px] text-muted/80">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

/**
 * Stage1Manifest — The pipeline's entry form.
 *
 * Layout:
 *   Left column (4/12) — explanatory prose + engine badge.
 *   Right column (8/12) — the actual form:
 *     1. Mode toggle (GitHub vs. Upload)
 *     2. Mode-specific fields (repo URL / file drop zone)
 *     3. Bug report textarea
 *     4. Optional ground-truth section (collapsible)
 *     5. Submit button + validation hints
 */
export default function Stage1Manifest() {
  // ── Pipeline context ──────────────────────────────────────────────
  const {
    sourceType,
    setField,
    problemStatement,
    groundTruthFile,
    groundTruthElements,
    repoUrl,
    ref,
    subdir,
    engine,
    loadingStage,
    loadingLabel,
    initializeFromGithub,
    initializeFromUpload,
  } = useProject();

  // ── Local UI state ────────────────────────────────────────────────
  const [files, setFiles] = useState([]);                  // uploaded .py files (static mode)
  const [dragging, setDragging] = useState(false);         // drop-zone drag-over indicator
  const [showGroundTruth, setShowGroundTruth] = useState(false); // collapsible section toggle
  const [localNotice, setLocalNotice] = useState("");      // per-field warnings (e.g. rejected files)
  // GitHub token is held locally — it should never enter pipeline state or
  // appear in an exported diagnostics file.
  const [githubToken, setGithubToken] = useState("");
  const fileInputRef = useRef(null);                        // ref for the hidden <input type="file">

  // ── Derived values ────────────────────────────────────────────────
  const busy = loadingStage !== null;
  const engineLabel = engine
    ? `${engine.default_model} · ${engine.default_backend}`
    : "—";

  // ── File management ───────────────────────────────────────────────
  /**
   * Merge incoming files into the manifest.
   * Non-Python files are silently dropped with a count shown to the user.
   * Duplicates (same path) are replaced — the most recent upload wins.
   */
  const addFiles = (incoming) => {
    setLocalNotice("");
    const pythonOnly = incoming.filter(({ file }) => file.name.endsWith(".py"));
    const rejected = incoming.length - pythonOnly.length;
    if (rejected > 0) {
      setLocalNotice(`${rejected} non-Python file${rejected === 1 ? "" : "s"} ignored.`);
    }
    setFiles((current) => {
      const byPath = new Map(current.map((entry) => [entry.path, entry]));
      pythonOnly.forEach((entry) => byPath.set(entry.path, entry));
      return Array.from(byPath.values());
    });
  };

  /** Handle a file/folder drop onto the drop zone. */
  const handleDrop = async (event) => {
    event.preventDefault();
    setDragging(false);
    if (busy) return;
    addFiles(await filesFromDrop(event.dataTransfer));
  };

  /** Handle the hidden file-input's change event (click-to-browse). */
  const handleBrowse = (event) => {
    const picked = Array.from(event.target.files || []).map((file) => ({
      file,
      path: file.webkitRelativePath || file.name,
    }));
    addFiles(picked);
    event.target.value = ""; // reset so the same file can be re-selected
  };

  // ── Submission guard ──────────────────────────────────────────────
  const canSubmit =
    problemStatement.trim().length > 0 &&
    !busy &&
    (sourceType === "github" ? repoUrl.trim().length > 0 : files.length > 0);

  /** Validate and dispatch to the appropriate ingestion path. */
  const handleSubmit = (event) => {
    event.preventDefault();
    if (!canSubmit) return;

    // Shared fields that both ingestion paths require.
    const shared = { problemStatement, groundTruthFile, groundTruthElements };
    if (sourceType === "github") {
      initializeFromGithub({ repoUrl, ref, subdir, githubToken, ...shared });
    } else {
      initializeFromUpload({ files, ...shared });
    }
  };

  // ── Render ────────────────────────────────────────────────────────

  return (
    <form onSubmit={handleSubmit}>
      <SectionHeading
        step="01"
        title="Manifest & Code Ingestion"
        description="Describe the failure, then point FaultScope at the source it should read."
      />

      {/* Two-column layout: explanatory aside (4 cols) + form (8 cols) */}
      <div className="grid grid-cols-1 gap-16 lg:grid-cols-12">

        {/* ── Left column: product explanation ───────────────────── */}
        <aside className="space-y-8 lg:col-span-4">
          <p className="max-w-prose text-base leading-relaxed text-secondary">
            FaultScope reads a bug report and a codebase the way a reviewer would: it examines each
            candidate <em>in isolation</em>, writes down what that code actually does, and only then
            ranks the candidates against one another — using the reasoning, never the raw source.
          </p>
          <p className="max-w-prose text-base leading-relaxed text-secondary">
            That two-step pass runs twice. Once over whole files, then again over the functions,
            classes, and globals inside the three files that survive. What comes out is not a
            keyword match but an argument for why one element causes the reported failure.
          </p>

          {/* Engine info — pulled from the backend, not configurable here. */}
          <div className="space-y-3 border-t border-hairline pt-6">
            <span className="label-meta">Engine</span>
            <div>
              <Badge variant={engine ? "default" : "error"}>{engineLabel}</Badge>
            </div>
            <p className="max-w-prose text-sm leading-relaxed text-muted">
              Reported by the backend, not configured here — the UI names whichever model the
              pipeline will actually call.
            </p>
          </div>
        </aside>

        {/* ── Right column: the form ─────────────────────────────── */}
        <div className="space-y-12 lg:col-span-8">

          {/* ── Mode selector ───────────────────────────────────── */}
          <ModeToggle value={sourceType} onChange={(mode) => setField("sourceType", mode)} disabled={busy} />

          {sourceType === "github" ? (
            /* ── GitHub mode fields ────────────────────────────── */
            <div className="space-y-8">
              <Field label="Repository URL" hint="required">
                <input
                  type="text"
                  value={repoUrl}
                  onChange={(event) => setField("repoUrl", event.target.value)}
                  placeholder="https://github.com/astropy/astropy"
                  className="field-underline font-mono text-sm"
                  disabled={busy}
                  spellCheck={false}
                />
              </Field>

              <div className="grid grid-cols-1 gap-8 sm:grid-cols-2">
                <Field label="Target ref" hint="optional">
                  <input
                    type="text"
                    value={ref}
                    onChange={(event) => setField("ref", event.target.value)}
                    placeholder="branch, tag, or commit SHA"
                    className="field-underline font-mono text-sm"
                    disabled={busy}
                    spellCheck={false}
                  />
                  <p className="pt-2 text-sm leading-relaxed text-muted">
                    Resolved to an exact commit before download, so a run stays reproducible.
                  </p>
                </Field>

                <Field label="Source subdirectory" hint="optional">
                  <input
                    type="text"
                    value={subdir}
                    onChange={(event) => setField("subdir", event.target.value)}
                    placeholder="astropy"
                    className="field-underline font-mono text-sm"
                    disabled={busy}
                    spellCheck={false}
                  />
                  <p className="pt-2 text-sm leading-relaxed text-muted">
                    Becomes the effective root. All paths — ground truth included — are then
                    relative to it.
                  </p>
                </Field>
              </div>

              {/* GitHub token — optional, raises unauthenticated rate limit from 60 to 5 000 req/hr. */}
              <Field label="GitHub token" hint="optional">
                <input
                  type="password"
                  value={githubToken}
                  onChange={(event) => setGithubToken(event.target.value)}
                  placeholder="ghp_…"
                  className="field-underline font-mono text-sm"
                  disabled={busy}
                  autoComplete="off"
                  spellCheck={false}
                />
                <p className="pt-2 max-w-prose text-sm leading-relaxed text-muted">
                  Unauthenticated GitHub API access allows 60 requests per hour and each run spends
                  two, so this limit is easy to hit. A token raises it to 5,000. Sent to your local
                  backend for this one request and never stored — setting{" "}
                  <span className="font-mono">GITHUB_TOKEN</span> in the backend environment works
                  just as well.
                </p>
              </Field>
            </div>
          ) : (
            /* ── Static upload mode ──────────────────────────────── */
            <div className="space-y-6">

              {/* Drop zone — also acts as a click-to-browse trigger. */}
              <div
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={handleDrop}
                onClick={() => !busy && fileInputRef.current?.click()}
                className={`cursor-pointer border border-dashed px-8 py-14 text-center transition-colors ${
                  dragging ? "border-accent bg-accent-tint/60" : "border-hairline hover:border-accent/50"
                }`}
              >
                <p className="font-mono text-xs uppercase tracking-[0.14em] text-secondary">
                  Drop .py files or a folder
                </p>
                <p className="mx-auto mt-3 max-w-prose text-sm leading-relaxed text-muted">
                  These become the candidate set directly. LLM candidate selection is skipped —
                  you have already made that choice.
                </p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".py"
                  multiple
                  onChange={handleBrowse}
                  className="hidden"
                />
              </div>

              {localNotice && <p className="font-mono text-xs text-status-error">{localNotice}</p>}

              {/* Uploaded file manifest — table with striped rows. */}
              {files.length > 0 && (
                <div className="border-t border-hairline">
                  <div className="flex items-center justify-between py-3">
                    <span className="label-meta">
                      Manifest · {files.length} file{files.length === 1 ? "" : "s"}
                    </span>
                    <button
                      type="button"
                      onClick={() => setFiles([])}
                      disabled={busy}
                      className="font-mono text-[11px] uppercase tracking-wider text-muted hover:text-status-error"
                    >
                      Clear
                    </button>
                  </div>
                  <table className="w-full border-collapse font-mono text-sm">
                    <tbody>
                      {files.map(({ file, path }) => (
                        <tr key={path} className="border-t border-hairline/70">
                          <td className="py-2 pr-4 text-primary">{path}</td>
                          <td className="w-24 py-2 text-right text-muted">{formatBytes(file.size)}</td>
                          <td className="w-8 py-2 text-right">
                            <button
                              type="button"
                              onClick={() => setFiles((c) => c.filter((e) => e.path !== path))}
                              disabled={busy}
                              className="text-muted transition-colors hover:text-status-error"
                              aria-label={`Remove ${path}`}
                            >
                              ×
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* ── Bug report textarea ──────────────────────────────── */}
          <Field label="Bug report" hint="required">
            <textarea
              value={problemStatement}
              onChange={(event) => setField("problemStatement", event.target.value)}
              rows={8}
              placeholder={
                "Issue: HTML output ignores the formats argument.\n" +
                "Symptom: Formatted values appear correctly in CSV and RST outputs, but HTML shows full-precision values.\n" +
                "Expected: HTML output should respect user-supplied formatting functions."
              }
              className="field-underline resize-y text-base leading-relaxed"
              disabled={busy}
            />
          </Field>

          {/* ── Ground truth (optional, collapsed by default) ──── */}
          <div className="border-t border-hairline pt-6">
            <button
              type="button"
              onClick={() => setShowGroundTruth((open) => !open)}
              className="flex items-center gap-3 font-mono text-[11px] uppercase tracking-[0.14em] text-muted transition-colors hover:text-primary"
            >
              <span>{showGroundTruth ? "−" : "+"}</span>
              <span>Ground truth reference — optional</span>
            </button>

            {showGroundTruth && (
              <div className="mt-8 grid animate-rise grid-cols-1 gap-8 sm:grid-cols-2">
                {/* Ground truth file — relative path to the expected buggy file. */}
                <Field label="Ground truth file">
                  <input
                    type="text"
                    value={groundTruthFile}
                    onChange={(event) => setField("groundTruthFile", event.target.value)}
                    placeholder="io/ascii/html.py"
                    className="field-underline font-mono text-sm"
                    disabled={busy}
                    spellCheck={false}
                  />
                  <p className="pt-2 text-sm leading-relaxed text-muted">
                    Relative to the effective root. Used only to score the run — never shown to the
                    model.
                  </p>
                </Field>

                {/* Ground truth elements — comma-separated "kind: name" pairs. */}
                <Field label="Ground truth elements">
                  <input
                    type="text"
                    value={groundTruthElements}
                    onChange={(event) => setField("groundTruthElements", event.target.value)}
                    placeholder="function: write, class: HTML"
                    className="field-underline font-mono text-sm"
                    disabled={busy}
                    spellCheck={false}
                  />
                  <p className="pt-2 text-sm leading-relaxed text-muted">
                    Comma separated, each as <span className="font-mono">{"<kind>: <name>"}</span>.
                  </p>
                </Field>
              </div>
            )}
          </div>

          {/* ── Loading indicator (while stage 01 is running) ────── */}
          <StageStatus active={loadingStage === 1} label={loadingLabel} />

          {/* ── Submit row ─────────────────────────────────────── */}
          <div className="flex items-center gap-6 border-t border-hairline pt-8">
            <Button type="submit" disabled={!canSubmit}>
              Initialize Localization Pipeline →
            </Button>
            {/* Inline validation hint when the button is disabled. */}
            {!canSubmit && !busy && (
              <span className="font-mono text-[11px] text-muted">
                {problemStatement.trim()
                  ? sourceType === "github"
                    ? "Repository URL required"
                    : "At least one .py file required"
                  : "Bug report required"}
              </span>
            )}
          </div>
        </div>
      </div>
    </form>
  );
}
