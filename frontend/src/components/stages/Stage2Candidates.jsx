/**
 * Stage 02 — Candidate Resolution
 *
 * This stage determines which Python files from the repository should be
 * analysed further. In GitHub mode the LLM reads the full repo tree and
 * nominates the files most likely to be involved in the reported bug.
 * In static mode the uploaded files are used directly — no nomination step.
 *
 * The stage renders a two-column layout:
 *   Left  (5/12) — the resolved file tree (read-only, for context).
 *   Right (7/12) — the candidate set with per-file metadata and controls.
 */
import React, { useState } from "react";
import { useProject } from "../../context/ProjectContext";
import Badge from "../common/Badge";
import Button from "../common/Button";
import SectionHeading from "../common/SectionHeading";
import StageStatus from "../common/StageStatus";
import { basename, formatBytes } from "../../lib/pipeline";

export default function Stage2Candidates() {
  // ── Pipeline context ──────────────────────────────────────────────
  const {
    sourceType,
    treePreview,
    numPythonFiles,
    candidateFiles,
    candidatesRawOutput,
    candidateMeta,
    githubMeta,
    engine,
    loadingStage,
    loadingLabel,
    runCandidates,
    runFileReasoning,
  } = useProject();

  // ── Local UI state ────────────────────────────────────────────────
  const [topN, setTopN] = useState(engine?.default_top_n_candidates ?? 5); // max files to nominate
  const [showRaw, setShowRaw] = useState(false);                           // toggle raw LLM output

  // ── Derived values ────────────────────────────────────────────────
  const busy = loadingStage !== null;
  const isStatic = sourceType === "static";
  const hasCandidates = candidateFiles.length > 0;
  // Index candidate metadata by path for fast lookup in the list render.
  const metaByPath = new Map(candidateMeta.map((entry) => [entry.path, entry]));

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div>
      <SectionHeading
        step="02"
        title="Candidate Resolution"
        description={
          isStatic
            ? "The uploaded files are the candidate set. Repository-wide nomination is skipped."
            : "Gemini reads the repository tree and nominates the files worth reasoning about."
        }
        aside={
          githubMeta?.resolvedCommitSha && (
            <Badge title={githubMeta.resolvedCommitSha}>
              commit {githubMeta.resolvedCommitSha.slice(0, 7)}
            </Badge>
          )
        }
      />

      {/* Two-column layout: resolved tree (5 cols) + candidate set (7 cols) */}
      <div className="grid grid-cols-1 gap-16 lg:grid-cols-12">

        {/* ── Left column: resolved file tree ───────────────────── */}
        <div className="lg:col-span-5">
          <div className="mb-4 flex items-baseline justify-between border-b border-hairline pb-3">
            <span className="label-meta">Resolved structure</span>
            <span className="font-mono text-[11px] text-muted">
              {numPythonFiles} .py file{numPythonFiles === 1 ? "" : "s"}
            </span>
          </div>
          {/* Read-only tree preview — shows the filtered repo structure. */}
          <pre className="max-h-[32rem] overflow-auto bg-surface p-4 font-mono text-xs leading-relaxed text-secondary">
            {treePreview || "—"}
          </pre>
          <p className="mt-4 max-w-prose text-sm leading-relaxed text-muted">
            Test files and directories are filtered out before this point, matching the original
            pipeline's own preprocessing.
          </p>
        </div>

        {/* ── Right column: candidate set ───────────────────────── */}
        <div className="lg:col-span-7">
          <div className="mb-4 flex items-baseline justify-between border-b border-hairline pb-3">
            <span className="label-meta">Candidate set</span>
            {hasCandidates && (
              <span className="font-mono text-[11px] text-muted">
                {candidateFiles.length} selected
              </span>
            )}
          </div>

          {/* ── Empty state: prompt to nominate ──────────────────── */}
          {!hasCandidates && !busy && (
            <div className="space-y-8 py-6">
              <p className="max-w-prose text-base leading-relaxed text-secondary">
                Nothing nominated yet. Gemini will read the tree above alongside the bug report and
                propose the files most likely to need editing.
              </p>
              <div className="flex items-end gap-8">
                <div className="space-y-2">
                  <label className="label-meta">Return at most</label>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={topN}
                    onChange={(event) => setTopN(Number(event.target.value))}
                    className="field-underline w-20 font-mono text-sm"
                  />
                </div>
                <Button onClick={() => runCandidates(topN)} disabled={busy}>
                  Nominate Candidate Files →
                </Button>
              </div>
            </div>
          )}

          {/* ── Populated candidate list ─────────────────────────── */}
          {hasCandidates && (
            <ol className="animate-rise">
              {candidateFiles.map((path, index) => {
                const meta = metaByPath.get(path);
                return (
                  <li
                    key={path}
                    className="flex items-baseline gap-6 border-b border-hairline/70 py-4"
                  >
                    {/* Zero-padded rank index */}
                    <span className="w-8 shrink-0 font-mono text-xs tabular-nums text-muted">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <div className="min-w-0 flex-1">
                      {/* Filename (basename only) */}
                      <div className="truncate font-mono text-sm font-medium text-primary">
                        {basename(path)}
                      </div>
                      {/* Full relative path */}
                      <div className="truncate font-mono text-xs text-muted">{path}</div>
                    </div>
                    {/* Line count and file size from candidate metadata */}
                    <span className="shrink-0 font-mono text-xs text-muted">
                      {meta?.line_count != null ? `${meta.line_count} lines` : "—"}
                      {meta?.size_bytes != null && ` · ${formatBytes(meta.size_bytes)}`}
                    </span>
                  </li>
                );
              })}
            </ol>
          )}

          {/* ── Raw model output (collapsible) ───────────────────── */}
          {hasCandidates && candidatesRawOutput && (
            <div className="mt-6">
              <button
                type="button"
                onClick={() => setShowRaw((open) => !open)}
                className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted hover:text-primary"
              >
                {showRaw ? "− Hide" : "+ Show"} raw model output
              </button>
              {showRaw && (
                <pre className="mt-3 animate-rise overflow-auto border-l-2 border-hairline bg-surface p-4 font-mono text-xs leading-relaxed text-secondary">
                  {candidatesRawOutput}
                </pre>
              )}
            </div>
          )}

          {/* ── Loading indicator ────────────────────────────────── */}
          <StageStatus active={busy} label={loadingLabel} />

          {/* ── Action row ───────────────────────────────────────── */}
          {hasCandidates && (
            <div className="mt-8 flex flex-wrap items-center gap-6 border-t border-hairline pt-8">
              <Button onClick={runFileReasoning} disabled={busy}>
                Generate Isolated File Reasoning →
              </Button>
              {/* Re-run is only available in GitHub mode (static uploads are fixed). */}
              {!isStatic && (
                <button
                  type="button"
                  onClick={() => runCandidates(topN)}
                  disabled={busy}
                  className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted hover:text-accent disabled:cursor-not-allowed"
                >
                  Re-run nomination
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
