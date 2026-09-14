/**
 * Stage 04 — File Ranking & Boundary
 *
 * A single model call reads every reasoning paragraph produced in stage 03
 * and reorders the candidate files by how likely they are to contain the
 * bug. The top-k files (default 3) are "promoted" to element-level
 * localization; the rest are dimmed but kept for context.
 *
 * The stage also displays an optional ground-truth evaluation panel when a
 * reference file was supplied in the manifest.
 */
import React, { useState } from "react";
import { useProject } from "../../context/ProjectContext";
import Badge from "../common/Badge";
import Button from "../common/Button";
import SectionHeading from "../common/SectionHeading";
import StageStatus from "../common/StageStatus";
import ReasoningProse from "../common/ReasoningProse";
import { basename } from "../../lib/pipeline";

/**
 * GroundTruthPanel — inline evaluation summary.
 *
 * Shown only when a ground-truth file was provided in stage 01. Displays
 * the rank the model assigned it and the Hit@k metrics.
 */
function GroundTruthPanel({ groundTruthFile, fileEval }) {
  if (!groundTruthFile || !fileEval) return null;

  const { groundTruthRank, hitAtK } = fileEval;
  const found = groundTruthRank != null;

  return (
    <div className="mb-12 border-l-2 border-accent bg-accent-tint/40 py-5 pl-5 pr-4">
      <div className="label-meta mb-3">Ground truth evaluation</div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <span className="break-all font-mono text-sm text-primary">{groundTruthFile}</span>
        <Badge variant={found ? "accent" : "error"}>
          {found ? `rank #${groundTruthRank}` : "not ranked"}
        </Badge>
        {hitAtK &&
          Object.entries(hitAtK).map(([k, hit]) => (
            <span key={k} className="font-mono text-xs text-secondary">
              Hit@{k}{" "}
              <span className={hit ? "text-accent" : "text-muted"}>{hit ? "PASS" : "MISS"}</span>
            </span>
          ))}
      </div>
    </div>
  );
}

export default function Stage4FileRank() {
  // ── Pipeline context ──────────────────────────────────────────────
  const {
    fileRanking,
    fileReasoning,
    fileEval,
    groundTruthFile,
    engine,
    loadingStage,
    loadingLabel,
    runFileRanking,
    runElementReasoning,
  } = useProject();

  // ── Local UI state ────────────────────────────────────────────────
  const defaultTopK = engine?.default_top_k_files ?? 3;
  const [topK, setTopK] = useState(defaultTopK);            // how many files to promote
  const [expanded, setExpanded] = useState(null);            // path of the expanded reasoning row

  // ── Derived values ────────────────────────────────────────────────
  const busy = loadingStage !== null;
  const ranked = fileRanking || [];

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div>
      <SectionHeading
        step="04"
        title="File Ranking & Boundary"
        description="Files reordered by their reasoning alone. The top three are promoted to element-level localization."
      />

      {/* ── Loading indicator ────────────────────────────────────── */}
      <StageStatus active={busy} label={loadingLabel} />

      {/* ── Empty state: prompt to rank ──────────────────────────── */}
      {ranked.length === 0 && !busy && (
        <div className="space-y-8 py-6">
          <p className="max-w-prose text-base leading-relaxed text-secondary">
            One model call now reads every reasoning paragraph at once and orders the candidates.
          </p>
          <Button onClick={runFileRanking} disabled={busy}>
            Rank Files by Reasoning →
          </Button>
        </div>
      )}

      {/* ── Populated: ranked file list ──────────────────────────── */}
      {ranked.length > 0 && (
        <div className="animate-rise">
          {/* Ground truth evaluation (only shown when a reference file was supplied). */}
          <GroundTruthPanel groundTruthFile={groundTruthFile} fileEval={fileEval} />

          <ol>
            {ranked.map((path, index) => {
              const rank = index + 1;
              const promoted = rank <= topK;
              const isGroundTruth = groundTruthFile && path === groundTruthFile;
              const open = expanded === path;

              return (
                <React.Fragment key={path}>
                  <li
                    className={`border-b border-hairline py-6 transition-opacity ${
                      promoted ? "" : "opacity-50"
                    }`}
                  >
                    <div className="flex items-baseline gap-8">
                      {/* Large rank numeral — accent for promoted, muted otherwise */}
                      <span
                        className={`numeral shrink-0 ${promoted ? "text-primary" : "text-muted"}`}
                      >
                        {String(rank).padStart(2, "0")}
                      </span>

                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-3">
                          <span className="break-all font-mono text-sm font-medium text-primary">
                            {basename(path)}
                          </span>
                          {isGroundTruth && <Badge variant="accent">ground truth</Badge>}
                        </div>
                        <div className="break-all pt-1 font-mono text-xs text-muted">{path}</div>
                      </div>

                      {/* Toggle to peek at the reasoning that drove this rank */}
                      {fileReasoning?.[path] && (
                        <button
                          type="button"
                          onClick={() => setExpanded(open ? null : path)}
                          className="shrink-0 font-mono text-[11px] uppercase tracking-[0.14em] text-muted hover:text-accent"
                        >
                          {open ? "− Reasoning" : "+ Reasoning"}
                        </button>
                      )}
                    </div>

                    {/* Expanded reasoning preview — accentCausal off to avoid
                        visual clutter in a compact row. */}
                    {open && (
                      <div className="mt-5 animate-rise border-l-2 border-hairline pl-6">
                        <ReasoningProse text={fileReasoning[path]} accentCausal={false} />
                      </div>
                    )}
                  </li>

                  {/* Divider between promoted and non-promoted files */}
                  {rank === topK && ranked.length > topK && (
                    <li aria-hidden className="flex items-center gap-4 py-6">
                      <span className="h-px flex-1 bg-hairline" />
                      <span className="text-center font-mono text-[10px] uppercase tracking-[0.18em] text-muted">
                        Promoted to element localization · top {topK} captures &gt;85% SWE-bench recall
                      </span>
                      <span className="h-px flex-1 bg-hairline" />
                    </li>
                  )}
                </React.Fragment>
              );
            })}
          </ol>

          {/* ── Action row: top-k control + proceed button ──────── */}
          <div className="mt-10 flex flex-wrap items-end gap-8 border-t border-hairline pt-8">
            <div className="space-y-2">
              <label className="label-meta">Promote top</label>
              <input
                type="number"
                min={1}
                max={Math.min(10, ranked.length)}
                value={topK}
                onChange={(event) => setTopK(Number(event.target.value))}
                disabled={busy}
                className="field-underline w-20 font-mono text-sm"
              />
            </div>
            <Button onClick={() => runElementReasoning(topK)} disabled={busy}>
              Extract & Reason Over Elements →
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
