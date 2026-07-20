import pytest

from meeting_copilot_web_mvp.diarization import (
    EmbeddingWindow,
    OnlineSpeakerClusterer,
    SpeakerTurn,
    attribute_segment,
    cosine_similarity,
    l2_normalize,
    match_clusters_to_stable_speakers,
    merge_speaker_turns,
)


def _window(
    window_id: str,
    start_ms: int,
    embedding: tuple[float, ...],
) -> EmbeddingWindow:
    return EmbeddingWindow(
        window_id=window_id,
        start_ms=start_ms,
        end_ms=start_ms + 400,
        embedding=embedding,
    )


def _clusterer() -> OnlineSpeakerClusterer:
    return OnlineSpeakerClusterer(
        similarity_threshold=0.80,
        margin_threshold=0.08,
        hysteresis=0.10,
        new_cluster_after=2,
        stable_after=2,
        candidate_similarity_threshold=0.90,
    )


def test_l2_normalize_and_cosine_are_pure_and_validate_vectors():
    vector = [3.0, 4.0]

    assert l2_normalize(vector) == pytest.approx((0.6, 0.8))
    assert vector == [3.0, 4.0]
    assert cosine_similarity((1.0, 0.0), (0.6, 0.8)) == pytest.approx(0.6)
    assert cosine_similarity((1.0, 0.0), (-1.0, 0.0)) == pytest.approx(-1.0)

    with pytest.raises(ValueError, match="non-zero"):
        l2_normalize((0.0, 0.0))
    with pytest.raises(ValueError, match="same dimension"):
        cosine_similarity((1.0,), (1.0, 0.0))


def test_online_clustering_finds_three_speakers_after_consecutive_evidence():
    clusterer = _clusterer()
    windows = [
        _window("a-1", 0, (1.0, 0.0, 0.0)),
        _window("a-2", 500, (0.99, 0.02, 0.0)),
        _window("b-1", 1_000, (0.0, 1.0, 0.0)),
        _window("b-2", 1_500, (0.01, 0.99, 0.0)),
        _window("c-1", 2_000, (0.0, 0.0, 1.0)),
        _window("c-2", 2_500, (0.0, 0.01, 0.99)),
    ]

    assignments = [clusterer.assign(window) for window in windows]

    assert [turn.cluster_label for turn in assignments] == [
        "cluster-1",
        "cluster-1",
        None,
        "cluster-2",
        None,
        "cluster-3",
    ]
    assert assignments[0].is_stable is False
    assert assignments[1].is_stable is True
    assert assignments[3].is_stable is True
    assert assignments[5].is_stable is True
    assert clusterer.stable_cluster_labels == ("cluster-1", "cluster-2", "cluster-3")
    assert clusterer.cluster_counts == {"cluster-1": 2, "cluster-2": 2, "cluster-3": 2}


def test_new_cluster_is_provisional_until_stable_window_count_is_reached():
    clusterer = OnlineSpeakerClusterer(
        similarity_threshold=0.80,
        hysteresis=0.10,
        new_cluster_after=2,
        stable_after=3,
        candidate_similarity_threshold=0.90,
    )
    setup = [
        _window("a-1", 0, (1.0, 0.0)),
        _window("a-2", 500, (1.0, 0.01)),
        _window("a-3", 1_000, (0.99, 0.0)),
    ]
    for window in setup:
        clusterer.assign(window)

    first_evidence = clusterer.assign(_window("b-1", 1_500, (0.0, 1.0)))
    provisional = clusterer.assign(_window("b-2", 2_000, (0.01, 1.0)))
    stable = clusterer.assign(_window("b-3", 2_500, (0.0, 0.99)))

    assert first_evidence.cluster_label is None
    assert provisional.cluster_label == "cluster-2"
    assert provisional.is_stable is False
    assert stable.cluster_label == "cluster-2"
    assert stable.is_stable is True


def test_hysteresis_absorbs_boundary_jitter_and_one_outlier_does_not_create_a_cluster():
    clusterer = _clusterer()
    setup = [
        _window("a-1", 0, (1.0, 0.0, 0.0)),
        _window("a-2", 500, (1.0, 0.01, 0.0)),
        _window("b-1", 1_000, (0.0, 1.0, 0.0)),
        _window("b-2", 1_500, (0.01, 1.0, 0.0)),
    ]
    for window in setup:
        clusterer.assign(window)

    jitter = clusterer.assign(_window("b-jitter", 2_000, (0.68, 0.73, 0.0)))
    outlier = clusterer.assign(_window("outlier", 2_500, (0.0, 0.0, 1.0)))
    recovered = clusterer.assign(_window("b-3", 3_000, (0.0, 1.0, 0.0)))

    assert jitter.cluster_label == "cluster-2"
    assert jitter.is_stable is True
    assert outlier.cluster_label is None
    assert recovered.cluster_label == "cluster-2"
    assert tuple(clusterer.centroids) == ("cluster-1", "cluster-2")


def test_merge_speaker_turns_joins_only_compatible_adjacent_turns():
    turns = [
        SpeakerTurn(0, 400, "cluster-1", confidence=0.8, window_ids=("a",)),
        SpeakerTurn(450, 900, "cluster-1", confidence=1.0, window_ids=("b",)),
        SpeakerTurn(850, 1_200, "cluster-2", confidence=0.9, window_ids=("c",)),
        SpeakerTurn(1_250, 1_500, None, confidence=None, is_stable=False, window_ids=("d",)),
    ]

    merged = merge_speaker_turns(turns, max_gap_ms=100)

    assert len(merged) == 3
    assert merged[0].start_ms == 0
    assert merged[0].end_ms == 900
    assert merged[0].cluster_label == "cluster-1"
    assert merged[0].window_ids == ("a", "b")
    assert merged[0].confidence == pytest.approx((0.8 * 400 + 1.0 * 450) / 850)
    assert merged[1].cluster_label == "cluster-2"
    assert merged[2].cluster_label is None


def test_segment_attribution_uses_overlap_and_stable_speaker_mapping():
    turns = [SpeakerTurn(100, 1_100, "cluster-1", confidence=0.94)]

    attribution = attribute_segment(
        "segment-1",
        200,
        1_000,
        turns,
        stable_speaker_ids={"cluster-1": "speaker-ada"},
    )

    assert attribution.segment_id == "segment-1"
    assert attribution.cluster_label == "cluster-1"
    assert attribution.speaker_id == "speaker-ada"
    assert attribution.confidence == pytest.approx(0.94)
    assert attribution.coverage_ratio == pytest.approx(1.0)
    assert attribution.reason == "attributed"
    assert attribution.is_unknown is False


@pytest.mark.parametrize(
    ("turns", "end_ms", "expected_reason"),
    [
        ([SpeakerTurn(0, 300, "cluster-1", confidence=0.95)], 1_000, "low_coverage"),
        ([SpeakerTurn(0, 1_000, "cluster-1", confidence=0.55)], 1_000, "low_confidence"),
        (
            [SpeakerTurn(0, 1_000, "cluster-1", confidence=0.95, is_stable=False)],
            1_000,
            "provisional_cluster",
        ),
    ],
)
def test_low_quality_segment_attribution_remains_unknown(turns, end_ms, expected_reason):
    attribution = attribute_segment("segment-unknown", 0, end_ms, turns)

    assert attribution.speaker_id is None
    assert attribution.cluster_label is None
    assert attribution.confidence is None
    assert attribution.reason == expected_reason
    assert attribution.is_unknown is True


def test_multi_speaker_overlap_remains_unknown():
    turns = [
        SpeakerTurn(0, 650, "cluster-1", confidence=0.95),
        SpeakerTurn(350, 1_000, "cluster-2", confidence=0.93),
    ]

    attribution = attribute_segment("segment-overlap", 0, 1_000, turns)

    assert attribution.speaker_id is None
    assert attribution.cluster_label is None
    assert attribution.coverage_ratio == pytest.approx(1.0)
    assert attribution.reason == "multiple_speakers"


def test_one_low_confidence_turn_is_not_hidden_by_high_confidence_neighbors():
    turns = [
        SpeakerTurn(0, 500, "cluster-1", confidence=0.98),
        SpeakerTurn(500, 700, "cluster-1", confidence=0.40),
        SpeakerTurn(700, 1_000, "cluster-1", confidence=0.98),
    ]

    attribution = attribute_segment("segment-mixed-confidence", 0, 1_000, turns)

    assert attribution.speaker_id is None
    assert attribution.reason == "low_confidence"


def test_stable_speaker_matching_is_global_one_to_one_and_thresholded():
    cluster_centroids = {
        "cluster-flexible": (0.8, 0.6),
        "cluster-fixed": (1.0, 0.0),
        "cluster-unmatched": (-1.0, 0.0),
    }
    stable_speaker_centroids = {
        "speaker-a": (1.0, 0.0),
        "speaker-b": (0.0, 1.0),
    }

    matched = match_clusters_to_stable_speakers(
        cluster_centroids,
        stable_speaker_centroids,
        minimum_similarity=0.5,
    )

    assert matched == {
        "cluster-fixed": "speaker-a",
        "cluster-flexible": "speaker-b",
    }


def test_duplicate_windows_are_idempotent_and_fresh_runs_are_deterministic():
    windows = [
        _window("a-1", 0, (1.0, 0.0)),
        _window("a-2", 500, (0.99, 0.01)),
        _window("b-1", 1_000, (0.0, 1.0)),
        _window("b-2", 1_500, (0.01, 0.99)),
    ]
    clusterer = _clusterer()
    first = [clusterer.assign(window) for window in windows]
    counts_before_replay = clusterer.cluster_counts
    centroids_before_replay = clusterer.centroids

    replayed = [clusterer.assign(window) for window in windows]
    fresh = _clusterer()
    repeated_from_scratch = [fresh.assign(window) for window in windows]

    assert replayed == first
    assert repeated_from_scratch == first
    assert clusterer.cluster_counts == counts_before_replay
    assert clusterer.centroids == centroids_before_replay
    assert match_clusters_to_stable_speakers(
        dict(reversed(list(clusterer.centroids.items()))),
        {"speaker-b": (0.0, 1.0), "speaker-a": (1.0, 0.0)},
    ) == match_clusters_to_stable_speakers(
        clusterer.centroids,
        {"speaker-a": (1.0, 0.0), "speaker-b": (0.0, 1.0)},
    )

    conflicting_replay = _window("a-1", 0, (0.0, 1.0))
    with pytest.raises(ValueError, match="conflicting payload"):
        clusterer.assign(conflicting_replay)
