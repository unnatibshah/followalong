"""Day-1 harness, now an alias of the evaluation command (server/evaluate.py): run the check engine on labelled JPEGs
against the real API and print each verdict, accuracy, median/p90 latency and spend. Writes no report.

    set -a; . ./.env; set +a
    .venv/bin/python tools/day1_frames.py <folder> [--tutorial install-thermostat] [--kind full]

Labels: a filename like `step4-wrong-red-not-in-rh.jpg` (step, then the expected verdict), or a `labels.json` in the
folder: {"some.jpg": {"step": 4, "expect": "wrong", "tutorial": "install-thermostat"}}. `unsure` and `waiting` mean
"not done".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.evaluate import main  # noqa: E402

if __name__ == "__main__":
    main(["--folder", *sys.argv[1:]] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else sys.argv[1:])
