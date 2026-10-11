import { randomUUID } from "node:crypto";

export const MAX_QUOTE_CHARACTERS = 300;
export const MAX_CATALOG_ENTRIES = 2048;
export const MAX_CATALOG_CHARACTERS = 160000;

function invalid(message) {
  return Object.assign(new Error(message), { code: "invalid_agent_action" });
}

function identity(value) {
  return JSON.stringify(Object.fromEntries(Object.entries(value).filter(([key]) => key !== "quote")
    .sort(([a], [b]) => a.localeCompare(b))));
}

// Exact slices only. Each sentence/newline is a separately selectable quote;
// long sentences are split at
// Unicode code-point boundaries. No whitespace, punctuation or wording changes.
export function exactQuoteSlices(text) {
  if (typeof text !== "string" || !text.trim()) throw invalid("quote source is empty");
  const sentences = text.match(/[^。！？!?\n]+[。！？!?\n]*|[。！？!?\n]+/gu) ?? [];
  const slices = [];
  for (const sentence of sentences) {
    let pending = "";
    for (const point of sentence) {
      if (pending.length + point.length > MAX_QUOTE_CHARACTERS) {
        slices.push(pending); pending = "";
      }
      pending += point;
    }
    if (pending) slices.push(pending);
  }
  if (slices.join("") !== text) throw invalid("quote slicing did not preserve input");
  return slices;
}

function contextIdentity(context) {
  return JSON.stringify([context.request_metadata?.topic_id, context.request_metadata?.analysis_basis_revision,
    context.request_metadata?.input_context ?? null]);
}

// Registration and rendering share the frozen transcript collection. Preserve
// duplicate identities here so registration still rejects conflicting versions.
export function participationTranscriptSources(context) {
  return [
    ...(context.request_metadata?.automatic_direct === true ? context.retrieval_paragraphs : []),
    ...context.context_paragraphs,
    ...context.new_paragraphs,
  ];
}

export function createParticipationQuoteCatalog(context, sessionId = null) {
  const catalog = { nonce: randomUUID().replaceAll("-", "").slice(0, 16), entries: new Map(),
    sources: new Map(), characters: 0, context_identity: contextIdentity(context), session_id: sessionId };
  registerTranscriptQuotes(catalog, participationTranscriptSources(context));
  registerDocumentQuotes(catalog, context.document_evidence);
  return catalog;
}

function register(catalog, kind, source, text) {
  const sourceId = kind === "transcript" ? source.id : source.source_id;
  const key = `${kind}:${sourceId}`;
  const prior = catalog.sources.get(key);
  const fingerprint = kind === "transcript" ? JSON.stringify([source.id, source.revision, text]) : identity(source);
  if (prior) {
    if (prior.fingerprint !== fingerprint || !(text.includes(prior.text) || kind === "document" && prior.text.includes(text))) {
      throw invalid("quote source revision changed");
    }
    return prior.ids;
  }
  if (catalog.characters + text.length > MAX_CATALOG_CHARACTERS) throw invalid("quote catalog budget exceeded");
  const slices = exactQuoteSlices(text);
  if (catalog.entries.size + slices.length > MAX_CATALOG_ENTRIES) throw invalid("quote catalog budget exceeded");
  const ids = [];
  let offset = 0;
  for (const quote of slices) {
    const id = `${kind === "transcript" ? "t" : "d"}-${catalog.nonce}-${catalog.entries.size + 1}`;
    const item = { id, kind, source_id: sourceId, quote, start: offset, end: offset + quote.length,
      fingerprint, source_text: text };
    catalog.entries.set(id, item); ids.push(id); offset = item.end;
  }
  catalog.sources.set(key, { fingerprint, text, ids });
  catalog.characters += text.length;
  return ids;
}

function registerBatch(catalog, sources, kind) {
  const next = { ...catalog, entries: new Map(catalog.entries), sources: new Map(catalog.sources) };
  const result = sources.map((source) => ({ ...source,
    quote_ids: register(next, kind, source, kind === "transcript" ? source.text : source.quote) }));
  catalog.entries = next.entries; catalog.sources = next.sources; catalog.characters = next.characters;
  return result;
}

export function registerTranscriptQuotes(catalog, paragraphs) {
  return registerBatch(catalog, paragraphs, "transcript");
}

export function registerDocumentQuotes(catalog, sources) {
  if (catalog.session_id && sources.some((source) => source.source_type === "meeting_attachment"
    && source.meeting_id !== catalog.session_id)) throw invalid("quote source escaped its meeting scope");
  return registerBatch(catalog, sources, "document");
}

function rendered(catalog, source, kind) {
  const key = `${kind}:${kind === "transcript" ? source.id : source.source_id}`;
  const known = catalog.sources.get(key);
  if (!known) throw invalid("quote source was not registered");
  const { text: _text, quote: _quote, quote_ids: _ids, ...metadata } = source;
  return { ...metadata, quotes: known.ids.map((id) => ({ quote_id: id, text: catalog.entries.get(id).quote })) };
}

export function renderTranscriptQuotes(catalog, paragraphs) {
  return paragraphs.map((source) => rendered(catalog, source, "transcript"));
}

export function renderDocumentQuotes(catalog, sources) {
  return sources.map((source) => rendered(catalog, source, "document"));
}

function selectionInvalid(reason) {
  return Object.assign(invalid(`quote selection rejected: ${reason}`), { quote_selection_code: `quote_selection_${reason}` });
}

function selection(catalog, ids) {
  if (!Array.isArray(ids)) throw selectionInvalid("obsolete_contract");
  if (ids.length > 4) throw selectionInvalid("budget");
  if (new Set(ids).size !== ids.length) throw selectionInvalid("duplicate");
  return ids.map((id) => {
    const entry = typeof id === "string" && catalog.entries.get(id);
    if (!entry) throw selectionInvalid("unknown_id");
    return entry;
  });
}

function documentReferences(documents) {
  const groups = new Map();
  for (const entry of documents) {
    const group = groups.get(entry.source_id) ?? [];
    group.push(entry); groups.set(entry.source_id, group);
  }
  return [...groups.values()].map((group) => {
    const ordered = [...group].sort((a, b) => a.start - b.start);
    const first = ordered[0];
    if (ordered.some((entry) => entry.fingerprint !== first.fingerprint || entry.source_text !== first.source_text)) {
      throw selectionInvalid("source_changed");
    }
    for (let index = 1; index < ordered.length; index += 1) {
      if (ordered[index - 1].end !== ordered[index].start) throw selectionInvalid("same_doc_source");
    }
    const quote = ordered.map((entry) => entry.quote).join("");
    if (quote.length > MAX_QUOTE_CHARACTERS) throw selectionInvalid("budget");
    if (first.source_text.slice(first.start, ordered.at(-1).end) !== quote) throw selectionInvalid("source_changed");
    return { source_id: first.source_id, quote };
  });
}

export function hydrateParticipationQuotes(params, context, catalog) {
  if (!catalog || catalog.context_identity !== contextIdentity(context)) throw selectionInvalid("source_changed");
  if (!params || typeof params !== "object" || Array.isArray(params)
    || ["transcript_quote_ids", "document_quote_ids", "supporting_fact_quote_ids",
      "evidence_segment_ids", "evidence_quote", "document_evidence_refs", "supporting_facts"]
      .some((key) => Object.hasOwn(params, key))) throw selectionInvalid("obsolete_contract");
  const selected = selection(catalog, params.citation_quote_ids);
  const transcript = selected.filter((entry) => entry.kind === "transcript");
  const documents = selected.filter((entry) => entry.kind === "document");
  if (!transcript.length) throw selectionInvalid("no_transcript");
  const paragraphs = new Map([...context.retrieval_paragraphs, ...context.context_paragraphs,
    ...context.candidate_evidence_paragraphs, ...context.new_paragraphs].map((source) => [source.id, source]));
  const sources = new Map(context.document_evidence.map((source) => [source.source_id, source]));
  for (const entry of [...transcript, ...documents]) {
    const source = entry.kind === "transcript" ? paragraphs.get(entry.source_id) : sources.get(entry.source_id);
    const text = entry.kind === "transcript" ? source?.text : source?.quote;
    const fingerprint = entry.kind === "transcript" ? JSON.stringify([source?.id, source?.revision, text]) : source && identity(source);
    if (fingerprint !== entry.fingerprint || typeof text !== "string" || !text.includes(entry.source_text)
      || entry.source_text.slice(entry.start, entry.end) !== entry.quote) throw selectionInvalid("source_changed");
  }
  const evidenceQuote = transcript.map((entry) => entry.quote).join("\n");
  if (evidenceQuote.length > 1000) throw selectionInvalid("budget");
  const documentEvidenceRefs = documentReferences(documents);
  const { citation_quote_ids: _citations, ...card } = params;
  return { ...card, evidence_segment_ids: [...new Set(transcript.map((entry) => entry.source_id))],
    evidence_quote: evidenceQuote,
    document_evidence_refs: documentEvidenceRefs,
    supporting_facts: selected.map((entry) => ({ source_ref: entry.source_id, text: entry.quote })) };
}
