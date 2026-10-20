#!/usr/bin/env python3
"""PostToolUse hook: after any edit to evals/golden.jsonl, run the offline golden lint.

Exit code 2 sends the lint output back to Claude so it fixes the file before moving on.
"""
import json
import os
import subprocess
import sys


def main() -> None:
    data = json.load(sys.stdin)
    path = (data.get("tool_input") or {}).get("file_path", "")
    if not path.endswith("evals/golden.jsonl"):
        sys.exit(0)
    root = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    env = dict(os.environ, PYTHONPATH=os.path.join(root, "evals"))
    res = subprocess.run(
        [sys.executable, "evals/validate_golden.py", "--no-corpus"],
        cwd=root, env=env, capture_output=True, text=True,
    )
    if res.returncode != 0:
        print("golden.jsonl lint failed:\n" + res.stdout + res.stderr, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
