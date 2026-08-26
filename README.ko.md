# harnessay

[English](README.md) | 한국어

**Claude Code가 컨텍스트를 어디에 낭비하는지 찾아준다.**

harnessay는 이미 로컬에 쌓여 있는 Claude Code 트랜스크립트
(`~/.claude/projects/*/*.jsonl`)를 분석해 컨텍스트 낭비, 스킬로 만들 가치가
있는 반복 워크플로, 그리고 스킬 수정 후의 회귀를 찾아냅니다.

훅 없음. API 키 없음. 아무것도 머신 밖으로 나가지 않음.

![리포트 예시](docs/report-example.png)

## 내 기록에서 실제로 찾아낸 것

22개 세션, 10개 프로젝트, 툴 결과 3.9 MB를 분석한 실제 결과:

- **툴 결과 컨텍스트의 53%가 Bash 출력** — 단일 최대 소비원으로, 모든 파일
  읽기를 합친 것의 두 배.
- **Read 바이트 중 진짜 낭비는 0.2%뿐.** 재읽기 낭비가 클 거라 예상했지만,
  정확히 측정하니(같은 파일·같은 범위·동일 내용·한 세션 안) Claude Code의
  재읽기는 거의 전부 정당했습니다 — 진짜 문제는 다른 곳에 있었던 거죠.
- **3개 이상 프로젝트에서 반복된 워크플로 10개**, 최다는 152회 — 스킬로
  만들었어야 할 브라우저 자동화 체인이었습니다.

여러분의 숫자는 다를 겁니다. 그게 핵심이에요 — 직접 돌려보세요.

## Quick start

```
/plugin marketplace add nks0614/harnessay
/plugin install harnessay@harnessay
/harnessay
```

플러그인 없이 단독 실행:

```bash
git clone https://github.com/nks0614/harnessay.git
python3 harnessay/skills/harnessay/harnessay.py -o report.html
```

요구사항: Claude Code, Python 3.8+. 서드파티 패키지 없음.

## 루프

harnessay는 기능 세 개의 묶음이 아니라, 사용 기록 위에서 도는 하나의 최적화
루프입니다:

```
Observe   →  내 컨텍스트는 실제로 어디로 가는가?
Detect    →  어떤 낭비와 반복이 있는가?
Promote   →  어떤 반복 워크플로를 스킬/CLAUDE.md로 만들 것인가?
Verify    →  그 변경이 실제로 도움이 됐는가?
```

### Observe — 컨텍스트 예산 리포트

`/harnessay`(또는 `harnessay.py`)가 모든 트랜스크립트를 파싱해 툴별 컨텍스트
소비, 프로젝트별 합계, compaction, 그리고 한 문장 헤드라인을 출력합니다:

> 53% of tool-result context is Bash. 0.2% of Read bytes re-read unchanged
> content.

습관을 바꾼 뒤에는 `--since YYYY-MM-DD`로 기간을 좁혀 재측정하세요.

### Detect — 낭비와 반복

- **Unchanged re-reads**: 같은 파일·같은 범위가 동일한 내용으로 한 세션 안에서
  다시 들어온 경우만 셉니다. 편집 후 재읽기는 낭비가 아니므로 세지 않습니다.
- **Most-read files**: 세션마다 반복해서 읽히는 파일. 읽기 하나하나는
  정당하지만, 해당 프로젝트 CLAUDE.md에 요약을 넣으면 읽을 필요가 없어집니다.
- **반복 툴 시퀀스**: 세션별 툴 호출의 n-gram(Bash는 명령 첫 단어로 구분),
  범용 편집 루프(`Read → Edit`)는 필터링.

### Promote — 스킬 후보

3회 이상 반복된 시퀀스를 후보로 제안합니다: 3개 이상 프로젝트 공통 →
**personal** 스킬(`~/.claude/skills`), 한 프로젝트 한정 → **project**
스킬(`.claude/skills`). harnessay는 스킬을 생성하지 않습니다 — 증거를
제시하고, 결정은 사람이 합니다.

### Verify — 스킬 회귀 테스트 하네스

스킬별 골든 태스크를 기존 구독으로 `claude -p` 배치 실행하고, 통과율을
`results.jsonl`에 누적합니다:

```
/harnessay eval
```

```json
{
  "id": "my-skill-smoke",
  "skill": "my-skill",
  "prompt": "/my-skill 늘 하던 그 작업",
  "check": { "type": "regex", "value": "기대하는 출력 패턴" },
  "model": "claude-haiku-4-5"
}
```

태스크마다 구독 사용량이 차감됩니다 — 스킬당 1~2개로 작게 유지하세요. 체크는
출력 기반(`contains`/`regex`)입니다. 저장소 상태·테스트 exit code 검증은
로드맵에 있으며, 현재의 PASS는 "스킬이 실행되고 올바르게 답했다"이지 "저장소가
멀쩡함이 보장된다"가 아닙니다.

## 뭐가 다른가?

사용량 트래커(ccusage, `/usage`)는 **얼마나** 썼는지 알려주고, 트레이스 뷰어는
**한 번의 실행**을 들여다보게 해주고, 스킬 생성기는 스킬을 **대신** 써줍니다.
harnessay는 그 사이의 루프를 맡습니다: 누적된 기록을 관찰하고, 낭비와 반복을
찾고, 재사용 가능한 지시로 승격하고, 그게 실제로 도움이 됐는지 측정합니다.
프로젝트 교차 시각이 차별점입니다 — 여러 리포를 굴려야만 드러나는 패턴은
단일 세션 도구에는 보이지 않습니다.

## 프라이버시

트랜스크립트에는 소스 코드, 명령어, 프로젝트 구조가 담길 수 있습니다.
harnessay는 전부 로컬에서 파싱하고 정적 `report.html`을 만듭니다. 텔레메트리
없음, 업로드 없음. 유일한 네트워크 활동은 회귀 하네스가 본인의 `claude` CLI를
호출하는 것뿐입니다.

## 한계

- **비공식 포맷.** 트랜스크립트 스키마는 공개 API가 아닙니다. 파싱은
  `parse_session()`에 격리돼 있고 `SCHEMA_VERSION`이 찍혀 있어, Claude Code
  업데이트로 깨져도 함수 하나만 고치면 됩니다.
- **추정 토큰.** `~tokens`는 토크나이저가 아닌 bytes/4 근사입니다.
- **헤비 유저용.** 인사이트는 사용량에 비례합니다. 세션 몇 개로는 리포트가
  빈약합니다.

## 개발

```bash
python3 skills/harnessay/test_harnessay.py   # self-check, 픽스처 없음
```

전부 `skills/harnessay/`에 있습니다: `SKILL.md`(Claude Code 진입점),
`harnessay.py`(파서 + 집계 + 리포트), `evalrun.py`(회귀 러너),
`eval/tasks.json`(골든 태스크). 파싱과 집계는 의도적으로 분리된 계층입니다.

## 라이선스

[MIT](LICENSE)
