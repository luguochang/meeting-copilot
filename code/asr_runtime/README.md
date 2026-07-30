# Local ASR Runtime

Local speech-recognition workers used by Meeting Copilot for realtime transcription, imported recordings, transcript refinement, punctuation, and speaker segmentation.

## Components

- `scripts/funasr_stream_worker.py`: resident realtime FunASR worker.
- `scripts/funasr_batch_worker.py`: recording transcription worker.
- `scripts/funasr_offline_refiner_worker.py`: offline transcript refinement.
- `scripts/funasr_diarization_worker.py`: local speaker segmentation.
- `scripts/sherpa_stream_worker.py`: sherpa-onnx realtime worker.
- `model_packs/`: versioned manifests for distributable capability packs.

## Models

Model weights are intentionally not stored in Git. Put local models under `code/asr_runtime/models/` or import a signed capability package through the desktop application. Model source, revision, hash, and license must be recorded in the corresponding manifest before distribution.

## Tests

The runtime tests use synthetic fixtures and do not require real meeting recordings:

```bash
python -m pytest code/asr_runtime/tests -q
```

Separate lock files are provided for FunASR and sherpa-onnx environments. Install only the runtime needed for the selected local capability package.
