"""Offline checks for the live benchmark recorder and grader."""
import json
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
    assert observed['usage'] == {'input_tokens': 1000, 'cached_input_tokens': 600, 'output_tokens': 50}
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
    print('benchmark offline checks passed')


if __name__ == '__main__':
    main()
