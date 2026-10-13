#!/usr/bin/env bash
# Старт облачной сессии штаба: репо штаба на свежем `main` и клоны активных
# проектов каталога (docs/проекты/, «Статус: активен») в /home/user/<репо>.
#
# Ничего не затирает: `main` двигается только вперёд и только при чистом дереве
# без своих коммитов — иначе стоп с объяснением. Подключить репо к сессии
# (`add_repo`) скрипт не может — это делает сессия; неподключённый репо он
# назовёт.
#
#   bash scripts/start.sh            — репо штаба и все активные репо
#   BASE=/путь bash scripts/start.sh — клоны в другой папке (по умолчанию /home/user)
#   SHTAB_OWNER=логин bash scripts/start.sh — владелец репо проектов; по умолчанию
#                                       владелец `origin` репо штаба
#
# Пример из Skills_Claude/templates/shtab.
set -uo pipefail

BASE="${BASE:-/home/user}"
cd "$(dirname "$0")/.."
HQ="$(basename "$(git remote get-url origin 2>/dev/null)" .git)"
OWNER="${SHTAB_OWNER:-$(git remote get-url origin 2>/dev/null | sed -nE 's#.*github\.com[:/]([^/]+)/.*#\1#p')}"
[ -n "$OWNER" ] || { echo "СТОП: владелец неизвестен — SHTAB_OWNER=логин bash scripts/start.sh"; exit 1; }
problems=()

# ─── Репо штаба ─────────────────────────────────────────────────────────────
# Клон облака мелкий: без истории `main` и `origin/main` выглядят чужими.
# Догружаем историю, потом сравниваем.
git fetch -q origin main || { echo "СТОП: $HQ — git fetch не прошёл"; exit 1; }
if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
  git fetch -q --depth=1000 origin main 2>/dev/null || true
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "СТОП: в $HQ есть незакоммиченные правки — сначала закоммитить или убрать:"
  git status --short --untracked-files=no
  exit 1
fi

if ! git show-ref -q --verify refs/heads/main; then
  git checkout -q -b main origin/main
elif git merge-base --is-ancestor main origin/main 2>/dev/null; then
  git checkout -q main && git merge -q --ff-only origin/main
else
  echo "СТОП: в локальном main $HQ есть коммиты, которых нет на GitHub:"
  git log --oneline origin/main..main 2>/dev/null | head -10 \
    || echo "  (история не сходится даже после догрузки — разобрать руками)"
  echo "Запушить их или убрать — решает сессия; скрипт ничего не трогает."
  exit 1
fi
echo "$HQ: main = $(git log --oneline -1)"

# ─── Проекты ────────────────────────────────────────────────────────────────
for repo in $(python3 scripts/projects.py --status активен --names); do
  [ "$repo" = "$HQ" ] && continue
  dir="$BASE/$repo"
  if [ ! -d "$dir/.git" ]; then
    if git clone -q --depth 50 "https://github.com/$OWNER/$repo" "$dir" 2>/dev/null; then
      echo "$repo: склонирован"
    else
      problems+=("$repo: не склонировался — подключить к сессии (add_repo, доступ push) и запустить скрипт снова")
      continue
    fi
  else
    if ! git -C "$dir" fetch -q origin main 2>/dev/null; then
      problems+=("$repo: git fetch не прошёл — подключён ли репо к сессии?")
      continue
    fi
    branch=$(git -C "$dir" branch --show-current)
    if [ "$branch" = "main" ] && [ -z "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
      git -C "$dir" merge -q --ff-only origin/main 2>/dev/null \
        || problems+=("$repo: main не двигается вперёд — свои коммиты? разобрать")
    else
      problems+=("$repo: ветка «$branch» или незакоммиченные правки — не трогал")
    fi
  fi
  # Подмодули (общий код проектов) в облаке сами не скачиваются.
  [ -f "$dir/.gitmodules" ] && git -C "$dir" submodule update -q --init 2>/dev/null
  echo "$repo: $(git -C "$dir" log --oneline -1 | cut -c1-70)"
done

if [ ${#problems[@]} -gt 0 ]; then
  echo
  echo "Требует внимания:"
  printf -- '- %s\n' "${problems[@]}"
  exit 2
fi
echo
echo "Готово: $HQ и все активные репо на свежем main."
