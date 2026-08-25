#!/usr/bin/env python3
"""Skill regression harness — run golden tasks via `claude -p`, track pass rate.

Usage: python3 evalrun.py [tasks.json]
Results accumulate in results.jsonl next to the tasks file.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def run_task(t, timeout):
    cmd = ["claude", "-p", t["prompt"], "--output-format", "json"]
    if t.get("model"):
        cmd += ["--model", t["model"]]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        try:
            text = json.loads(r.stdout).get("result", "")
        except json.JSONDecodeError:
            text = r.stdout
    except subprocess.TimeoutExpired:
        text = "<timeout>"
    chk = t["check"]
    if chk["type"] == "contains":
        ok = chk["value"] in text
    elif chk["type"] == "regex":
        ok = re.search(chk["value"], text) is not None
    else:
        raise ValueError(f"unknown check type: {chk['type']}")
    return ok, text, round(time.time() - t0, 1)


def main():
    ap = argparse.ArgumentParser(description="스킬 골든 태스크 배치 실행")
    ap.add_argument("tasks", nargs="?",
                    default=os.path.join(HERE, "eval", "tasks.json"))
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--only", help="task id 부분 일치 필터")
    args = ap.parse_args()

    tasks = json.load(open(args.tasks, encoding="utf-8"))
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
            ok, text, dur = run_task(t, args.timeout)
            passed += ok
            print(f"{'PASS' if ok else 'FAIL'}  {t['id']}  ({dur}s)")
            if not ok:
                print(f"      → {text[:200]}")
            out.write(json.dumps({
                "ts": ts, "id": t["id"], "skill": t.get("skill"),
                "pass": ok, "duration": dur,
            }, ensure_ascii=False) + "\n")
    print(f"\n{passed}/{len(tasks)} passed")
    sys.exit(0 if passed == len(tasks) else 1)


if __name__ == "__main__":
    main()
