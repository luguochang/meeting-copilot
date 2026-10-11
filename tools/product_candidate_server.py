"""Serve an isolated candidate build; never migrate the user's running data or replace live assets."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code/web_mvp/backend"))
sys.path.insert(0, str(ROOT / "code/core"))
sys.path.insert(0, str(ROOT / "tools"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--frontend-dir", type=Path, required=True)
    parser.add_argument("--prewarm-asr", action="store_true")
    parser.add_argument(
        "--refiner-policy", choices=("prewarm", "online_only"),
        help="offline refinement policy; default is prewarm with --prewarm-asr, otherwise online_only. "
             "Use online_only with --prewarm-asr to warm realtime ASR without loading the offline refiner.",
    )
    args = parser.parse_args()
    artifacts = (ROOT / "artifacts").resolve()
    data, frontend = args.data_dir.resolve(), args.frontend_dir.resolve()
    if args.port == 8765 or not 1024 <= args.port <= 65535:
        parser.error("candidate must use a separate local port")
    if not data.is_relative_to(artifacts) or not frontend.is_relative_to(artifacts):
        parser.error("candidate data and assets must stay under ignored artifacts")
    if not (frontend / "index.html").is_file():
        parser.error("build candidate frontend first")
    from web_quality import quality_environment, DEFAULT_MODELS
    env = quality_environment(DEFAULT_MODELS, data)
    os.environ.update(env)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    os.environ["MEETING_COPILOT_REALTIME_REFINER_POLICY"] = args.refiner_policy or (
        "prewarm" if args.prewarm_asr else "online_only"
    )
    os.environ["MEETING_COPILOT_PI_BRIDGE_PREWARM"] = "0"
    from meeting_copilot_web_mvp.app import create_app
    import uvicorn
    app = create_app(data_dir=data, frontend_dir=frontend, prewarm_funasr=args.prewarm_asr, semantic_projection_mode="llm_first")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
