#!/usr/bin/env python3
"""Сводка сделанного агентами за период: без Claude, только GitHub REST.

Обходит репо конвейера и собирает таблицу «задача → класс, модель/усилие,
ходы, минуты, PR, вердикт ревьюера, итог, красные проверки, развязчик», а
перед ней — сигналы для ретро: ошибки первыми. Ниже — PR сессии.

Ходы, отказы в правах и последние слова агента лежат только в логе прогона
(«Замер прогона»), а логи из облака через REST не отдаются (403). Их кладут в
папку --logs файлами <репо>-<job_id>.log (в облаке — через GitHub MCP,
get_job_logs, хвост ~80 строк). Каких не хватает — показывает --need-logs.
Минуты берутся из времени шага Claude и без логов.

    python3 scripts/summary.py --since 2026-10-01 --until 2026-10-07
    python3 scripts/summary.py --since 2026-10-01 --until 2026-10-07 --need-logs
    python3 scripts/summary.py ... --logs /путь/к/логам > сводка.md
    python3 scripts/summary.py ... --prev docs/ретро/<новый>.md … <старый>.md   # «Динамика», до 4

Нужен только `gh` с доступом к репо (`gh api repos/…`). Владелец репо —
--gh-owner, иначе SHTAB_OWNER, иначе владелец `origin` этого репо.

Пример из Skills_Claude/templates/shtab: имена меток и шагов — как у агентов
Skills_Claude; свои — правь константы ниже.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

OWNER = None  # владелец репо на GitHub — из входа, см. main()
# Список проектов — каталог штаба: «Сводка: да».
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from projects import gh_owner, repos as catalog_repos  # noqa: E402


def load_repos():
    found = catalog_repos(**{"Сводка": "да"})
    if not found:
        sys.exit("в каталоге docs/проекты/ нет проектов со «Сводка: да»")
    return found

BOT = "claude[bot]"
ACTIONS_BOT = "github-actions[bot]"
AGENTS_PATH = ".github/workflows/executor.yml"  # файл «Агенты» в проекте
CI_PATH = ".github/workflows/ci.yml"  # «Проверки PR»
CLAUDE_STEP = "Run anthropics/claude-code-action@v1"


def api(path):
    """GET через gh. Страницы — вручную: --paginate ходит по repositories/{id},
    а их прокси облака не пускает."""
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"gh api {path}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def api_pages(path, key=None, limit=20):
    sep = "&" if "?" in path else "?"
    items = []
    for page in range(1, limit + 1):
        data = api(f"{path}{sep}per_page=100&page={page}")
        chunk = data[key] if key else data
        items.extend(chunk)
        if len(chunk) < 100:
            break
    return items


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def minutes(a, b):
    return round((ts(b) - ts(a)).total_seconds() / 60, 1) if a and b else None


# --- Замер прогона из лога ---------------------------------------------------

LOG_LINE = re.compile(r"^\S+Z ")  # отметка времени в начале строки лога


def parse_measure(lines):
    """Строки «Замера прогона» → словарь: задача, модель, ходы, предел, отказы."""
    m = {"denials": []}
    in_denials = False
    for raw in lines:
        line = LOG_LINE.sub("", raw.rstrip("\n"))
        if line.startswith("Модель: "):
            m["model"] = line[len("Модель: "):].strip()
        elif line.startswith("Ходов: "):
            g = re.match(r"Ходов: (\d+|null)(?: из (\d+))?, минут: ([\d.]+|null)", line)
            if g:
                m["turns"] = None if g[1] == "null" else int(g[1])
                m["limit"] = int(g[2]) if g[2] else None
        elif line.startswith("Задача: #"):
            m["issue"] = int(re.match(r"Задача: #(\d+)", line)[1])
        elif line.startswith("Оценка, $: "):
            m["cost"] = line[len("Оценка, $: "):]
        elif line.startswith("Отказано в правах: "):
            in_denials = True
            continue
        elif line.startswith("Последние слова агента: "):
            m["last_words"] = line[len("Последние слова агента: "):][:200]
        elif line.startswith("Лимит подписки: "):
            m["rate"] = line[len("Лимит подписки: "):]
        elif line.startswith("Стоп очереди: "):
            m["stop"] = line[len("Стоп очереди: "):]
        if in_denials:
            if line.startswith("- "):
                m["denials"].append(line[2:][:120])
            elif line.strip():
                in_denials = False
    return m


def read_measure(logs_dir, repo, job_id):
    """«Замер прогона»: аннотация задания (её отдаёт REST), иначе сохранённый
    лог <репо>-<job_id>.log; None — ни того, ни другого."""
    try:
        notes = api(f"repos/{OWNER}/{repo}/check-runs/{job_id}/annotations")
    except RuntimeError:
        notes = []
    note = next((a for a in notes if a.get("title") == "Замер прогона"), None)
    if note:
        return parse_measure(note["message"].split(" | "))
    if not logs_dir:
        return None
    for name in (f"{repo}-{job_id}.log", f"{repo.lower()}-{job_id}.log"):
        path = os.path.join(logs_dir, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                return parse_measure(f.readlines())
    return None


# --- Сбор по репо --------------------------------------------------------------

def claude_jobs(repo, runs):
    """Задания, где шаг Claude действительно шёл: исполнитель, ревьюер, развязчик."""
    jobs = []
    for run in runs:
        if run["path"] != AGENTS_PATH:
            continue
        kind = {"issues": "executor", "workflow_run": "reviewer",
                "pull_request": "resolver"}.get(run["event"])
        if not kind:
            continue
        for job in api(f"repos/{OWNER}/{repo}/actions/runs/{run['id']}/jobs?per_page=100")["jobs"]:
            step = next((s for s in job.get("steps") or [] if s["name"] == CLAUDE_STEP), None)
            if not step or step["conclusion"] in (None, "skipped"):
                continue
            # Вид — по последнему сегменту имени: развязчик идёт и в хвосте очереди
            # (issues), а очередь — и после слияния (pull_request, «resolver / queue / executor»).
            leaf = job["name"].rsplit(" / ", 1)[-1]
            job_kind = next((k for k in ("resolver", "executor") if leaf.startswith(k)), kind)
            jobs.append({
                "repo": repo, "kind": job_kind, "run_id": run["id"], "job_id": job["id"],
                "name": job["name"], "conclusion": job["conclusion"],
                "step_conclusion": step["conclusion"],
                "started": job["started_at"], "step_start": step["started_at"],
                "step_end": step["completed_at"],
                "minutes": minutes(step["started_at"], step["completed_at"]),
                "url": job["html_url"],
            })
    return jobs


def collect(repo, since, until, logs_dir):
    lo, hi = since.isoformat(), (until + timedelta(days=1)).isoformat()
    runs = api_pages(f"repos/{OWNER}/{repo}/actions/runs?created={since}..{until}",
                     key="workflow_runs")
    pulls = [p for p in api_pages(f"repos/{OWNER}/{repo}/pulls?state=all&sort=created&direction=desc", limit=3)
             if lo <= p["created_at"] < hi]
    events = [e for e in api_pages(f"repos/{OWNER}/{repo}/issues/events", limit=10)
              if lo <= e["created_at"] < hi]
    comments = api_pages(f"repos/{OWNER}/{repo}/issues/comments?since={lo}T00:00:00Z", limit=10)
    # Задачи, заведённые за период (в списке issues есть и PR — их отсеиваем).
    issues_new = [i for i in api_pages(f"repos/{OWNER}/{repo}/issues?state=all&since={lo}T00:00:00Z", limit=5)
                  if "pull_request" not in i and lo <= i["created_at"] < hi]
    jobs = claude_jobs(repo, runs)
    for j in jobs:
        j["measure"] = read_measure(logs_dir, repo, j["job_id"]) if j["kind"] == "executor" else None

    # Задача прогона исполнителя: метку «в-работе» ставит шаг «Очередь»
    # перед Claude — по её времени и находим issue, лог не нужен.
    taken = [(ts(e["created_at"]), e["issue"]["number"]) for e in events
             if e["event"] == "labeled" and (e.get("label") or {}).get("name") == "в-работе"
             and e["actor"]["login"] == ACTIONS_BOT]
    for j in (j for j in jobs if j["kind"] == "executor"):
        a, b = ts(j["started"]), ts(j["step_start"]) + timedelta(seconds=5)
        hits = [n for t, n in taken if a <= t <= b]
        j["issue"] = (j["measure"] or {}).get("issue") or (hits[0] if hits else None)

    stops = {}  # issue → когда стала «нужно-решение»
    for e in events:
        if e["event"] == "labeled" and (e.get("label") or {}).get("name") == "нужно-решение":
            stops.setdefault(e["issue"]["number"], []).append(e["created_at"][:10])

    by_issue_comments = {}
    for c in comments:
        n = int(c["issue_url"].rsplit("/", 1)[1])
        by_issue_comments.setdefault(n, []).append(c)

    ci_red = {}
    for r in runs:
        if r["path"] == CI_PATH and r["conclusion"] in ("failure", "timed_out"):
            ci_red.setdefault(r["head_branch"], []).append(r["html_url"])

    issues_cache = {}

    def issue(n):
        if n not in issues_cache:
            try:
                issues_cache[n] = api(f"repos/{OWNER}/{repo}/issues/{n}")
            except RuntimeError:
                issues_cache[n] = None
        return issues_cache[n]

    rows, session_prs = [], []
    for p in sorted(pulls, key=lambda p: p["created_at"]):
        state = "слит" if p.get("merged_at") else ("закрыт без слияния" if p["state"] == "closed" else "открыт")
        if p["user"]["login"] != BOT:
            session_prs.append({"repo": repo, "number": p["number"], "title": p["title"],
                                "state": state, "url": p["html_url"], "author": p["user"]["login"]})
            continue
        branch = p["head"]["ref"]
        g = re.match(r"agent/(\d+)(?:-(\w+)-(\w+)|-(\w+))?$", branch)
        n = int(g[1]) if g else None
        iss = issue(n) if n else None
        labels = {l["name"] for l in (iss or {}).get("labels", [])}

        # Вердикты ревьюера: комментарии шага ревьюера с отметкой коммита.
        verdicts = []
        for c in by_issue_comments.get(p["number"], []):
            if c["user"]["login"] != ACTIONS_BOT:
                continue
            body = c["body"]
            if "<!-- ревьюер:" not in body and "Ревьюер не дошёл" not in body:
                continue
            head = body.split("\n", 1)[0]
            v = ("не дошёл" if "не дошёл" in head else
                 "не сливать" if head.startswith("**Не сливать") else
                 "можно, с оговоркой" if "оговорк" in head else "можно")
            warns = 0
            if "**Оговорки:**" in body:
                tail = body.split("**Оговорки:**", 1)[1].split("\n\n", 1)[0]
                warns = sum(1 for l in tail.splitlines() if l.startswith("- "))
            run = re.search(r"actions/runs/(\d+)", body)
            verdicts.append({"v": v, "warns": warns, "at": c["created_at"],
                             "run_id": int(run[1]) if run else None})

        # Развязчик: коммиты слияния main в ветку PR от бота Actions.
        merges = [c for c in api(f"repos/{OWNER}/{repo}/pulls/{p['number']}/commits?per_page=100")
                  if len(c["parents"]) > 1]

        # Прогон исполнителя, внутри шага Claude которого открыт PR.
        created = ts(p["created_at"])
        ex = next((j for j in jobs if j["kind"] == "executor"
                   and ts(j["step_start"]) <= created <= ts(j["step_end"]) + timedelta(minutes=1)), None)
        if ex:
            ex["pr"] = p["number"]
            ex["issue"] = ex.get("issue") or n
        rev_jobs = [j for j in jobs if j["kind"] == "reviewer"
                    and j["run_id"] in {v["run_id"] for v in verdicts}]

        rows.append({
            "repo": repo, "issue": n, "issue_title": (iss or {}).get("title", ""),
            "issue_state": (iss or {}).get("state"),
            "labels": labels, "branch": branch, "pair_suffix": (g[2], g[3]) if g and g[2] else None,
            "pr": p["number"], "pr_url": p["html_url"], "state": state,
            "verdicts": verdicts, "merges": merges, "ci_red": ci_red.get(branch, []),
            "exec": ex, "reviewer_jobs": rev_jobs, "stops": stops.get(n, []),
            **authorship((iss or {}).get("body") or ""),
        })

    orphans = [j for j in jobs if j["kind"] == "executor" and "pr" not in j]
    queue_stops = [c for c in comments if c["user"]["login"] == ACTIONS_BOT
                   and c["body"].startswith("Очередь остановлена")]
    return {"repo": repo, "rows": rows, "session_prs": session_prs, "jobs": jobs,
            "issues_new": len(issues_new),
            # Предложения аналитика: принято — получили «готово» или закрыты
            # сделанными; отклонено — закрыты «не планируется».
            "proposals": [i for i in issues_new if any(l["name"] == "предложение" for l in i["labels"])],
            "orphans": orphans, "queue_stops": queue_stops, "stops": stops,
            "issue": issue}


def authorship(body):
    """Шапка «Написал: …» и строка «Оценка: …» задачи (agents/specifier.md
    в Skills_Claude). Задачи без шапки — «неизвестно»; предложения аналитика
    до шапки узнаются по строке «Предложено аналитиком»."""
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    first = lines[0] if lines else ""
    m = re.match(r">?\s*\**Написал:?\**:?\s*([^\s,.;]+)", first)
    author = (m[1].lower() if m else
              "аналитик" if any("Предложено аналитиком" in l for l in lines[:3]) else "неизвестно")
    est = None
    e = re.search(r"Оценка:?\**:?\s*\**(простая|обычная|крупная)", body)
    if e:
        tail = body[e.end():].split("\n", 1)[0]
        scr = re.search(r"экран\w*:\s*(\d+)", tail)
        fil = re.search(r"файл\w*:\s*~?(\d+)", tail)
        est = {"class": e[1], "screens": int(scr[1]) if scr else None,
               "files": int(fil[1]) if fil else None}
    return {"author": author, "estimate": est}


# --- Вывод -------------------------------------------------------------------

def task_class(labels):
    """Класс по меткам: medium — простая, крупная — крупная, иначе обычная."""
    if "крупная" in labels:
        return "крупная"
    if "medium" in labels:
        return "простая"
    return "обычная"


def model_effort(row):
    m = (row["exec"] or {}).get("measure") or {}
    if m.get("model"):
        return m["model"].replace("claude-", "")
    if row["pair_suffix"]:
        return f"~{row['pair_suffix'][0]}/{row['pair_suffix'][1]}"
    labels = row["labels"]
    model = "sonnet" if "sonnet" in labels else "opus"
    effort = "medium" if "medium" in labels else "xhigh" if "xhigh" in labels else "high"
    return f"~{model}/{effort}"


def turns_cell(ex):
    m = (ex or {}).get("measure")
    if not ex:
        return "—"
    if not m:
        return "?"
    t, lim = m.get("turns"), m.get("limit")
    if t is None:
        return "нет"
    s = f"{t}/{lim}" if lim else str(t)
    return f"**{s}**" if lim and t >= lim else s


def verdict_cell(vs):
    if not vs:
        return "—"
    first, last = vs[0], vs[-1]

    def one(v):
        return v["v"] + (f" ({v['warns']})" if v["warns"] else "")
    return one(first) if len(vs) == 1 else f"{one(first)} → {one(last)} ×{len(vs)}"


def link(repo, n, kind="issues"):
    return f"[{repo}#{n}](https://github.com/{OWNER}/{repo}/{kind}/{n})"


def denial_kind(cmd):
    """Вид отказа: первые слова команды, без аргументов."""
    cmd = cmd.replace("Bash: ", "", 1)
    if cmd.startswith("gh pr create"):
        return "gh pr create"
    bare = cmd.replace("2>&1", "")
    if re.search(r"[;&|]|\$\(", bare):
        return "составная команда (; && | $())"
    if re.search(r"(^|\s)\d?>", bare):
        return "перенаправление вывода (> файл)"
    words = cmd.split()
    return " ".join(words[:2]) if words else cmd


def is_pair(r, rows):
    return bool(r["pair_suffix"]) or sum(1 for x in rows if x["repo"] == r["repo"] and x["issue"] == r["issue"]) > 1


def signals(data):
    rows = [r for d in data for r in d["rows"]]
    groups = {k: [] for k in ("fail", "closed", "review", "ci", "class", "stops", "resolver")}
    denials = {}
    for d in data:
        repo = d["repo"]
        for r in d["rows"]:
            pr = link(repo, r["pr"], "pull")
            ex = r["exec"]
            m = (ex or {}).get("measure") or {}
            if r["state"] == "закрыт без слияния":
                why = "проигравший пары" if is_pair(r, rows) else "не пара"
                groups["closed"].append(f"- {pr} (задача #{r['issue']}) — {why}")
            if any(v["v"] == "не сливать" for v in r["verdicts"]):
                groups["review"].append(f"- {pr}: «не сливать» — {verdict_cell(r['verdicts'])}")
            if any(v["v"] == "не дошёл" for v in r["verdicts"]):
                groups["review"].append(f"- {pr}: ревьюер не дошёл до вердикта")
            if r["ci_red"]:
                groups["ci"].append(f"- {pr}: «Проверки PR» красные {len(r['ci_red'])} раз")
            if ex and ex["step_conclusion"] != "success":
                groups["fail"].append(f"- {pr}: шаг исполнителя красный ({ex['step_conclusion']}), PR открыт — [прогон]({ex['url']})")
            if m.get("turns") and m.get("limit") and m["turns"] >= m["limit"]:
                groups["fail"].append(f"- {pr}: упор в предел ходов {m['turns']}/{m['limit']}")
            if m.get("stop"):
                groups["fail"].append(f"- {pr}: стоп очереди — {m['stop']}")
            cls, t = task_class(r["labels"]), m.get("turns")
            if t and ((cls == "простая" and t > 80) or (cls == "обычная" and t >= 100)):
                groups["class"].append(f"- {pr}: класс «{cls}», ходов {t} (повод проверить признаки класса)")
            if cls == "простая" and any(v["v"] == "не сливать" for v in r["verdicts"]):
                groups["class"].append(f"- {pr}: класс «простая», а вердикт «не сливать»")
            if r["merges"]:
                groups["resolver"].append(f"- {pr}: развязчик влил main {len(r['merges'])} раз")
            if r["stops"]:
                groups["stops"].append(f"- {link(repo, r['issue'])}: `нужно-решение` ({', '.join(r['stops'])})")
        for j in d["jobs"]:
            for x in ((j["measure"] or {}).get("denials") or []) if j["kind"] == "executor" else []:
                denials.setdefault(denial_kind(x), []).append(f"{repo}#{j.get('issue') or '?'}")
        for j in d["orphans"]:
            m = j["measure"] or {}
            why = m.get("stop") or m.get("last_words") or "причины в замере нет"
            t = f", ходов {m['turns']}" if m.get("turns") else ""
            groups["fail"].append(
                f"- {repo}: прогон без PR — задача {('#' + str(j['issue'])) if j['issue'] else '?'}{t}, "
                f"{j['minutes']} мин, шаг {j['step_conclusion']} — [прогон]({j['url']}); {why[:150]}")
        seen_rows = {r["issue"] for r in d["rows"]}
        for n, days in d["stops"].items():
            if n not in seen_rows:
                groups["stops"].append(f"- {link(repo, n)}: `нужно-решение` ({', '.join(days)})")

    titles = {"fail": "Провалы прогонов", "closed": "Закрыты без слияния",
              "review": "Ревьюер против или молчит", "ci": "Красные «Проверки PR»",
              "class": "Сверка классов", "stops": "Стопы `нужно-решение`",
              "resolver": "Работа развязчика"}
    out = []
    for k, title in titles.items():
        out += [f"### {title}", ""] + (groups[k] or ["- нет"]) + [""]
    out += ["### Отказы в правах исполнителю — по видам", ""]
    if denials:
        out += [f"- {k}: {len(v)} — {', '.join(sorted(set(v)))}"
                for k, v in sorted(denials.items(), key=lambda kv: -len(kv[1]))]
    else:
        out.append("- нет (или замеры не прочитаны)")
    out.append("")
    return out


def class_stats(rows):
    """Сверка классов: класс против факта. Только прогоны с пределом ходов
    в замере (очередь), без прогонов пар на другом усилии."""
    by = {}
    for r in rows:
        m = ((r["exec"] or {}).get("measure") or {})
        if not m.get("turns") or not m.get("limit"):
            continue
        by.setdefault(task_class(r["labels"]), []).append((m["turns"], r["exec"]["minutes"], r))
    out = ["## Сверка классов", "",
           "| Класс | Прогонов | Ходы: медиана / макс | Минуты: медиана / макс | «не сливать» |",
           "|---|---|---|---|---|"]
    for cls in ("простая", "обычная", "крупная"):
        xs = by.get(cls, [])
        if not xs:
            out.append(f"| {cls} | 0 | — | — | — |")
            continue
        t = sorted(x[0] for x in xs)
        mi = sorted(x[1] or 0 for x in xs)
        no = sum(any(v["v"] == "не сливать" for v in x[2]["verdicts"]) for x in xs)
        out.append(f"| {cls} | {len(xs)} | {t[len(t) // 2]} / {t[-1]} | {mi[len(mi) // 2]} / {mi[-1]} | {no} |")
    out += ["", "Признаки класса: «простая» больше 80 ходов или с «не сливать», «обычная» за 100 — повод править признаки.", ""]
    return out


def author_stats(rows):
    """Оценка против факта по тому, кто писал задачу. У предела — ходов
    не меньше 90 % предела из замера."""
    by = {}
    for r in rows:
        by.setdefault(r["author"], []).append(r)
    out = ["## Кто писал задачу", "",
           "| Автор | PR | Ходы: медиана / макс | У предела | «не сливать» | С «Оценкой» | Оценка ≠ метка |",
           "|---|---|---|---|---|---|---|"]
    misses = []
    for author in sorted(by, key=lambda a: (a == "неизвестно", a)):
        xs = by[author]
        ms = [((x["exec"] or {}).get("measure") or {}) for x in xs]
        t = sorted(m["turns"] for m in ms if m.get("turns"))
        near = sum(1 for m in ms if m.get("turns") and m.get("limit") and m["turns"] >= 0.9 * m["limit"])
        no = sum(any(v["v"] == "не сливать" for v in x["verdicts"]) for x in xs)
        est = [x for x in xs if x["estimate"]]
        diff = [x for x in est if x["estimate"]["class"] != task_class(x["labels"])]
        out.append(f"| {author} | {len(xs)} | " + (f"{t[len(t) // 2]} / {t[-1]}" if t else "—")
                   + f" | {near} | {no} | {len(est)} | {len(diff)} |")
        for x in est:
            m = (x["exec"] or {}).get("measure") or {}
            e = x["estimate"]
            near_x = m.get("turns") and m.get("limit") and m["turns"] >= 0.9 * m["limit"]
            if x in diff or near_x or any(v["v"] == "не сливать" for v in x["verdicts"]):
                misses.append(f"- {link(x['repo'], x['issue'])} ({author}): оценка «{e['class']}», "
                              f"экранов {e['screens'] if e['screens'] is not None else '?'}, "
                              f"файлов {e['files'] if e['files'] is not None else '?'}; метка «{task_class(x['labels'])}», "
                              f"ходов {m.get('turns') or '?'} из {m.get('limit') or '?'}, "
                              f"вердикт {verdict_cell(x['verdicts'])}")
    out += ["", "Промахи оценки — у предела, «не сливать» или класс по «Оценке» не совпал с меткой:", ""]
    out += misses or ["- нет (или задач с «Оценкой» не было)"]
    out.append("")
    return out


def metrics(data, since, until):
    """Итоги периода числами — для «Динамики» следующего отчёта."""
    rows = [r for d in data for r in d["rows"]]
    jobs = [j for d in data for j in d["jobs"]]
    ms = [j["measure"] for j in jobs if j["kind"] == "executor" and j["measure"]]
    costs = [float(m["cost"]) for m in ms if (m.get("cost") or "").replace(".", "", 1).isdigit()]
    den = {}
    for m in ms:
        for x in m.get("denials") or []:
            k = denial_kind(x)
            den[k] = den.get(k, 0) + 1
    ex = [j for j in jobs if j["kind"] == "executor"]
    return {
        "период": f"{since}…{until}",
        "PR всего": len(rows) + sum(len(d["session_prs"]) for d in data),
        "PR агентов": len(rows),
        "слито": sum(r["state"] == "слит" for r in rows),
        "задач заведено": sum(d["issues_new"] for d in data),
        "запусков исполнителя": len(ex),
        "прогонов без PR": sum(len(d["orphans"]) for d in data),
        "минут исполнителя": round(sum(j["minutes"] or 0 for j in ex)),
        "ходов исполнителя": sum(m.get("turns") or 0 for m in ms),
        "ходов на прогон": round(sum(m.get("turns") or 0 for m in ms) / len(ms)) if ms else 0,
        "отказов в правах": sum(den.values()),
        "отказов на прогон": round(sum(den.values()) / len(ms), 2) if ms else 0,
        "оценка, $": round(sum(costs), 2) if costs else None,
        "«не сливать»": sum(any(v["v"] == "не сливать" for v in r["verdicts"]) for r in rows),
        "предложено аналитиком": sum(len(d["proposals"]) for d in data),
        "из них принято": sum(1 for d in data for i in d["proposals"]
                              if any(l["name"] == "готово" for l in i["labels"])
                              or (i["state"] == "closed" and i.get("state_reason") == "completed")),
        "из них отклонено": sum(1 for d in data for i in d["proposals"]
                                if i["state"] == "closed" and i.get("state_reason") == "not_planned"),
        "отказы по видам": den,
    }


def read_prev(path):
    """Итоги прошлого отчёта из строки `<!-- итоги-сводки: {...} -->`."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    m = re.search(r"<!-- итоги-сводки: (\{.*?\}) -->", text)
    return json.loads(m[1]) if m else None


def dynamics(now, prevs):
    """Динамика к прошлым отчётам: prevs — [(имя, итоги)], новый первым."""
    prevs = [(n, p) for n, p in prevs if p]
    if not prevs:
        return []
    def week(p, n):
        d = p.get("период", "")
        return f"нед. с {d[8:10]}.{d[5:7]}" if len(d) >= 10 else n
    head = " | ".join(week(p, n) for n, p in prevs)
    out = [f"### Динамика к прошлым отчётам ({', '.join(n for n, _ in prevs)})", "",
           f"| Показатель | Сейчас | {head} | Разница с прошлой |",
           "|---|---|" + "---|" * len(prevs) + "---|"]
    fmt = lambda v: "—" if v is None else str(v)
    for k, v in now.items():
        if k in ("период", "отказы по видам"):
            continue
        p0 = prevs[0][1].get(k)
        diff = f"{v - p0:+g}" if isinstance(v, (int, float)) and isinstance(p0, (int, float)) else "—"
        out.append(f"| {k} | {fmt(v)} | " + " | ".join(fmt(p.get(k)) for _, p in prevs) + f" | {diff} |")
    kinds = {}
    for _, p in [("", now)] + prevs:
        for k, c in (p.get("отказы по видам") or {}).items():
            kinds[k] = kinds.get(k, 0) + c
    if kinds:
        out += ["", "Отказы в правах по видам — сейчас / " + " / ".join(n for n, _ in prevs) + ":", ""]
        for k in sorted(kinds, key=lambda k: -kinds[k]):
            vals = [now["отказы по видам"].get(k, 0)] + [(p.get("отказы по видам") or {}).get(k, 0) for _, p in prevs]
            out.append(f"- {k}: " + " / ".join(map(str, vals)))
    out.append("")
    return out


def picture(data):
    """Картина недели для человека: числа и что слито в приложения."""
    rows = [r for d in data for r in d["rows"]]
    jobs = [j for d in data for j in d["jobs"]]
    sp = [s for d in data for s in d["session_prs"]]
    merged = [r for r in rows if r["state"] == "слит"]
    ms = [j["measure"] for j in jobs if j["kind"] == "executor" and j["measure"]]
    turns = sum(m.get("turns") or 0 for m in ms)
    costs = [float(m["cost"]) for m in ms if (m.get("cost") or "").replace(".", "", 1).isdigit()]

    def mins(kind):
        js = [j for j in jobs if j["kind"] == kind]
        return len(js), round(sum(j["minutes"] or 0 for j in js))
    ex, rv, rs = mins("executor"), mins("reviewer"), mins("resolver")
    hours = round((ex[1] + rv[1] + rs[1]) / 60, 1)
    out = ["## Картина недели", "",
           f"- PR всего: {len(rows) + len(sp)} — от агентов {len(rows)} (слито {len(merged)}, "
           f"закрыто без слияния {sum(r['state'] == 'закрыт без слияния' for r in rows)}), от сессии штаба {len(sp)}",
           f"- задач заведено: {sum(d['issues_new'] for d in data)}"
           + (f", из них предложено аналитиком {sum(len(d['proposals']) for d in data)}"
              if any(d["proposals"] for d in data) else ""),
           f"- запусков агентов: {ex[0] + rv[0] + rs[0]} — исполнитель {ex[0]} ({ex[1]} мин), "
           f"ревьюер {rv[0]} ({rv[1]} мин), развязчик {rs[0]} ({rs[1]} мин); всего ~{hours} ч",
           f"- ходов исполнителя: {turns} по {len(ms)} замерам из {ex[0]}"
           + (f"; оценка Claude Code: {sum(costs):.2f} $ по {len(costs)} прогонам" if costs else ""),
           "", "### Слито в приложения", ""]
    for repo in [d["repo"] for d in data]:
        titles = [r for r in merged if r["repo"] == repo]
        if not titles:
            continue
        out.append(f"**{repo}** — {len(titles)}")
        out += [f"- {r['issue_title'] or '?'} ({link(repo, r['pr'], 'pull')}, {task_class(r['labels'])})" for r in titles]
        out.append("")
    return out


def render(data, since, until, prevs=()):
    lines = [f"# Сводка агентов: {since:%d.%m}–{until:%d.%m.%Y}", "",
             f"Собрано скриптом `scripts/summary.py` {datetime.now(timezone.utc):%d.%m.%Y %H:%M} UTC. "
             "Класс — по меткам задачи сейчас; `~` — модель/усилие по меткам, не из замера; "
             "`?` — замера нет (лог не сохранён). Ходы **жирным** — упор в предел.", ""]
    now = metrics(data, since, until)
    lines += picture(data) + dynamics(now, list(prevs))
    lines += ["## Сигналы — ошибки первыми", ""] + signals(data)

    rows = [r for d in data for r in d["rows"]]
    lines += ["## Задачи и PR бота", "",
              "| Задача | Класс | Модель/усилие | Ходы | Мин | Ревью, мин | PR | Вердикт (оговорок) | Итог | Развязчик |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["exec"] or {}).get("step_start") or r["pr_url"]):
        ex = r["exec"]
        rev_min = sum(j["minutes"] or 0 for j in r["reviewer_jobs"])
        title = r["issue_title"][:45].replace("|", "/")
        lines.append("| " + " | ".join([
            f"{link(r['repo'], r['issue'])} {title}" if r["issue"] else "?",
            task_class(r["labels"]) + (" · пара" if is_pair(r, rows) else ""),
            model_effort(r), turns_cell(ex),
            str(ex["minutes"]) if ex else "—",
            str(round(rev_min, 1)) if r["reviewer_jobs"] else "—",
            link(r["repo"], r["pr"], "pull"),
            verdict_cell(r["verdicts"]), r["state"],
            str(len(r["merges"])) if r["merges"] else "—",
        ]) + " |")
    lines.append("")

    lines += class_stats(rows)
    lines += author_stats(rows)
    ex_jobs = [j for d in data for j in d["jobs"] if j["kind"] == "executor"]
    rv_jobs = [j for d in data for j in d["jobs"] if j["kind"] == "reviewer"]
    rs_jobs = [j for d in data for j in d["jobs"] if j["kind"] == "resolver"]
    merged = sum(r["state"] == "слит" for r in rows)
    closed = sum(r["state"] == "закрыт без слияния" for r in rows)

    def total(js):
        return round(sum(j["minutes"] or 0 for j in js), 1)
    lines += ["## Итого", "",
              f"- PR бота: {len(rows)} — слито {merged}, закрыто без слияния {closed}, открыто {len(rows) - merged - closed}",
              f"- прогонов Claude: исполнитель {len(ex_jobs)} ({total(ex_jobs)} мин), "
              f"ревьюер {len(rv_jobs)} ({total(rv_jobs)} мин), развязчик {len(rs_jobs)} ({total(rs_jobs)} мин)",
              f"- замеров ходов прочитано: {sum(1 for j in ex_jobs if j['measure'])} из {len(ex_jobs)} прогонов исполнителя",
              ""]

    sp = [s for d in data for s in d["session_prs"]]
    lines += ["## PR сессии штаба", ""]
    for s in sp:
        lines.append(f"- {link(s['repo'], s['number'], 'pull')} {s['title'][:70]} — {s['state']}")
    lines.append("")
    lines += ["<!-- итоги-сводки: " + json.dumps(now, ensure_ascii=False) + " -->"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Сводка сделанного агентами за период")
    ap.add_argument("--since", required=True, help="первый день, ГГГГ-ММ-ДД (UTC)")
    ap.add_argument("--until", required=True, help="последний день включительно, ГГГГ-ММ-ДД")
    ap.add_argument("--repos", help="через запятую; по умолчанию — «Сводка: да» из каталога docs/проекты/")
    ap.add_argument("--logs", help="папка с логами прогонов <репо>-<job_id>.log")
    ap.add_argument("--prev", nargs="+", default=[],
                    help="прошлые отчёты ретро (.md), новый первым — для «Динамики»; обычно четыре")
    ap.add_argument("--need-logs", action="store_true",
                    help="только перечислить задания Claude, чьих логов нет в --logs")
    ap.add_argument("--gh-owner", help="владелец репо на GitHub; иначе SHTAB_OWNER или origin")
    a = ap.parse_args()
    global OWNER
    OWNER = gh_owner(a.gh_owner)
    since = datetime.strptime(a.since, "%Y-%m-%d").date()
    until = datetime.strptime(a.until, "%Y-%m-%d").date()

    repos = a.repos.split(",") if a.repos else load_repos()
    data = [collect(r, since, until, a.logs) for r in repos]
    if a.need_logs:
        for d in data:
            for j in d["jobs"]:
                if j["kind"] == "executor" and not j["measure"]:
                    print(f"{d['repo']}\t{j['job_id']}\t{j['step_start'][:16]}")
        return
    prevs = [(os.path.basename(p), read_prev(p)) for p in a.prev]
    print(render(data, since, until, prevs))


if __name__ == "__main__":
    sys.exit(main())
