#!/usr/bin/env python3
"""합성 트랜스크립트로 파서+집계 self-check. python3 test_harnessay.py"""
import json
import os
import tempfile
import subprocess
import sys
from pathlib import Path

from harnessay import aggregate, headline, render, skill_candidates
from report_data import export_stats, compare_reports


def usage_coverage_checks():
    # Missing tokens must not masquerade as zero; caches and reasoning are subsets.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        codex, claude = root / 'codex', root / 'claude'
        codex.mkdir()
        (claude / 'project').mkdir(parents=True)
        def record(kind, payload, day='2026-10-01'):
            return {'type': kind, 'timestamp': day, 'payload': payload}
        def modern(rid, **usage):
            return record('token_usage_record', {'response_id': rid, 'usage': usage})
        meta = record('session_meta', {'id': 'parent', 'cwd': '/example'})
        one = modern('one', input_tokens=100, cached_input_tokens=40,
                     cache_write_input_tokens=0, output_tokens=20, reasoning_output_tokens=5)
        two = modern('two', cached_input_tokens=0, output_tokens=0, reasoning_output_tokens=0)
        path = codex / 'parent.jsonl'
        path.write_text(''.join(line(r) for r in (meta, one, one, two)))
        st = aggregate(source='codex', codex_dir=str(codex))
        assert 'usage' in st, 'recorded input and reasoning usage is discarded'
        assert st['usage']['records'] == 2
        fields = st['usage']['fields']
        assert fields['input_tokens'] == {'tokens': 100, 'records': 1}
        assert fields['uncached_input_tokens'] == {'tokens': 60, 'records': 1}
        assert fields['reasoning_output_tokens'] == {'tokens': 5, 'records': 2}
        snap = export_stats(st)
        metrics = {r['metric']: r for r in compare_reports(snap, snap)['metrics']}
        assert metrics['input_tokens']['before'] is None
        assert metrics['output_tokens']['before'] == 20
        assert 'Recorded token usage' in render(st) and '1/2' in render(st)

        two['payload']['usage']['input_tokens'] = 0
        path.write_text(''.join(line(r) for r in (meta, one, two)))
        # A copied response in a fork must not count a second time.
        child = record('session_meta', {'id': 'child', 'cwd': '/example', 'forked_from_id': 'parent'})
        (codex / 'child.jsonl').write_text(line(child) + line(one))
        complete = export_stats(aggregate(source='codex', codex_dir=str(codex)))
        metrics = {r['metric']: r for r in compare_reports(complete, complete)['metrics']}
        assert metrics['input_tokens']['before'] == 100
        assert metrics['uncached_input_tokens']['before'] == 60
        assert complete['usage']['records'] == 2
        for invalid in (modern('bad-cache', input_tokens=10, cached_input_tokens=11),
                        modern('bad-write', input_tokens=10, cached_input_tokens=5, cache_write_input_tokens=6),
                        modern('bad-reasoning', output_tokens=10, reasoning_output_tokens=11)):
            with path.open('a') as stream:
                stream.write(line(invalid))
        checked = aggregate(source='codex', codex_dir=str(codex))
        assert checked['usage']['records'] == 2 and checked['diagnostics']['invalid_records'] == 3
        path.write_text(''.join(line(r) for r in (meta, one, two)))

        msg = {'type': 'assistant', 'timestamp': '2026-10-01', 'message': {
            'id': 'claude-one', 'content': [], 'usage': {'input_tokens': 20,
            'cache_creation_input_tokens': 10, 'cache_read_input_tokens': 70, 'output_tokens': 4}}}
        (claude / 'project' / 's.jsonl').write_text(line(msg) + line(msg))
        both = aggregate(str(claude), source='all', codex_dir=str(codex))
        assert both['usage']['fields']['input_tokens'] == {'tokens': 200, 'records': 3}
        assert both['usage']['fields']['uncached_input_tokens'] == {'tokens': 90, 'records': 3}
        assert both['usage']['fields']['output_tokens']['tokens'] == 24
        assert both['usage']['fields']['reasoning_output_tokens']['records'] == 2
        del msg['message']['usage']['cache_creation_input_tokens']
        (claude / 'project' / 's.jsonl').write_text(line(msg))
        partial = aggregate(str(claude))
        assert partial['usage']['fields']['input_tokens']['records'] == 0
        assert partial['usage']['fields'].get('fresh_input_tokens') == {'tokens': 20, 'records': 1}

        # Cumulative deltas need their preceding observation, including before the date bound.
        def cumulative(values, day='2026-10-01'):
            return record('event_msg', {'type': 'token_count', 'info': {'total_token_usage': values}}, day)
        rows = [meta, cumulative({'input_tokens': 100, 'cached_input_tokens': 60,
                                 'output_tokens': 10, 'reasoning_output_tokens': 3}, '2026-09-30'),
                cumulative({'input_tokens': 200, 'cached_input_tokens': 120,
                            'output_tokens': 20, 'reasoning_output_tokens': 6})]
        (codex / 'child.jsonl').unlink()
        path.write_text(''.join(map(line, rows)))
        delta = aggregate(source='codex', codex_dir=str(codex), since='2026-10-01')
        assert delta['usage']['fields']['input_tokens'] == {'tokens': 100, 'records': 1}
        assert delta['usage']['fields']['reasoning_output_tokens']['tokens'] == 3
        rows += [cumulative({'output_tokens': 30}), cumulative({'input_tokens': 400, 'output_tokens': 40})]
        path.write_text(''.join(map(line, rows)))
        gap = aggregate(source='codex', codex_dir=str(codex), since='2026-10-01')
        assert gap['usage']['fields']['input_tokens'] == {'tokens': 100, 'records': 1}
        rows = [meta, cumulative({'input_tokens': 100, 'cached_input_tokens': 80,
                                 'output_tokens': 10, 'reasoning_output_tokens': 8}, '2026-09-30'),
                cumulative({'input_tokens': 200, 'output_tokens': 20}),
                cumulative({'input_tokens': 300, 'cached_input_tokens': 200,
                            'output_tokens': 30, 'reasoning_output_tokens': 22})]
        path.write_text(''.join(map(line, rows)))
        resumed = aggregate(source='codex', codex_dir=str(codex), since='2026-10-01')
        assert resumed['usage']['fields']['input_tokens'] == {'tokens': 200, 'records': 2}
        assert resumed['usage']['fields']['output_tokens'] == {'tokens': 20, 'records': 2}
        assert resumed['usage']['fields']['cached_input_tokens']['records'] == 0
        assert resumed['diagnostics']['invalid_records'] == 0


def candidate_session_checks():
    gram = ('Bash:git', 'Bash:pytest')
    single = skill_candidates([('project', list(gram) * 3)])
    assert single[0].get('sessions') == 1, 'single-session repetition lacks coverage'
    assert single[0]['single_session'] is True
    multi = skill_candidates([('project', list(gram)) for _ in range(3)])
    assert multi[0]['count'] == 3 and multi[0]['sessions'] == 3
    assert multi[0]['single_session'] is False


def cli_checks():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = root / "<project>"
        project.mkdir()
        rows = []
        for date, amount in (("2026-09-01", 10), ("2026-09-02", 20)):
            rows += [{"type": "assistant", "timestamp": date, "message": {
                "usage": {"output_tokens": amount, "service_tier": "standard",
                          "cache_creation": {"ephemeral_5m_input_tokens": 0},
                          "server_tool_use": {"web_search_requests": 0}, "iterations": [],
                          "speed": "standard", "inference_geo": "global"}, "content": [
                    {"type": "tool_use", "id": date, "name": "Bash", "input": {"command": "git status"}}]}},
                {"type": "user", "timestamp": date, "message": {"content": [
                    {"type": "tool_result", "tool_use_id": date, "content": "<script>private output</script>"}]}}]
        (project / "s.jsonl").write_text("".join(line(row) for row in rows), encoding="utf-8")
        script = str(Path(__file__).with_name("harnessay.py"))
        output, before, after = root / "output.html", root / "before.json", root / "after.json"
        cmd = [sys.executable, script, str(root), "--source", "claude", "-o", str(output)]
        def run(*args):
            return subprocess.run(cmd + list(args), capture_output=True, text=True)
        result = run("--since", "2026-09-01", "--until", "2026-09-02", "--json-out", str(before))
        assert result.returncode == 0, result.stderr
        result = run("--since", "2026-09-02", "--until", "2026-09-03",
                     "--json-out", str(after), "--compare", str(before))
        assert result.returncode == 0, result.stderr
        snapshot = json.loads(after.read_text())
        metric = next(m for m in snapshot["comparison"]["metrics"] if m["metric"] == "output_tokens")
        assert (metric["before"], metric["after"], metric["percent_change"]) == (10, 20, 100)
        page = output.read_text()
        assert "Before / after comparison" in page and "&lt;project&gt;" in page
        assert "private output" not in page and "private output" not in after.read_text()
        assert snapshot["evidence"]["large_results"][0]["source"]["line"] == 4
        before_text = before.read_text()
        for args in [("--until", "2026-02-30"),
                     ("--since", "2026-09-02", "--until", "2026-09-02"),
                     ("--json-out", str(output)),
                     ("--compare", str(before), "--json-out", str(before))]:
            assert run(*args).returncode == 2, args
            assert output.read_text() == page and before.read_text() == before_text


def evidence_checks():
    with tempfile.TemporaryDirectory() as d:
        project = os.path.join(d, "project")
        os.makedirs(project)
        path = os.path.join(project, "s.jsonl")
        def message(kind, content, timestamp="2026-09-02T00:00:00Z"):
            return {"type": kind, "timestamp": timestamp, "message": {"content": content}}
        rows = [message("assistant", [{"type": "tool_use", "id": "x", "name": "Read",
                    "input": {"file_path": "/<a>"}}]),
                message("user", [{"type": "tool_result", "tool_use_id": "x", "content": "가"}]),
                message("assistant", [{"type": "tool_use", "id": "y", "name": "Read",
                    "input": {"file_path": "/<a>"}}]),
                message("user", [{"type": "tool_result", "tool_use_id": "y", "content": "가"}]),
                message("assistant", [], "2026-09-03T00:00:00Z"),
                {"type": "assistant", "message": []},
                message("user", [{"type": "tool_result", "tool_use_id": [], "content": "bad"}]),
                {"type": "assistant", "timestamp": "2026-09-02", "message": {"usage": {"output_tokens": "bad"}}},
                {"type": "future_format", "timestamp": "2026-09-02"}]
        with open(path, "wb") as f:
            f.write("".join(line(o) for o in rows).encode())
            f.write(b'null\nnot json\n\xff\n')
        st = aggregate(d, since="2026-09-02", until="2026-09-03")
        assert st["totals"]["result_bytes"] == 6
        assert st["totals"]["redundant_bytes"] == 3
        assert st["diagnostics"]["invalid_records"] >= 3, st["diagnostics"]
        assert st["diagnostics"]["invalid_json"] == 1
        assert st["diagnostics"]["invalid_encoding"] == 1
        assert st["diagnostics"]["unsupported_records"] == 1
        ev = st["evidence"]["redundant_reads"][0]
        assert ev["source"]["path"] == path and ev["source"]["line"] == 4, ev
        assert ev["source"]["timestamp"] == "2026-09-02T00:00:00Z"
        page = render(st)
        assert "Input diagnostics" in page and "Largest tool results" in page
        assert "&lt;a&gt;" in page and "/<a>" not in page

        # The ending date is exclusive; timestamps are compared in UTC.
        boundary = message("assistant", [], "2026-09-03T01:00:00+09:00")
        boundary["message"]["usage"] = {"output_tokens": 9}
        with open(path, "w") as f:
            f.write(line(boundary))
        assert aggregate(d, since="2026-09-02", until="2026-09-03")["totals"]["output"] == 9
        assert not aggregate(d, since="2026-09-03")["projects"]

        codex = os.path.join(d, "codex")
        os.makedirs(codex)
        def record(kind, payload):
            return {"type": kind, "timestamp": "2026-09-02", "payload": payload}
        def meta(sid, cwd, **fields):
            return record("session_meta", {"id": sid, "cwd": cwd, **fields})
        def output(tid, body="identical"):
            return [record("response_item", {"type": "function_call", "call_id": tid,
                        "name": "exec_command", "arguments": '{}'}),
                    record("response_item", {"type": "function_call_output", "call_id": tid, "output": body})]
        # Child sorts first by filename, but originals must keep their own attribution.
        parent_meta = meta("parent", "/original", source="cli")
        parent_rows = [parent_meta, *output("shared")]
        child_rows = [meta("child", "/child", forked_from_id="parent",
                          subagent_history_start_ordinal=5,
                          source={"subagent": {}}), parent_meta,
                      record("compacted", {}), *output("shared"),
                      record("token_usage_record", {"response_id": "own", "thread_id": "child",
                                                      "usage": {"output_tokens": 7}}),
                      *output("new")]
        for name, data in [("z-parent", parent_rows), ("a-child", child_rows),
                           ("b-resume", [meta("resume", "/resume", forked_from_id="parent"), *output("shared"), *output("newer")]),
                           ("c-independent", [meta("independent", "/independent"), *output("shared")]),
                           ("d-old-child", [meta("old-child", "/old", source={"subagent": {}},
                                                 subagent_history_start_ordinal=12),
                                            record("event_msg", {"type": "token_count", "info": {
                                                "total_token_usage": {"output_tokens": 799}}})])]:
            with open(os.path.join(codex, name + ".jsonl"), "w") as f:
                f.writelines(line(o) for o in data)
        st = aggregate(source="codex", codex_dir=codex)
        assert st["totals"]["sidechain_output"] == 806, st["totals"]
        assert st["totals"]["output"] == 0
        assert st["totals"]["compactions"] == 0
        assert st["projects"]["codex:/original"]["tool_calls"] == 1
        assert st["projects"]["codex:/child"]["tool_calls"] == 1
        assert st["projects"]["codex:/resume"]["tool_calls"] == 1
        assert st["projects"]["codex:/independent"]["tool_calls"] == 1
        assert st["tools"]["exec_command"]["bytes"] == 4 * len("identical")
        assert st["diagnostics"]["duplicate_events"] == 2
        assert st["diagnostics"]["inherited_records"] == 3

        bad = [record("response_item", {"type": "function_call", "call_id": []}),
               record("event_msg", {"type": "token_count", "info": "bad"}),
               record("token_usage_record", {"usage": {"output_tokens": -1}}),
               record("response_item", {"type": "function_call_output", "output": [{"text": {}}]}),
               record("response_item", [])]
        with open(os.path.join(codex, "bad.jsonl"), "w") as f:
            f.writelines(line(o) for o in bad)
        st = aggregate(source="codex", codex_dir=codex)
        assert st["diagnostics"]["invalid_records"] == len(bad)

        replay_dir = os.path.join(d, "replays")
        os.makedirs(replay_dir)
        originals = [parent_meta, *output("outside-period")]
        for row in originals:
            row["timestamp"] = "2026-09-01"
        replay = [meta("fork", "/fork", forked_from_id="parent"), *output("outside-period")]
        with open(os.path.join(replay_dir, "parent.jsonl"), "w") as f:
            f.writelines(line(row) for row in originals)
        with open(os.path.join(replay_dir, "fork.jsonl"), "w") as f:
            f.writelines(line(row) for row in replay)
        st = aggregate(source="codex", codex_dir=replay_dir, since="2026-09-02")
        assert not st["projects"], st["projects"]

        child = meta("ordinal-child", "/ordinal", forked_from_id="parent", subagent_history_start_ordinal=3)
        copied, own = record("compacted", {}), record("compacted", {})
        copied["ordinal"], own["ordinal"] = 2, 3
        with open(os.path.join(replay_dir, "ordinal.jsonl"), "w") as f:
            f.write(line(child) + line(parent_meta) + 'bad json\n' + line(copied) + line(own))
        st = aggregate(source="codex", codex_dir=replay_dir)
        assert st["totals"]["compactions"] == 1


def compatibility_checks():
    with tempfile.TemporaryDirectory() as d:
        claude = os.path.join(d, "claude")
        codex = os.path.join(d, "codex")
        os.makedirs(os.path.join(claude, "project"))
        os.makedirs(os.path.join(codex, "2026", "09", "28"))
        def read(tid, path):
            return {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": tid, "name": "Read",
                 "input": {"file_path": path}}]}}
        def result(tid, text):
            return {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": tid, "content": text}]}}
        calls = [read("a", "/a"), read("b", "/b"), result("b", "나"),
                 result("a", "가"), read("c", "/a"), result("c", "가"),
                 {"type": "system", "subtype": "compact_boundary"},
                 read("d", "/a"), result("d", "가")]
        path = os.path.join(claude, "project", "s.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(line(o) for o in calls)
        st = aggregate(claude)
        assert st["tools"]["Read"]["bytes"] == 12, st["tools"]
        assert st["redundant"][("project", "/a")] == 3, st["redundant"]

        def record(kind, payload, date="2026-09-28"):
            return {"type": kind, "timestamp": date + "T00:00:00Z", "payload": payload}
        def usage(out, cached, date="2026-09-28"):
            return record("event_msg", {"type": "token_count", "info": {
                "total_token_usage": {"output_tokens": out, "cached_input_tokens": cached}}}, date)
        rows = [record("session_meta", {"cwd": "/repo/demo", "source": "cli"}, "2026-09-01"),
                usage(10, 20, "2026-09-01"),
                record("response_item", {"type": "function_call", "call_id": "x",
                    "name": "exec_command", "arguments": '{"cmd":"git status"}'}, "2026-09-01"),
                record("response_item", {"type": "function_call_output", "call_id": "x", "output": "한글"}),
                usage(15, 24), usage(15, 24),
                record("response_item", {"type": "custom_tool_call", "call_id": "y", "name": "apply_patch", "input": "patch"}),
                record("response_item", {"type": "custom_tool_call_output", "call_id": "y", "output": [{"type":"input_text", "text":"ok"}]}),
                record("compacted", {"message": "summary"})]
        path = os.path.join(codex, "2026", "09", "28", "rollout-test.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(line(o) for o in rows)
            f.write('not json\nnull\n[]\n')
        st = aggregate(claude, since="2026-09-28", source="codex", codex_dir=codex)
        assert st["totals"]["output"] == 5, st["totals"]
        assert st["totals"]["cache_read"] == 4
        assert st["tools"]["exec_command"]["bytes"] == 6
        assert st["tools"]["exec_command"]["calls"] == 0
        assert st["tools"]["apply_patch"] == {"calls": 1, "bytes": 2}
        assert st["projects"]["codex:/repo/demo"]["sessions"] == 1
        assert st["totals"]["compactions"] == 1
        both = aggregate(claude, source="all", codex_dir=codex)
        assert len(both["projects"]) == 2
        assert both["totals"]["result_bytes"] == 20
        assert "AGENTS.md" in render(both)
        empty = aggregate(claude, since="2099-01-01", source="codex", codex_dir=codex)
        assert not empty["projects"]

        # Resuming a legacy session on a newer CLI retains its older usage.
        modern = record("token_usage_record", {"response_id":"r1", "usage": {
            "output_tokens": 7, "cached_input_tokens": 3, "cache_write_input_tokens": 2}})
        rows += [modern, modern, usage(22, 27), usage(22, 27)]
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(line(o) for o in rows)
        st = aggregate(claude, source="codex", codex_dir=codex)
        assert st["totals"]["output"] == 22, st["totals"]
        assert st["totals"]["cache_creation"] == 2
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(line(o) for o in [rows[0], modern, usage(7, 3), modern, usage(7, 3)])
        st = aggregate(claude, source="codex", codex_dir=codex)
        assert st["totals"]["output"] == 7, st["totals"]

        # Claude streaming chunks share a message id; usage is counted once.
        old_call = read("old", "/a")
        old_call["timestamp"] = "2026-09-01T00:00:00Z"
        new_result = result("old", "한글")
        new_result["timestamp"] = "2026-09-28T00:00:00Z"
        usage_row = {"type": "assistant", "timestamp": "2026-09-28", "message": {"id": "same", "usage": {"output_tokens": 4}}}
        with open(os.path.join(claude, "project", "s.jsonl"), "w", encoding="utf-8") as f:
            f.writelines(line(o) for o in [old_call, new_result, usage_row, usage_row])
        child = os.path.join(claude, "project", "s", "subagents")
        os.makedirs(child)
        with open(os.path.join(child, "child.jsonl"), "w", encoding="utf-8") as f:
            f.write(line(usage_row))
        st = aggregate(claude, since="2026-09-28")
        assert st["totals"]["output"] == 4
        assert st["totals"]["sidechain_output"] == 4
        assert st["tools"]["Read"]["bytes"] == 6
        assert st["tools"]["Read"]["calls"] == 0


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
        # 기간을 지정하면 날짜 없는 s1은 제외하고 그 사실을 진단에 표시한다.
        st2 = aggregate(d, since="2026-03-01")
        assert st2["totals"]["output"] == 1, st2["totals"]
        assert st2["diagnostics"]["undated_excluded"] > 0
    compatibility_checks()
    usage_coverage_checks()
    candidate_session_checks()
    evidence_checks()
    cli_checks()
    print("ok")


if __name__ == "__main__":
    main()
