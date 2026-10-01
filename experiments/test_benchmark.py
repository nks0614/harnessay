"""Offline checks for the live benchmark recorder and grader."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from benchmark import inspect_run, grade
from summarize import measure, delta


def main():
    events = [
        {'type': 'thread.started', 'thread_id': 'test'},
        {'type': 'item.completed', 'item': {'type': 'command_execution',
            'command': 'python3 skills/harnessay/test_harnessay.py',
            'exit_code': 0, 'aggregated_output': 'ok\n'}},
        {'type': 'turn.completed', 'usage': {
            'input_tokens': 1000, 'cached_input_tokens': 600, 'output_tokens': 50}},
    ]
    observed = inspect_run('\n'.join(json.dumps(e) for e in events))
    assert observed['usage'] == {'input_tokens': 1000, 'cached_input_tokens': 600, 'output_tokens': 50,
                                 'reasoning_output_tokens': None, 'cache_write_input_tokens': None}
    # Optional totals must never turn missing/invalid turns into measured zero.
    for key in ('reasoning_output_tokens', 'cache_write_input_tokens'):
        for raw, expected in [(0, 0), (7, 7), (None, None), (True, None), (-1, None), ('7', None)]:
            turn = {'type': 'turn.completed', 'usage': {**events[-1]['usage'], key: raw}}
            result = inspect_run(json.dumps(turn))
            assert result['usage'][key] == expected
            assert measure([result], key, sum) == expected
            assert measure([{'usage': {key: raw}}], key, sum) == expected
        turn['usage'][key] = 7
        complete = inspect_run('\n'.join(map(json.dumps, [turn, turn])))
        assert complete['usage'][key] == 14
        assert complete['usage']['output_tokens'] == 100  # reasoning is already included
        for missing in (events[-1], {'type': 'turn.completed'}):
            partial = inspect_run('\n'.join(map(json.dumps, [turn, missing])))
            assert partial['usage'][key] is None
        assert measure([complete, observed], key, sum) is None
        assert measure([{'usage': {'output_tokens': 50}}], key) is None  # old saved records
    assert observed['tool_output_bytes'] == 3
    assert len(observed['commands']) == 1
    task = {'expected': {'passed': True}, 'required_commands': ['test_harnessay.py']}
    assert grade(task, '{"passed":true}', 0, observed) == []
    silent = json.loads(json.dumps(events))
    silent[1]['item']['aggregated_output'] = ''
    assert grade(task, '{"passed":true}', 0, inspect_run('\n'.join(map(json.dumps, silent)))) == []
    assert grade(task, '{"passed":true}', 1, observed)
    assert grade(task, '{"passed":false}', 0, observed)
    assert grade(task, '{"passed":true}', 0, inspect_run(''))
    not_executed = json.loads(json.dumps(events))
    not_executed[1]['item'].update(command='cat skills/harnessay/test_harnessay.py',
                                  aggregated_output='print("ok")')
    assert grade(task, '{"passed":true}', 0, inspect_run('\n'.join(map(json.dumps, not_executed))))
    failed = json.loads(json.dumps(events)); failed[1]['item']['exit_code'] = 1
    assert grade(task, '{"passed":true}', 0, inspect_run('\n'.join(map(json.dumps, failed))))
    assert grade({'expected': {'n': 2}}, '{"n":true}', 0, observed)
    assert grade({'expected': {'n': 2}}, '{"n":2.0}', 0, observed) == []
    assert inspect_run('junk\n{}')['usage'] is None
    rows = [{'usage': {'input_tokens': 100, 'cached_input_tokens': 60}},
            {'usage': {'input_tokens': 200, 'cached_input_tokens': 100}}]
    assert measure(rows, 'uncached_input') == 70
    assert measure(rows, 'input_tokens', sum) == 300
    assert measure(rows + [{'usage': None}], 'input_tokens') is None
    assert delta(100, 75) == '-25.0%'
    assert delta(0, 10) == '계산 불가'
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        schedule = [{'task': 'date-contract', 'repeat': 0, 'arm': arm}
                    for arm in ('baseline', 'guided')]
        manifest = {'schedule': schedule, 'tasks': [{'id': 'date-contract'}],
                    'model': 'offline', 'reasoning_effort': 'high', 'cli_version': 'test',
                    'snapshot_sha256': 'test'}
        records = [{**entry, 'index': index + 1, 'pass': True, 'failures': [],
                    'duration_seconds': 1, 'tool_calls': 1, 'tool_output_bytes': 3,
                    'usage': {'input_tokens': 1000, 'cached_input_tokens': 600, 'output_tokens': 50}}
                   for index, entry in enumerate(schedule)]
        records[1]['usage'].update(reasoning_output_tokens=7, cache_write_input_tokens=0)
        (directory / 'manifest.json').write_text(json.dumps(manifest))
        (directory / 'results.jsonl').write_text('\n'.join(map(json.dumps, records)))
        report = directory / 'summary.md'
        subprocess.run([sys.executable, str(Path(__file__).with_name('summarize.py')),
                        str(directory), '--out', str(report)], check=True, capture_output=True)
        summary = report.read_text(encoding='utf-8')
        assert '| 총 출력 토큰 | 50.0 | 50.0 | +0.0% |' in summary
        assert '| 그중 추론 출력 토큰 | 미수집 | 7.0 | 계산 불가 |' in summary
        assert '| 캐시 쓰기 입력 토큰 | 미수집 | 0.0 | 계산 불가 |' in summary
    print('benchmark offline checks passed')


if __name__ == '__main__':
    main()
