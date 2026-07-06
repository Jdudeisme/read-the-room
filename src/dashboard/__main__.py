"""CLI entry point: `python -m dashboard` or the `read-the-room-dashboard`
script. Hosts the sensing engine (on its own thread) + Mapper + FastAPI."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="read-the-room-dashboard",
        description="Live shadow-mode dashboard: sensing engine + mapping layer.",
    )
    parser.add_argument(
        "--source",
        choices=("mic", "synth"),
        default="mic",
        help="audio source: live microphone (default) or a synthetic test signal",
    )
    parser.add_argument("--device", help="input device name substring or index")
    parser.add_argument(
        "--no-emotion", action="store_true", help="run without the emotion layer"
    )
    parser.add_argument(
        "--no-headcount", action="store_true", help="run without the headcount layer"
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=8000, help="bind port")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    import dataclasses

    import uvicorn

    from mapping import Mapper, MappingConfig
    from sensing.audio import MicSource, SynthSource
    from sensing.config import Config
    from sensing.engine import Engine

    from .app import create_app
    from .bridge import DashboardBridge

    config = Config.from_env()
    if args.no_emotion:
        config = dataclasses.replace(config, emotion_enabled=False)
    if args.no_headcount:
        config = dataclasses.replace(config, headcount_enabled=False)
    if args.device:
        config = dataclasses.replace(config, input_device=args.device)

    buffer_s = config.window_s * 2 + 2.0
    if args.source == "synth":
        source = SynthSource(config.sample_rate, buffer_s)
    else:
        source = MicSource(config.sample_rate, buffer_s, config.input_device)

    mapper = Mapper(MappingConfig.from_env())
    # ~10 minutes of frames — exactly what the page timeline needs on load.
    history_maxlen = max(1, int(600.0 / config.hop_s))
    bridge = DashboardBridge(mapper, history_maxlen=history_maxlen)
    engine = Engine(source, config, consumers=[bridge])
    bridge.engine = engine  # regime extras + worker statuses on each frame

    annotations_dir = Path(
        os.environ.get("RTR_DASHBOARD_ANNOTATIONS_DIR", "data/annotations")
    )
    overrides_dir = Path(
        os.environ.get("RTR_DASHBOARD_OVERRIDES_DIR", "data/overrides")
    )
    # playback=None until a concrete provider lands (M4 D1): overrides are
    # still recorded in shadow mode, they just don't act on a player.
    from playback import PlaybackConfig

    playback_config = PlaybackConfig.from_env()
    app = create_app(
        bridge,
        annotations_dir,
        overrides_dir,
        playlists_path=Path(playback_config.playlists_path),
    )

    engine_thread = threading.Thread(target=engine.run, daemon=True, name="engine")
    engine_thread.start()
    print(
        f"Dashboard on http://{args.host}:{args.port}  "
        f"(source={args.source}; shadow mode — nothing is played)",
        file=sys.stderr,
    )
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
