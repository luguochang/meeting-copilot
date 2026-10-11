"""Deterministic terminology recall; spelling alternatives never change source text."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Callable, Mapping, Sequence


MAX_SEARCH_TERMS = 40
MAX_MATCHED_GROUPS = 6
MAX_QUERY_TOKENS = 24
MAX_EXPANDED_TOKENS = 128


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def confirmed_search_terms(repository, *, project_id: str, folder_ids: Sequence[str] | None,
                           document_ids: Sequence[str] | None, meeting_id: str | None,
                           scene: str) -> list[dict[str, Any]]:
    # Import only when searching: user_memory itself depends on the source validator.
    from .user_memory import SCENES, memory_context

    if scene not in SCENES:
        raise ValueError("unsupported search memory scene")
    if meeting_id is not None:
        # This includes the same meeting/project/global precedence and current
        # source + selected-directory checks used by the model's input memory.
        return memory_context(repository._conn, meeting_id, scene)["terms"]
    rows = repository._conn.execute(
        "SELECT * FROM user_memories WHERE status='active' AND kind='term' AND scene IN ('',?) "
        "AND (scope='global' OR (scope='project' AND scope_id=?)) "
        "ORDER BY CASE scope WHEN 'project' THEN 0 ELSE 1 END,CASE WHEN scene=? THEN 0 ELSE 1 END,"
        "updated_at_ms DESC,id LIMIT 201", (scene, project_id, scene),
    ).fetchall()
    result, slots, characters = [], set(), 0
    for row in rows[:200]:
        if row["memory_key"] in slots:
            continue
        refs = json.loads(row["source_refs_json"])
        try:
            for ref in refs:
                # Global explicit terms have no project-derived sources.
                if row["scope"] == "global" or ref.get("source_type") != "document":
                    raise ValueError("term source is outside search scope")
                source = repository.validate_evidence(ref, project_id=project_id)
                if document_ids is not None and source["document_id"] not in document_ids:
                    raise ValueError("term source is not selected")
                if folder_ids is not None and repository._get("knowledge_documents", source["document_id"])["folder_id"] not in folder_ids:
                    raise ValueError("term source directory is not selected")
        except (KeyError, ValueError):
            continue
        slots.add(row["memory_key"])
        if len(result) == MAX_SEARCH_TERMS or characters + len(row["value"]) > 1600:
            continue
        result.append({"canonical": row["value"], "aliases": json.loads(row["aliases_json"])})
        characters += len(row["value"])
    return result


def expanded_fts_expression(query: str, terms: Sequence[Mapping[str, Any]], *,
                            tokenize: Callable[[str], list[str]], match_mode: str) -> str:
    """Replace only an explicit matched spelling with a bounded OR group.

    Other query words retain their AND/OR relationship. Groups do not expand
    transitively, ambiguous spellings remain literal, and ASCII substrings such
    as Alpha inside AlphaBeta do not become terminology matches.
    """
    separator = " AND " if match_mode == "all" else " OR "
    original = tokenize(query)[:MAX_QUERY_TOKENS]
    literal = separator.join('"' + token + '"' for token in original)
    families: dict[str, list[str]] = {}
    spellings: dict[str, set[str]] = {}
    for term in terms[:MAX_SEARCH_TERMS]:
        canonical = term.get("canonical")
        if not isinstance(canonical, str) or not 0 < len(canonical) <= 64:
            continue
        key = _normalized(canonical)
        family = families.setdefault(key, [])
        for spelling in [canonical, *term.get("aliases", [])[:8]]:
            if not isinstance(spelling, str) or not 0 < len(spelling) <= 64:
                continue
            word = _normalized(spelling)
            if not tokenize(word):
                continue
            if word not in family and len(family) < 9:
                family.append(word)
            spellings.setdefault(word, set()).add(key)
    unambiguous = {word: next(iter(keys)) for word, keys in spellings.items() if len(keys) == 1}
    families = {key: [word for word in family if unambiguous.get(word) == key] for key, family in families.items()}
    unambiguous = {word: key for word, key in unambiguous.items() if len(families[key]) > 1}
    if not unambiguous:
        return literal
    pattern = re.compile("|".join(re.escape(word) for word in sorted(unambiguous, key=lambda word: (-len(word), word))))
    normalized = _normalized(query)
    spans, position, matched = [], 0, 0

    def ascii_word(character: str) -> bool:
        return character.isascii() and (character.isalnum() or character == "_")

    for match in pattern.finditer(normalized):
        word = match.group()
        if (ascii_word(word[0]) and match.start() and ascii_word(normalized[match.start() - 1])
                or ascii_word(word[-1]) and match.end() < len(normalized) and ascii_word(normalized[match.end()])):
            continue
        if matched == MAX_MATCHED_GROUPS:
            break
        spans.append((normalized[position:match.start()], None))
        spans.append((word, unambiguous[word]))
        position, matched = match.end(), matched + 1
    if not matched:
        return literal
    spans.append((normalized[position:], None))
    units, original_count = [], 0
    for text, family_key in spans:
        tokens = tokenize(text)[:MAX_QUERY_TOKENS - original_count]
        original_count += len(tokens)
        if not tokens:
            continue
        if family_key is None:
            units.extend(([token], []) for token in tokens)
        else:
            units.append((tokens, [word for word in families[family_key] if word != text][:8]))
        if original_count == MAX_QUERY_TOKENS:
            break
    remaining, expressions = MAX_EXPANDED_TOKENS - original_count, []
    for tokens, alternatives in units:
        variants = [tokens]
        for word in alternatives:
            extra = tokenize(word)[:MAX_QUERY_TOKENS]
            if extra and extra not in variants and len(extra) <= remaining:
                variants.append(extra)
                remaining -= len(extra)
        expressions.append("(" + " OR ".join("(" + " AND ".join('"' + token + '"' for token in variant) + ")"
                                             for variant in variants) + ")" if alternatives or len(tokens) > 1 else '"' + tokens[0] + '"')
    return separator.join(expressions)
