"""Deterministic speaker clustering over precomputed embedding windows.

This module deliberately does not load or run an embedding model.  A caller may
feed embeddings from a separately selected runtime; the code here only owns the
testable clustering, turn, attribution, and stable-identity matching policies.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from math import fsum, hypot, isfinite
from numbers import Real


Vector = tuple[float, ...]


def _finite_vector(vector: Sequence[float]) -> Vector:
    if isinstance(vector, (str, bytes)):
        raise ValueError("embedding must be a non-empty numeric vector")
    try:
        values = tuple(float(value) for value in vector)
    except (TypeError, ValueError) as exc:
        raise ValueError("embedding must be a non-empty numeric vector") from exc
    if not values:
        raise ValueError("embedding must be a non-empty numeric vector")
    if not all(isfinite(value) for value in values):
        raise ValueError("embedding values must be finite")
    return values


def l2_normalize(vector: Sequence[float]) -> Vector:
    """Return a new unit-length tuple without mutating the input vector."""

    values = _finite_vector(vector)
    norm = hypot(*values)
    if norm == 0.0:
        raise ValueError("embedding must have a non-zero L2 norm")
    if not isfinite(norm):
        raise ValueError("embedding L2 norm must be finite")
    return tuple(value / norm for value in values)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Compute cosine similarity in [-1, 1] with input validation."""

    normalized_left = l2_normalize(left)
    normalized_right = l2_normalize(right)
    if len(normalized_left) != len(normalized_right):
        raise ValueError("embedding vectors must have the same dimension")
    similarity = fsum(a * b for a, b in zip(normalized_left, normalized_right, strict=True))
    return max(-1.0, min(1.0, similarity))


def _validate_interval(start_ms: float, end_ms: float) -> None:
    if (
        not isinstance(start_ms, Real)
        or isinstance(start_ms, bool)
        or not isinstance(end_ms, Real)
        or isinstance(end_ms, bool)
        or not isfinite(float(start_ms))
        or not isfinite(float(end_ms))
    ):
        raise ValueError("start_ms and end_ms must be finite numbers")
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("speaker interval must have 0 <= start_ms < end_ms")


def _validate_probability(value: float, name: str) -> None:
    if (
        not isinstance(value, Real)
        or isinstance(value, bool)
        or not isfinite(float(value))
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError(f"{name} must be a finite number between 0 and 1")


@dataclass(frozen=True, slots=True)
class EmbeddingWindow:
    """A timestamped embedding produced by an external embedding runtime."""

    window_id: str
    start_ms: float
    end_ms: float
    embedding: Vector

    def __post_init__(self) -> None:
        if not isinstance(self.window_id, str) or not self.window_id.strip():
            raise ValueError("window_id must be a non-empty string")
        _validate_interval(self.start_ms, self.end_ms)
        values = _finite_vector(self.embedding)
        l2_normalize(values)
        object.__setattr__(self, "embedding", values)


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    """A time interval assigned to a provisional or stable online cluster."""

    start_ms: float
    end_ms: float
    cluster_label: str | None
    confidence: float | None = 1.0
    is_stable: bool = True
    window_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_interval(self.start_ms, self.end_ms)
        if self.cluster_label is not None and (
            not isinstance(self.cluster_label, str) or not self.cluster_label.strip()
        ):
            raise ValueError("cluster_label must be a non-empty string or None")
        if self.confidence is not None:
            _validate_probability(self.confidence, "confidence")
        if not isinstance(self.is_stable, bool):
            raise ValueError("is_stable must be a boolean")
        if self.cluster_label is None and self.is_stable:
            object.__setattr__(self, "is_stable", False)
        window_ids = tuple(self.window_ids)
        if any(not isinstance(window_id, str) or not window_id for window_id in window_ids):
            raise ValueError("window_ids must contain non-empty strings")
        object.__setattr__(self, "window_ids", window_ids)


@dataclass(frozen=True, slots=True)
class SegmentAttribution:
    """Conservative speaker attribution for one immutable ASR segment."""

    segment_id: str
    start_ms: float
    end_ms: float
    cluster_label: str | None
    speaker_id: str | None
    confidence: float | None
    coverage_ratio: float
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.segment_id, str) or not self.segment_id.strip():
            raise ValueError("segment_id must be a non-empty string")
        _validate_interval(self.start_ms, self.end_ms)
        _validate_probability(self.coverage_ratio, "coverage_ratio")
        if self.confidence is not None:
            _validate_probability(self.confidence, "confidence")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason must be a non-empty string")

    @property
    def is_unknown(self) -> bool:
        return self.speaker_id is None


@dataclass(slots=True)
class _Cluster:
    label: str
    vector_sum: list[float]
    count: int

    @property
    def centroid(self) -> Vector:
        return l2_normalize(self.vector_sum)

    def add(self, embedding: Vector) -> None:
        if len(embedding) != len(self.vector_sum):
            raise ValueError("embedding vectors must have the same dimension")
        for index, value in enumerate(embedding):
            self.vector_sum[index] += value
        self.count += 1


@dataclass(slots=True)
class OnlineSpeakerClusterer:
    """Assign precomputed windows to deterministic online centroid clusters.

    ``hysteresis`` lowers the stay threshold for the active cluster and raises
    the evidence required to switch away from it.  A new cluster is created
    only after ``new_cluster_after`` consecutive low-similarity windows that
    are mutually coherent.  Clusters remain provisional until ``stable_after``
    windows have contributed to their centroid.
    """

    similarity_threshold: float = 0.78
    margin_threshold: float = 0.08
    hysteresis: float = 0.06
    new_cluster_after: int = 2
    stable_after: int = 3
    candidate_similarity_threshold: float = 0.82
    cluster_prefix: str = "cluster"
    _clusters: list[_Cluster] = field(default_factory=list, init=False, repr=False)
    _candidate_windows: list[tuple[EmbeddingWindow, Vector]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _seen_windows: dict[str, tuple[EmbeddingWindow, SpeakerTurn]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _dimension: int | None = field(default=None, init=False, repr=False)
    _last_cluster_label: str | None = field(default=None, init=False, repr=False)
    _last_start_ms: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_probability(self.similarity_threshold, "similarity_threshold")
        _validate_probability(self.margin_threshold, "margin_threshold")
        _validate_probability(self.hysteresis, "hysteresis")
        _validate_probability(
            self.candidate_similarity_threshold,
            "candidate_similarity_threshold",
        )
        if self.hysteresis > self.similarity_threshold:
            raise ValueError("hysteresis must not exceed similarity_threshold")
        if not isinstance(self.new_cluster_after, int) or self.new_cluster_after < 2:
            raise ValueError("new_cluster_after must be an integer of at least 2")
        if not isinstance(self.stable_after, int) or self.stable_after < 1:
            raise ValueError("stable_after must be a positive integer")
        if not isinstance(self.cluster_prefix, str) or not self.cluster_prefix.strip():
            raise ValueError("cluster_prefix must be a non-empty string")

    @property
    def centroids(self) -> dict[str, Vector]:
        return {cluster.label: cluster.centroid for cluster in self._clusters}

    @property
    def cluster_counts(self) -> dict[str, int]:
        return {cluster.label: cluster.count for cluster in self._clusters}

    @property
    def stable_cluster_labels(self) -> tuple[str, ...]:
        return tuple(
            cluster.label for cluster in self._clusters if cluster.count >= self.stable_after
        )

    def assign(self, window: EmbeddingWindow) -> SpeakerTurn:
        """Assign one chronological window, replaying duplicate IDs idempotently."""

        if not isinstance(window, EmbeddingWindow):
            raise TypeError("window must be an EmbeddingWindow")
        replayed = self._seen_windows.get(window.window_id)
        if replayed is not None:
            prior_window, prior_turn = replayed
            if prior_window != window:
                raise ValueError(f"window_id {window.window_id!r} has a conflicting payload")
            return prior_turn
        if self._last_start_ms is not None and window.start_ms < self._last_start_ms:
            raise ValueError("embedding windows must be assigned in chronological order")

        embedding = l2_normalize(window.embedding)
        if self._dimension is None:
            self._dimension = len(embedding)
        elif len(embedding) != self._dimension:
            raise ValueError("embedding vectors must have the same dimension")

        if not self._clusters:
            cluster = self._create_cluster([embedding])
            turn = self._turn_for(window, cluster, confidence=1.0)
            self._last_cluster_label = cluster.label
        else:
            scores = [cosine_similarity(embedding, cluster.centroid) for cluster in self._clusters]
            selected_index = self._select_cluster(scores)
            if selected_index is not None:
                self._candidate_windows.clear()
                cluster = self._clusters[selected_index]
                confidence = max(0.0, scores[selected_index])
                cluster.add(embedding)
                self._last_cluster_label = cluster.label
                turn = self._turn_for(window, cluster, confidence=confidence)
            elif max(scores) < self.similarity_threshold - self.hysteresis:
                turn = self._consider_new_cluster(window, embedding)
            else:
                self._candidate_windows.clear()
                turn = self._unknown_turn(window)

        self._last_start_ms = window.start_ms
        self._seen_windows[window.window_id] = (window, turn)
        return turn

    def _select_cluster(self, scores: Sequence[float]) -> int | None:
        best_index = max(range(len(scores)), key=lambda index: (scores[index], -index))
        best_score = scores[best_index]
        active_index = next(
            (
                index
                for index, cluster in enumerate(self._clusters)
                if cluster.label == self._last_cluster_label
            ),
            None,
        )

        if active_index is not None:
            active_score = scores[active_index]
            decisive_switch = (
                best_index != active_index
                and best_score >= self.similarity_threshold
                and best_score - active_score >= self.margin_threshold + self.hysteresis
            )
            if decisive_switch:
                return best_index
            if active_score >= self.similarity_threshold - self.hysteresis:
                return active_index

        if best_score < self.similarity_threshold:
            return None
        if len(scores) == 1:
            return best_index
        second_best = max(score for index, score in enumerate(scores) if index != best_index)
        if best_score - second_best >= self.margin_threshold:
            return best_index
        return None

    def _consider_new_cluster(self, window: EmbeddingWindow, embedding: Vector) -> SpeakerTurn:
        if self._candidate_windows:
            candidate_centroid = l2_normalize(
                tuple(
                    fsum(candidate[index] for _, candidate in self._candidate_windows)
                    for index in range(len(embedding))
                )
            )
            if cosine_similarity(embedding, candidate_centroid) < self.candidate_similarity_threshold:
                self._candidate_windows.clear()
        self._candidate_windows.append((window, embedding))
        if len(self._candidate_windows) < self.new_cluster_after:
            return self._unknown_turn(window)

        candidate_embeddings = [candidate for _, candidate in self._candidate_windows]
        cluster = self._create_cluster(candidate_embeddings)
        confidence = min(
            cosine_similarity(candidate, cluster.centroid) for candidate in candidate_embeddings
        )
        self._candidate_windows.clear()
        self._last_cluster_label = cluster.label
        return self._turn_for(window, cluster, confidence=max(0.0, confidence))

    def _create_cluster(self, embeddings: Sequence[Vector]) -> _Cluster:
        vector_sum = [
            fsum(embedding[index] for embedding in embeddings)
            for index in range(len(embeddings[0]))
        ]
        cluster = _Cluster(
            label=f"{self.cluster_prefix}-{len(self._clusters) + 1}",
            vector_sum=vector_sum,
            count=len(embeddings),
        )
        self._clusters.append(cluster)
        return cluster

    def _turn_for(
        self,
        window: EmbeddingWindow,
        cluster: _Cluster,
        *,
        confidence: float,
    ) -> SpeakerTurn:
        return SpeakerTurn(
            start_ms=window.start_ms,
            end_ms=window.end_ms,
            cluster_label=cluster.label,
            confidence=confidence,
            is_stable=cluster.count >= self.stable_after,
            window_ids=(window.window_id,),
        )

    @staticmethod
    def _unknown_turn(window: EmbeddingWindow) -> SpeakerTurn:
        return SpeakerTurn(
            start_ms=window.start_ms,
            end_ms=window.end_ms,
            cluster_label=None,
            confidence=None,
            is_stable=False,
            window_ids=(window.window_id,),
        )


def _combined_confidence(
    left: SpeakerTurn,
    right: SpeakerTurn,
) -> float | None:
    if left.confidence is None or right.confidence is None:
        return None
    left_duration = left.end_ms - left.start_ms
    right_duration = right.end_ms - right.start_ms
    return (
        left.confidence * left_duration + right.confidence * right_duration
    ) / (left_duration + right_duration)


def _unique_in_order(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return tuple(result)


def merge_speaker_turns(
    turns: Iterable[SpeakerTurn],
    *,
    max_gap_ms: float = 250,
) -> list[SpeakerTurn]:
    """Merge nearby turns only when cluster identity and stability agree."""

    if (
        not isinstance(max_gap_ms, Real)
        or isinstance(max_gap_ms, bool)
        or not isfinite(float(max_gap_ms))
        or max_gap_ms < 0
    ):
        raise ValueError("max_gap_ms must be a finite non-negative number")
    ordered = sorted(
        turns,
        key=lambda turn: (
            turn.start_ms,
            turn.end_ms,
            turn.cluster_label is None,
            turn.cluster_label or "",
        ),
    )
    if any(not isinstance(turn, SpeakerTurn) for turn in ordered):
        raise TypeError("turns must contain SpeakerTurn values")
    merged: list[SpeakerTurn] = []
    for turn in ordered:
        if not merged:
            merged.append(turn)
            continue
        previous = merged[-1]
        compatible = (
            previous.cluster_label == turn.cluster_label
            and previous.is_stable == turn.is_stable
            and turn.start_ms <= previous.end_ms + max_gap_ms
        )
        if not compatible:
            merged.append(turn)
            continue
        merged[-1] = SpeakerTurn(
            start_ms=previous.start_ms,
            end_ms=max(previous.end_ms, turn.end_ms),
            cluster_label=previous.cluster_label,
            confidence=_combined_confidence(previous, turn),
            is_stable=previous.is_stable,
            window_ids=_unique_in_order((*previous.window_ids, *turn.window_ids)),
        )
    return merged


def _union_duration(intervals: Iterable[tuple[float, float]]) -> float:
    ordered = sorted(intervals)
    if not ordered:
        return 0.0
    duration = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            duration += current_end - current_start
            current_start, current_end = start, end
    return duration + current_end - current_start


def _unknown_attribution(
    segment_id: str,
    start_ms: float,
    end_ms: float,
    coverage_ratio: float,
    reason: str,
) -> SegmentAttribution:
    return SegmentAttribution(
        segment_id=segment_id,
        start_ms=start_ms,
        end_ms=end_ms,
        cluster_label=None,
        speaker_id=None,
        confidence=None,
        coverage_ratio=coverage_ratio,
        reason=reason,
    )


def attribute_segment(
    segment_id: str,
    start_ms: float,
    end_ms: float,
    turns: Iterable[SpeakerTurn],
    *,
    stable_speaker_ids: Mapping[str, str] | None = None,
    minimum_coverage: float = 0.60,
    minimum_confidence: float = 0.70,
    maximum_secondary_overlap: float = 0.20,
    minimum_dominant_share: float = 0.80,
) -> SegmentAttribution:
    """Attribute an ASR segment only when overlap evidence is unambiguous."""

    if not isinstance(segment_id, str) or not segment_id.strip():
        raise ValueError("segment_id must be a non-empty string")
    _validate_interval(start_ms, end_ms)
    _validate_probability(minimum_coverage, "minimum_coverage")
    _validate_probability(minimum_confidence, "minimum_confidence")
    _validate_probability(maximum_secondary_overlap, "maximum_secondary_overlap")
    _validate_probability(minimum_dominant_share, "minimum_dominant_share")

    duration = end_ms - start_ms
    overlapping: list[tuple[SpeakerTurn, float, float]] = []
    for turn in turns:
        if not isinstance(turn, SpeakerTurn):
            raise TypeError("turns must contain SpeakerTurn values")
        overlap_start = max(start_ms, turn.start_ms)
        overlap_end = min(end_ms, turn.end_ms)
        if overlap_end > overlap_start and turn.cluster_label is not None:
            overlapping.append((turn, overlap_start, overlap_end))
    coverage_ratio = min(
        1.0,
        _union_duration((start, end) for _, start, end in overlapping) / duration,
    )
    if not overlapping:
        return _unknown_attribution(segment_id, start_ms, end_ms, 0.0, "no_speaker")
    if coverage_ratio < minimum_coverage:
        return _unknown_attribution(
            segment_id,
            start_ms,
            end_ms,
            coverage_ratio,
            "low_coverage",
        )

    intervals_by_cluster: dict[str, list[tuple[float, float]]] = {}
    turns_by_cluster: dict[str, list[tuple[SpeakerTurn, float]]] = {}
    for turn, overlap_start, overlap_end in overlapping:
        label = turn.cluster_label
        if label is None:
            continue
        intervals_by_cluster.setdefault(label, []).append((overlap_start, overlap_end))
        turns_by_cluster.setdefault(label, []).append((turn, overlap_end - overlap_start))
    overlap_by_cluster = {
        label: _union_duration(intervals) for label, intervals in intervals_by_cluster.items()
    }
    ranked_clusters = sorted(
        overlap_by_cluster,
        key=lambda label: (-overlap_by_cluster[label], label),
    )
    dominant_label = ranked_clusters[0]
    dominant_overlap = overlap_by_cluster[dominant_label]
    total_cluster_overlap = fsum(overlap_by_cluster.values())
    if len(ranked_clusters) > 1:
        secondary_ratio = overlap_by_cluster[ranked_clusters[1]] / duration
        dominant_share = dominant_overlap / total_cluster_overlap
        if (
            secondary_ratio >= maximum_secondary_overlap
            or dominant_share < minimum_dominant_share
        ):
            return _unknown_attribution(
                segment_id,
                start_ms,
                end_ms,
                coverage_ratio,
                "multiple_speakers",
            )

    dominant_turns = turns_by_cluster[dominant_label]
    if any(not turn.is_stable for turn, _ in dominant_turns):
        return _unknown_attribution(
            segment_id,
            start_ms,
            end_ms,
            coverage_ratio,
            "provisional_cluster",
        )
    confidence_values = [
        (turn.confidence, overlap_duration) for turn, overlap_duration in dominant_turns
    ]
    if any(confidence is None for confidence, _ in confidence_values):
        return _unknown_attribution(
            segment_id,
            start_ms,
            end_ms,
            coverage_ratio,
            "low_confidence",
        )
    weighted_confidence = fsum(
        float(confidence) * overlap_duration
        for confidence, overlap_duration in confidence_values
        if confidence is not None
    ) / fsum(overlap_duration for _, overlap_duration in confidence_values)
    if (
        weighted_confidence < minimum_confidence
        or any(
            confidence is not None and confidence < minimum_confidence
            for confidence, _ in confidence_values
        )
    ):
        return _unknown_attribution(
            segment_id,
            start_ms,
            end_ms,
            coverage_ratio,
            "low_confidence",
        )

    if stable_speaker_ids is None:
        speaker_id = dominant_label
    else:
        speaker_id = stable_speaker_ids.get(dominant_label)
        if speaker_id is None:
            return _unknown_attribution(
                segment_id,
                start_ms,
                end_ms,
                coverage_ratio,
                "unmatched_cluster",
            )
    return SegmentAttribution(
        segment_id=segment_id,
        start_ms=start_ms,
        end_ms=end_ms,
        cluster_label=dominant_label,
        speaker_id=speaker_id,
        confidence=weighted_confidence,
        coverage_ratio=coverage_ratio,
        reason="attributed",
    )


def attribute_segments(
    segments: Iterable[tuple[str, float, float]],
    turns: Iterable[SpeakerTurn],
    **kwargs: object,
) -> list[SegmentAttribution]:
    """Bulk deterministic wrapper around :func:`attribute_segment`."""

    reusable_turns = tuple(turns)
    return [
        attribute_segment(segment_id, start_ms, end_ms, reusable_turns, **kwargs)
        for segment_id, start_ms, end_ms in segments
    ]


def _minimum_cost_assignment(costs: Sequence[Sequence[float]]) -> list[int]:
    """Return the selected column for each row using rectangular Hungarian matching."""

    row_count = len(costs)
    if row_count == 0:
        return []
    column_count = len(costs[0])
    if column_count < row_count or any(len(row) != column_count for row in costs):
        raise ValueError("assignment matrix must be rectangular with at least as many columns as rows")

    row_potential = [0.0] * (row_count + 1)
    column_potential = [0.0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    predecessor = [0] * (column_count + 1)
    epsilon = 1e-12

    for row in range(1, row_count + 1):
        matched_row[0] = row
        minimum = [float("inf")] * (column_count + 1)
        used = [False] * (column_count + 1)
        current_column = 0
        while True:
            used[current_column] = True
            current_row = matched_row[current_column]
            delta = float("inf")
            next_column = 0
            for column in range(1, column_count + 1):
                if used[column]:
                    continue
                reduced_cost = (
                    costs[current_row - 1][column - 1]
                    - row_potential[current_row]
                    - column_potential[column]
                )
                if reduced_cost < minimum[column] - epsilon:
                    minimum[column] = reduced_cost
                    predecessor[column] = current_column
                if (
                    minimum[column] < delta - epsilon
                    or abs(minimum[column] - delta) <= epsilon
                    and (next_column == 0 or column < next_column)
                ):
                    delta = minimum[column]
                    next_column = column
            for column in range(column_count + 1):
                if used[column]:
                    row_potential[matched_row[column]] += delta
                    column_potential[column] -= delta
                else:
                    minimum[column] -= delta
            current_column = next_column
            if matched_row[current_column] == 0:
                break
        while True:
            prior_column = predecessor[current_column]
            matched_row[current_column] = matched_row[prior_column]
            current_column = prior_column
            if current_column == 0:
                break

    assignment = [-1] * row_count
    for column in range(1, column_count + 1):
        if matched_row[column] != 0:
            assignment[matched_row[column] - 1] = column - 1
    return assignment


def match_clusters_to_stable_speakers(
    cluster_centroids: Mapping[str, Sequence[float]],
    stable_speaker_centroids: Mapping[str, Sequence[float]],
    *,
    minimum_similarity: float = 0.70,
) -> dict[str, str]:
    """Globally maximize one-to-one cluster-to-speaker centroid similarity."""

    _validate_probability(minimum_similarity, "minimum_similarity")
    cluster_labels = sorted(cluster_centroids)
    stable_speaker_ids = sorted(stable_speaker_centroids)
    if not cluster_labels or not stable_speaker_ids:
        return {}

    normalized_clusters = {
        label: l2_normalize(cluster_centroids[label]) for label in cluster_labels
    }
    normalized_speakers = {
        speaker_id: l2_normalize(stable_speaker_centroids[speaker_id])
        for speaker_id in stable_speaker_ids
    }
    similarities = [
        [
            cosine_similarity(normalized_clusters[label], normalized_speakers[speaker_id])
            for speaker_id in stable_speaker_ids
        ]
        for label in cluster_labels
    ]
    # One zero-cost dummy per cluster permits any row to remain unmatched.
    costs = [
        [
            -similarity if similarity >= minimum_similarity else 1.0
            for similarity in row
        ]
        + [0.0] * len(cluster_labels)
        for row in similarities
    ]
    assignment = _minimum_cost_assignment(costs)

    matched: dict[str, str] = {}
    for row, column in enumerate(assignment):
        if (
            0 <= column < len(stable_speaker_ids)
            and similarities[row][column] >= minimum_similarity
        ):
            matched[cluster_labels[row]] = stable_speaker_ids[column]
    return matched


__all__ = [
    "EmbeddingWindow",
    "OnlineSpeakerClusterer",
    "SegmentAttribution",
    "SpeakerTurn",
    "attribute_segment",
    "attribute_segments",
    "cosine_similarity",
    "l2_normalize",
    "match_clusters_to_stable_speakers",
    "merge_speaker_turns",
]
