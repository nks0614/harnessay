#!/usr/bin/env python3
"""Summarize a completed fixed benchmark without rerunning any model."""
import argparse
import json
from pathlib import Path
import statistics

LABELS = {'date-contract': '날짜 필터 확인', 'usage-validation': '사용량 검증 확인',
          'fork-contract': '포크 처리 확인', 'report-comparison': '집계 비교',
          'offline-checks': '오프라인 검사 실행'}


def value(row, key):
    if key == 'uncached_input':
        usage = row.get('usage') or {}
        a, b = usage.get('input_tokens'), usage.get('cached_input_tokens')
        return a - b if a is not None and b is not None else None
    if key in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
        return (row.get('usage') or {}).get(key)
    return row.get(key)


def measure(rows, key, operation=statistics.median):
    values = [value(row, key) for row in rows]
    return operation(values) if values and all(x is not None for x in values) else None


def fmt(number):
    return '미수집' if number is None else f'{number:,.1f}'


def delta(before, after):
    return '계산 불가' if before in (None, 0) or after is None else f'{(after/before-1)*100:+.1f}%'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('--out', required=True)
    parser.add_argument('--results', default='results.jsonl', help='recorded or explicitly reviewed results filename')
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    manifest = json.loads((directory/'manifest.json').read_text())
    rows = [json.loads(line) for line in (directory/args.results).read_text().splitlines()]
    if len(rows) != len(manifest['schedule']):
        parser.error('experiment is incomplete; do not summarize it as a completed trial')
    for row, entry in zip(rows, manifest['schedule']):
        if any(row[key] != entry[key] for key in ('task', 'repeat', 'arm')):
            parser.error('results do not match the frozen schedule')
    base = [row for row in rows if row['arm'] == 'baseline']
    guided = [row for row in rows if row['arm'] == 'guided']
    passed = lambda group: f"{sum(row['pass'] for row in group)}/{len(group)}"
    task_count, run_count = len(manifest['tasks']), len(rows)
    lines = ['# harnessay 실제 모델 비교 실험', '',
        f"모델: `{manifest['model']}` · 추론: `{manifest['reasoning_effort']}` · CLI: `{manifest['cli_version']}`",
        f"실행 환경: `{manifest.get('sandbox', 'read-only')}` · 스냅샷: `{manifest['snapshot_sha256']}`",
        '', f'동일 소스에서 유지보수 작업 {task_count}개를 조건별 2회씩 실행했습니다. 총 {run_count}회이며 독립된 작업 수는 {task_count}개입니다.',
        '기존 지침을 유지한 baseline과, 제한된 탐색·출력을 권하는 짧은 안내를 추가한 guided를 비교했습니다.',
        '추가 안내는 harnessay의 셸 출력 분석을 참고해 사람이 작성했으며 자동 생성 결과가 아닙니다.', '',
        f'정답 및 실행 검증: baseline **{passed(base)}**, guided **{passed(guided)}**.', '',
        '## 전체 결과', '', '| 지표 | baseline | guided | 변화 |', '|---|---:|---:|---:|']
    for label, key, op in [
        ('총 입력 토큰', 'input_tokens', sum), ('그중 캐시된 입력 토큰', 'cached_input_tokens', sum),
        ('캐시되지 않은 입력 토큰', 'uncached_input', sum), ('총 출력 토큰', 'output_tokens', sum),
        ('실행 시간 중앙값(초)', 'duration_seconds', statistics.median),
        ('전체 실행 시간 합계(초)', 'duration_seconds', sum), ('셸 호출 수', 'tool_calls', sum),
        ('셸 출력 바이트', 'tool_output_bytes', sum)]:
        b, g = measure(base, key, op), measure(guided, key, op)
        lines.append(f'| {label} | {fmt(b)} | {fmt(g)} | {delta(b,g)} |')
    lines += ['', '캐시 토큰은 입력 토큰의 부분집합입니다. 입력에 다시 더하지 않았습니다. 비용으로 환산하지 않았습니다.',
              '', '## 작업별 결과', '',
              '각 조건의 두 실행 중앙값을 비교합니다. 음수 변화율은 감소를 뜻합니다.', '',
              '| 작업 | 통과 B/G | 입력 토큰 변화 | 비캐시 입력 변화 | 시간 변화 | 셸 출력 변화 |',
              '|---|---|---:|---:|---:|---:|']
    for task in manifest['tasks']:
        b = [r for r in base if r['task'] == task['id']]
        g = [r for r in guided if r['task'] == task['id']]
        changes = [delta(measure(b,key),measure(g,key)) for key in
                   ('input_tokens','uncached_input','duration_seconds','tool_output_bytes')]
        lines.append('| '+ ' | '.join([LABELS[task['id']], passed(b)+' / '+passed(g)]+changes)+' |')
    lines += ['', '## 모든 실행', '', '| 순서 | 작업 | 조건 | 반복 | 통과 | 시간(초) | 실패 이유 |',
              '|---:|---|---|---:|---|---:|---|']
    for row in rows:
        lines.append(f"| {row['index']} | {LABELS[row['task']]} | {row['arm']} | {row['repeat']+1} | "
                     f"{'PASS' if row['pass'] else 'FAIL'} | {row['duration_seconds']:.1f} | {', '.join(row['failures']) or '-'} |")
    lines += ['', '## 해석 범위', '',
        '- 모든 실패와 시간 초과를 포함했습니다. 성공한 결과만 골라 집계하지 않았습니다.',
        f'- baseline 먼저 {task_count}쌍, guided 먼저 {task_count}쌍으로 순서를 균형 있게 무작위 배치했습니다. 캐시는 완전히 통제하지 못했습니다.',
        '- 안내문 자체의 토큰도 guided 사용량에 포함됩니다. 시간이 줄었다고 요금이 같은 비율로 줄었다고 볼 수 없습니다.',
        '- 무인 실행으로 실제 사용자 개입은 두 조건 모두 0회입니다. 개입 감소 효과는 검증하지 않았습니다.',
        f'- 한 저장소의 작업 {task_count}개로, 코드 수정 품질·다른 저장소·장기 사용에 일반화할 수 없습니다.',
        '- 일반적인 효율 지침의 효과를 탐색한 실험입니다. harnessay만의 독점적 효과나 전체 제품의 절감률을 증명하지 않습니다.',
        '- 재현 조건·정답·순서는 manifest에 고정했고, 읽은 소스의 해시와 압축 사본을 보관했습니다.',
        '', f"원시 결과: `{directory/'results.jsonl'}`", f"집계 입력: `{directory/args.results}`", f"실험 설정: `{directory/'manifest.json'}`", '']
    Path(args.out).write_text('\n'.join(lines), encoding='utf-8')
    print(f'baseline {passed(base)}, guided {passed(guided)} → {args.out}')


if __name__ == '__main__':
    main()
