# harnessay

[English](README.md) | 한국어

**Claude Code가 컨텍스트를 어디에 낭비하는지 찾아줘요.**

Claude Code는 모든 세션 기록을 `~/.claude/projects/*/*.jsonl`에 남겨요.
harnessay로 이 기록을 분석하면 세 가지를 알 수 있어요. 컨텍스트가 어디서
낭비되는지, 어떤 반복 작업을 스킬로 만들면 좋을지, 스킬을 고친 뒤에도 여전히
잘 동작하는지.

훅도, API 키도 필요 없어요. 어떤 데이터도 컴퓨터 밖으로 나가지 않아요.

![리포트 예시](docs/report-example.png)

## 제 기록에서 실제로 찾아낸 것

세션 22개, 프로젝트 10개, 툴 결과 3.9 MB를 분석한 결과예요.

- **툴 결과 컨텍스트의 53%가 Bash 출력이었어요.** 파일 읽기 전체를 합친 것의
  두 배로, 가장 큰 컨텍스트 소비원이었어요.
- **Read 바이트 중 진짜 낭비는 0.2%뿐이었어요.** 재읽기 낭비가 클 거라
  예상했지만, 같은 파일·같은 범위·동일 내용·한 세션 안이라는 기준으로 정확히
  재보니 재읽기는 거의 전부 정당했어요. 진짜 문제는 다른 곳에 있었던 거죠.
- **3개 이상 프로젝트에서 반복된 워크플로가 10개 나왔어요.** 가장 잦은 것은
  152번 반복된 브라우저 자동화 체인으로, 진작 스킬로 만들었어야 했어요.

여러분의 숫자는 다를 거예요. 그게 핵심이에요 — 직접 돌려보세요.

## 빠른 시작

```
/plugin marketplace add nks0614/harnessay
/plugin install harnessay@harnessay
/harnessay
```

플러그인 없이 스크립트만 실행할 수도 있어요.

```bash
git clone https://github.com/nks0614/harnessay.git
python3 harnessay/skills/harnessay/harnessay.py -o report.html
```

Claude Code와 Python 3.8 이상만 있으면 돼요. 서드파티 패키지는 쓰지 않아요.

## 최적화 루프

harnessay는 기능 세 개를 묶어 놓은 도구가 아니라, 사용 기록 위에서 도는 하나의
최적화 루프예요.

```
Observe   →  내 컨텍스트는 실제로 어디로 가는가?
Detect    →  어떤 낭비와 반복이 있는가?
Promote   →  어떤 반복 워크플로를 스킬이나 CLAUDE.md로 만들 것인가?
Verify    →  그 변경이 실제로 도움이 됐는가?
```

### Observe — 컨텍스트 예산 리포트

`/harnessay`를 실행하면 모든 트랜스크립트를 파싱해서 툴별 컨텍스트 소비,
프로젝트별 합계, compaction 횟수를 집계하고, 한 문장 헤드라인을 보여줘요.

> 53% of tool-result context is Bash. 0.2% of Read bytes re-read unchanged
> content.

습관을 바꾼 뒤에는 `--since YYYY-MM-DD`를 붙여 기간을 좁혀서 다시 재보세요.

### Detect — 낭비와 반복

- **Unchanged re-reads**: 같은 파일의 같은 범위가 동일한 내용으로 한 세션
  안에서 다시 들어온 경우만 낭비로 세요. 파일을 고친 뒤 다시 읽는 것은 낭비가
  아니므로 세지 않아요.
- **Most-read files**: 세션마다 반복해서 읽히는 파일이에요. 읽기 하나하나는
  정당하지만, 해당 프로젝트의 CLAUDE.md에 요약을 넣어 두면 읽을 필요 자체가
  없어져요.
- **반복 툴 시퀀스**: 세션별 툴 호출을 n-gram(연속된 호출 묶음)으로 세요.
  Bash는 `git`, `npm` 같은 명령 첫 단어로 구분하고, `Read → Edit` 같은 일반
  편집 루프는 걸러내요.

### Promote — 스킬 후보

3번 이상 반복된 시퀀스를 스킬 후보로 보여줘요. 3개 이상 프로젝트에서 나오면
**personal** 스킬(`~/.claude/skills`), 한 프로젝트에서만 나오면 **project**
스킬(`.claude/skills`)을 권해요. harnessay는 스킬을 만들지 않아요 — 증거만
보여주고, 만들지는 여러분이 결정해요.

### Verify — 스킬 회귀 테스트

스킬마다 골든 태스크를 정의해 두고 `claude -p`로 한꺼번에 실행하면, 통과율이
`results.jsonl`에 쌓여요. 기존 구독으로 돌아가므로 별도 비용은 없어요.

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

태스크를 실행할 때마다 구독 사용량을 소모하니, 스킬당 태스크 1~2개로 작게
유지하세요. 체크는 출력 문자열 기반(`contains`/`regex`)이에요. 저장소 상태나
테스트 exit code 검증은 로드맵에 있어요. 그래서 지금의 PASS는 "스킬이 실행되고
올바르게 답했다"는 뜻이지, "저장소가 멀쩡하다"는 보장이 아니에요.

## 뭐가 다른가요?

사용량 트래커(ccusage, `/usage`)는 **얼마나** 썼는지 알려줘요. 트레이스 뷰어는
**한 번의 실행**을 들여다보게 해줘요. 스킬 생성기는 스킬을 **대신** 만들어줘요.
harnessay는 그 사이의 루프를 맡아요. 쌓인 기록을 관찰하고, 낭비와 반복을 찾고,
재사용할 지시로 승격하고, 그 변경이 실제로 도움이 됐는지 재요. 특히 프로젝트를
가로지르는 시각이 차별점이에요 — 여러 저장소를 굴려야만 드러나는 패턴은 단일
세션 도구로는 볼 수 없어요.

## 프라이버시

트랜스크립트에는 소스 코드, 명령어, 프로젝트 구조가 담길 수 있어요. harnessay는
모든 파싱을 로컬에서 하고, 결과로 정적 `report.html` 파일 하나만 만들어요.
텔레메트리도, 업로드도 없어요. 네트워크를 쓰는 곳은 회귀 테스트가 여러분의
`claude` CLI를 호출할 때뿐이에요.

## 한계

- **비공식 포맷이에요.** 트랜스크립트 스키마는 공개 API가 아니에요. 파싱을
  `parse_session()` 함수 하나에 격리하고 `SCHEMA_VERSION`을 찍어 뒀으니,
  Claude Code 업데이트로 포맷이 바뀌어도 이 함수만 고치면 돼요.
- **토큰은 추정치예요.** `~tokens` 열은 토크나이저가 아니라 bytes/4 근사예요.
- **많이 쓰는 사람을 위한 도구예요.** 인사이트는 사용량에 비례해요. 세션 몇
  개로는 리포트가 빈약해요.

## 개발

```bash
python3 skills/harnessay/test_harnessay.py   # self-check, 픽스처 없음
```

모든 코드는 `skills/harnessay/`에 있어요. `SKILL.md`(Claude Code 진입점),
`harnessay.py`(파서 + 집계 + 리포트), `evalrun.py`(회귀 러너),
`eval/tasks.json`(골든 태스크)으로 구성돼요. 파싱 계층과 집계 계층은 일부러
분리해 뒀어요.

## 라이선스

[MIT](LICENSE)
