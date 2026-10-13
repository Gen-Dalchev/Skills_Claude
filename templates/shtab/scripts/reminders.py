#!/usr/bin/env python3
"""Напоминания владельцу задачами в репо штаба: сроки токенов и свежесть клонов.

Читает таблицу docs/токены.md. Задача открывается, если: срок прошёл или до
него меньше WARN_DAYS дней; срок не вписан (одна задача на все такие);
первая неделя месяца — «обнови резервные клоны». Открытая задача с тем же
заголовком — второй раз не заводится. Без Claude и без новых секретов:
в Actions — свой GITHUB_TOKEN репо штаба. DRY_RUN=1 — только показать.
"""

import json
import os
import re
import subprocess
import sys
from datetime import date

REPO = os.environ.get("GITHUB_REPOSITORY") or sys.exit("нужен GITHUB_REPOSITORY=<логин>/<репо штаба>")
OWNER = REPO.split("/")[0]
LABEL = "напоминание"
WARN_DAYS = 30
TOKENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "токены.md")
LINK = f"https://github.com/{REPO}/blob/main/docs/%D1%82%D0%BE%D0%BA%D0%B5%D0%BD%D1%8B.md"


def rows():
    for line in open(TOKENS, encoding="utf-8"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0] not in ("Токен", "---") and not cells[0].startswith("-"):
            yield {"name": cells[0].strip("`"), "where": cells[2], "until": cells[4], "how": cells[5]}


def due(today):
    out, unknown = [], []
    for r in rows():
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", r["until"]):
            unknown.append(r["name"])
            continue
        left = (date.fromisoformat(r["until"]) - today).days
        if left <= WARN_DAYS:
            when = f"истёк {r['until']}" if left < 0 else f"истекает {r['until']} — через {left} дн."
            out.append((f"Токен {r['name']}: {when}",
                        f"Где лежит: {r['where']}.\n\nКак заменить: {r['how']}\n\nОпись — {LINK}"))
    if unknown:
        out.append(("Впиши сроки токенов: " + ", ".join(unknown),
                    f"Без срока напоминалка не предупредит об истечении. Срок виден там, где токен выпускался; "
                    f"впиши в столбец «Истекает» — {LINK}"))
    if today.day <= 7:
        out.append((f"Обнови резервные клоны — {today:%m.%Y}",
                    "Клоны на своём компьютере — резервная копия всех репо: "
                    "`git pull` в каждом (команда — в docs/06-Процесс.md). Сделал — закрой задачу."))
    return out


def gh(*args, data=None):
    r = subprocess.run(["gh", "api", *args], input=data, capture_output=True, text=True, check=True)
    return r.stdout


def main():
    items = due(date.today())
    dry = os.environ.get("DRY_RUN") == "1"
    if dry:
        for title, body in items:
            print(f"[пробно] {title}\n  {body[:120]}")
        return
    open_titles = set(json.loads(gh(f"repos/{REPO}/issues?state=open&labels={LABEL}&per_page=100", "--jq", "[.[].title]")))
    for title, body in items:
        if title in open_titles:
            print(f"уже открыта: {title}")
            continue
        payload = json.dumps({"title": title, "body": body, "labels": [LABEL], "assignees": [OWNER]})
        print("открыта:", gh(f"repos/{REPO}/issues", "--input", "-", "--jq", ".html_url", data=payload).strip())


if __name__ == "__main__":
    main()
