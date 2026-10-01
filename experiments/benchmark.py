#!/usr/bin/env python3
"""Small paired Codex experiment. Explicit execution consumes account usage."""
import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
GUIDE = """Efficiency guidance derived from harnessay's large shell-output findings:
Locate relevant symbols with rg -n before reading files. Read the relevant line
ranges instead of printing entire files. Reuse existing repository functions
and checks for computation and verification. Batch independent reads when
practical. Keep successful command output concise; expand output around a
failure when needed. Once the requested evidence is established, do not repeat
the same inspection. Preserve input validation, edge cases and required checks.
"""
COMMON = """Work only inside the provided repository snapshot. This is a read-only
maintainer task. Do not edit repository files, spawn subagents, invoke other
models, use network tools or inspect other projects. Temporary files for the
existing offline tests are allowed. Return only the requested JSON object in
your final answer, without markdown fences. Inspect the repository to verify
facts; do not guess. Do not read outside the snapshot for task answers.
"""


def inspect_run(text):
    commands, usages = [], []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get('item', {})
        if event.get('type') == 'item.completed' and item.get('type') == 'command_execution':
            commands.append({'command': item.get('command', ''), 'exit_code': item.get('exit_code'),
                             'output': item.get('aggregated_output', '')})
        if event.get('type') == 'turn.completed':
            usages.append(event['usage'] if isinstance(event.get('usage'), dict) else {})
    usage = None
    if usages:
        usage = {key: sum(row[key] for row in usages)
                 if all(type(row.get(key)) is int and row[key] >= 0 for row in usages) else None
                 for key in ('input_tokens', 'cached_input_tokens', 'output_tokens',
                             'reasoning_output_tokens', 'cache_write_input_tokens')}
    return {'usage': usage, 'commands': commands,
            'tool_output_bytes': sum(len(row['output'].encode()) for row in commands)}


def grade(task, text, returncode, observed):
    failures = []
    if returncode != 0:
        failures.append('process_failed')
    try:
        answer = json.loads(text)
    except ValueError:
        return failures + ['invalid_json_answer']
    if not isinstance(answer, dict):
        return failures + ['answer_not_object']
    for key, expected in task['expected'].items():
        actual = answer.get(key)
        numeric = type(expected) in (int, float)
        ok = (type(actual) in (int, float) and math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-6)
              if numeric else type(actual) is type(expected) and actual == expected)
        if key not in answer or not ok:
            failures.append('wrong:' + key)
    for name in task.get('required_commands', []):
        invocation = r'\bpython(?:\d(?:\.\d+)?)?\s+(?:-B\s+)?[\x22\x27]?(?:[^\s\x22\x27]*/)?' + re.escape(name) + r'(?=[\s\x22\x27;&|]|$)'
        matching = [row for row in observed['commands'] if re.search(invocation, row['command'])
                    and row['exit_code'] == 0 and 'Traceback (' not in row['output']]
        if not matching:
            failures.append('missing_successful_execution:' + name)
    if not observed['commands']:
        failures.append('no_repository_inspection')
    return failures


def fingerprint(directory):
    digest = hashlib.sha256()
    for path in sorted(directory.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            digest.update(str(path.relative_to(directory)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare(output, model, effort, sandbox='read-only', only=None):
    snapshot = Path(tempfile.mkdtemp(prefix='harnessay-benchmark-'))
    for name in ('AGENTS.md', 'README.md', 'README.ko.md'):
        shutil.copy2(ROOT / name, snapshot / name)
    shutil.copytree(ROOT / 'skills', snapshot / 'skills',
                    ignore=shutil.ignore_patterns('__pycache__', 'results.jsonl'))
    (snapshot / '.agents/skills').mkdir(parents=True)
    (snapshot / '.agents/skills/harnessay').symlink_to('../../skills/harnessay', target_is_directory=True)
    inputs = snapshot / 'benchmark-inputs'
    inputs.mkdir()
    # These aggregates came from real local history; remove paths and evidence before model access.
    for name in ('before', 'after'):
        data = json.loads((ROOT / ('report-' + name + '.json')).read_text())
        data['projects'] = [{**row, 'project': 'project-' + str(i)} for i, row in enumerate(data['projects'])]
        data.update(reads=[], redundant_reads=[], evidence={})
        data.pop('comparison', None)
        (inputs / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    import sys
    sys.path.insert(0, str(ROOT / 'skills/harnessay'))
    from report_data import compare_reports
    comparison = compare_reports(json.loads((inputs/'after.json').read_text()), json.loads((inputs/'before.json').read_text()))
    metrics = {row['metric']: row for row in comparison['metrics']}
    tasks = [
        {'id': 'date-contract', 'prompt': 'Verify the report CLI date filtering contract. Return keys timezone (string), since_inclusive (bool), until_inclusive (bool), bounded_undated_included (bool), invalid_date_exit (integer).',
         'expected': {'timezone': 'UTC', 'since_inclusive': True, 'until_inclusive': False,
                      'bounded_undated_included': False, 'invalid_date_exit': 2}},
        {'id': 'usage-validation', 'prompt': 'Verify how the Claude parser handles usage metadata. Return booleans accepts_service_tier_string, accepts_nested_cache_creation, accepts_negative_output_tokens, accepts_string_output_tokens; also validator_symbol (function name string).',
         'expected': {'accepts_service_tier_string': True, 'accepts_nested_cache_creation': True,
                      'accepts_negative_output_tokens': False, 'accepts_string_output_tokens': False,
                      'validator_symbol': '_tokens'}},
        {'id': 'fork-contract', 'prompt': 'Verify Codex fork handling. Return owner_metadata as "first" or "last", and booleans boundary_alone_excludes_history, explicit_ordinal_preferred, independent_session_same_ids_deduplicated, dedup_before_date_filter.',
         'expected': {'owner_metadata': 'first', 'boundary_alone_excludes_history': False,
                      'explicit_ordinal_preferred': True, 'independent_session_same_ids_deduplicated': False,
                      'dedup_before_date_filter': True}},
        {'id': 'report-comparison', 'prompt': 'Compare benchmark-inputs/after.json against benchmark-inputs/before.json using this repository\'s report semantics. Return numbers output_tokens_delta, bytes_per_session_delta, compactions_delta, warning_count, and zero_to_positive_percent_change (the representation used when a baseline metric is zero and the current value is positive).',
         'expected': {'output_tokens_delta': metrics['output_tokens']['delta'],
                      'bytes_per_session_delta': metrics['bytes_per_session']['delta'],
                      'compactions_delta': metrics['compactions']['delta'],
                      'warning_count': len(comparison['warnings']), 'zero_to_positive_percent_change': None}},
        {'id': 'offline-checks', 'prompt': 'Execute all three offline self-check scripts documented by the repository. Do not run live evaluations. Return booleans test_harnessay, test_evalrun, test_report_data indicating successful completion, and integer passed_count. Actual execution is required.',
         'expected': {'test_harnessay': True, 'test_evalrun': True, 'test_report_data': True, 'passed_count': 3},
         'required_commands': ['test_harnessay.py', 'test_evalrun.py', 'test_report_data.py']},
    ]
    if only:
        tasks = [task for task in tasks if task['id'] == only]
        if not tasks:
            raise ValueError('unknown task: ' + only)
    pairs = [(task['id'], repeat) for repeat in range(2) for task in tasks]
    rng = random.Random(20260928)
    rng.shuffle(pairs)
    orders = [('baseline', 'guided')] * len(tasks) + [('guided', 'baseline')] * len(tasks)
    rng.shuffle(orders)
    schedule = [{'task': task, 'repeat': repeat, 'arm': arm} for (task, repeat), order in zip(pairs, orders) for arm in order]
    manifest = {'created': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'snapshot': str(snapshot), 'snapshot_sha256': fingerprint(snapshot),
                'model': model, 'reasoning_effort': effort, 'sandbox': sandbox,
                'guide': GUIDE, 'common': COMMON,
                'tasks': tasks, 'schedule': schedule, 'repetitions': 2,
                'cli_version': subprocess.check_output(['codex', '--version'], text=True).strip()}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--effort', default='high')
    parser.add_argument('--sandbox', choices=('read-only', 'workspace-write'), default='read-only')
    parser.add_argument('--only', help='run one task in a new separately recorded experiment')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--limit', type=int, default=20, help='max completed schedule entries; resume uses same manifest')
    args = parser.parse_args()
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else prepare(
        output, args.model, args.effort, args.sandbox, args.only)
    if (manifest['model'], manifest['reasoning_effort']) != (args.model, args.effort):
        parser.error('model/effort differ from frozen manifest')
    if manifest.get('sandbox', 'read-only') != args.sandbox:
        parser.error('sandbox differs from frozen manifest')
    snapshot = Path(manifest['snapshot'])
    if fingerprint(snapshot) != manifest['snapshot_sha256']:
        parser.error('snapshot changed; use a fresh experiment directory')
    result_file = output / 'results.jsonl'
    completed = len(result_file.read_text().splitlines()) if result_file.exists() else 0
    tasks = {task['id']: task for task in manifest['tasks']}
    for index, entry in enumerate(manifest['schedule'][completed:args.limit], completed):
        task = tasks[entry['task']]
        prompt = manifest['common'] + '\n' + task['prompt']
        if entry['arm'] == 'guided':
            prompt += '\n\n' + manifest['guide']
        prefix = output / ('%02d-%s-%s' % (index + 1, entry['task'], entry['arm']))
        final_path = str(prefix) + '.answer.txt'
        command = ['codex', 'exec', '--model', args.model, '-c', 'model_reasoning_effort="' + args.effort + '"',
                   '--sandbox', args.sandbox, '--skip-git-repo-check', '--ephemeral', '--json',
                   '--cd', str(snapshot), '--output-last-message', final_path, '-']
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        started = time.monotonic()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=env, start_new_session=True)
        timeout = False
        try:
            stdout, stderr = process.communicate(prompt, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timeout = True
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        duration = time.monotonic() - started
        Path(str(prefix) + '.events.jsonl').write_text(stdout, encoding='utf-8')
        Path(str(prefix) + '.stderr.txt').write_text(stderr, encoding='utf-8')
        answer = Path(final_path).read_text() if Path(final_path).exists() else ''
        observed = inspect_run(stdout)
        failures = grade(task, answer, process.returncode, observed)
        if timeout:
            failures.append('timeout')
        if fingerprint(snapshot) != manifest['snapshot_sha256']:
            failures.append('snapshot_modified')
        result = {**entry, 'index': index + 1, 'model': args.model, 'reasoning_effort': args.effort,
                  'pass': not failures, 'failures': failures, 'duration_seconds': round(duration, 3),
                  'returncode': process.returncode, 'timeout': timeout, 'usage': observed['usage'],
                  'tool_calls': len(observed['commands']), 'tool_output_bytes': observed['tool_output_bytes'],
                  'human_interventions': 0, 'answer': answer, 'artifact_prefix': str(prefix)}
        with result_file.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + '\n')
        print('%02d/%s %s %s %s %.1fs %s' % (index+1, len(manifest['schedule']), entry['task'], entry['arm'],
              'PASS' if result['pass'] else 'FAIL', duration, json.dumps(observed['usage'])), flush=True)
        if 'snapshot_modified' in failures:
            raise SystemExit('Snapshot changed; remaining runs stopped.')


if __name__ == '__main__':
    main()
