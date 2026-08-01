#!/usr/bin/env python3
"""
Claude Code CLI client for Python scripts.

Provides a clean interface to invoke Claude Code CLI with
structured JSON output support.
"""
import json
import os
import subprocess
import sys

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/usr/local/bin/claude")
CLAUDE_TIMEOUT = 600  # 10 minutes default


def call_claude(
    prompt,
    json_schema=None,
    max_budget_usd=None,
    add_dirs=None,
    timeout=CLAUDE_TIMEOUT,
):
    """Invoke Claude Code CLI with the given prompt.

    Args:
        prompt: The prompt text to send.
        json_schema: Optional dict defining a JSON Schema for structured output.
                     When provided, the CLI returns parsed JSON in the
                     'structured_output' field.
        max_budget_usd: Maximum API cost budget.
        add_dirs: List of directory paths to allow Claude Code to access
                  via --add-dir.
        timeout: Subprocess timeout in seconds.

    Returns:
        Parsed JSON object if json_schema provided, or raw text string.
        None on error.
    """
    prompt_bytes = len(prompt.encode("utf-8"))
    print(f"  [claude] prompt size: {prompt_bytes:,} bytes", file=sys.stderr)
    if add_dirs:
        for d in add_dirs:
            print(f"  [claude] add-dir: {d}", file=sys.stderr)

    cmd = [
        CLAUDE_BIN, "-p", prompt, "--print",
        "--dangerously-skip-permissions",
    ]

    if json_schema:
        schema_str = json.dumps(json_schema, ensure_ascii=False)
        cmd.extend(["--json-schema", schema_str])
        cmd.extend(["--output-format", "json"])

    if add_dirs:
        for d in add_dirs:
            cmd.extend(["--add-dir", d])

    if max_budget_usd is not None:
        cmd.extend(["--max-budget-usd", str(max_budget_usd)])
    else:
        cmd.extend(["--max-budget-usd", "200"])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"  [claude] ERROR: Timed out after {timeout}s", file=sys.stderr)
        return None
    except FileNotFoundError:
        print(f"  [claude] ERROR: Binary not found at {CLAUDE_BIN}", file=sys.stderr)
        print(f"  [claude] Install with: npm install -g @anthropic-ai/claude-code", file=sys.stderr)
        return None

    if result.returncode != 0:
        print(f"  [claude] ERROR: exit code {result.returncode}", file=sys.stderr)
        stderr_tail = result.stderr.strip()[-1000:] if result.stderr else ""
        stdout_tail = result.stdout.strip()[-500:] if result.stdout else ""
        if stderr_tail:
            print(f"  [claude] stderr: {stderr_tail}", file=sys.stderr)
        if stdout_tail:
            print(f"  [claude] stdout: {stdout_tail}", file=sys.stderr)
        return None

    if json_schema:
        # Parse the JSON output wrapper
        try:
            wrapper = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            print(f"  [claude] ERROR: Invalid JSON output: {e}", file=sys.stderr)
            print(f"  [claude] stdout preview: {result.stdout[:500]}", file=sys.stderr)
            return None

        # Extract structured_output or fallback to result
        structured = wrapper.get("structured_output")
        if structured is not None:
            return structured

        # Fallback: try to parse the 'result' field as JSON
        raw = wrapper.get("result", "")
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                print(f"  [claude] WARNING: result field is not valid JSON, returning raw text", file=sys.stderr)
                return raw
        return None
    else:
        return result.stdout


def build_env():
    """Return environment dict for Claude Code invocation."""
    env = os.environ.copy()
    return env