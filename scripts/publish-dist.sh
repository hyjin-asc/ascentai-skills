#!/usr/bin/env bash
# publish-dist.sh · dist/PUBLISHED.json 에 적힌 스킬만 public 배포 리포로 올린다.
#
# 왜 매니페스트인가:
#   dist/ 는 빌드할 때마다 덮어써지는 작업 산출물이다. 통째로 미러링하면
#   "빌드했다 = 공개됐다" 가 되어 검증 안 끝난 것까지 나간다.
#   여기 적힌 것만 · 적힌 버전으로만 나간다.
#
# 사용:
#   scripts/publish-dist.sh              # 배포
#   scripts/publish-dist.sh --dry-run    # 무엇이 나갈지만 확인
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$REPO_ROOT/dist/PUBLISHED.json"
PUBLIC_REPO="${LIMA_DIST_REPO:-https://github.com/ascentkorea/ascent-skills-dist.git}"
WORK="${TMPDIR:-/tmp}/ascent-skills-dist-$$"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

[[ -f "$MANIFEST" ]] || { echo "❌ 매니페스트 없음: $MANIFEST" >&2; exit 1; }

# ── 1. 매니페스트 읽고 · zip 존재 + 버전 일치 검증 ──────────────────
echo "▶ 검증"
# macOS 기본 bash 는 3.2 라 mapfile 이 없다 · while read 로 채운다
ENTRIES=()
while IFS= read -r line; do
  [[ -n "$line" ]] && ENTRIES+=("$line")
done < <(python3 -c "
import json
d = json.load(open('$MANIFEST'))
for k, v in d['skills'].items():
    print(f'{k}\t{v}')
")
[[ ${#ENTRIES[@]} -gt 0 ]] || { echo "❌ 매니페스트에 공개 대상이 없음" >&2; exit 1; }

FAIL=0
for e in "${ENTRIES[@]}"; do
  name="${e%%$'\t'*}"; want="${e##*$'\t'}"
  zip="$REPO_ROOT/dist/$name.zip"
  if [[ ! -f "$zip" ]]; then
    echo "  ✗ $name · zip 없음 ($zip)"; FAIL=1; continue
  fi
  # zip 안 SKILL.md 의 metadata.version 을 읽어 매니페스트와 대조
  got=$(unzip -p "$zip" "$name/SKILL.md" 2>/dev/null \
        | sed -n 's/^[[:space:]]*version:[[:space:]]*"\(.*\)".*/\1/p' | head -1)
  if [[ -z "$got" ]]; then
    echo "  ✗ $name · zip 안 SKILL.md 에서 version 못 읽음"; FAIL=1; continue
  fi
  if [[ "$got" != "$want" ]]; then
    echo "  ✗ $name · 버전 불일치 · 매니페스트=$want zip=$got"
    echo "      → 매니페스트를 고치거나 zip 을 다시 빌드할 것"; FAIL=1; continue
  fi
  echo "  ✓ $name v$got"
done
[[ $FAIL -eq 0 ]] || { echo "❌ 검증 실패 · 배포 중단" >&2; exit 1; }

if [[ $DRY_RUN -eq 1 ]]; then
  echo "▶ --dry-run · 여기까지 (실제 배포 안 함)"; exit 0
fi

# ── 2. public 리포 클론 · zip 교체 ──────────────────────────────────
echo "▶ public 리포 클론 · $PUBLIC_REPO"
rm -rf "$WORK"
git clone --depth 1 "$PUBLIC_REPO" "$WORK" 2>&1 | sed 's/^/  /'
trap 'rm -rf "$WORK"' EXIT

# 경로 구조는 admin sync 잡이 기대하는 dist/ · examples/ 를 따른다
#   (backend/jobs/sync_skills.py 가 sparse-checkout 으로 이 둘만 받아간다)
# 매니페스트에서 빠진 스킬은 public 쪽에서도 지운다 (공개 철회 반영)
rm -rf "$WORK/dist" "$WORK/examples"
mkdir -p "$WORK/dist"
for e in "${ENTRIES[@]}"; do
  name="${e%%$'\t'*}"
  cp "$REPO_ROOT/dist/$name.zip" "$WORK/dist/$name.zip"
  # 산출물 예시 · examples/<슬러그>.html · admin sync 잡이 이 평평한 구조를 기대한다.
  #   스킬이 언어별로 갈리지 않으므로 스킬당 예시도 한 벌이다.
  if [[ -f "$REPO_ROOT/examples/$name.html" ]]; then
    mkdir -p "$WORK/examples"
    cp "$REPO_ROOT/examples/$name.html" "$WORK/examples/$name.html"
  fi
done
# 옛 평평한 구조로 올렸던 zip 잔재 제거
find "$WORK" -maxdepth 1 -name '*.zip' -delete
cp "$MANIFEST" "$WORK/PUBLISHED.json"
[[ -f "$REPO_ROOT/dist/_public/README.md" ]] && cp "$REPO_ROOT/dist/_public/README.md" "$WORK/README.md"

# ── 3. 커밋 · 푸시 ────────────────────────────────────────────────
cd "$WORK"
if git diff --quiet && git diff --cached --quiet && [[ -z "$(git status --porcelain)" ]]; then
  echo "▶ 변경 없음 · 푸시 생략"; exit 0
fi
git add -A
# paste -d 는 구분자를 "바이트 단위로 순환" 시킨다. ' · ' 는 UTF-8 4바이트라
# (공백·0xC2·0xB7·공백) 이 번갈아 끼면서 메시지가 깨진다 — 직접 이어 붙인다.
SUMMARY=""
for e in "${ENTRIES[@]}"; do
  [[ -n "$SUMMARY" ]] && SUMMARY+=" · "
  SUMMARY+="${e%%$'\t'*} v${e##*$'\t'}"
done
git commit -q -m "[dist] $SUMMARY"
git push -q origin HEAD
echo "▶ 배포 완료 · $SUMMARY"
