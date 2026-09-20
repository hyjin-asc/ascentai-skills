#!/usr/bin/env bash
# build.sh · _core + skills/<name> → dist/lm-<name>.zip
#
# 왜 빌드인가:
#   공통 코드(_core)와 스킬 코드를 합쳐 설치 가능한 zip 한 벌을 만든다.
#
# 스킬은 언어마다 나누지 않는다. 프롬프트는 영어 한 벌이고, 분석 대상 시장과
# 리포트 언어는 실행할 때 정한다(--gl / --lang). 그래서 라벨 3종(kr·jp·us)을
# 모두 싣고, 스킬 하나당 zip 하나만 나온다.
#
#   skills/ 폴더 수 = dist/ zip 수
#
# 사용:
#   scripts/build.sh                     # 전 스킬
#   scripts/build.sh queryfinder-report  # 한 스킬만
#
# 산출 zip 의 내부 구조는 기존과 동일하다 (스킬 루트 아래 _shared/ api/ scripts/ references/).
# 구조를 바꾸면 설치된 스킬이 자기 파일을 못 찾으므로 여기서 원래 배치로 되돌려 놓는다.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE="$ROOT/_core"
SRC="$ROOT/skills"
OUT="$ROOT/dist"

# 리포트를 쓸 수 있는 언어 (라벨이 있는 언어) · 시장(gl)과 별개다
REPORT_LANGS="kr jp us"

ONLY_SKILL="${1:-}"

# 작명 규약 · lm-<스킬명> · 언어 코드는 붙지 않는다 (DaaS 판이 -daas 를 단다)
zip_name() { echo "lm-$1"; }

# skill.yaml 에서 값 하나 읽기 (외부 의존 없이)
yval() { sed -n "s/^$2: *//p" "$1" | head -1 | tr -d '"'; }

build_one() {
  local name="$1"
  local sdir="$SRC/$name"

  local skill_name; skill_name="$(zip_name "$name")"
  local version;    version="$(yval "$sdir/skill.yaml" version)"
  local vendor;     vendor="$(yval "$sdir/skill.yaml" vendor_chart)"

  local stage; stage="$(mktemp -d)"
  local d="$stage/$skill_name"
  mkdir -p "$d"/{_shared/{render,styles,templates,labels},api,scripts,references}

  # ── 공통 코드 (_core) ──
  cp "$CORE"/styles/*.css              "$d/_shared/styles/"
  cp "$CORE"/render/*.py               "$d/_shared/render/"
  cp "$CORE"/api/*.py                  "$d/api/"
  cp "$CORE"/scripts/*.py              "$d/scripts/"

  # ── 형제 스킬에서 빌려오는 코드 (borrows) ──
  #   total-report 처럼 다른 리포트를 통째로 품는 스킬은 그 코드를 복제하지 않는다.
  #   먼저 빌려온 뒤 자기 파일로 덮어써야 고유 render_report.py 가 살아남는다.
  local borrows; borrows="$(sed -n 's/^borrows: *\[\(.*\)\]/\1/p' "$sdir/skill.yaml" | tr -d ' ' | tr ',' ' ')"
  local bname
  for bname in $borrows; do
    [[ -d "$SRC/$bname" ]] || { echo "  ✗ $name · borrows 대상 없음: $bname" >&2; rm -rf "$stage"; return 1; }
    find "$SRC/$bname/render" -name '*.py' \
         ! -name render_report.py ! -name style_order.py \
         -exec cp {} "$d/_shared/render/" \;
    cp "$SRC/$bname"/templates/*        "$d/_shared/templates/"
    cp "$SRC/$bname"/styles/*.css       "$d/_shared/styles/" 2>/dev/null || true
    cp "$SRC/$bname"/labels/*.json      "$d/_shared/labels/"
    cp "$SRC/$bname"/references/*.md     "$d/references/"
  done

  # ── 스킬 전용 ──
  cp "$sdir"/render/*.py               "$d/_shared/render/"
  cp "$sdir"/styles/*.css              "$d/_shared/styles/" 2>/dev/null || true
  cp "$sdir"/templates/*               "$d/_shared/templates/"
  [[ "$vendor" == "true" ]] && { mkdir -p "$d/_shared/vendor"; cp "$sdir"/vendor/* "$d/_shared/vendor/"; }
  # prompts/ 는 zip 에 넣지 않는다 — DaaS 운영 프롬프트를 무수정으로 뜬 사본이라
  # 실행에 쓰이지 않고, 대조는 저장소를 보는 유지보수자만 한다 (고객 배포물 제외).
  cp "$CORE/LICENSE.txt"               "$d/LICENSE.txt"

  # ── 문서 · 라벨 ──
  cp "$sdir/SKILL.md"                  "$d/SKILL.md"
  cp "$sdir"/references/*.md           "$d/references/" 2>/dev/null || true
  # 라벨은 리포트 언어(kr·jp·us)마다 한 벌씩 있고 전부 싣는다 — 실행 시 --lang
  # 으로 고른다. 빠진 언어가 있으면 그 언어로 렌더할 때 파일을 못 찾아 죽는다.
  cp "$sdir"/labels/*.json             "$d/_shared/labels/"
  for rl in $REPORT_LANGS; do
    ls "$d/_shared/labels/"*."$rl".json >/dev/null 2>&1 \
      || { echo "  ✗ $name · $rl 라벨 없음" >&2; rm -rf "$stage"; return 1; }
  done

  # ── 플레이스홀더 주입 ──
  #   _core 의 logging.py · log_event.py 는 스킬·버전을 모른다. 여기서 박아 넣는다.
  #   안 박으면 admin 에 __SKILL_NAME__ 으로 기록된다.
  local py
  for py in "$d/api/logging.py" "$d/scripts/log_event.py"; do
    sed -i '' -e "s/__SKILL_NAME__/$skill_name/g" -e "s/__SKILL_VERSION__/$version/g" "$py"
  done

  # ── frontmatter 에 service_type · locale 선언 ──
  #   admin 이 스킬 목록을 갈라 보기 위해 읽는 값이다. 이름에서 추론하게 두면
  #   작명 규칙이 바뀔 때 admin 파싱도 같이 깨지므로, 빌드가 여기서 박는다.
  #   이 리포는 SaaS 판 전용이라 service_type 은 항상 saas.
  #   locale 은 "스킬 자체가 쓰인 언어" 다 — 프롬프트가 영어이므로 us 로 고정이며,
  #   리포트 언어와는 무관하다 (그건 실행 시 --lang 으로 정한다).
  if ! grep -q '^  service_type:' "$d/SKILL.md"; then
    awk '
      { print }
      /^  author:/ && !done { print "  service_type: saas"; print "  locale: us"; done=1 }
    ' "$d/SKILL.md" > "$d/SKILL.md.tmp" && mv "$d/SKILL.md.tmp" "$d/SKILL.md"
  fi

  # ── zip ──
  mkdir -p "$OUT"
  rm -f "$OUT/$skill_name.zip"
  (cd "$stage" && zip -qr "$OUT/$skill_name.zip" "$skill_name" \
      -x "*.pyc" -x "**/__pycache__/**" -x "*.DS_Store")
  rm -rf "$stage"

  echo "  ✓ $skill_name.zip  v$version  ($(unzip -Z1 "$OUT/$skill_name.zip" | wc -l | tr -d ' ')파일)"
}

echo "▶ 빌드"
for sdir in "$SRC"/*/; do
  name="$(basename "$sdir")"
  [[ -n "$ONLY_SKILL" && "$name" != "$ONLY_SKILL" ]] && continue
  [[ -f "$sdir/skill.yaml" ]] || { echo "  · $name · skill.yaml 없음 · 건너뜀"; continue; }

  build_one "$name"
done
echo "▶ 완료 · $OUT"
