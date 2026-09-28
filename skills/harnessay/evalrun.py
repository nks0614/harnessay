#!/usr/bin/env python3
"""Skill regression harness — run golden tasks via Claude or Codex.

Usage: python3 evalrun.py [tasks.json] [--provider claude|codex]
Results accumulate in results.jsonl next to the tasks file.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def validate_task(t, provider):
    if not isinstance(t, dict):
        raise ValueError("each task must be an object")
    for field in ("id", "prompt"):
        if not isinstance(t.get(field), str) or not t[field].strip():
            raise ValueError(f"task {field} must be a nonempty string")
    if provider not in ("claude", "codex") or t.get("provider", provider) not in ("claude", "codex"):
        raise ValueError("provider must be claude or codex")
    for field in ("model", "skill"):
        if t.get(field) is not None and (not isinstance(t[field], str) or not t[field].strip()):
            raise ValueError(f"task {field} must be a nonempty string or null")
    chk = t.get("check")
    if not isinstance(chk, dict) or chk.get("type") not in ("contains", "regex"):
        raise ValueError("check type must be contains or regex")
    if not isinstance(chk.get("value"), str):
        raise ValueError("check value must be a string")
    if chk["type"] == "regex":
        try:
            re.compile(chk["value"])
        except re.error as exc:
            raise ValueError(f"invalid check regex: {exc}") from exc


def run_task(t, timeout, provider="claude"):
    validate_task(t, provider)
    provider = t.get("provider", provider)
    t0 = time.monotonic()
    succeeded = False
    try:
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "last-message.txt")
            cmd = (["codex", "exec", "--sandbox", "read-only", "--output-last-message", output]
                   if provider == "codex" else
                   ["claude", "-p", t["prompt"], "--output-format", "json"])
            if t.get("model"):
                cmd += ["--model", t["model"]]
            if provider == "codex":
                cmd += ["-"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                               **({"input": t["prompt"]} if provider == "codex" else {}))
            succeeded = r.returncode == 0
            if provider == "codex":
                with open(output, encoding="utf-8") as message:
                    text = message.read()
            else:
                try:
                    response = json.loads(r.stdout)
                    text = response.get("result", "") if isinstance(response, dict) else ""
                    succeeded = succeeded and isinstance(response, dict) and not response.get("is_error")
                    if not isinstance(text, str):
                        text, succeeded = "<invalid result>", False
                except json.JSONDecodeError:
                    text = r.stdout
            if r.returncode:
                text = f"<exit {r.returncode}> {text}\n{r.stderr}"
    except subprocess.TimeoutExpired:
        text, succeeded = "<timeout>", False
    except OSError as exc:
        text, succeeded = f"<execution error: {exc}>", False
    chk = t["check"]
    ok = succeeded and (chk["value"] in text if chk["type"] == "contains"
                        else re.search(chk["value"], text) is not None)
    return ok, text, round(time.monotonic() - t0, 1)


def main():
    ap = argparse.ArgumentParser(description="스킬 골든 태스크 배치 실행")
    ap.add_argument("tasks", nargs="?",
                    default=os.path.join(HERE, "eval", "tasks.json"))
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--provider", choices=("claude", "codex"), default="claude")
    ap.add_argument("--only", help="task id 부분 일치 필터")
    args = ap.parse_args()

    try:
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        with open(args.tasks, encoding="utf-8") as source:
            tasks = json.load(source)
        if not isinstance(tasks, list):
            raise ValueError("tasks must be a list")
        for t in tasks:
            validate_task(t, args.provider)
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    if args.only:
        tasks = [t for t in tasks if args.only in t["id"]]
    if not tasks:
        sys.exit("no tasks to run")

    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    passed = 0
    results = os.path.join(os.path.dirname(os.path.abspath(args.tasks)),
                           "results.jsonl")
    with open(results, "a", encoding="utf-8") as out:
        for t in tasks:  # ponytail: 순차 실행, 태스크 10개 넘으면 병렬화
            ok, text, dur = run_task(t, args.timeout, args.provider)
            passed += ok
            print(f"{'PASS' if ok else 'FAIL'}  {t['id']}  ({dur}s)")
            if not ok:
                print(f"      → {text[:200]}")
            out.write(json.dumps({
                "ts": ts, "id": t["id"], "skill": t.get("skill"),
                "provider": t.get("provider", args.provider), "model": t.get("model"),
                "pass": ok, "duration": dur,
            }, ensure_ascii=False) + "\n")
    print(f"\n{passed}/{len(tasks)} passed")
    sys.exit(0 if passed == len(tasks) else 1)


if __name__ == "__main__":
    main()
