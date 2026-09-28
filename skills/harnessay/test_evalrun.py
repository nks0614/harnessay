#!/usr/bin/env python3
"""Offline regression checks: python3 skills/harnessay/test_evalrun.py."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

import evalrun


TASK = {"id": "echo", "prompt": "Say PONG; $(unsafe)\n- literal",
        "check": {"type": "contains", "value": "PONG"}}


def main():
    # A failing process must never pass just because its output matches.
    with patch.object(evalrun.subprocess, "run", return_value=
                      subprocess.CompletedProcess([], 1, '{"result":"PONG"}', "error")):
        assert not evalrun.run_task(TASK, 5)[0], "nonzero exit must fail"

    for provider in ("claude", "codex"):
        output_paths = []
        returncode = 0

        def execute(cmd, **kwargs):
            assert kwargs["timeout"] == 5 and kwargs["capture_output"]
            assert kwargs["text"] and not kwargs.get("shell")
            assert cmd[0] == provider
            assert cmd[cmd.index("--model") + 1] == "test-model"
            if provider == "claude":
                assert cmd[1:5] == ["-p", TASK["prompt"], "--output-format", "json"]
                return subprocess.CompletedProcess(cmd, returncode, '{"result":"PONG"}', "")
            assert cmd[1] == "exec" and cmd[-1] == "-"
            assert cmd[cmd.index("--sandbox") + 1] == "read-only"
            assert kwargs["input"] == TASK["prompt"] and TASK["prompt"] not in cmd
            path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_paths.append(path)
            path.write_text("PONG", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode, "diagnostics without answer", "")

        task = dict(TASK, provider=provider, model="test-model")
        with patch.object(evalrun.subprocess, "run", side_effect=execute):
            ok, text, duration = evalrun.run_task(task, 5, "codex")
            assert ok and text == "PONG" and duration >= 0
            assert evalrun.run_task(dict(task, check={"type": "regex", "value": "^P.NG$"}), 5)[0]
            returncode = 1
            assert not evalrun.run_task(task, 5)[0], "matching final message cannot mask failed exit"
        assert all(not path.exists() for path in output_paths)

        for failure in (FileNotFoundError("PONG executable missing"),
                        subprocess.TimeoutExpired(provider, 5, output="PONG")):
            with patch.object(evalrun.subprocess, "run", side_effect=failure):
                assert not evalrun.run_task(task, 5)[0]
        with patch.object(evalrun.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 1, "PONG", "failed")):
            assert not evalrun.run_task(task, 5)[0]

    for stdout in ('{"result":"PONG","is_error":true}', '[]',
                   '{"result":null}', '{"result":123}'):
        with patch.object(evalrun.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0, stdout, "")):
            assert not evalrun.run_task(TASK, 5)[0]
    with patch.object(evalrun.subprocess, "run", return_value=
                      subprocess.CompletedProcess([], 0, "PONG", "")):
        assert evalrun.run_task(TASK, 5)[0]  # Legacy plain-text Claude output.
        assert not evalrun.run_task(TASK, 5, "codex")[0]  # Missing final-message file.

    with tempfile.TemporaryDirectory() as directory:
        tasks_path = Path(directory) / "tasks.json"
        results_path = Path(directory) / "results.jsonl"

        def invoke(tasks, *options):
            tasks_path.write_text(json.dumps(tasks), encoding="utf-8")
            with patch("sys.argv", ["evalrun.py", str(tasks_path), *options]), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                try:
                    evalrun.main()
                except SystemExit as exc:
                    return exc.code
            raise AssertionError("main must exit")

        # Validate the complete input before launching tasks or touching results.
        for invalid in ({}, [TASK, {}], [dict(TASK, provider="other")],
                        [dict(TASK, prompt=3)], [dict(TASK, model=[])],
                        [dict(TASK, id="")], [dict(TASK, check=[])],
                        [dict(TASK, check={"type": "other", "value": "PONG"})],
                        [dict(TASK, check={"type": "contains", "value": 3})],
                        [dict(TASK, check={"type": "regex", "value": "["})]):
            with patch.object(evalrun.subprocess, "run") as run:
                assert invoke(invalid, "--only", "echo") != 0
                run.assert_not_called()
            assert not results_path.exists()

        def batch_execute(cmd, **kwargs):
            if cmd[0] == "codex":
                Path(cmd[cmd.index("--output-last-message") + 1]).write_text("PONG")
            return subprocess.CompletedProcess(cmd, 0, '{"result":"PONG"}', "")

        with patch.object(evalrun.subprocess, "run", side_effect=batch_execute):
            assert invoke([TASK, dict(TASK, id="override", provider="claude",
                                      model="test-model")], "--provider", "codex") == 0
        rows = [json.loads(line) for line in results_path.read_text().splitlines()]
        assert [(row["provider"], row["model"], row["pass"]) for row in rows] == [
            ("codex", None, True), ("claude", "test-model", True)]
    print("evalrun offline checks passed")


if __name__ == "__main__":
    main()
