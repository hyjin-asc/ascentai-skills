#!/usr/bin/env python3
"""
skill_common_check.py — 모든 스킬에 공통으로 적용되는 최소 검사.

스킬 고유 규율(정본 6개·슬롯 enum·readiness 등급 등)은 각 스킬이 자기 검사기로
처리한다. 여기는 **스킬이면 무조건 지켜야 하는 것**만 본다:

  1) SKILL.md 존재 · frontmatter 파싱 가능
  2) name — kebab-case · 최대 64자 · 예약어(anthropic·claude) 불가
  3) description — 존재 · 최대 1024자 · XML/HTML 태그 불가
  4) LICENSE.txt 존재
  5) 참조 깊이 — SKILL.md 가 가리키는 md 파일이 또 다른 md 를 가리키지 않는가

2·3 은 Anthropic 스킬 업로드 검증 규칙이다. 위반하면 Claude Desktop 업로드 자체가
거부되므로(실전 관찰 2026-08-04) 커밋 단계에서 미리 잡는다.

사용:
    python3 scripts/skill_common_check.py                    # 전체 스킬
    python3 scripts/skill_common_check.py <스킬명> [스킬명...]  # 지정 스킬만

종료 코드:
    0 = 통과 · 1 = 하나 이상 실패
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RESERVED = ("anthropic", "claude")
DESC_MAX = 1024
NAME_MAX = 64


class Result:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.checks_run = 0

    def fail(self, skill: str, msg: str) -> None:
        self.failures.append(f"[{skill}] {msg}")

    def warn(self, skill: str, msg: str) -> None:
        self.warnings.append(f"[{skill}] {msg}")

    def tick(self) -> None:
        self.checks_run += 1

    def summary(self, skill_count: int) -> int:
        print(f"  · 스킬 {skill_count}개 · 검사 {self.checks_run}건")
        if self.warnings:
            print(f"  경고 {len(self.warnings)}건 (커밋은 통과):")
            for w in self.warnings:
                print(f"    ⚠ {w}")
        if self.failures:
            print(f"  실패 {len(self.failures)}건:")
            for f in self.failures:
                print(f"    ✗ {f}")
            return 1
        return 0


def parse_frontmatter(text: str) -> dict | None:
    """SKILL.md YAML frontmatter 를 얕게 파싱.

    pyyaml 이 없는 환경에서도 돌아야 하므로 필요한 필드만 정규식으로 뽑는다.
    description 은 블록 스칼라(`>` · `|`)를 쓰므로 들여쓰기 기준으로 수집한다.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm = text[3:end]

    data: dict = {}
    lines = fm.split("\n")

    for i, line in enumerate(lines):
        m = re.match(r"^name:\s*(.+?)\s*$", line)
        if m:
            data["name"] = m.group(1).strip().strip("\"'")
            continue

        # description: 한 줄 형태 또는 블록 스칼라(>, |) 형태 둘 다 지원
        m = re.match(r"^description:\s*(?:([>|][-+]?)\s*)?(.*)$", line)
        if m:
            if m.group(1):
                # 블록 스칼라 · 다음 줄부터 들여쓴 줄을 전부 모은다
                collected = []
                for nxt in lines[i + 1:]:
                    if nxt.strip() and not nxt.startswith((" ", "\t")):
                        break  # 들여쓰기가 끝나면 다음 키
                    collected.append(nxt.strip())
                data["description"] = " ".join(x for x in collected if x)
            else:
                data["description"] = m.group(2).strip().strip("\"'")

    return data


def check_skill(skill_dir: Path, res: Result) -> None:
    # frontmatter 기대값은 빌드가 만들 zip 이름(lm-<스킬명>)을 따른다.
    # 스킬은 언어별로 갈리지 않는다 — 시장·리포트 언어는 실행할 때 정한다.
    name = skill_dir.name
    expected_name = f"lm-{name}"
    license_dir = skill_dir
    skill_md = skill_dir / "SKILL.md"

    # 1) SKILL.md 존재
    res.tick()
    if not skill_md.exists():
        res.fail(name, "SKILL.md 부재")
        return

    text = skill_md.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    res.tick()
    if fm is None:
        res.fail(name, "SKILL.md frontmatter 파싱 실패 (--- 블록 확인)")
        return

    # 2) name
    res.tick()
    fm_name = fm.get("name", "")
    if not fm_name:
        res.fail(name, "frontmatter 에 name 부재")
    else:
        if fm_name != expected_name:
            res.fail(name, f"frontmatter name({fm_name!r}) != 기대값({expected_name!r})")
        if len(fm_name) > NAME_MAX:
            res.fail(name, f"name 길이 {len(fm_name)} > {NAME_MAX}")
        if not NAME_RE.match(fm_name):
            res.fail(name, f"name 이 kebab-case 아님: {fm_name!r}")
        for word in RESERVED:
            if word in fm_name.lower():
                res.fail(name, f"name 에 예약어 {word!r} 포함 · Anthropic 검증기가 거부")

    # 3) description
    res.tick()
    desc = fm.get("description", "")
    if not desc:
        res.fail(name, "description 부재 (Anthropic 필수 필드)")
    else:
        if len(desc) > DESC_MAX:
            res.fail(
                name,
                f"description 길이 {len(desc)} > {DESC_MAX} · 업로드 거부. "
                f"{len(desc) - DESC_MAX}자 축약 필요",
            )
        tags = re.findall(r"<[^\s][^>]*>", desc)
        if tags:
            res.fail(
                name,
                f"description 안 XML/HTML 태그 {len(tags)}개 · '<...>' 대신 '[...]' 사용. "
                f"예: {tags[0]!r}",
            )

    # 4) LICENSE.txt — 네 스킬이 같은 내용을 쓰므로 _core 에 한 벌만 둔다.
    #    빌드가 여기서 복사해 zip 마다 넣는다 (scripts/build.sh).
    res.tick()
    if not (ROOT / "_core" / "LICENSE.txt").exists():
        res.fail(name, "_core/LICENSE.txt 부재 — 빌드가 zip 에 넣을 라이선스가 없다")

    # 5) 참조 깊이 1단계
    #    SKILL.md 가 가리키는 md 가 또 다른 md 를 가리키면, Claude 가 부분 읽기(head -100)로
    #    내용을 놓친다. 정본 상호 참조는 예외로 두기 어려우므로 경고 대신 실패로 잡되
    #    references/ 안 md 만 대상으로 한다.
    res.tick()
    first_level = set()
    for m in re.finditer(r"\]\(([^)]+\.md)\)", text):
        first_level.add(m.group(1).lstrip("./"))
    for rel in sorted(first_level):
        target = skill_dir / rel
        if not target.exists():
            continue
        nested = re.findall(r"\]\(([^)]+\.md)\)", target.read_text(encoding="utf-8"))
        if nested:
            res.fail(
                name,
                f"참조 깊이 2단계: SKILL.md → {rel} → {nested[0]} · "
                f"모든 참조는 SKILL.md 에서 1단계로",
            )


def main(argv: list[str]) -> int:
    if not SKILLS_DIR.is_dir():
        print(f"✗ skills/ 디렉터리 부재: {SKILLS_DIR}")
        return 1

    if argv:
        targets = [SKILLS_DIR / a for a in argv]
        missing = [t.name for t in targets if not t.is_dir()]
        if missing:
            print(f"✗ 스킬 폴더 부재: {', '.join(missing)}")
            return 1
    else:
        targets = sorted(d for d in SKILLS_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))

    # 스킬 폴더가 곧 검사 단위다 (skills/<name>/SKILL.md).
    res = Result()
    for skill_dir in targets:
        check_skill(skill_dir, res)

    return res.summary(len(targets))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
