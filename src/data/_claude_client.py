#!/usr/bin/env python3
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "/usr/local/bin/claude"
CLAUDE_TIMEOUT = 600


def _enqueue_output(stream, line_queue, done_event):
    for line in iter(stream.readline, ""):
        line_queue.put(line)
    done_event.set()


def _drain_queue(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            break
    return items


def call_claude(
    prompt,
    json_schema=None,
    add_dirs=None,
    timeout=CLAUDE_TIMEOUT,
):
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

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        print(f"  [claude] ERROR: Binary not found at {CLAUDE_BIN}", file=sys.stderr)
        print(f"  [claude] Install with: npm install -g @anthropic-ai/claude-code", file=sys.stderr)
        return None

    stdout_queue = queue.Queue()
    stderr_queue = queue.Queue()
    stdout_done = threading.Event()
    stderr_done = threading.Event()

    stdout_thread = threading.Thread(
        target=_enqueue_output, args=(proc.stdout, stdout_queue, stdout_done), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_enqueue_output, args=(proc.stderr, stderr_queue, stderr_done), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    stdout_lines = []
    last_line = ""
    start_time = time.time()

    while not stdout_done.is_set() or not stderr_done.is_set() or not stdout_queue.empty():
        try:
            line = stdout_queue.get(timeout=0.5)
            stdout_lines.append(line)
            display = line.rstrip("\n\r")[:120]
            if len(line) > 120:
                display += "..."
            if display != last_line:
                if last_line:
                    sys.stderr.write(f"\033[K\r{last_line}\n")
                sys.stderr.write(f"\r[claude] {display}")
                sys.stderr.flush()
                last_line = display
        except queue.Empty:
            if proc.poll() is not None and stdout_queue.empty():
                break
            elapsed = int(time.time() - start_time)
            sys.stderr.write(f"\r[claude] ⏳ 运行中... ({elapsed}s)")
            sys.stderr.flush()
            continue

    proc.wait(timeout=30)
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)

    if last_line:
        sys.stderr.write(f"\033[K\r{last_line}\n")

    stderr_text = "".join(_drain_queue(stderr_queue))
    stdout_text = "".join(stdout_lines)

    if proc.returncode != 0:
        print(f"  [claude] ERROR: exit code {proc.returncode}", file=sys.stderr)
        stderr_tail = stderr_text.strip()[-1000:] if stderr_text else ""
        stdout_tail = stdout_text.strip()[-500:] if stdout_text else ""
        if stderr_tail:
            print(f"  [claude] stderr: {stderr_tail}", file=sys.stderr)
        if stdout_tail:
            print(f"  [claude] stdout: {stdout_tail}", file=sys.stderr)
        return None

    if not json_schema:
        return stdout_text

    try:
        wrapper = json.loads(stdout_text)
    except json.JSONDecodeError as e:
        print(f"  [claude] ERROR: Invalid JSON output: {e}", file=sys.stderr)
        print(f"  [claude] stdout preview: {stdout_text[:500]}", file=sys.stderr)
        return None

    structured = wrapper.get("structured_output")
    if structured is not None:
        return structured

    raw = wrapper.get("result", "")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            print(f"  [claude] WARNING: result field is not valid JSON, returning raw text", file=sys.stderr)
            return raw
    return None


def build_env():
    env = os.environ.copy()
    return env