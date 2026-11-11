"""Launch the UI:   python ui/run.py   (or double-click ui/launch.bat)

Options:  --port 8765   --workspace <folder>   --no-browser
"""

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))  # so `import service` / `import server` work from anywhere


def _echo_events() -> None:
    """Print engine events (sanitized, as the UI stream shows them) to stdout, one line each."""
    import time

    from extractor.events import LOG
    from service import public_event

    seq = LOG.last_seq()
    while True:
        for e in LOG.since(seq):
            seq = e.seq
            p = public_event(e)
            clock = time.strftime("%H:%M:%S", time.localtime(p["time"]))
            print(f"{clock} {p['level']:<5} {p['stage']:<4} {p['message']}", flush=True)
        time.sleep(0.25)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extractor UI")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")  # local only; do not expose without auth
    parser.add_argument("--workspace", type=Path, default=HERE.parent / ".extractor_artifacts" / "workspace")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--echo-events", action="store_true",
                        help="print every engine event to this terminal (the same events the UI streams)")
    args = parser.parse_args()

    import uvicorn

    if args.echo_events:
        threading.Thread(target=_echo_events, daemon=True).start()
    from server import create_app

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Extractor UI running at {url}  (Ctrl+C to stop)\nworkspace: {args.workspace}")
    uvicorn.run(create_app(args.workspace), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
