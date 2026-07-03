"""CLI entry point: `python -m sensing` or the `read-the-room` script."""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="read-the-room",
        description="Live room-sensing engine: loudness, speech, and emotion.",
    )
    parser.add_argument(
        "--source",
        choices=("mic", "synth"),
        default="mic",
        help="audio source: live microphone (default) or a synthetic test signal",
    )
    parser.add_argument("--device", help="input device name substring or index")
    parser.add_argument(
        "--list-devices", action="store_true", help="list input devices and exit"
    )
    parser.add_argument(
        "--no-emotion", action="store_true", help="run without the emotion layer"
    )
    parser.add_argument(
        "--no-headcount", action="store_true", help="run without the headcount layer"
    )
    parser.add_argument(
        "--ticks", type=int, metavar="N", help="stop after N analysis windows"
    )
    parser.add_argument(
        "--jsonl", metavar="PATH", help="also append each RoomState to a JSONL file"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.list_devices:
        from .audio import list_input_devices

        print(list_input_devices())
        return 0

    import dataclasses

    from .audio import MicSource, SynthSource
    from .config import Config
    from .consumers import ConsoleRenderer, JsonlWriter
    from .engine import Engine

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

    engine = Engine(source, config, consumers=[])
    consumers: list = [ConsoleRenderer(engine)]
    jsonl = None
    if args.jsonl:
        jsonl = JsonlWriter(args.jsonl)
        consumers.append(jsonl)
    engine.consumers = consumers

    print("Starting engine (Ctrl+C to stop)...", file=sys.stderr)
    try:
        engine.run(max_ticks=args.ticks)
    finally:
        if jsonl is not None:
            jsonl.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
