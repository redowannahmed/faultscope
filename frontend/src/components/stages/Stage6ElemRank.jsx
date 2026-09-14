/**
 * Stage 06 — Suspicious Element Ranking
 *
 * The final ordering step. For each promoted file the model reads every
 * element's causal reasoning paragraph and produces a ranked list ordered
 * by how directly the described behaviour explains the reported failure.
 *
 * The top-ranked element in each file becomes the primary suspect. Users
 * can click through to stage 07 to inspect the source code alongside the
 * reasoning that nominated it.
 */
import React, { useMemo } from "react";
import { useProject } from "../../context/ProjectContext";
import Badge from "../common/Badge";
import Button from "../common/Button";
import SectionHeading from "../common/SectionHeading";
import StageStatus from "../common/StageStatus";
import { basename, fileKeyIndex, parseElementKey, pathForFileKey } from "../../lib/pipeline";

// Human-readable labels for the first three positions.
const RANK_LABELS = ["Most suspicious", "Secondary", "Tertiary"];

export default function Stage6ElemRank() {
  // ── Pipeline context ──────────────────────────────────────────────
  const {
    elementRanking,
    elementReasoning,
    elementEval,
    fileRanking,
    loadingStage,
    loadingLabel,
    runElementRanking,
    inspectElement,
  } = useProject();

  // ── Derived: sorted ranking keys ──────────────────────────────────
  const rankingKeys = useMemo(
    () =>
      Object.keys(elementRanking || {}).sort(
        (a, b) => (fileKeyIndex(a) ?? 0) - (fileKeyIndex(b) ?? 0),
      ),
    [elementRanking],
  );

  // ── Derived values ────────────────────────────────────────────────
  const busy = loadingStage !== null;
  const hasRanking = rankingKeys.length > 0;

  // Build a map of ground-truth hits for quick lookup in the ranked list.
  // elementEval is keyed by ground-truth string:
  // { "function: write": { found, file_key, position } }
  const groundTruthHits = useMemo(() => {
    const map = new Map();
    Object.entries(elementEval || {}).forEach(([identifier, result]) => {
      if (result?.found) map.set(identifier, result);
    });
    return map;
  }, [elementEval]);

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div>
      <SectionHeading
        step="06"
        title="Suspicious Element Ranking"
        description="The final ordering within each promoted file, argued from the reasoning rather than the code."
        aside={
          // Show ground-truth evaluation badges when available.
          elementEval && (
            <div className="flex flex-wrap items-center gap-3">
              {Object.entries(elementEval).map(([identifier, result]) => (
                <Badge key={identifier} variant={result.found ? "accent" : "error"}>
                  {identifier} {result.found ? `#${result.position}` : "not found"}
                </Badge>
              ))}
            </div>
          )
        }
      />

      {/* ── Loading indicator ────────────────────────────────────── */}
      <StageStatus active={busy} label={loadingLabel} />

      {/* ── Empty state: prompt to rank ──────────────────────────── */}
      {!hasRanking && !busy && (
        <div className="space-y-8 py-6">
          <p className="max-w-prose text-base leading-relaxed text-secondary">
            One model call per promoted file orders its elements by how directly their described
            behaviour explains the reported failure.
          </p>
          <Button onClick={runElementRanking} disabled={busy}>
            Rank Elements by Causal Relevance →
          </Button>
        </div>
      )}

      {/* ── Populated: per-file ranked element lists ─────────────── */}
      {hasRanking && (
        <div className="animate-rise space-y-16">
          {rankingKeys.map((key) => {
            const index = fileKeyIndex(key);
            const path = pathForFileKey(key, fileRanking);
            // Build the key used to look up element reasoning for this file.
            const reasoningKey = `file${index}_elements_reasoning`;
            const ranked = elementRanking[key] || [];

            return (
              <section key={key}>
                {/* File header with index badge and full path */}
                <div className="mb-6 flex flex-wrap items-baseline justify-between gap-4 border-b border-hairline pb-3">
                  <div className="flex items-baseline gap-3">
                    <span className="font-mono text-[11px] text-muted">
                      File {String(index).padStart(2, "0")}
                    </span>
                    <span className="font-mono text-sm font-medium text-primary">
                      {path ? basename(path) : key}
                    </span>
                  </div>
                  <span className="break-all font-mono text-[11px] text-muted">{path}</span>
                </div>

                {ranked.length === 0 ? (
                  <p className="py-4 text-base text-muted">
                    No elements were ranked for this file.
                  </p>
                ) : (
                  <ol>
                    {ranked.map((identifier, position) => {
                      const { kind, name } = parseElementKey(identifier);
                      const hit = groundTruthHits.get(identifier);
                      const isTop = position === 0;
                      const hasReasoning = Boolean(
                        elementReasoning?.[reasoningKey]?.[identifier],
                      );

                      return (
                        <li
                          key={`${key}-${identifier}`}
                          className="flex flex-wrap items-baseline gap-x-8 gap-y-3 border-b border-hairline/70 py-5"
                        >
                          {/* Large rank numeral — accent for the top element */}
                          <span
                            className={`numeral shrink-0 ${isTop ? "text-accent" : "text-muted"}`}
                          >
                            {String(position + 1).padStart(2, "0")}
                          </span>

                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-3">
                              <span
                                className={`font-mono text-sm ${
                                  isTop ? "font-medium text-primary" : "text-primary"
                                }`}
                              >
                                {name}
                              </span>
                              <span className="font-mono text-[10px] uppercase tracking-wider text-muted">
                                {kind}
                              </span>
                              {hit && <Badge variant="accent">Ground truth match</Badge>}
                            </div>
                            <div className="pt-1 font-mono text-[11px] text-muted">
                              {RANK_LABELS[position] || `Rank ${position + 1}`} · {identifier}
                            </div>
                          </div>

                          {/* Link to inspect source + reasoning in stage 07 */}
                          {path && hasReasoning && (
                            <button
                              type="button"
                              onClick={() =>
                                inspectElement({ file: path, identifier, fileKey: reasoningKey })
                              }
                              className="link-quiet shrink-0"
                            >
                              Inspect code &amp; causal chain →
                            </button>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                )}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
