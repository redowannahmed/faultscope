/**
 * Stage 07 — Localized Code & Causal Diagnostic
 *
 * The final stage of the pipeline. Shows the nominated element's source
 * code alongside the reasoning that led to its selection. Users can
 * compare the reasoning-guided pick against a surface-similarity proxy
 * (element whose name most resembles the bug report) to see whether
 * reasoning overturned or confirmed the obvious candidate.
 *
 * Layout:
 *   Left column  (7/12) — CodeViewer with line numbers.
 *   Right column (5/12) — reasoning prose + comparison panel.
 *
 * A full diagnostics export (JSON) is available at the bottom.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useProject } from "../../context/ProjectContext";
import * as api from "../../api/client";
import Badge from "../common/Badge";
import Button from "../common/Button";
import CodeViewer from "../common/CodeViewer";
import SectionHeading from "../common/SectionHeading";
import ReasoningProse from "../common/ReasoningProse";
import {
  basename,
  hasCausalSignal,
  parseElementKey,
  rankingKeyForReasoningKey,
  surfaceSimilarityPick,
} from "../../lib/pipeline";

/**
 * The comparison panel from the RGFL paper's Figure 1, computed rather than
 * asserted.
 *
 * The left half is a surface-similarity pick — the element whose *name* reads
 * most like the bug report — standing in for a name-matching localizer. The
 * right half is what reasoning-guided ranking actually returned. This is an
 * illustration of the two selection criteria, not a second pipeline run, and
 * the copy says so.
 */
/**
 * ComparisonPanel — side-by-side surface-similarity vs. causal pick.
 *
 * Renders two columns with left-border accent:
 *   Left  — the element whose *name* most resembles the bug report
 *           (computed via `surfaceSimilarityPick`, a lexical proxy).
 *   Right — the element FaultScope actually ranked first.
 *
 * The copy below the columns explains whether the two criteria agree or
 * disagree. This is an illustration, not a second pipeline run.
 */
function ComparisonPanel({ identifiers, problemStatement, causalPick, elementReasoning }) {
  const surfacePick = useMemo(
    () => surfaceSimilarityPick(identifiers, problemStatement),
    [identifiers, problemStatement],
  );

  if (!surfacePick || !causalPick) return null;

  const agree = surfacePick === causalPick;

  /** Single comparison column with left-border accent. */
  const Column = ({ label, note, identifier, accent }) => (
    <div className={`space-y-3 ${accent ? "border-l-2 border-accent pl-5" : "border-l-2 border-hairline pl-5"}`}>
      <div
        className={`font-mono text-[11px] uppercase tracking-[0.14em] ${
          accent ? "text-accent" : "text-muted"
        }`}
      >
        {label}
      </div>
      <div className="font-mono text-sm text-primary">{identifier}</div>
      <p className="max-w-prose text-sm leading-relaxed text-secondary">{note}</p>
    </div>
  );

  return (
    <section className="mt-16 border-t border-hairline pt-10">
      <h3 className="label-meta mb-8">Selection criteria compared</h3>
      <div className="grid grid-cols-1 gap-10 md:grid-cols-2">
        <Column
          label="Surface similarity"
          identifier={surfacePick}
          note="What a name-matching localizer reaches for: the element whose identifier most resembles the wording of the report."
        />
        <Column
          accent
          label="Causal match · FaultScope"
          identifier={causalPick}
          note={
            hasCausalSignal(elementReasoning)
              ? "Ranked first because its reasoning describes behaviour that bypasses or breaks the expected path — not because its name matched."
              : "Ranked first on the strength of its reasoning paragraph rather than any lexical resemblance to the report."
          }
        />
      </div>
      <p className="mt-8 max-w-prose font-mono text-[11px] leading-relaxed text-muted">
        {agree
          ? "Both criteria land on the same element here, so reasoning confirmed the obvious candidate rather than overturning it."
          : "The two criteria disagree — the case the RGFL paper builds its Figure 1 around."}{" "}
        The left column is a lexical proxy computed in the browser, not a second localizer run.
      </p>
    </section>
  );
}

export default function Stage7Inspector() {
  // ── Pipeline context ──────────────────────────────────────────────
  const state = useProject();
  const {
    activeElement,
    elementReasoning,
    elementRanking,
    problemStatement,
    projectId,
    goToStage,
  } = state;

  // ── Local UI state ────────────────────────────────────────────────
  const [source, setSource] = useState(null);    // fetched source code
  const [loading, setLoading] = useState(false); // fetch in progress
  const [error, setError] = useState(null);      // fetch error message

  // ── Derived: active element metadata ──────────────────────────────
  const identifier = activeElement?.identifier;
  const filePath = activeElement?.file;
  const fileKey = activeElement?.fileKey;

  // ── Fetch element source from the backend ─────────────────────────
  // Resets on every new element selection; cleans up on unmount.
  useEffect(() => {
    if (!projectId || !filePath || !identifier) return undefined;

    let cancelled = false;
    setLoading(true);
    setError(null);
    setSource(null);

    api
      .getElementSource(projectId, filePath, identifier)
      .then((result) => {
        if (!cancelled) setSource(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [projectId, filePath, identifier]);

  // ── Derived: reasoning + ranking position ─────────────────────────
  const reasoning = fileKey ? elementReasoning?.[fileKey]?.[identifier] || "" : "";
  const rankingKey = fileKey ? rankingKeyForReasoningKey(fileKey) : null;
  const rankedIdentifiers = rankingKey ? elementRanking?.[rankingKey] || [] : [];
  const rankPosition = rankedIdentifiers.indexOf(identifier) + 1;

  // ── Diagnostics export ────────────────────────────────────────────
  // Bundles every pipeline stage's output into a single JSON file.
  const handleExport = useCallback(() => {
    const diagnostics = {
      exported_at: new Date().toISOString(),
      project_id: projectId,
      source_type: state.sourceType,
      repository: state.githubMeta
        ? {
            owner: state.githubMeta.owner,
            repo: state.githubMeta.repo,
            resolved_ref: state.githubMeta.resolvedRef,
            resolved_commit_sha: state.githubMeta.resolvedCommitSha,
          }
        : null,
      problem_statement: state.problemStatement,
      ground_truth: {
        file: state.groundTruthFile || null,
        elements: state.groundTruthElements || null,
      },
      candidates: state.candidateFiles,
      file_reasoning: state.fileReasoning,
      file_ranking: state.fileRanking,
      file_ranking_eval: state.fileEval,
      element_reasoning: state.elementReasoning,
      element_ranking: state.elementRanking,
      element_ranking_eval: state.elementEval,
      inspected_element: activeElement
        ? {
            ...activeElement,
            rank_position: rankPosition > 0 ? rankPosition : null,
            start_line: source?.start_line ?? null,
            end_line: source?.end_line ?? null,
            reasoning,
            code: source?.code ?? null,
          }
        : null,
    };

    const blob = new Blob([JSON.stringify(diagnostics, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `faultscope-${projectId?.slice(0, 8) || "session"}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }, [state, projectId, activeElement, source, reasoning, rankPosition]);

  // ── Empty state: no element selected ──────────────────────────────
  if (!activeElement) {
    return (
      <div>
        <SectionHeading
          step="07"
          title="Localized Code & Causal Diagnostic"
          description="Nothing selected yet."
        />
        <p className="max-w-prose text-base leading-relaxed text-secondary">
          Choose an element from the ranking to read its source next to the reasoning that
          nominated it.
        </p>
        <button type="button" onClick={() => goToStage(6)} className="link-quiet mt-6 inline-block">
          ← Back to element ranking
        </button>
      </div>
    );
  }

  // ── Derived: element display info ─────────────────────────────────
  const { kind, name } = parseElementKey(identifier);
  const lineRange = source ? `lines ${source.start_line}–${source.end_line}` : "";

  // ── Render: code + rationale side by side ─────────────────────────
  return (
    <div>
      <SectionHeading
        step="07"
        title="Localized Code & Causal Diagnostic"
        description="The nominated element, shown beside the argument for why it is responsible."
        aside={
          <div className="flex flex-wrap items-center gap-3">
            {rankPosition > 0 && <Badge variant="accent">rank #{rankPosition}</Badge>}
            <Badge>{kind}</Badge>
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-12 lg:grid-cols-12">
        {/* ── Left column: source code (7/12) ────────────────────── */}
        <div className="min-w-0 lg:col-span-7">
          {/* File path + line range header */}
          <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3 border-b border-hairline pb-3">
            <span className="break-all font-mono text-xs text-secondary">
              {filePath}
              {lineRange && <span className="text-muted"> · {lineRange}</span>}
            </span>
            <span className="font-mono text-[11px] text-muted">{name}</span>
          </div>

          {/* Loading: sweep animation across the full width */}
          {loading && (
            <div className="relative h-px w-full overflow-hidden bg-hairline">
              <div className="absolute inset-y-0 w-1/4 animate-sweep bg-accent" />
            </div>
          )}

          {/* Error: red left-border banner */}
          {error && (
            <p className="border-l-2 border-status-error py-3 pl-4 text-sm leading-relaxed text-status-error">
              {error}
            </p>
          )}

          {/* Code viewer + duplicate-identifier warning */}
          {source && (
            <>
              <CodeViewer
                code={source.code}
                startLine={source.start_line}
                filePath={`${basename(filePath)} · ${kind} ${name}`}
              />
              {source.match_count > 1 && (
                <p className="mt-3 font-mono text-[11px] leading-relaxed text-muted">
                  {source.match_count} elements in this file share the identifier{" "}
                  <span className="text-secondary">{identifier}</span>; the first is shown. Element
                  keys are names, not fully-qualified paths — a quirk inherited from the original
                  extraction code.
                </p>
              )}
            </>
          )}
        </div>

        {/* ── Right column: rationale (5/12) ─────────────────────── */}
        <div className="min-w-0 lg:col-span-5">
          <h3 className="label-meta mb-5">Why this element is responsible</h3>
          <ReasoningProse text={reasoning} />
        </div>
      </div>

      {/* ── Comparison panel: surface similarity vs. causal pick ── */}
      <ComparisonPanel
        identifiers={rankedIdentifiers.length ? rankedIdentifiers : [identifier]}
        problemStatement={problemStatement}
        causalPick={rankedIdentifiers[0] || identifier}
        elementReasoning={reasoning}
      />

      {/* ── Action row: export + back link ───────────────────────── */}
      <div className="mt-12 flex flex-wrap items-center gap-6 border-t border-hairline pt-8">
        <Button variant="secondary" onClick={handleExport}>
          Export Diagnostics as JSON
        </Button>
        <button type="button" onClick={() => goToStage(6)} className="link-quiet">
          ← Back to element ranking
        </button>
      </div>
    </div>
  );
}
