#!/usr/bin/env python3
"""Каталог проектов штаба: поиск, связи, даты, проверка.

Карточки — docs/проекты/<Репо>.md: шапка из строк «Поле: значение»
между `---`, ниже — текст. Оглавление docs/проекты/каталог.md собирается
отсюда, руками не правится.

  projects.py                        все проекты, коротко
  projects.py --group приложения --status активен --fields Репо,Режим
  projects.py --summary да --names   только имена (для других скриптов)
  projects.py --links MyCore         кто связан с проектом
  projects.py --new Repo --group …   новая карточка (режим «новичок»)
  projects.py --refresh              даты из GitHub, затем --build
  projects.py --build                пересобрать каталог.md
  projects.py --check                изъяны каталога; код 1, если есть

Агенты спрашивают скрипт, а не читают карточки целиком: ответ — строки
с нужными полями. Карточку читают, только когда нужен замысел проекта.

Владелец репо на GitHub (для --refresh и --new) — по порядку: флаг
--gh-owner, переменная SHTAB_OWNER, владелец `origin` этого репо.
Папка карточек — --dir (по умолчанию docs/проекты рядом со scripts/).

Пример из Skills_Claude/templates/shtab — подгони группы и поля под себя.
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "..", "docs", "проекты")
INDEX = "каталог.md"

FIELDS = ["Название", "Репо", "Группа", "Чей", "Статус", "Связи", "Данные", "Видимость",
          "Стек", "Начат", "Активность", "Сводка", "Аналитик", "Режим"]
# Допустимые значения; новое значение — правкой здесь и записью в docs/05-Решения.md.
ALLOWED = {
    "Группа": ["приложения", "игры", "личное", "работа", "инфраструктура"],
    "Чей": ["мой", "друга", "заказчика"],
    "Статус": ["идея", "активен", "пауза", "архив"],
    "Видимость": ["публичный", "приватный", "—"],
    "Сводка": ["да", "нет"],
    "Аналитик": ["приложение", "план", "нет"],
    "Режим": ["новичок", "обычный", "—"],
}
GROUP_TITLES = {"приложения": "Приложения", "игры": "Игры", "личное": "Личное",
                "работа": "Работа", "инфраструктура": "Инфраструктура конвейера"}
# Флаги поиска — поля с допустимыми значениями.
FILTERS = {"group": "Группа", "owner": "Чей", "status": "Статус", "summary": "Сводка",
           "analyst": "Аналитик", "mode": "Режим"}
STALE_DAYS = 30  # «активен» без пушей дольше — повод спросить про паузу


def gh_owner(given=None):
    """Владелец репо на GitHub: флаг, SHTAB_OWNER или владелец origin."""
    if given or os.environ.get("SHTAB_OWNER"):
        return given or os.environ["SHTAB_OWNER"]
    r = subprocess.run(["git", "-C", HERE, "remote", "get-url", "origin"], capture_output=True, text=True)
    m = re.search(r"github\.com[:/]([^/]+)/", r.stdout)
    if not m:
        sys.exit("владелец GitHub неизвестен: --gh-owner ЛОГИН или SHTAB_OWNER=ЛОГИН")
    return m.group(1)


def load():
    cards = []
    for name in sorted(os.listdir(DIR)):
        if not name.endswith(".md") or name == INDEX:
            continue
        text = open(os.path.join(DIR, name), encoding="utf-8").read()
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        card = {"_file": name}
        for line in (m.group(1).splitlines() if m else []):
            if ":" in line:
                k, v = line.split(":", 1)
                card[k.strip()] = v.strip()
        cards.append(card)
    return cards


def repos(**flt):
    """Имена репо по фильтру полей — для summary.py и промптов."""
    return [c["Репо"] for c in load() if c.get("Репо", "—") != "—"
            and all(c.get(k) == v for k, v in flt.items())]


def links_of(card):
    """Имена проектов из поля «Связи»: «ядро MyCore; читает A, B»."""
    return set(re.findall(r"[A-Za-z_][\w-]*", card.get("Связи", "")))


def write_card(card, body):
    head = "\n".join(f"{k}: {card.get(k, '—')}" for k in FIELDS)
    open(os.path.join(DIR, card["_file"]), "w", encoding="utf-8").write(f"---\n{head}\n---\n{body}")


def card_body(card):
    text = open(os.path.join(DIR, card["_file"]), encoding="utf-8").read()
    return re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)


def refresh(cards, owner):
    for c in cards:
        repo = c.get("Репо", "—")
        if repo == "—":
            continue
        r = subprocess.run(["gh", "api", f"repos/{owner}/{repo}", "--jq",
                            '"\\(.created_at[:10]) \\(.pushed_at[:10]) \\(.private)"'],
                           capture_output=True, text=True)
        if r.returncode:
            print(f"{repo}: нет доступа — даты не обновлены", file=sys.stderr)
            continue
        created, pushed, private = r.stdout.split()
        c["Начат"] = c.get("Начат", "—") if c.get("Начат", "—") != "—" else created
        c["Активность"] = pushed
        c["Видимость"] = "приватный" if private == "true" else "публичный"
        write_card(c, card_body(c))


def build(cards):
    out = ["# Каталог проектов", "",
           "Собирается `python3 scripts/projects.py --build` из карточек этой папки — руками не правится. "
           "Поиск — `scripts/projects.py`, подробности — карточка проекта. Внутри группы — свежие сверху.", ""]
    for g in ALLOWED["Группа"]:
        rows = [c for c in cards if c.get("Группа") == g]
        if not rows:
            continue
        rows.sort(key=lambda c: c.get("Активность", "").replace("—", ""), reverse=True)
        out += [f"## {GROUP_TITLES[g]}", "",
                "| Проект | Чей | Статус | Связи | Активность | Режим |", "|---|---|---|---|---|---|"]
        for c in rows:
            out.append(f"| [{c.get('Название', c['_file'])}]({c['_file']}) | {c.get('Чей', '—')} | "
                       f"{c.get('Статус', '—')} | {c.get('Связи', '—')} | {c.get('Активность', '—')} | "
                       f"{c.get('Режим', '—')} |")
        out.append("")
    text = "\n".join(out)
    path = os.path.join(DIR, INDEX)
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    open(path, "w", encoding="utf-8").write(text)
    return old != text


def check(cards):
    problems = []
    names = {c.get("Репо") for c in cards} | {c["_file"][:-3] for c in cards}
    stale = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    for c in cards:
        who = c["_file"]
        for k in FIELDS:
            if k not in c:
                problems.append(f"{who}: нет поля «{k}»")
        for k, vals in ALLOWED.items():
            if k in c and c[k] not in vals:
                problems.append(f"{who}: «{k}: {c[k]}» — не из {', '.join(vals)}")
        for link in links_of(c) - names:
            problems.append(f"{who}: связь с «{link}» — нет карточки")
        if c.get("Статус") == "активен" and c.get("Активность", "—") not in ("—", "") and c["Активность"] < stale:
            problems.append(f"{who}: «активен», а пушей нет с {c['Активность']} — пауза?")
        if c.get("Статус") in ("идея", "архив") and c.get("Сводка") == "да":
            problems.append(f"{who}: «{c['Статус']}», а сводка по нему идёт")
    path = os.path.join(DIR, INDEX)
    snapshot = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if build(cards):
        problems.append(f"{INDEX} отставал от карточек — пересобран, закоммить")
    elif not snapshot:
        problems.append(f"нет {INDEX}")
    return problems


def main():
    global DIR
    ap = argparse.ArgumentParser(description="Каталог проектов штаба")
    for flag, k in FILTERS.items():
        ap.add_argument("--" + flag, dest=k, help=" | ".join(ALLOWED[k]))
    ap.add_argument("--fields", default="Репо,Группа,Статус,Связи,Активность")
    ap.add_argument("--names", action="store_true", help="только имена репо")
    ap.add_argument("--links", help="проекты, связанные с данным, в обе стороны")
    ap.add_argument("--new", help="новая карточка: имя репо; поля — флагами")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--gh-owner", help="владелец репо на GitHub; иначе SHTAB_OWNER или origin")
    ap.add_argument("--dir", help="папка карточек; по умолчанию docs/проекты")
    a = ap.parse_args()
    DIR = a.dir or DIR
    cards = load()
    flt = {k: getattr(a, k) for k in FILTERS.values() if getattr(a, k)}

    if a.new:
        card = {k: "—" for k in FIELDS} | {"Название": a.new, "Репо": a.new, "Чей": "мой", "Статус": "активен",
                                           "Сводка": "да", "Аналитик": "приложение", "Режим": "новичок",
                                           "_file": a.new + ".md"} | flt
        if os.path.exists(os.path.join(DIR, card["_file"])):
            sys.exit(f"карточка {card['_file']} уже есть")
        write_card(card, "\nЗамысел — одной-двумя фразами. Документы — в репо проекта.\n")
        refresh([card], gh_owner(a.gh_owner))
        build(load())
        print(f"заведена {card['_file']}; допиши «Название», «Группа», «Связи», «Стек» и замысел")
        return
    if a.refresh:
        refresh(cards, gh_owner(a.gh_owner))
        cards = load()
        build(cards)
        print(f"даты обновлены, {INDEX} пересобран")
        return
    if a.build:
        print(f"{INDEX}: {'пересобран' if build(cards) else 'без изменений'}")
        return
    if a.check:
        problems = check(cards)
        print("\n".join(problems) if problems else "изъянов нет")
        sys.exit(1 if problems else 0)

    rows = [c for c in cards if all(c.get(k) == v for k, v in flt.items())]
    if a.links:
        target = next((c for c in cards if a.links in (c.get("Репо"), c["_file"][:-3])), None)
        mine = links_of(target) if target else set()
        rows = [c for c in rows if a.links in links_of(c) or c.get("Репо") in mine]
    if a.names:
        print("\n".join(c["Репо"] for c in rows if c.get("Репо", "—") != "—"))
        return
    cols = a.fields.split(",")
    for c in rows:
        print("\t".join(c.get(k, "—") for k in cols))


if __name__ == "__main__":
    main()
