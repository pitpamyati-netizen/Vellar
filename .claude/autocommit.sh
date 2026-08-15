#!/usr/bin/env bash
# Страховка на конец задачи: изменения, оставшиеся в рабочем дереве, коммитятся
# сами. Запускается хуком Stop (`.claude/settings.json`).
#
# Осмысленное сообщение пишет тот, кто делал работу, — этот коммит появляется
# только тогда, когда его никто не написал, и говорит об этом прямо. Поэтому в
# истории он выглядит как «chore: автосохранение»: разбирать такой коммит проще,
# чем искать пропавшую работу.
#
# Гейт не обходится: `.githooks/pre-commit` гоняет ruff, mypy и тесты, и если он
# не прошёл, коммита нет, индекс возвращается в исходное состояние, а изменения
# остаются в дереве. Молчаливого коммита с красными тестами тут быть не может.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

git_dir="$(git rev-parse --git-dir)"
# Незаконченное слияние, ребейз или разбор конфликта - чужая работа: трогать
# индекс в этот момент значит испортить её.
for marker in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG rebase-merge rebase-apply; do
    if [ -e "$git_dir/$marker" ]; then
        exit 0
    fi
done

# Чисто - значит работа уже закоммичена как надо. Это обычный случай.
if [ -z "$(git status --porcelain)" ]; then
    exit 0
fi

git add -A
if git diff --cached --quiet; then
    # Всё изменённое оказалось игнорируемым: коммитить нечего.
    exit 0
fi

if git commit -m "chore: автосохранение $(date '+%Y-%m-%d %H:%M')" >/dev/null 2>&1; then
    printf '{"systemMessage":"Автокоммит: %s %s"}\n' \
        "$(git log -1 --format=%h)" \
        "$(git show --stat --format= --oneline HEAD | tail -1 | tr -d '"')"
else
    git reset --quiet
    printf '{"systemMessage":"%s"}\n' \
        "Автокоммит не состоялся: гейт .githooks/pre-commit не прошёл. Изменения остались в рабочем дереве."
fi
