#!/usr/bin/env python3
"""컨텍스트 예산 프로파일러 — ~/.claude/projects/*.jsonl 파싱 → 단일 HTML 리포트.

파싱 계층(parse_session)과 집계 계층(aggregate)을 분리. 트랜스크립트 포맷이
바뀌면 parse_session만 고친다.
"""
import argparse
import glob
import html
import json
import os
from collections import Counter, defaultdict

SCHEMA_VERSION = "2026-08-cc"  # 파싱 기준이 된 Claude Code 트랜스크립트 포맷


# ---------- 파싱 계층 ----------

def _content_size(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(b.get("text", "")) for b in content if isinstance(b, dict))
    return 0


def parse_session(path, since=None):
    """jsonl 한 파일 → 정규화된 이벤트 리스트.

    이벤트: {"kind": "usage"|"tool_use"|"tool_result"|"compact", ...}
    since: "YYYY-MM-DD" — ISO 타임스탬프라 문자열 비교로 충분.
    """
    events = []
    tool_names = {}  # tool_use_id -> name (결과 귀속용)
    for line in open(path, encoding="utf-8"):
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and (o.get("timestamp") or "9999") < since:
            continue
        t = o.get("type")
        if t == "assistant":
            m = o.get("message") or {}
            u = m.get("usage") or {}
            events.append({
                "kind": "usage",
                "sidechain": bool(o.get("isSidechain")),
                "output": u.get("output_tokens", 0),
                "cache_creation": u.get("cache_creation_input_tokens", 0),
                "cache_read": u.get("cache_read_input_tokens", 0),
            })
            for c in m.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tool_names[c.get("id")] = c.get("name", "?")
                    inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                    cmd = inp.get("command")
                    events.append({
                        "kind": "tool_use",
                        "name": c.get("name", "?"),
                        "file_path": inp.get("file_path"),
                        "cmd": cmd.split()[0] if isinstance(cmd, str) and cmd.split() else None,
                        "sidechain": bool(o.get("isSidechain")),
                    })
        elif t == "user":
            content = (o.get("message") or {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        events.append({
                            "kind": "tool_result",
                            "name": tool_names.get(b.get("tool_use_id"), "?"),
                            "size": _content_size(b.get("content")),
                        })
        elif t == "system" and o.get("subtype") == "compact_boundary":
            events.append({"kind": "compact"})
    return events


# ---------- 집계 계층 ----------

def aggregate(projects_dir, since=None):
    """{project: [events]} → 리포트용 stats dict."""
    st = {
        "since": since,
        "projects": defaultdict(lambda: Counter()),  # project -> counters
        "tools": defaultdict(lambda: Counter()),     # tool -> {calls, bytes}
        "reads": Counter(),                          # (project, file) -> count
        "read_bytes": Counter(),                     # (project, file) -> bytes
        "totals": Counter(),
        "seqs": [],                                  # (project, [tool token,...]) 세션별
    }
    pending_reads = []  # Read tool_use 순서대로 결과 크기를 귀속
    for f in sorted(glob.glob(os.path.join(projects_dir, "*", "*.jsonl"))):
        project = os.path.basename(os.path.dirname(f))
        p = st["projects"][project]
        p["sessions"] += 1
        seq = []
        st["seqs"].append((project, seq))
        for e in parse_session(f, since):
            k = e["kind"]
            if k == "usage":
                bucket = "sidechain_output" if e["sidechain"] else "output"
                p[bucket] += e["output"]
                st["totals"][bucket] += e["output"]
                for key in ("cache_creation", "cache_read"):
                    p[key] += e[key]
                    st["totals"][key] += e[key]
            elif k == "tool_use":
                st["tools"][e["name"]]["calls"] += 1
                p["tool_calls"] += 1
                seq.append(e["name"] + (":" + e["cmd"] if e.get("cmd") else ""))
                if e["name"] == "Read" and e["file_path"]:
                    st["reads"][(project, e["file_path"])] += 1
                    pending_reads.append((project, e["file_path"]))
            elif k == "tool_result":
                st["tools"][e["name"]]["bytes"] += e["size"]
                st["totals"]["result_bytes"] += e["size"]
                if e["name"] == "Read" and pending_reads:
                    st["read_bytes"][pending_reads.pop(0)] += e["size"]
            elif k == "compact":
                p["compactions"] += 1
                st["totals"]["compactions"] += 1
    return st


# 일반 코딩 동작 — 이것만으로 이뤄진 시퀀스는 스킬감이 아니라 코딩 그 자체
GENERIC = {"Read", "Edit", "Write", "Grep", "Glob", "TodoWrite",
           "Bash:cd", "Bash:ls", "Bash:cat", "Bash:echo", "Bash:mkdir"}


def skill_candidates(seqs, min_count=3, top=20):
    """반복 툴 시퀀스 n-gram → 스킬 후보. 3개 이상 프로젝트 공통이면 personal.

    자동 생성 안 함 — 증거만 제시하고 승격은 사람이 결정한다.
    """
    count, projects = Counter(), defaultdict(set)
    for project, seq in seqs:
        for n in (2, 3, 4):
            for i in range(len(seq) - n + 1):
                g = tuple(seq[i:i + n])
                if len(set(g)) == 1:
                    continue  # 같은 툴 연타는 스킬 후보가 아님
                count[g] += 1
                projects[g].add(project)
    kept = [g for g, c in count.most_common()
            if c >= min_count and not all(t in GENERIC for t in g)]

    def sub(a, b):  # a가 b의 연속 부분열인가
        return len(a) < len(b) and any(
            b[i:i + len(a)] == a for i in range(len(b) - len(a) + 1))

    # 짧은 gram이 항상 긴 gram 안에서만 등장(count 동일)하면 긴 쪽만 남긴다
    # ponytail: O(n²) 비교, 후보 수십 개 수준이라 충분
    kept = [g for g in kept
            if not any(sub(g, h) and count[g] == count[h] for h in kept)]
    return [{"gram": g, "count": count[g], "projects": sorted(projects[g]),
             "scope": "personal" if len(projects[g]) >= 3 else "project"}
            for g in kept[:top]]


def headline(st):
    """성공 기준 문장: 최대 비중 툴 + Read 재읽기 비율."""
    total = st["totals"]["result_bytes"]
    if not total:
        return "No data."
    top, c = max(st["tools"].items(), key=lambda kv: kv[1]["bytes"])
    dup_bytes = sum(st["read_bytes"][k] - st["read_bytes"][k] // st["reads"][k]
                    for k in st["reads"] if st["reads"][k] > 1)
    read_bytes = st["tools"].get("Read", Counter())["bytes"]
    s = f"{c['bytes'] * 100 // total}% of tool-result context is {top}."
    if read_bytes:
        s += (f" {dup_bytes * 100 // read_bytes}% of Read bytes are"
              " same-file re-reads.")
    return s


# ---------- 리포트 ----------

def fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def render(st):
    e = html.escape
    tok = lambda b: fmt(b // 4)  # ponytail: chars/4 근사, 정확한 토크나이저 필요해지면 교체
    rows_t = "".join(
        f"<tr><td>{e(name)}</td><td>{fmt(c['calls'])}</td><td>{fmt(c['bytes'])}</td>"
        f"<td>~{tok(c['bytes'])}</td><td>{c['bytes'] * 100 // max(1, st['totals']['result_bytes'])}%</td></tr>"
        for name, c in sorted(st["tools"].items(), key=lambda kv: -kv[1]["bytes"]))
    rows_p = "".join(
        f"<tr><td>{e(p)}</td><td>{c['sessions']}</td><td>{fmt(c['output'])}</td>"
        f"<td>{fmt(c['sidechain_output'])}</td><td>{fmt(c['cache_creation'])}</td>"
        f"<td>{fmt(c['tool_calls'])}</td><td>{c['compactions']}</td></tr>"
        for p, c in sorted(st["projects"].items(), key=lambda kv: -kv[1]["output"]))
    dups = [(k, n) for k, n in st["reads"].most_common(20) if n > 1]
    rows_d = "".join(
        f"<tr><td>{e(proj)}</td><td>{e(fp)}</td><td>{n}</td>"
        f"<td>{fmt(st['read_bytes'][(proj, fp)] - st['read_bytes'][(proj, fp)] // n)}</td></tr>"
        for (proj, fp), n in dups)
    rows_c = "".join(
        f"<tr><td><code>{e(' → '.join(c['gram']))}</code></td><td>{c['count']}</td>"
        f"<td>{len(c['projects'])}</td><td>{c['scope']}</td></tr>"
        for c in skill_candidates(st["seqs"]))
    T = st["totals"]
    return f"""<!doctype html><meta charset="utf-8"><title>harnessay report</title>
<style>body{{font:14px/1.5 -apple-system,sans-serif;max-width:960px;margin:2em auto;padding:0 1em}}
table{{border-collapse:collapse;width:100%;margin:1em 0}}td,th{{border:1px solid #ddd;padding:4px 8px;text-align:left}}
th{{background:#f5f5f5}}h2{{margin-top:2em}}.hl{{background:#fffbe6;padding:.8em 1em;border:1px solid #eed}}</style>
<h1>Context Budget Report <small>(schema {SCHEMA_VERSION}{
        ", since " + e(st["since"]) if st.get("since") else ""})</small></h1>
<p class="hl"><b>{e(headline(st))}</b></p>
<p>output {fmt(T['output'])} tok (sidechain {fmt(T['sidechain_output'])}) ·
cache write {fmt(T['cache_creation'])} · cache read {fmt(T['cache_read'])} ·
tool results {fmt(T['result_bytes'])} B · {T['compactions']} compactions</p>
<h2>Context consumption by tool</h2>
<table><tr><th>tool</th><th>calls</th><th>result bytes</th><th>~tokens</th><th>share</th></tr>{rows_t}</table>
<h2>By project</h2>
<table><tr><th>project</th><th>sessions</th><th>output tok</th><th>sidechain tok</th><th>cache write</th><th>tool calls</th><th>compactions</th></tr>{rows_p}</table>
<h2>Top 20 duplicate reads</h2>
<table><tr><th>project</th><th>file</th><th>count</th><th>wasted bytes</th></tr>{rows_d}</table>
<h2>Skill candidates (repeated tool sequences)</h2>
<p>Sequences repeated 3+ times. Shared across 3+ projects → <code>personal</code>
(~/.claude/skills), otherwise <code>project</code> (.claude/skills). Promotion is manual.</p>
<table><tr><th>sequence</th><th>count</th><th>projects</th><th>suggestion</th></tr>{rows_c}</table>"""


def main():
    ap = argparse.ArgumentParser(description="Claude Code 컨텍스트 예산 프로파일러")
    ap.add_argument("projects_dir", nargs="?",
                    default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("-o", "--out", default="report.html")
    ap.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후 라인만 집계")
    args = ap.parse_args()
    st = aggregate(args.projects_dir, args.since)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(st))
    print(headline(st))
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
