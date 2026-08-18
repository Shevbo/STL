"""Исполнитель задач из очереди dev_tasks. Работает на smain, спит бесплатно.

УСТРОЙСТВО. Цикл: спросить очередь (обычный ssh, ноль токенов) -> если задачи
нет, спать -> если есть, отдать пакет модели, отчитаться о расходе, закрыть.
Пустая очередь не стоит ничего — это и есть разница с почтовым автоответчиком,
который 17.08 запускал модель раз в 2.9 минуты просто потому, что пришло письмо.

ГДЕ ПИШЕТ. В СВОЁМ git-worktree на ветке agent/task-<id>, никогда не в main и
никогда в чужом рабочем каталоге. Ветку пушит, слияние делает человек. Так
работа видна (не запушено = не существует), но ни один автомат не двигает main
и не может уронить прод.

ЧЕГО НЕ ДЕЛАЕТ. Задачи уровня `trading` не берёт вовсе: всё, что двигает живые
деньги, лимиты или релиз агента, остаётся человеку. Это не осторожность ради
осторожности — вчера четыре независимых кандидата выглядели убедительно и
рассыпались на проверке; автомат бы этой проверки не сделал.

РАСХОД. Считается не на глаз: claude отдаёт usage в JSON, и это единственная
цифра, которой мы верим. Невидимый расход опаснее большого — 17.08 никто не мог
назвать, сколько сожгла петля, потому что её запуски не попадали ни в один
счётчик.

    python scripts/devworker.py --agent dev1 --zone lab
    python scripts/devworker.py --agent dev1 --once      # один круг и выход
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOSTER = "hoster"
TASKQ = ("cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a; "
         "PY=$(/home/ubuntu/.local/bin/poetry env info --path)/bin/python; "
         "PYTHONPATH=. $PY scripts/taskq.py")
WORKTREES = Path("/tmp/agent-work")
# Уровни, которые исполнитель берёт сам. `trading` намеренно отсутствует.
ALLOWED_TIERS = ("mechanical", "standard")

# ── ВЫБОР МОДЕЛИ ─────────────────────────────────────────────────────────────
# Профиль = чем именно запускать. Ключи и адреса в git не живут: реестр
# переопределяется файлом ~/.stl-agent-models.json того же вида.
#
# ПОРЯДОК ПРОФИЛЕЙ ЗАДАЁТ УРОВЕНЬ ЗАДАЧИ, и это не про экономию, а про
# безопасность. Механическую работу (снести мёртвый файл, поправить импорты)
# не жалко отдать дешёвой модели: ошибка видна сразу и стоит ноль. Задача
# уровня standard трогает смысл, и молчаливая подмена модели там означает
# правдоподобный результат, который некому проверить, — ровно тот класс
# ошибки, который мы ловили весь день 17.08.
# Когда роутинг по моделям появится внутри самого claude, профиль вырождается в
# ОДНО ИМЯ МОДЕЛИ: {"cmd": "claude", "model": "<имя>"}. Поле env остаётся для
# случаев, которые роутинг не покрывает (свой endpoint, отдельный ключ), и по
# умолчанию пусто — секретам в git не место.
MODEL_PROFILES: dict[str, dict] = {
    "claude": {"cmd": "claude", "model": "", "env": {}},
    "kimi": {"cmd": "claude", "model": "", "env": {}},
}
TIER_PROFILES = {
    "mechanical": ["claude", "kimi"],
    "standard": ["claude"],
}
MODELS_FILE = Path.home() / ".stl-agent-models.json"


def load_profiles() -> tuple[dict, dict]:
    """Реестр профилей и раскладка по уровням, с внешним переопределением."""
    profiles, tiers = dict(MODEL_PROFILES), dict(TIER_PROFILES)
    try:
        cfg = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
    except Exception:                                 # noqa: BLE001 — файла нет, это норма
        return profiles, tiers
    for name, spec in (cfg.get("profiles") or {}).items():
        profiles[name] = {**profiles.get(name, {}), **spec}
    for tier, order in (cfg.get("tiers") or {}).items():
        tiers[tier] = list(order)
    return profiles, tiers


def profile_chain(tier: str, profiles: dict, tiers: dict, only: str = "") -> list[str]:
    """Кого пробовать по порядку. Профиль без команды пропускаем молча: это
    означает «Kimi ещё не настроен», а не ошибку."""
    if only:
        return [only] if only in profiles else []
    return [n for n in tiers.get(tier, ["claude"])
            if profiles.get(n, {}).get("cmd")]


def q(cmd: str) -> str:
    """Команда очереди на хостере. Ошибку не глотаем: молчащий исполнитель хуже
    упавшего — упавшего видно."""
    r = subprocess.run(["ssh", HOSTER, f"{TASKQ} {cmd}"],
                       capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(f"очередь ответила {r.returncode}: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def prompt_for(task: dict, branch: str) -> str:
    """Пакет ввода. Самодостаточен намеренно: исполнитель начинает с чтения, а не
    с воспоминаний, поэтому смена модели или машины ничего не меняет."""
    return (
        f"Ты автономный исполнитель STL. Задача #{task['id']} из очереди.\n\n"
        f"ЗОНА: {task['zone']}\nЗАГОЛОВОК: {task['title']}\n\n"
        f"{task['body']}\n\n"
        "--- ПРАВИЛА ---\n"
        f"Ты уже находишься в отдельном git-worktree на ветке {branch}. Работай "
        "здесь, коммить сюда. Ветку не переключай, main не трогай.\n"
        f"Бюджет задачи: {task['budget_tokens']:,} токенов. Он не пожелание: "
        "исчерпав его, ОСТАНОВИСЬ и напиши в отчёте, что успел и что осталось. "
        "Половина сделанной задачи с честным отчётом лучше целой, но неизвестно "
        "какой ценой.\n"
        "Перед коммитом прогони тесты, которых касается правка, и приведи их вывод "
        "в отчёте. Утверждение «работает» без вывода команды не принимается.\n"
        "НЕ ТРОГАЙ живую торговлю: не арминай роботов, не публикуй релизы агента, "
        "не перезапускай службы, не меняй лимиты. Если задача этого требует — "
        "закрой её отказом и объясни, что нужно от человека.\n"
        "Последним сообщением дай КОРОТКИЙ отчёт: что сделано, чем проверено, "
        "что осталось.")


def run_model(prompt: str, spec: dict, cwd: Path, timeout_s: int) -> tuple[str, int, str]:
    """(текст отчёта, потрачено токенов, ошибка). Расход берём из usage самой
    модели: другой честной цифры не существует."""
    cmd = [spec["cmd"], "-p", prompt, "--output-format", "json"]
    if spec.get("model"):
        cmd += ["--model", spec["model"]]
    env = {**os.environ, **{k: str(v) for k, v in (spec.get("env") or {}).items()}}
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired:
        return "", 0, f"модель не уложилась в {timeout_s} с"
    raw = (r.stdout or "").strip()
    try:
        d = json.loads(raw)
    except ValueError:
        return raw[:4000], 0, "" if raw else f"модель вернула пусто, код {r.returncode}"
    u = d.get("usage") or {}
    inp = int(u.get("input_tokens", 0))
    out = int(u.get("output_tokens", 0))
    cw = int(u.get("cache_creation_input_tokens", 0))
    cr = int(u.get("cache_read_input_tokens", 0))
    # ЧТЕНИЕ КЭША СЧИТАЕМ ДЕСЯТОЙ ЧАСТЬЮ. Первая же настоящая задача показала, зачем:
    # сырая сумма дала 507 132 токена при бюджете 60 000 на работу, занявшую 98
    # секунд, — потому что 9/10 этой суммы приходились на перечитывание кэша, а оно
    # стоит примерно десятую долю свежего ввода. Бюджет, считанный по сырой сумме,
    # срабатывал бы на каждой задаче и обесценился бы за день.
    spent = inp + out + cw + cr // 10
    tail = (f"\n\nРАСХОД: ввод {inp:,}, вывод {out:,}, запись кэша {cw:,}, "
            f"чтение кэша {cr:,} -> к бюджету {spent:,}")
    return (str(d.get("result") or "") + tail)[:4000], spent, ""


def one_round(a) -> bool:
    """True = задача была взята. False = очередь пуста или закрыта."""
    got = json.loads(q(f"claim --agent {a.agent} --zone {a.zone}"))
    task = got.get("task")
    if not task:
        print(f"[{a.agent}] {got.get('reason', 'пусто')}", flush=True)
        return False
    tid, tier = task["id"], task.get("tier", "standard")
    print(f"[{a.agent}] взял #{tid} [{task['zone']}/{tier}] {task['title']}", flush=True)
    if tier not in ALLOWED_TIERS:
        q(f"done --id {tid} --agent {a.agent} --tokens 0 --fail "
          f"--result 'уровень {tier} исполнителю запрещён: нужен человек'")
        print(f"[{a.agent}] #{tid} возвращён: уровень {tier} только для человека", flush=True)
        return True

    branch = f"agent/task-{tid}"
    wt = WORKTREES / f"task-{tid}"
    shutil.rmtree(wt, ignore_errors=True)
    WORKTREES.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", a.repo, "fetch", "-q", "origin"], timeout=120)
    subprocess.run(["git", "-C", a.repo, "worktree", "add", "-q", "-B", branch,
                    str(wt), "origin/main"], check=True, timeout=120)
    t0 = time.time()
    try:
        profiles, tiers = load_profiles()
        chain = profile_chain(tier, profiles, tiers, a.profile)
        if not chain:
            raise RuntimeError(f"для уровня {tier} нет ни одного настроенного профиля")
        prompt = prompt_for(task, branch)
        report = err = ""
        spent = 0
        used = ""
        for name in chain:
            used = name
            report, spent, err = run_model(prompt, profiles[name], wt, a.timeout)
            if not err:
                break
            print(f"[{a.agent}] профиль {name} не отработал: {err}", flush=True)
        report += f"\n\nМОДЕЛЬ: {used}"
        # Пушим ВСЕГДА, если что-то закоммичено: незапушенная работа для остальных
        # не существует — на этом 13.08 сутки простоял чужой фикс.
        head = subprocess.run(["git", "-C", str(wt), "log", "--oneline", "origin/main..HEAD"],
                              capture_output=True, text=True, timeout=60).stdout.strip()
        if head:
            subprocess.run(["git", "-C", str(wt), "push", "-q", "-u", "origin", branch],
                           timeout=180)
            report += f"\n\nВЕТКА {branch}:\n{head}"
        else:
            report += "\n\nКоммитов нет."
        res = (report or err or "пусто").replace("'", "’")[:3500]
        flag = " --fail" if err else ""
        q(f"done --id {tid} --agent {a.agent} --tokens {spent}{flag} --result '{res}'")
        print(f"[{a.agent}] #{tid} закрыт за {time.time() - t0:.0f} с, "
              f"токенов {spent:,}{' ОШИБКА: ' + err if err else ''}", flush=True)
    finally:
        subprocess.run(["git", "-C", a.repo, "worktree", "remove", "--force", str(wt)],
                       timeout=120)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--zone", default="")
    ap.add_argument("--repo", default=str(Path.home() / "stl"))
    ap.add_argument("--profile", default="",
                    help="принудительный профиль модели вместо цепочки уровня")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--idle", type=int, default=60, help="сон при пустой очереди, с")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    while True:
        try:
            busy = one_round(a)
        except Exception as exc:                      # noqa: BLE001
            print(f"[{a.agent}] круг сорвался: {exc}", file=sys.stderr, flush=True)
            busy = False
        if a.once:
            return 0
        if not busy:
            time.sleep(a.idle)


if __name__ == "__main__":
    raise SystemExit(main())
