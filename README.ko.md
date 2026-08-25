# harnessay

[English](README.md) | 한국어

**이미 쌓여 있는 세션 트랜스크립트로 Claude Code 워크플로를 프로파일링하고 회귀
테스트하는 도구.**

Claude Code는 모든 세션의 전체 트랜스크립트를
`~/.claude/projects/*/*.jsonl`에 기록합니다 — 토큰 사용량, 모든 툴 호출, 모든
툴 결과까지. 그런데 아무도 읽지 않죠. harnessay는 그 방치된 데이터를 세 가지
도구로 바꿉니다:

| 도구 | 답하는 질문 |
|---|---|
| 컨텍스트 예산 프로파일러 | 내 토큰은 실제로 어디에 쓰이는가? |
| 스킬 승격 탐지기 | 반복되는 워크플로 중 뭘 스킬로 만들 가치가 있는가? |
| 스킬 회귀 테스트 하네스 | 스킬을 고친 뒤에도 여전히 잘 동작하는가? |

전부 Python 표준 라이브러리만으로 로컬에서 동작합니다. API 키 불필요 — 회귀
하네스는 기존 Claude 구독으로 `claude -p`를 실행합니다.

## 설치

**Claude Code 플러그인으로** (권장):

```
/plugin marketplace add nks0614/harnessay
/plugin install harnessay@harnessay
```

**독립 개인 스킬로:**

```bash
git clone https://github.com/nks0614/harnessay.git
ln -s "$(pwd)/harnessay/skills/harnessay" ~/.claude/skills/harnessay
```

요구사항: Claude Code, Python 3.8+. 서드파티 패키지 없음.

## 사용법

아무 Claude Code 세션에서:

```
/harnessay          # 컨텍스트 예산 리포트 + 스킬 후보
/harnessay eval     # 골든 태스크 실행, 통과율 보고
```

스크립트 직접 실행도 가능:

```bash
python3 skills/harnessay/harnessay.py -o report.html
python3 skills/harnessay/evalrun.py [tasks.json] [--only id부분문자열]
```

## 컨텍스트 예산 프로파일러

모든 트랜스크립트를 파싱해 집계합니다:

- **툴별 컨텍스트 소비** — 각 툴의 결과가 컨텍스트 윈도우에 넣은 바이트
  (Read, Bash, Grep, MCP 툴, …)
- **중복 읽기** — 반복해서 읽힌 파일과 재읽기로 낭비된 바이트
- **프로젝트별 합계** — 출력 토큰, 사이드체인(서브에이전트) 토큰, 캐시 쓰기,
  compaction 횟수

리포트는 행동으로 이어지는 한 문장으로 시작합니다. 예:

> 52% of tool-result context is Bash. 31% of Read bytes are same-file
> re-reads.

이 문장이 핵심입니다: 대시보드를 뒤지지 않아도 뭘 고칠지 알려줍니다 (자주
재읽는 파일의 요약을 `CLAUDE.md`에 넣기, 시끄러운 Bash 출력 줄이기).

## 스킬 승격 탐지기

같은 파싱 과정에서 세션별 툴 호출 시퀀스를 추출하고(Bash는 `Bash:git`처럼 명령
첫 단어로 구분), 3회 이상 반복된 n-gram을 보여줍니다:

- **3개 이상 프로젝트** 공통 → **personal** 스킬 제안 (`~/.claude/skills`)
- **한 프로젝트**에 한정 → **project** 스킬 제안 (`.claude/skills`)

탐지기는 증거만 제시합니다. 스킬을 자동 생성하지 않습니다 — 일회성 워크플로가
자동으로 승격되면 노이즈가 되므로, 승격은 항상 사람이 결정합니다.

## 스킬 회귀 테스트 하네스

스킬용 CI입니다. 골든 태스크를 정의하고 `claude -p`로 배치 실행한 뒤, 통과율을
`results.jsonl`에 시간순으로 누적합니다.

```json
[
  {
    "id": "my-skill-smoke",
    "skill": "my-skill",
    "prompt": "/my-skill 늘 하던 그 작업",
    "check": { "type": "regex", "value": "기대하는 출력 패턴" },
    "model": "claude-haiku-4-5"
  }
]
```

- `check.type`은 `contains` 또는 `regex`, 세션의 최종 출력에 적용됩니다.
- `model`은 선택 — 가벼운 sanity 태스크에는 작은 모델을 쓰세요.
- 실행마다 `{ts, id, skill, pass, duration}`이 tasks 파일 옆 `results.jsonl`에
  추가되어, 회귀는 통과율 하락으로 드러납니다.
- 골든 태스크 초안은 프로파일러의 스킬 후보 시퀀스에서 뽑으세요 — 정의상 가장
  많이 반복하는 워크플로입니다.

참고: 태스크 실행마다 Claude 구독 사용량이 차감됩니다. 골든 스위트는 스킬당
1~2개로 작게 유지하세요.

## 프라이버시

트랜스크립트에는 소스 코드, 파일 경로 등 세션의 모든 것이 담길 수 있습니다.
harnessay는 아무것도 업로드하지 않습니다: 파싱과 리포트 생성은 전부 로컬이고,
생성된 `report.html`은 로컬에 남는 정적 파일입니다. 유일한 네트워크 활동은 회귀
하네스가 본인의 `claude` CLI를 호출하는 것뿐입니다.

## 한계

- **비공식 포맷.** 트랜스크립트 스키마는 공개 API가 아니며 Claude Code 릴리스에
  따라 바뀔 수 있습니다. 파싱은 `parse_session()`에 격리돼 있고
  `SCHEMA_VERSION`이 찍혀 있어, 포맷이 깨져도 함수 하나만 고치면 됩니다.
- **추정 토큰.** 툴 결과 크기는 바이트로 측정하며, `~tokens` 열은 토크나이저가
  아닌 bytes/4 근사입니다.
- **헤비 유저용.** 인사이트는 사용량에 비례합니다. 일주일에 세션 몇 개 수준이면
  리포트가 빈약할 겁니다.

## 개발

```bash
python3 skills/harnessay/test_harnessay.py   # self-check, 픽스처 없음
```

구조: `skills/harnessay/`에 전부 들어 있습니다 — `SKILL.md`(Claude Code 진입점),
`harnessay.py`(파서 + 집계 + 리포트), `evalrun.py`(회귀 러너),
`eval/tasks.json`(골든 태스크). 파싱 계층과 집계 계층은 의도적으로 분리돼
있으며, 포맷 변경은 `parse_session()`만 건드려야 합니다.

## 라이선스

[MIT](LICENSE)
