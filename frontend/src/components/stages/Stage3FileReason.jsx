/**
 * Stage 03 — Isolated File Reasoning
 *
 * Each candidate file is evaluated independently against the failure
 * symptom. The model writes a short analytical paragraph describing what
 * the file does and how it might relate to the bug — without seeing any
 * other file's reasoning. This isolation prevents one explanation from
 * biasing another.
 *
 * Once all files have been evaluated the reasoning paragraphs are displayed
 * as readable prose, ready for the user to review before ranking.
 */
import React from "react";
import { useProject } from "../../context/ProjectContext";
import Badge from "../common/Badge";
import Button from "../common/Button";
import SectionHeading from "../common/SectionHeading";
import StageStatus from "../common/StageStatus";
import ReasoningProse from "../common/ReasoningProse";
import { basename } from "../../lib/pipeline";

export default function Stage3FileReason() {
  // ── Pipeline context ──────────────────────────────────────────────
  const {
    candidateFiles,
    candidateMeta,
    fileReasoning,
    loadingStage,
    loadingLabel,
    runFileReasoning,
    runFileRanking,
  } = useProject();

  // ── Derived values ────────────────────────────────────────────────
  const busy = loadingStage !== null;
  // fileReasoning is an object keyed by file path; entries gives us [path, reasoning] pairs.
  const entries = Object.entries(fileReasoning || {});
  // Index candidate metadata by path for quick lookup when rendering.
  const metaByPath = new Map(candidateMeta.map((entry) => [entry.path, entry]));
  // "complete" means at least one file has been reasoned over.
  const complete = entries.length > 0;

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div>
      <SectionHeading
        step="03"
        title="Isolated Reasoning"
        description="Each file is evaluated independently against the failure symptom, so no file's explanation is coloured by what the model just read about another."
        aside={
          complete && (
            <span className="font-mono text-[11px] text-muted">
              {entries.length} of {candidateFiles.length} evaluated
            </span>
          )
        }
      />

      {/* ── Loading indicator (runs in parallel per-file) ────────── */}
      <StageStatus active={busy} label={loadingLabel} />

      {/* ── Empty state: prompt to start reasoning ───────────────── */}
      {!complete && !busy && (
        <div className="space-y-8 py-6">
          <p className="max-w-prose text-base leading-relaxed text-secondary">
            {candidateFiles.length} candidate file{candidateFiles.length === 1 ? "" : "s"} are ready
            for reasoning. Each gets its own model call, run in parallel.
          </p>
          <Button onClick={runFileReasoning} disabled={busy}>
            Generate Isolated File Reasoning →
          </Button>
        </div>
      )}

      {/* ── Populated: per-file reasoning articles ───────────────── */}
      {complete && (
        <div className="animate-rise">
          {entries.map(([path, reasoning]) => {
            const meta = metaByPath.get(path);
            // Detect model-level errors — these are returned as plain text
            // prefixed with "Error during reasoning:".
            const failed = reasoning.startsWith("Error during reasoning:");

            return (
              <article
                key={path}
                className="grid grid-cols-1 gap-8 border-b border-hairline py-10 lg:grid-cols-12"
              >
                {/* ── File header (3/12) ─────────────────────────── */}
                <header className="space-y-3 lg:col-span-3">
                  <h3 className="break-all font-mono text-sm font-medium text-primary">
                    {basename(path)}
                  </h3>
                  <p className="break-all font-mono text-xs text-muted">{path}</p>
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <Badge variant={failed ? "error" : "default"}>
                      {failed ? "Failed" : "Complete"}
                    </Badge>
                    {meta?.line_count != null && (
                      <span className="font-mono text-[11px] text-muted">
                        {meta.line_count} lines
                      </span>
                    )}
                  </div>
                </header>

                {/* ── Reasoning prose (9/12) ─────────────────────── */}
                <div className="min-w-0 lg:col-span-9">
                  <ReasoningProse text={reasoning} />
                </div>
              </article>
            );
          })}

          {/* ── Proceed to ranking ───────────────────────────────── */}
          <div className="flex flex-wrap items-center gap-6 pt-10">
            <Button onClick={runFileRanking} disabled={busy}>
              Proceed to Reasoning-Guided Ranking →
            </Button>
            <p className="max-w-prose font-mono text-[11px] leading-relaxed text-muted">
              Ranking reads only the paragraphs above — never the source again.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
