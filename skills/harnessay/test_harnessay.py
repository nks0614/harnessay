#!/usr/bin/env python3
"""합성 트랜스크립트로 파서+집계 self-check. python3 test_harnessay.py"""
import json
import os
import tempfile

from harnessay import aggregate, headline, render, skill_candidates


def line(o):
    return json.dumps(o) + "\n"


def main():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "proj-a"))
        with open(os.path.join(d, "proj-a", "s1.jsonl"), "w") as f:
            # Read 4회: 동일 내용 재읽기 1회(t2)만 낭비. 다른 범위(t3)·바뀐 내용(t4)은 아님
            reads = [("t1", {}, "x" * 400), ("t2", {}, "x" * 400),
                     ("t3", {"offset": 10}, "x" * 400), ("t4", {}, "z" * 400)]
            for tid, extra, body in reads:
                f.write(line({"type": "assistant", "message": {
                    "usage": {"output_tokens": 10, "cache_creation_input_tokens": 5,
                              "cache_read_input_tokens": 2},
                    "content": [{"type": "tool_use", "id": tid, "name": "Read",
                                 "input": {"file_path": "/a.py", **extra}}]}}))
                f.write(line({"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": body}]}}))
            f.write(line({"type": "assistant", "message": {
                "usage": {"output_tokens": 3, "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 0},
                "content": [{"type": "tool_use", "id": "t3", "name": "Bash",
                             "input": {"command": "ls"}}]}}))
            f.write(line({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t3",
                 "content": [{"type": "text", "text": "y" * 100}]}]}}))
            f.write(line({"type": "assistant", "isSidechain": True, "message": {
                "usage": {"output_tokens": 7, "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 0}, "content": []}}))
            f.write(line({"type": "system", "subtype": "compact_boundary"}))
            f.write("not json\n")  # 깨진 라인 무시 확인

        st = aggregate(d)
        assert st["totals"]["output"] == 43
        assert st["totals"]["sidechain_output"] == 7
        assert st["totals"]["compactions"] == 1
        assert st["tools"]["Read"] == {"calls": 4, "bytes": 1600}
        assert st["tools"]["Bash"] == {"calls": 1, "bytes": 100}
        assert st["reads"][("proj-a", "/a.py")] == 4
        assert st["redundant"][("proj-a", "/a.py")] == 400  # t2만 unchanged re-read
        h = headline(st)
        assert "Read" in h and "94%" in h, h      # 1600/1700
        assert "25.0%" in h, h                    # 재읽기 낭비 400/1600
        assert "proj-a" in render(st)

        # 스킬 후보: 같은 2-gram이 3개 프로젝트에서 반복 → personal
        seqs = [(p, ["Edit", "Bash:git", "Edit", "Bash:git"]) for p in ("a", "b", "c")]
        cands = skill_candidates(seqs)
        top = cands[0]
        assert top["gram"] == ("Edit", "Bash:git") and top["scope"] == "personal", top
        assert all(c["count"] >= 3 for c in cands)
        assert skill_candidates([("a", ["Read", "Read", "Read"])]) == []  # 연타 제외
        # 포함관계 접기: (Bash:git, Edit)은 항상 긴 gram 안에서만 등장 → 제거
        grams = [c["gram"] for c in cands]
        assert ("Bash:git", "Edit") not in grams, grams
        # 범용 툴로만 이뤄진 시퀀스는 후보 아님
        assert skill_candidates([("a", ["Read", "Edit", "Read", "Edit"])] * 3) == []

        # --since: 날짜 이전 라인 제외
        with open(os.path.join(d, "proj-a", "s2.jsonl"), "w") as f:
            for ts, tok in (("2026-01-01T00:00:00Z", 100), ("2026-06-01T00:00:00Z", 1)):
                f.write(line({"type": "assistant", "timestamp": ts, "message": {
                    "usage": {"output_tokens": tok, "cache_creation_input_tokens": 0,
                              "cache_read_input_tokens": 0}, "content": []}}))
        # s2의 1월 라인(100)만 걸러지고, timestamp 없는 s1 라인(23)은 포함
        st2 = aggregate(d, since="2026-03-01")
        assert st2["totals"]["output"] == 44, st2["totals"]
    print("ok")


if __name__ == "__main__":
    main()
