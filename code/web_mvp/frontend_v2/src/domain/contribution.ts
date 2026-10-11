import type { ContributionFields, MaterialContextSnapshot } from "./events";

export function parseMaterialContext(value: unknown): MaterialContextSnapshot | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const x = value as Record<string, unknown>;
  const setId = x.material_set_id ?? x.materialSetId;
  const revision = x.material_context_revision ?? x.materialContextRevision;
  const catalog = x.attachment_catalog ?? x.attachmentCatalog;
  const count = (n: unknown): n is number => typeof n === "number" && Number.isSafeInteger(n) && n >= 0;
  if (typeof setId !== "string" || !count(revision) || !Array.isArray(catalog) || (x.catalog_is_evidence ?? x.catalogIsEvidence) !== false) return undefined;
  const attachmentCatalog: MaterialContextSnapshot["attachmentCatalog"] = [];
  for (const raw of catalog.slice(0, 6)) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
    const item = raw as Record<string, unknown>;
    const coverage = item.coverage as Record<string, unknown> | undefined;
    const attachmentId = item.attachment_id ?? item.attachmentId;
    const versionId = item.version_id ?? item.versionId;
    if (!coverage || typeof attachmentId !== "string" || typeof item.title !== "string" || typeof versionId !== "string") return undefined;
    const authorizedSpanCount = coverage.authorized_span_count ?? coverage.authorizedSpanCount;
    const authorizedCharacters = coverage.authorized_characters ?? coverage.authorizedCharacters;
    const providedSpanCount = coverage.provided_span_count ?? coverage.providedSpanCount;
    const providedCharacters = coverage.provided_characters ?? coverage.providedCharacters;
    if (!count(authorizedSpanCount) || !count(authorizedCharacters) || !count(providedSpanCount) || !count(providedCharacters) || providedSpanCount > authorizedSpanCount || providedCharacters > authorizedCharacters || !["selected_spans", "whole_document"].includes(String(coverage.selection))) return undefined;
    attachmentCatalog.push({ attachmentId, title: item.title, versionId, coverage: {
      authorizedSpanCount, authorizedCharacters, providedSpanCount, providedCharacters,
      completeRead: (coverage.complete_read ?? coverage.completeRead) === true && providedSpanCount === authorizedSpanCount && providedCharacters === authorizedCharacters,
      selection: coverage.selection as "selected_spans" | "whole_document",
    } });
  }
  return { materialSetId: setId, materialContextRevision: revision, attachmentCatalog,
    catalogTruncated: (x.catalog_truncated ?? x.catalogTruncated) === true || catalog.length > 6, catalogIsEvidence: false };
}

export function parseContributionFields(value: unknown): ContributionFields {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const x = value as Record<string, unknown>;
  const string = (snake: string, camel: string) => typeof (x[snake] ?? x[camel]) === "string" ? (x[snake] ?? x[camel]) as string : undefined;
  const analysisKind = string("analysis_kind", "analysisKind");
  const topicId = string("topic_id", "topicId");
  const type = string("proposal_type", "proposalType");
  const revision = x.analysis_basis_revision ?? x.analysisBasisRevision;
  const facts = x.supporting_facts ?? x.supportingFacts;
  const assumptions = Array.isArray(x.assumptions) ? x.assumptions.filter((v): v is string => typeof v === "string").slice(0, 8) : undefined;
  const materialContext = parseMaterialContext(x.material_context ?? x.materialContext);
  return {
    ...(analysisKind ? { analysisKind } : {}), ...(topicId ? { topicId } : {}),
    ...(type === "grounded_fact" || type === "professional_proposal" || type === "external_claim" ? { proposalType: type } : {}),
    ...(typeof revision === "number" && Number.isSafeInteger(revision) && revision >= 0 ? { analysisBasisRevision: revision } : {}),
    ...(Array.isArray(facts) ? { supportingFacts: facts.slice(0, 8).flatMap((fact) => {
      if (!fact || typeof fact !== "object") return [];
      const f = fact as Record<string, unknown>;
      const sourceRef = f.source_ref ?? f.sourceRef;
      return typeof f.text === "string" && typeof sourceRef === "string" ? [{ text: f.text, sourceRef }] : [];
    }) } : {}), ...(assumptions ? { assumptions } : {}), ...(materialContext ? { materialContext } : {}),
  };
}
