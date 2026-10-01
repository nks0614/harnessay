#!/usr/bin/env python3
"""Claude Code / Codex 로컬 기록 → 단일 HTML 컨텍스트 리포트.

파싱 계층(parse_session)과 집계 계층(aggregate)을 분리. 트랜스크립트 포맷이
바뀌면 parse_session만 고친다.
"""
import argparse
import datetime
import glob
import hashlib
import html
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from report_data import export_stats, compare_reports, USAGE_FIELDS, usage_value

SCHEMA_VERSION = "2026-09-claude-codex"


# ---------- 파싱 계층 ----------

def _object(value):
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    return value


def _text(value, default=None):
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError("expected text")
    return value


def _tokens(usage):
    usage = _object(usage)
    fields = {"input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
              "cached_input_tokens", "cache_write_input_tokens", "reasoning_output_tokens", "total_tokens"}
    values = {k: v for k, v in usage.items() if k in fields}
    if any(type(v) is not int or v < 0 for v in values.values()):
        raise ValueError("invalid token count")
    return values


def _usage_metrics(u, provider):
    """Normalize provider counters without treating an omitted component as zero."""
    values = {"output_tokens": u.get("output_tokens")}
    if provider == "claude":
        fresh, written, cached = (u.get(k) for k in
                                 ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        values.update(fresh_input_tokens=fresh, cached_input_tokens=cached, cache_write_input_tokens=written)
        if fresh is not None and written is not None:
            values["uncached_input_tokens"] = fresh + written
            if cached is not None:
                values["input_tokens"] = fresh + written + cached
    else:
        values.update({key: u.get(key) for key in USAGE_FIELDS
                       if key not in {"uncached_input_tokens", "fresh_input_tokens"}})
        total, cached = u.get("input_tokens"), u.get("cached_input_tokens")
        if total is not None and cached is not None:
            if cached > total:
                raise ValueError("cached input exceeds total input")
            values["uncached_input_tokens"] = total - cached
            written = u.get("cache_write_input_tokens")
            if written is not None:
                if written > total - cached:
                    raise ValueError("cache write exceeds uncached input")
                values["fresh_input_tokens"] = total - cached - written
        if (u.get("reasoning_output_tokens") is not None and u.get("output_tokens") is not None
                and u["reasoning_output_tokens"] > u["output_tokens"]):
            raise ValueError("reasoning output exceeds total output")
    return {key: value for key, value in values.items() if value is not None}


def _content_text(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_text(_object(b).get("text"), "") for b in content)
    raise ValueError("invalid content")


def _records(path, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else Counter()
    try:
        with open(path, "rb") as stream:
            diagnostics["files_read"] += 1
            for number, line in enumerate(stream, 1):
                diagnostics["lines_read"] += 1
                try:
                    record = json.loads(line.decode("utf-8"))
                except UnicodeDecodeError:
                    diagnostics["invalid_encoding"] += 1
                    continue
                except (ValueError, RecursionError):
                    diagnostics["invalid_json"] += 1
                    continue
                if not isinstance(record, dict):
                    diagnostics["invalid_records"] += 1
                    continue
                record["_source"] = {"path": os.path.abspath(path), "line": number,
                                     "timestamp": record.get("timestamp")}
                yield record
    except OSError:
        diagnostics["unreadable_files"] += 1


def _selected(record, since, until, diagnostics):
    stamp = _text(record.get("timestamp"))
    if not stamp:
        if since or until:
            diagnostics["undated_excluded"] += 1
            return False
        return True
    moment = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    # Transcripts without an offset are interpreted as UTC.
    day = moment.replace(tzinfo=moment.tzinfo or datetime.timezone.utc).astimezone(
        datetime.timezone.utc).date().isoformat()
    return (not since or day >= since) and (not until or day < until)


def _event(record, kind, **fields):
    return {"kind": kind, "source": record["_source"], **fields}


def parse_session(path, since=None, until=None, diagnostics=None):
    """Claude JSONL → normalized events, with physical source line numbers."""
    diagnostics = diagnostics if diagnostics is not None else Counter()
    events, tool_names, usage_indices = [], {}, {}
    for o in _records(path, diagnostics):
        batch = []
        try:
            selected = _selected(o, since, until, diagnostics)
            t = _text(o.get("type"))
            if t == "assistant":
                m = _object(o.get("message"))
                u = _tokens(m.get("usage", {}))
                mid = _text(m.get("id"))
                sidechain = bool(o.get("isSidechain")) or "subagents" in str(path).split(os.sep)
                usage = _event(o, "usage", sidechain=sidechain, metrics=_usage_metrics(u, "claude"),
                               output=u.get("output_tokens", 0),
                               cache_creation=u.get("cache_creation_input_tokens", 0),
                               cache_read=u.get("cache_read_input_tokens", 0))
                content = m.get("content", [])
                if not isinstance(content, list):
                    raise ValueError("invalid assistant content")
                for c in content:
                    c = _object(c)
                    if c.get("type") != "tool_use":
                        continue
                    tid = _text(c.get("id"))
                    name = _text(c.get("name"), "?")
                    inp = _object(c.get("input", {}))
                    cmd = _text(inp.get("command"))
                    file_path = _text(inp.get("file_path"))
                    for field in ("offset", "limit"):
                        if inp.get(field) is not None and (type(inp[field]) is not int or inp[field] < 0):
                            raise ValueError("invalid read range")
                    tool_names[tid] = name
                    batch.append(_event(o, "tool_use", id=tid, name=name,
                                        file_path=file_path, offset=inp.get("offset"), limit=inp.get("limit"),
                                        cmd=cmd.split()[0] if cmd and cmd.split() else None))
                if selected:
                    if mid and mid in usage_indices:
                        events[usage_indices[mid]] = usage
                    else:
                        if mid:
                            usage_indices[mid] = len(events)
                        events.append(usage)
            elif t == "user":
                content = _object(o.get("message")).get("content")
                if isinstance(content, str) or content is None:
                    continue
                if not isinstance(content, list):
                    raise ValueError("invalid user content")
                for b in content:
                    b = _object(b)
                    if b.get("type") != "tool_result":
                        continue
                    tid = _text(b.get("tool_use_id"))
                    name = tool_names.get(tid, "?")
                    text = _content_text(b.get("content"))
                    batch.append(_event(o, "tool_result", id=tid, name=name,
                                        size=len(text.encode("utf-8")), error=bool(b.get("is_error")),
                                        hash=hashlib.md5(text.encode()).hexdigest() if name == "Read" else None))
            elif t == "system" and o.get("subtype") == "compact_boundary":
                batch.append(_event(o, "compact"))
            elif t not in {"system", "progress", "summary", "file-history-snapshot", "queue-operation",
                           "custom-title", "last-prompt", "saved_hook_context"}:
                diagnostics["unsupported_records"] += 1
            if selected:
                events.extend(batch)
        except (ValueError, TypeError, OverflowError):
            diagnostics["invalid_records"] += 1
    return events


def _codex_owner(path):
    for record in _records(path):
        if record.get("type") == "session_meta" and isinstance(record.get("payload"), dict):
            return record["payload"]
    return {}


def parse_codex_session(path, since=None, until=None, diagnostics=None, owner=None, include_excluded=False):
    """Codex rollout → (project, events). First metadata identifies the owner."""
    diagnostics = diagnostics if diagnostics is not None else Counter()
    owner = _codex_owner(path) if owner is None else owner
    project = owner.get("cwd") if isinstance(owner.get("cwd"), str) else "unknown"
    sid = owner.get("id") if isinstance(owner.get("id"), str) else None
    parent = owner.get("forked_from_id") if isinstance(owner.get("forked_from_id"), str) else None
    source = owner.get("source")
    sidechain = isinstance(source, dict) and "subagent" in source
    boundary = owner.get("subagent_history_start_ordinal")
    boundary = boundary if type(boundary) is int and boundary > 0 else 0
    events, tool_names, previous, seen_usage = [], {}, {}, set()
    has_usage_records, copied_meta = False, False
    previous_fields, has_cumulative = set(), False
    for o in _records(path, diagnostics):
        batch = []
        try:
            t, p = _text(o.get("type")), _object(o.get("payload"))
            if t == "session_meta":
                if parent and p.get("id") == parent:
                    copied_meta = True
                continue
            selected = _selected(o, since, until, diagnostics)
            ordinal = o.get("ordinal", o["_source"]["line"] - 1)
            if type(ordinal) is not int or ordinal < 0:
                raise ValueError("invalid record ordinal")
            inherited = bool(parent and copied_meta and ordinal < boundary)
            if inherited:
                diagnostics["inherited_records"] += 1
                selected = False
            if t == "token_usage_record" or (t == "event_msg" and p.get("type") == "token_count"):
                if t == "token_usage_record":
                    u = _tokens(p.get("usage", {}))
                    rid = _text(p.get("response_id"))
                    thread_id = _text(p.get("thread_id"))
                    if sid and thread_id and thread_id != sid:
                        if not inherited:
                            diagnostics["inherited_records"] += 1
                        continue
                    if rid and rid in seen_usage:
                        diagnostics["duplicate_events"] += 1
                        continue
                    if rid:
                        seen_usage.add(rid)
                    has_usage_records = True
                    identity = rid
                else:
                    if has_usage_records:
                        continue
                    total = _object(p.get("info", {}) or {}).get("total_token_usage")
                    if total is None:
                        continue
                    total = _tokens(total)
                    u = {key: value - previous.get(key, 0) if value >= previous.get(key, 0) else value
                         for key, value in total.items()}
                    previous = total
                    identity = None  # cumulative counters have no stable response identity
                if t != "token_usage_record":
                    # A reappearing counter spans an unknown gap, possibly outside the requested dates.
                    u = {key: value for key, value in u.items()
                         if not has_cumulative or key in previous_fields}
                    previous_fields, has_cumulative = set(total), True
                metrics = _usage_metrics(u, "codex")
                batch.append(_event(o, "usage", id=identity, sidechain=sidechain, metrics=metrics,
                                    output=u.get("output_tokens", 0),
                                    cache_creation=u.get("cache_write_input_tokens", 0),
                                    cache_read=u.get("cached_input_tokens", 0)))
            elif t == "response_item":
                kind, tid = _text(p.get("type")), _text(p.get("call_id"))
                if kind in ("function_call", "custom_tool_call"):
                    name = _text(p.get("name"), "?")
                    tool_names[tid] = name
                    raw = p.get("arguments")
                    inp = _object(json.loads(raw)) if isinstance(raw, str) and raw else {}
                    cmd = inp.get("cmd", inp.get("command"))
                    # shell tool command arrays are valid but not reduced to a command label.
                    cmd = cmd if isinstance(cmd, str) else None
                    batch.append(_event(o, "tool_use", id=tid, name=name,
                                        cmd=cmd.split()[0] if cmd and cmd.split() else None))
                elif kind in ("function_call_output", "custom_tool_call_output"):
                    text = _content_text(p.get("output"))
                    batch.append(_event(o, "tool_result", id=tid, name=tool_names.get(tid, "?"),
                                        size=len(text.encode("utf-8"))))
                elif kind not in {"message", "reasoning", "agent_message"}:
                    diagnostics["unsupported_records"] += 1
            elif t == "compacted":
                batch.append(_event(o, "compact"))
            elif t not in {"event_msg", "turn_context", "world_state", "inter_agent_communication_metadata"}:
                diagnostics["unsupported_records"] += 1
            # Deduplicate original identities before date filtering, including replay timestamps.
            for event in batch:
                event["selected"] = selected
                event["inherited"] = inherited
                if event.get("id"):
                    event["dedup_key"] = (event["kind"], event["id"])
            if selected or include_excluded:
                events.extend(batch)
        except (ValueError, TypeError, OverflowError):
            diagnostics["invalid_records"] += 1
    return "codex:" + project, events


# ---------- 집계 계층 ----------

def _keep_largest(rows, row):
    rows.append(row)
    rows.sort(key=lambda r: r["bytes"], reverse=True)
    del rows[20:]


def aggregate(projects_dir=None, since=None, source="claude", codex_dir=None, until=None):
    """{project: [events]} → 리포트용 stats dict."""
    st = {
        "since": since,
        "until": until,
        "source": source,
        "diagnostics": Counter(),
        "evidence": {"large_results": [], "redundant_reads": [], "candidates": []},
        "projects": defaultdict(lambda: Counter()),  # project -> counters
        "tools": defaultdict(lambda: Counter()),     # tool -> {calls, bytes}
        "reads": Counter(),                          # (project, file) -> Read 횟수
        "redundant": Counter(),                      # (project, file) -> 동일 내용 재읽기 bytes
        "totals": Counter(),
        "usage": {"records": 0, "fields": {key: {"tokens": 0, "records": 0} for key in USAGE_FIELDS}},
        "seqs": [],                                  # (project, [tool token,...]) 세션별
    }
    if source not in ("claude", "codex", "all"):
        raise ValueError("source must be claude, codex, or all")
    sessions = []
    if source in ("claude", "all"):
        root = os.path.expanduser(projects_dir or "~/.claude/projects")
        sessions.extend((f, "claude", os.path.relpath(f, root).split(os.sep)[0], {})
                        for f in sorted(glob.glob(os.path.join(root, "*", "**", "*.jsonl"), recursive=True)))
    if source in ("codex", "all"):
        root = codex_dir or os.path.join(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")), "sessions")
        sessions.extend((f, "codex", None, _codex_owner(f)) for f in sorted(glob.glob(
            os.path.join(os.path.expanduser(root), "**", "*.jsonl"), recursive=True)))
    parents = {o["id"]: o.get("forked_from_id") for _, p, _, o in sessions
               if p == "codex" and isinstance(o.get("id"), str)}

    def lineage(owner, path):
        sid = owner.get("id")
        root = sid if isinstance(sid, str) else path
        visited = set()
        while isinstance(parents.get(root), str) and root not in visited:
            visited.add(root)
            root = parents[root]
        return root, len(visited)

    sessions.sort(key=lambda s: (lineage(s[3], s[0])[1], s[0]))
    seen_events, sequence_sources = set(), []
    for f, provider, project, owner in sessions:
        if provider == "codex":
            project, events = parse_codex_session(f, since, until, st["diagnostics"], owner, True)
            unique = []
            for event in events:
                key = event.get("dedup_key")
                key = (lineage(owner, f)[0], key) if key else None
                if key and key in seen_events:
                    if not event["inherited"]:
                        st["diagnostics"]["duplicate_events"] += 1
                    continue
                if key:
                    seen_events.add(key)
                if event["selected"]:
                    unique.append(event)
            events = unique
        else:
            events = parse_session(f, since, until, st["diagnostics"])
        if not events:
            continue
        p = st["projects"][project]
        p["sessions"] += 1
        seq = []
        sources = []
        st["seqs"].append((project, seq))
        sequence_sources.append(sources)
        # 세션(=컨텍스트 윈도우) 단위로 판정: 새 세션의 재읽기는 낭비가 아니다
        pending_reads = {}  # 병렬 호출은 결과 도착 순서가 호출 순서와 다를 수 있다
        last_hash = {}      # (file, offset, limit) -> 직전 결과 hash
        for e in events:
            k = e["kind"]
            if k == "usage":
                st["usage"]["records"] += 1
                for key, value in e["metrics"].items():
                    st["usage"]["fields"][key]["tokens"] += value
                    st["usage"]["fields"][key]["records"] += 1
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
                sources.append(e["source"])
                if e["name"] == "Read" and e.get("file_path"):
                    st["reads"][(project, e["file_path"])] += 1
                    pending_reads[e["id"]] = (e["file_path"], e["offset"], e["limit"])
            elif k == "tool_result":
                st["tools"][e["name"]]["bytes"] += e["size"]
                st["totals"]["result_bytes"] += e["size"]
                _keep_largest(st["evidence"]["large_results"], {
                    "project": project, "tool": e["name"], "bytes": e["size"], "source": e["source"]})
                key = pending_reads.pop(e.get("id"), None)
                if key and not e.get("error"):
                    # 같은 파일·같은 범위·같은 내용이 다시 들어왔을 때만 낭비
                    if last_hash.get(key) == e["hash"]:
                        st["redundant"][(project, key[0])] += e["size"]
                        st["totals"]["redundant_bytes"] += e["size"]
                        _keep_largest(st["evidence"]["redundant_reads"], {
                            "project": project, "file": key[0], "bytes": e["size"], "source": e["source"]})
                    last_hash[key] = e["hash"]
            elif k == "compact":
                last_hash.clear()
                pending_reads.clear()
                p["compactions"] += 1
                st["totals"]["compactions"] += 1
    st["diagnostics"]["files_found"] = len(sessions)
    for candidate in skill_candidates(st["seqs"]):
        examples = []
        gram = candidate["gram"]
        for (project, seq), sources in zip(st["seqs"], sequence_sources):
            for i in range(len(seq) - len(gram) + 1):
                if tuple(seq[i:i + len(gram)]) == gram:
                    examples.append({"project": project, "source": sources[i]})
                    break  # one example per context
            if len(examples) >= 3:
                break
        st["evidence"]["candidates"].append({**candidate, "examples": examples})
    return st


# 일반 코딩 동작 — 이것만으로 이뤄진 시퀀스는 스킬감이 아니라 코딩 그 자체
GENERIC = {"Read", "Edit", "Write", "Grep", "Glob", "TodoWrite",
           "Bash:cd", "Bash:ls", "Bash:cat", "Bash:echo", "Bash:mkdir",
           "apply_patch", "update_plan", "write_stdin",
           "exec_command:cd", "exec_command:ls", "exec_command:cat", "exec_command:echo",
           "exec_command:mkdir", "exec_command:rg", "exec_command:sed"}


def skill_candidates(seqs, min_count=3, top=20):
    """반복 툴 시퀀스 n-gram → 스킬 후보. 3개 이상 프로젝트 공통이면 personal.

    자동 생성 안 함 — 증거만 제시하고 승격은 사람이 결정한다.
    """
    count, projects, sessions = Counter(), defaultdict(set), defaultdict(set)
    for session, (project, seq) in enumerate(seqs):
        for n in (2, 3, 4):
            for i in range(len(seq) - n + 1):
                g = tuple(seq[i:i + n])
                if len(set(g)) == 1:
                    continue  # 같은 툴 연타는 스킬 후보가 아님
                count[g] += 1
                projects[g].add(project)
                sessions[g].add(session)
    kept = [g for g, c in count.most_common()
            if c >= min_count and not all(t in GENERIC for t in g)]

    def sub(a, b):  # a가 b의 연속 부분열인가
        return len(a) < len(b) and any(
            b[i:i + len(a)] == a for i in range(len(b) - len(a) + 1))

    # 짧은 gram이 항상 긴 gram 안에서만 등장(count 동일)하면 긴 쪽만 남긴다
    # ponytail: O(n²) 비교, 후보 수십 개 수준이라 충분
    kept = [g for g in kept
            if not any(sub(g, h) and count[g] == count[h] for h in kept)]
    return [{"gram": g, "count": count[g], "sessions": len(sessions[g]),
             "single_session": len(sessions[g]) == 1, "projects": sorted(projects[g]),
             "scope": "personal" if len(projects[g]) >= 3 else "project"}
            for g in kept[:top]]


def headline(st):
    """성공 기준 문장: 최대 비중 툴 + Read 재읽기 비율."""
    total = st["totals"]["result_bytes"]
    if not total:
        return "No data."
    top, c = max(st["tools"].items(), key=lambda kv: kv[1]["bytes"])
    read_bytes = st["tools"].get("Read", Counter())["bytes"]
    s = f"{c['bytes'] * 100 // total}% of tool-result context is {top}."
    if read_bytes:
        s += (f" {st['totals']['redundant_bytes'] * 100 / read_bytes:.1f}% of"
              " Read bytes re-read unchanged content.")
    return s


# ---------- 리포트 ----------

def fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _source_link(source):
    path = source["path"]
    label = f"{os.path.basename(path)}:{source['line']}"
    return (f'<a href="{html.escape(Path(path).as_uri(), quote=True)}" '
            f'title="{html.escape(path, quote=True)}">{html.escape(label)}</a>'
            f' <small>{html.escape(source.get("timestamp") or "undated")}</small>')


def render(st, comparison=None):
    e = html.escape
    tok = lambda b: fmt(b // 4)  # ponytail: UTF-8 bytes/4 근사, 정확한 비교가 필요해지면 토크나이저 사용
    rows_t = "".join(
        f"<tr><td>{e(name)}</td><td>{fmt(c['calls'])}</td><td>{fmt(c['bytes'])}</td>"
        f"<td>~{tok(c['bytes'])}</td><td>{c['bytes'] * 100 // max(1, st['totals']['result_bytes'])}%</td></tr>"
        for name, c in sorted(st["tools"].items(), key=lambda kv: -kv[1]["bytes"]))
    rows_p = "".join(
        f"<tr><td>{e(p)}</td><td>{c['sessions']}</td><td>{fmt(c['output'])}</td>"
        f"<td>{fmt(c['sidechain_output'])}</td><td>{fmt(c['cache_creation'])}</td>"
        f"<td>{fmt(c['tool_calls'])}</td><td>{c['compactions']}</td></tr>"
        for p, c in sorted(st["projects"].items(), key=lambda kv: -kv[1]["output"]))
    rows_d = "".join(
        f"<tr><td>{e(proj)}</td><td>{e(fp)}</td><td>{st['reads'][(proj, fp)]}</td>"
        f"<td>{fmt(b)}</td></tr>"
        for (proj, fp), b in st["redundant"].most_common(20))
    rows_m = "".join(
        f"<tr><td>{e(proj)}</td><td>{e(fp)}</td><td>{n}</td></tr>"
        for (proj, fp), n in st["reads"].most_common(15) if n >= 5)
    rows_c = "".join(
        f"<tr><td><code>{e(' → '.join(c['gram']))}</code></td><td>{c['count']}</td>"
        f"<td>{c.get('sessions', 'n/a')}</td><td>{len(c['projects'])}</td>"
        f"<td>{'Review single-session repetition' if c.get('single_session') else c['scope']}</td>"
        f"<td>{'<br>'.join(_source_link(x['source']) for x in c.get('examples', []))}</td></tr>"
        for c in st.get("evidence", {}).get("candidates", skill_candidates(st["seqs"])))
    diagnostics = " · ".join(f"{e(key)}: {value}" for key, value in sorted(st.get("diagnostics", {}).items()))
    rows_large = "".join(
        f"<tr><td>{e(row['project'])}</td><td>{e(row['tool'])}</td><td>{fmt(row['bytes'])}</td>"
        f"<td>{_source_link(row['source'])}</td></tr>" for row in st.get("evidence", {}).get("large_results", []))
    rows_repeat = "".join(
        f"<tr><td>{e(row['file'])}</td><td>{fmt(row['bytes'])}</td>"
        f"<td>{_source_link(row['source'])}</td></tr>" for row in st.get("evidence", {}).get("redundant_reads", []))
    usage = st.get("usage", {})
    rows_usage = ""
    for key in USAGE_FIELDS:
        field = usage.get("fields", {}).get(key, {})
        known = field.get("records", 0)
        total = usage_value(usage, key)
        rows_usage += (f"<tr><td>{key}</td><td>{fmt(total) if total is not None else 'n/a'}</td>"
                       f"<td>{fmt(field['tokens']) if known else 'n/a'}</td>"
                       f"<td>{known}/{usage.get('records', 0)}</td></tr>")
    comparison_html = ""
    if comparison:
        def number(value):
            return "n/a" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")
        compare_rows = "".join(
            f"<tr><td>{e(row['metric'])}</td><td>{number(row['before'])}</td>"
            f"<td>{number(row['after'])}</td><td>{number(row['delta'])}</td>"
            f"<td>{number(row['percent_change'])}{'%' if row['percent_change'] is not None else ''}</td></tr>"
            for row in comparison["metrics"])
        baseline, current = comparison["baseline"], comparison["current"]
        comparison_html = ("<h2>Before / after comparison</h2>"
            f"<p>Before: [{e(baseline.get('since') or 'unbounded')}, {e(baseline.get('until') or 'unbounded')}); "
            f"after: [{e(current.get('since') or 'unbounded')}, {e(current.get('until') or 'unbounded')}). UTC.</p>"
            + "".join(f"<p>{e(warning)}</p>" for warning in comparison["warnings"])
            + "<table><tr><th>metric</th><th>before</th><th>after</th><th>change</th><th>relative change</th></tr>"
            + compare_rows + "</table><p>Changes are descriptive; different tasks and models can also change usage.</p>")
    T = st["totals"]
    return f"""<!doctype html><meta charset="utf-8"><title>harnessay report</title>
<style>body{{font:14px/1.5 -apple-system,sans-serif;max-width:960px;margin:2em auto;padding:0 1em}}
table{{border-collapse:collapse;width:100%;margin:1em 0}}td,th{{border:1px solid #ddd;padding:4px 8px;text-align:left}}
td{{overflow-wrap:anywhere}}code{{word-break:break-word}}
th{{background:#f5f5f5}}h2{{margin-top:2em}}.hl{{background:#fffbe6;padding:.8em 1em;border:1px solid #eed}}</style>
<h1>Context Budget Report <small>(schema {SCHEMA_VERSION}{
        ", since " + e(st["since"]) if st.get("since") else ""}{
        ", until (exclusive) " + e(st["until"]) if st.get("until") else ""})</small></h1>
<p class="hl"><b>{e(headline(st))}</b></p>
<p>observed output {fmt(T['output'])} tok (sidechain {fmt(T['sidechain_output'])}) ·
cache write {fmt(T['cache_creation'])} · cache read {fmt(T['cache_read'])} ·
tool results {fmt(T['result_bytes'])} B · {T['compactions']} compactions</p>
{comparison_html}
<h2>Recorded token usage</h2>
<p>Input includes cached reads and cache writes; uncached input excludes cached reads;
fresh input excludes both cached reads and writes. Claude's raw input is fresh input.
Cache and reasoning counts are breakdowns, not extra tokens to add to input or output.
Complete totals require the field in every retained usage record. Missing or partial totals are n/a;
observed subtotals may omit usage. Coverage describes parsed records, not all account activity.</p>
<table><tr><th>metric</th><th>complete total</th><th>observed subtotal</th><th>records with value / usage records</th></tr>{rows_usage}</table>
<h2>Input diagnostics</h2>
<p>{diagnostics or 'No diagnostics available.'}</p>
<p>Invalid or unsupported records indicate incomplete coverage. Bounded reports exclude undated records.
Inherited records and duplicate events are excluded from totals.</p>
<h2>Largest tool results</h2>
<p>Up to 20 examples. Links open the local transcript; the label gives the physical line number.
Raw tool outputs and prompts are not copied into this report.</p>
<table><tr><th>project</th><th>tool</th><th>bytes</th><th>source</th></tr>{rows_large}</table>
<h2>Context consumption by tool</h2>
<table><tr><th>tool</th><th>calls</th><th>result bytes</th><th>~tokens</th><th>share</th></tr>{rows_t}</table>
<h2>By project</h2>
<table><tr><th>project</th><th>sessions</th><th>output tok</th><th>sidechain tok</th><th>cache write</th><th>tool calls</th><th>compactions</th></tr>{rows_p}</table>
<h2>Unchanged re-reads (waste)</h2>
<p>Only counted when the same file, same range came back with identical
content within one context window. Read tracking requires structured Claude Read calls;
Codex shell reads are not inferred. Changed content and reads after compaction are excluded.</p>
<table><tr><th>project</th><th>file</th><th>reads</th><th>re-read bytes</th></tr>{rows_d}</table>
<details><summary>Repeated read examples</summary>
<table><tr><th>file</th><th>bytes</th><th>source</th></tr>{rows_repeat}</table></details>
<h2>Most-read files (CLAUDE.md / AGENTS.md candidates)</h2>
<p>Files with at least five structured Read calls. A summary in the project's
CLAUDE.md (Claude) or AGENTS.md (Codex) may reduce repeated reading.</p>
<table><tr><th>project</th><th>file</th><th>reads</th></tr>{rows_m}</table>
<h2>Skill candidates (repeated tool sequences)</h2>
<p>Sequences repeated 3+ times. Shared across 3+ projects → <code>personal</code>
(Claude: ~/.claude/skills; Codex: ~/.agents/skills), otherwise <code>project</code>
(Claude: .claude/skills; Codex: .agents/skills). Single-session repetition is marked for review,
not skill promotion. Sessions count contributing transcript contexts, not unique tasks or users.
Promotion is manual.</p>
<p>Tool result sizes are UTF-8 text bytes; ~tokens is only bytes/4, not billed tokens.
Codex projects are prefixed with codex:. Generic exec wrappers remain opaque tool calls.</p>
<table><tr><th>sequence</th><th>count</th><th>sessions</th><th>projects</th><th>suggestion</th><th>examples</th></tr>{rows_c}</table>"""


def main():
    ap = argparse.ArgumentParser(description="Claude Code / Codex 컨텍스트 예산 프로파일러")
    ap.add_argument("projects_dir", nargs="?",
                    default=None, help="Claude projects 경로")
    ap.add_argument("-o", "--out", default="report.html")
    ap.add_argument("--source", choices=("claude", "codex", "all"), default="all")
    ap.add_argument("--codex-dir", help="Codex sessions 경로 (기본: $CODEX_HOME/sessions 또는 ~/.codex/sessions)")
    ap.add_argument("--since", help="시작 날짜 포함 (YYYY-MM-DD, UTC)")
    ap.add_argument("--until", help="종료 날짜 제외 (YYYY-MM-DD, UTC)")
    ap.add_argument("--json-out", help="비교 가능한 JSON 스냅샷 저장 경로")
    ap.add_argument("--compare", help="이전 JSON 스냅샷과 비교")
    args = ap.parse_args()
    for name in ("since", "until"):
        value = getattr(args, name)
        if not value:
            continue
        try:
            if datetime.date.fromisoformat(value).isoformat() != value:
                raise ValueError("invalid date")
        except ValueError:
            ap.error(f"--{name} must be a valid YYYY-MM-DD date")
    if args.since and args.until and args.until <= args.since:
        ap.error("--until must be later than --since")
    for path in (args.projects_dir, args.codex_dir):
        if path and not os.path.isdir(os.path.expanduser(path)):
            ap.error(f"input directory does not exist: {path}")
    outputs = [Path(p).expanduser().resolve() for p in (args.out, args.json_out) if p]
    if len(set(outputs)) != len(outputs):
        ap.error("HTML and JSON output paths must differ")
    for path in outputs:
        if not path.parent.is_dir() or path.is_dir() or path.suffix == ".jsonl":
            ap.error(f"invalid report output path: {path}")
        if args.compare and path == Path(args.compare).expanduser().resolve():
            ap.error("output must not overwrite the comparison baseline")
    try:
        baseline = None
        if args.compare:
            with open(os.path.expanduser(args.compare), encoding="utf-8") as stream:
                baseline = json.load(stream)
            compare_reports(baseline, baseline)  # validate before scanning private history
            if baseline["source"] != args.source:
                ap.error("comparison baseline and --source must match")
        st = aggregate(args.projects_dir, args.since, args.source, args.codex_dir, args.until)
        snapshot = export_stats(st)
        comparison = compare_reports(snapshot, baseline) if baseline is not None else None
        if comparison is not None:
            snapshot["comparison"] = comparison
        contents = [render(st, comparison)]
        if args.json_out:
            contents.append(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        for path, content in zip(outputs, contents):
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                                 delete=False) as stream:
                    temporary = stream.name
                    stream.write(content)
                os.replace(temporary, path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
    except (OSError, ValueError, RecursionError) as exc:
        ap.error(str(exc))
    print(headline(st))
    print("Input: " + ", ".join(f"{key}={value}" for key, value in sorted(st["diagnostics"].items())))
    for path in outputs:
        print(f"→ {path}")


if __name__ == "__main__":
    main()
