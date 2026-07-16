"""Pytest collection policy for retired pre-V2 scaffolding contracts.

These tests remain in the repository as historical audit evidence, but their
tool and policy files were intentionally retired by the V2 cleanup commits.
They must not be counted as current regression tests or silently recreated as
empty compatibility shims.
"""


collect_ignore = [
    # Retired by 27f3e26: event-generation planning scaffold moved to tools/_archive.
    "test_asr_event_generation_from_public_or_synthetic_audio.py",
    # Retired by 5456586: rust toolchain policy file was intentionally deleted.
    "test_desktop_rust_toolchain_installation_decision.py",
    # Retired by 27f3e26: manual FunASR packet scaffold moved to tools/_archive.
    "test_funasr_synthetic_smoke_batch_evidence_assembler.py",
]
