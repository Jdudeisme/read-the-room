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

    annotations_dir = Path(
        os.environ.get("RTR_DASHBOARD_ANNOTATIONS_DIR", "data/annotations")
    )
    overrides_dir = Path(
        os.environ.get("RTR_DASHBOARD_OVERRIDES_DIR", "data/overrides")
    )

    # Playback (M4): opt-in via RTR_PLAYBACK_ENABLED. Anything missing or
    # broken leaves controller=None — shadow mode is the first-class default.
    from playback import (
        PlaybackConfig,
        PlaybackController,
        SpotifyProvider,
        TrackSelector,
    )

    playback_config = PlaybackConfig.from_env()
    controller = None
    if playback_config.enabled:
        if not playback_config.client_id:
            print(
                "RTR_PLAYBACK_ENABLED=1 but RTR_PLAYBACK_CLIENT_ID is unset — "
                "staying in shadow mode",
                file=sys.stderr,
            )
        else:
            if not Path(playback_config.token_cache_path).exists():
                print(
                    "no Spotify token cache yet — run read-the-room-spotify-auth "
                    "once; playback will surface as degraded until then",
                    file=sys.stderr,
                )
            # A malformed playlists.json raises here, loudly and on purpose.
            provider = SpotifyProvider(playback_config)
            controller = PlaybackController(
                provider,
                TrackSelector(provider, playback_config.recently_played_window),
                playback_config,
            )

    # ~10 minutes of frames — exactly what the page timeline needs on load.
    history_maxlen = max(1, int(600.0 / config.hop_s))
    bridge = DashboardBridge(
        mapper, history_maxlen=history_maxlen, playback=controller
    )
    engine = Engine(source, config, consumers=[bridge], playback_source=controller)
    bridge.engine = engine  # regime extras + worker statuses on each frame

    if controller is not None:
        from .overrides import append_override, build_override_record

        def played_through_sink(now_playing: dict, recommendation: dict) -> None:
            """Implicit weak positive: stamped with the latest frame (there
            is no tap to snapshot at)."""
            frames, _ = bridge.snapshot()
            state = {k: v for k, v in (frames[-1] if frames else {}).items()
                     if k != "type"} or {"unavailable": True}
            try:
                append_override(
                    overrides_dir,
                    build_override_record(
                        "played_through", state, recommendation, now_playing
                    ),
                )
            except Exception:
                logging.getLogger(__name__).exception(
                    "failed to log played_through; label lost"
                )

        controller.on_played_through = played_through_sink
        controller.start()

    app = create_app(
        bridge,
        annotations_dir,
        overrides_dir,
        playback=controller,
        playlists_path=Path(playback_config.playlists_path),
    )

    engine_thread = threading.Thread(target=engine.run, daemon=True, name="engine")
    engine_thread.start()
    mode = "playback enabled" if controller is not None else "shadow mode — nothing is played"
    print(
        f"Dashboard on http://{args.host}:{args.port}  "
        f"(source={args.source}; {mode})",
        file=sys.stderr,
    )
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        engine.stop()
        if controller is not None:
            controller.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
