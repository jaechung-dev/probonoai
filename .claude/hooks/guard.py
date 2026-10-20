#!/usr/bin/env python3
"""PreToolUse hook: block access to secrets, Terraform state and destructive git/shell commands.

Exit code 2 blocks the tool call and feeds stderr back to Claude. Exit 0 allows it.
A hook is used (not a prompt rule) because these must hold on every call.
"""
import json
import re
import sys

PROTECTED_PATHS = re.compile(
    r"(^|/)(\.env(\.[^/]*)?|api_keys\.json|[^/]*\.tfstate(\.backup)?|\.terraform/|\.blocklist\.txt)$"
)
ALLOWED_EXAMPLES = re.compile(r"(\.example|\.sample)$")
DESTRUCTIVE_BASH = re.compile(
    r"(git\s+push\s+.*--force|git\s+push\s+.*\s-f\b|git\s+reset\s+--hard|git\s+clean\s+-[a-z]*f|rm\s+-[a-z]*r[a-z]*f?\s+(/|~|\.)(\s|$)"
    r"|terraform\s+(apply|destroy)|DROP\s+(TABLE|DATABASE)|TRUNCATE\s+)",
    re.IGNORECASE,
)
SECRET_READ_BASH = re.compile(r"(cat|less|more|head|tail|grep|sed|awk|cp|source|\.)\s+[^|;&]*(\.env\b|api_keys\.json|\.tfstate)")


def block(msg: str) -> None:
    print(f"Blocked by .claude/hooks/guard.py: {msg}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}

    if tool in {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}:
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        if PROTECTED_PATHS.search(path) and not ALLOWED_EXAMPLES.search(path):
            block(f"{path} is a secret/state file. Use the .example file or ask the user.")

    if tool == "Bash":
        cmd = inp.get("command", "")
        if DESTRUCTIVE_BASH.search(cmd):
            block("destructive command. Ask the user to run it themselves.")
        if SECRET_READ_BASH.search(cmd) and not ALLOWED_EXAMPLES.search(cmd):
            block("command reads a secret file. Use the .example file or ask the user.")

    sys.exit(0)


if __name__ == "__main__":
    main()
