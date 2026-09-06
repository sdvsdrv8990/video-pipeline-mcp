"""
tests/quick/test_hooks.py — хуки `.claude/hooks/` судят СОБЫТИЕ сессии, а не дерево репозитория.

Standalone-прогон:  python tests/quick/test_hooks.py
Проверяет: каждый хук ловит своё событие и молчит на чужом; обе ложные тревоги гейта фактов
(проза про запись, путь в кавычках) закреплены регрессией; шим без репозитория молчит, а не
падает, а на неисправной форме тронутого файла — говорит, и на здоровом молчит.
Состояние пишется в подставной HOME — настоящее не трогается.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".claude" / "hooks"


def дом_хвостов(корень: Path) -> Path:
    """Дом остатков в подставном дереве — там же, где в настоящем: под git, а не в следе."""
    путь = корень / "scripts" / "guards" / "tails.jsonl"
    путь.parent.mkdir(parents=True, exist_ok=True)
    return путь

_checks = 0
_fails = []


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
    print(f"  {'✓' if cond else '✗'} {msg}")


def fire(hook: str, payload: dict, *args, home: str | None = None, **env) -> tuple[int, dict, str]:
    """Событие в хук через stdin. HOME подставной: у хуков состояние с TTL, и оно лежит на диске."""
    env_full = {**os.environ, "HOME": home or tempfile.mkdtemp(prefix="vpm-hooks-home-"), **env}
    done = subprocess.run([sys.executable, str(HOOKS / hook), *args],
                          input=json.dumps(payload), capture_output=True, text=True,
                          timeout=120, env=env_full, cwd=str(ROOT))
    try:
        out = json.loads(done.stdout) if done.stdout.strip() else {}
    except json.JSONDecodeError:
        out = {}
    return done.returncode, out, done.stdout + done.stderr


def decision(out: dict) -> str:
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision", "")


def reason(out: dict) -> str:
    spec = out.get("hookSpecificOutput") or {}
    return spec.get("permissionDecisionReason") or spec.get("additionalContext") or ""


def edit(path: str, tool: str = "Edit", **extra) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": "набор-хуков",
            "tool_input": {"file_path": str(ROOT / path), **extra}}


def bash(command: str, sid: str = "набор-хуков") -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": sid,
            "tool_input": {"command": command}}


def main() -> int:
    print("§1 гейт фактов: правка несущего файла")
    _, out, _ = fire("vpm-fact-gate.py", edit("core/engine/engine.py"))
    ok(decision(out) == "deny", f"правка ядра без фактов — отказ (решение {decision(out)!r})")
    ok("Замер места" in reason(out),
       "отказ ОТДАЁТ замер, а не просит его сделать: просьбу удовлетворяет одна фраза")
    _, out, _ = fire("vpm-fact-gate.py", edit("docs/roadmap/02_findings.md"))
    ok(not out, "документы вне несущей зоны — молчим")
    _, out, _ = fire("vpm-fact-gate.py", edit("tests/quick/test_invariants.py"))
    ok(not out, "правка СУЩЕСТВУЮЩЕГО набора — не рождение, гейт молчит")

    print("§2 гейт фактов: рождение набора — отдельная дверь")
    _, out, _ = fire("vpm-fact-gate.py", edit("tests/quick/test_ещё_один.py", tool="Write"))
    ok(decision(out) == "deny", "новый набор — решение «не плодить», а не правка")
    ok("Замер зон" in reason(out) and "запас:" in reason(out),
       "отказ ДОСТАВЛЯЕТ таблицу зон с запасами — без неё на «кто хозяин» не ответить")

    print("§3 гейт фактов: команда оболочки")
    _, out, _ = fire("vpm-fact-gate.py", bash("rm -rf build/"))
    ok(decision(out) == "deny", "разрушительная команда — отказ")
    _, out, _ = fire("vpm-fact-gate.py", bash("cat > core/новый.py <<'EOF'\nx = 1\nEOF"))
    ok(decision(out) == "deny", "запись несущего файла через оболочку — та же правка")
    _, out, _ = fire("vpm-fact-gate.py", bash("git status --porcelain"))
    ok(not out, "чтение состояния — не запись и не разрушение")

    print("§3б гейт фактов: путь, СОБРАННЫЙ из литералов, виден до записи")
    собран = ("python3 - <<'PY'\nfrom pathlib import Path\n"
              "корень = Path('core')\nцель = корень / 'engine' / 'engine.py'\n"
              "цель.write_text('x')\nPY")
    _, out, _ = fire("vpm-fact-gate.py", bash(собран))
    ok(decision(out) == "deny",
       f"склейка `корень / 'a' / 'b.py'` разрешается точно и судится как прямой путь "
       f"(решение {decision(out)!r})")
    вычисляем = ("python3 - <<'PY'\nimport pathlib\n"
                 "for имя in ['engine']:\n"
                 "    pathlib.Path(f'core/{имя}/engine.py').write_text('x')\nPY")
    _, out, _ = fire("vpm-fact-gate.py", bash(вычисляем))
    ok(not out,
       "путь, вычисляемый из данных, до записи не разрешим — у него объявленный контракт "
       "«постфактум», и молчание здесь честное, а не дыра")

    print("§3а гейт фактов: взгляд ДО правки снимает пошлину")
    цель = "core/engine/engine.py"
    # Общий HOME на взгляд и правку: состояние гейта лежит на диске, и свежий дом на каждый
    # вызов стирал бы ровно то, что проверяется.
    дом = tempfile.mkdtemp(prefix="vpm-пошлина-")
    взгляд = lambda путь, sid: fire("vpm-fact-gate.py", {  # noqa: E731
        "hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": sid,
        "tool_input": {"command": f"grep -n ToolResult {путь}"}}, home=дом)
    правка = lambda sid: fire("vpm-fact-gate.py", {  # noqa: E731
        "hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": sid,
        "tool_input": {"file_path": цель}}, home=дом)[1]
    взгляд(цель, "looked-own")
    ok(decision(правка("looked-own")) != "deny",
       "взгляд на файл ДО правки отпирает её без отказа")
    ok(decision(правка("looked-none")) == "deny",
       "правка без взгляда на этот файл по-прежнему отказывает")
    взгляд("core/auth.py", "looked-other")
    ok(decision(правка("looked-other")) == "deny",
       "взгляд на ЧУЖОЙ файл правку не отпирает")

    слипание = tempfile.mkdtemp(prefix="vpm-слипание-")
    for ключ in ("сессия-один", "сессия-два"):
        fire("vpm-fact-gate.py", {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                                  "session_id": ключ, "tool_input": {"file_path": цель}},
             home=слипание)
    состояния = list((Path(слипание) / ".claude" / "state" / "vpm-fact-gate").glob("*.json"))
    ok(len(состояния) == 2,
       f"ключи сессий не слипаются — состояние одной не течёт в другую  → файлов {len(состояния)}")

    print("§3в гейт фактов: форма запуска границы не двигает")
    прямой = "python3 -c \"open('core/engine/engine.py','w').write(1)\""
    _, out, _ = fire("vpm-fact-gate.py", bash(прямой))
    ok(decision(out) == "deny",
       f"литерал в inline-запуске виден Pre-половине, а не только в heredoc "
       f"(решение {decision(out)!r})")
    глоб = ("python3 -c \"import pathlib; "
            "[q.write_text('x') for q in pathlib.Path('core').rglob('*.py')]\"")
    _, out, _ = fire("vpm-fact-gate.py", bash(глоб))
    ok(not out, "вычисляемый путь в inline-запуске остаётся постфактумным")
    чтение = "python3 -c \"print(open('core/engine/engine.py').read())\""
    _, out, _ = fire("vpm-fact-gate.py", bash(чтение))
    ok(not out, "inline-запуск, который только читает имя, тревоги не даёт")

    print("§4 гейт фактов: обе ложные тревоги, найденные на себе же")
    _, out, _ = fire("vpm-fact-gate.py",
                     bash("cat > docs/roadmap/_sessions.md <<'EOF'\nприём записи: rm -rf лишнего\nEOF"))
    ok(not out, "тело here-document — ДАННЫЕ: текст про опасную команду не запрещает сам себя")
    _, out, _ = fire("vpm-fact-gate.py", bash("echo 'core/engine/engine.py' | wc -l"))
    ok(not out, "путь в кавычках — упоминание, а не цель записи")

    print("§5 рубильники сторожей живы (у сторожа остатков рубильника больше нет — §15)")
    _, out, _ = fire("vpm-fact-gate.py", edit("core/engine/engine.py"), VPM_FACT_GATE="off")
    ok(not out, "VPM_FACT_GATE=off — сторож выключается объявленным способом")
    home = tempfile.mkdtemp(prefix="vpm-hooks-home-")
    _, first, _ = fire("vpm-fact-gate.py", bash("rm -rf a/", sid="повтор"), home=home)
    _, second, _ = fire("vpm-fact-gate.py", bash("rm -rf a/", sid="повтор"), home=home)
    ok(decision(first) == "deny" and len(reason(second)) < len(reason(first)),
       "второй отказ по тому же поводу короче первого: сторож не превращается в шум")

    print("§6 обоснование текста в коде")
    added = "\n".join(["    # почему именно так", "    x = 1"])
    _, out, _ = fire("vpm-comment-intent.py", edit("core/engine/engine.py", new_string=added))
    ok(decision(out) == "deny", "добавлен комментарий — обоснование требуется ДО записи")
    _, out, _ = fire("vpm-comment-intent.py", edit("core/engine/engine.py", new_string="    x = 1\n"))
    ok(not out, "текста в код не добавлено — молчим")
    _, out, _ = fire("vpm-comment-intent.py", edit("core/engine/engine.py", new_string=added),
                     VPM_COMMENT_INTENT="off")
    ok(not out, "VPM_COMMENT_INTENT=off — рубильник жив")

    print("§7 шимы: правило живёт в репозитории, копии снаружи нет")
    for shim, guard in (("vpm-comment-guard.py", "comment_guard.py"),
                        ("vpm-invariants.py", "invariants.py")):
        text = (HOOKS / shim).read_text(encoding="utf-8")
        ok("/home/" not in text,
           f"{shim}: корень не зашит путём одной машины — иначе хук молчит у всех, кроме автора")
        ok(guard in text, f"{shim} ведёт в scripts/guards/{guard}, а не держит копию правила")
    done = subprocess.run([sys.executable, str(HOOKS / "vpm-comment-guard.py"), "--check"],
                          capture_output=True, text=True, timeout=120, cwd=tempfile.mkdtemp())
    ok("Храповик" in done.stdout,
       "запущенный из ЧУЖОГО каталога шим судит репозиторий: корень взят от файла, не от cwd")

    print("§7б не-ASCII в позиции ИМЕНИ оболочки ловится до запуска")
    факты: dict = {"__name__": "не-главный", "__file__": str(HOOKS / "vpm-fact-gate.py")}
    exec(compile((HOOKS / "vpm-fact-gate.py").read_text(encoding="utf-8"),
                 str(HOOKS / "vpm-fact-gate.py"), "exec"), факты)
    чужое, херед = факты["SHELL_NON_ASCII"], факты["HEREDOC"]
    встроенный = факты["INLINE_CODE"]

    def ловится(команда: str) -> bool:
        return bool(чужое.search(встроенный.sub("", херед.sub("", команда))))

    # Судится КЛАСС — чужая буква там, где bash ждёт имя, — поэтому проба перечисляет позиции и
    # алфавиты, а не команды: список примеров рушится на первом же отклонении от него.
    ok(all(ловится(к) for к in ('ls -d "$ДОМ"', 'ls -d "${ДОМ}"', 'ls "$ДІМ"', 'cd "$ΟΙΚΟΣ"')),
       "подстановка: обе формы записи и любой чужой алфавит, а не только кириллица")
    ok(all(ловится(к) for к in ("К=/tmp", "git status; К=1", "true && ДОМ=/tmp", "(ДОМ=1; echo)",
                                "export ДОМ=/tmp", "klon_дом=/tmp")),
       "присваивание: любое место команды и имя, где чужая буква хоть одна")
    ok(all(ловится(к) for к in ("unset ДОМ", "for дом in a b; do :; done")),
       "слово, забирающее имя, судится без знака «=» — иначе `unset` и `for` проходили бы")
    ok(not any(ловится(к) for к in ("grep -n 'остаток' f.py", "grep -rn остаток docs/",
                                    'git commit -m "ключ подставлен"', "echo дом=1",
                                    './скрипт.sh --зона "путь/каталог"',
                                    ".venv/bin/python - <<'PY'\nдом = 1\nPY")),
       "русский текст, русское имя файла и питон в heredoc законны: граница по ПОЗИЦИИ, не по алфавиту")
    # Тело снимается по КЛАССУ записи heredoc, а не по одной его форме: маркер бывает в конце
    # строки, перед `&&`, перед конвейером, с кавычками и без, с отступом `<<-`.
    ok(all(not ловится(f"cat {маркер}\nтело $ДОМ\nEOF") for маркер in
           ("<<'EOF'", "<<EOF", "<<-EOF", '<<"EOF" > f', "<<'EOF' && echo да", "<<'EOF' | tail -1")),
       "тело here-document снимается во всех формах записи маркера, а не только в одной")
    # Данные приезжают в команду ДВУМЯ способами — телом here-document и аргументом `-c`; оба
    # несут чужой язык, где кириллица законна. Поймано на ложном отказе собственной работе.
    ok(not any(ловится(к) for к in ('python3 -c "for имя in (1,2): print(имя)"',
                                    "python3 -c 'дом = 1'",
                                    '.venv/bin/python -c "хвост = _stamp.tails()"')),
       "аргумент `-c` — данные, а не оболочка: правило не отказывает на python-однострочнике")

    print("§8 заявление о зелёном отличается от пожелания")
    gate: dict = {"__name__": "не-главный", "__file__": str(HOOKS / "vpm-delivery-gate.py")}
    exec(compile((HOOKS / "vpm-delivery-gate.py").read_text(encoding="utf-8"),
                 str(HOOKS / "vpm-delivery-gate.py"), "exec"), gate)
    claim, modal = gate["CLAIM"], gate["MODAL"]
    said, wished = "гейт зелёный, можно коммитить", "убедись, что гейт зелёный"
    ok(bool(claim.search(said)) and not modal.search(said), "«гейт зелёный» — утверждение")
    ok(bool(claim.search(wished)) and bool(modal.search(wished)),
       "«убедись, что гейт зелёный» — те же слова, но модальность отменяет утверждение")
    ok(not claim.search("тесты я пока не гонял"), "отсутствие заявления заявлением не считается")

    своё = Path(tempfile.mkdtemp(prefix="vpm-подпись-набора-"))
    подпись = gate["sign_refusal"](
        "Гейт поставки: прогон задетых сценариев не уложился в срок", своё)
    ok(подпись.startswith("⟦vpm ") and "ОТКАЗ" in подпись.splitlines()[0],
       "отказ гейта несёт подпись с ролью — скопированный в другую сессию, он говорит, чем был")
    ok(not (ROOT / "tests" / ".journal" / "stamps-проба.jsonl").exists()
       and any(своё.rglob("stamps-*.jsonl")),
       "подпись набора легла в СВОЁ дерево: боевой журнал улик не засоряется прогонами")
    ok("род=гейт" in подпись and "запись=" in подпись,
       "подпись отказа даёт ключи для подъёма записи, а не только слова")

    print("§8а снесённый файл не заклинивает выбор прогона")
    # Карту собирают ПОСЛЕ правки, поэтому снесённого пути в ней не будет никогда, а покрывать
    # уже нечего: без отсечки гейт требовал пересборки, которая помочь не могла.
    _all_sc = list(gate["scenarios_on_disk"]())
    _radius = {"server.py": _all_sc}
    ok(gate["map_untrustworthy"](["core/снесённого-нет.py"], _radius) == "",
       "тронутый файл, которого нет на диске, картой не судится — покрывать нечего")
    ok("нет в карте" in gate["map_untrustworthy"](["core/paths.py"], _radius),
       "существующий файл вне карты по-прежнему запрещает выбирать прогон — отсечка не глушит своё")
    ok("не знает сценариев" in gate["map_untrustworthy"](["server.py"], {"server.py": []}),
       "карта без сегодняшних сценариев недостоверна раньше и независимо от разговора про файлы")

    print("§9 подсказка зоны: рост ВНУТРИ набора")
    check = "ok(1, 'новая проверка')\n"
    _, out, _ = fire("vpm-fact-gate.py", edit("tests/quick/test_invariants.py", new_string=check))
    ok(decision(out) == "deny", "проверки прибавляются к существующему набору — подсказка ДО записи")
    ok("ЗАПАС:" in reason(out),
       "подсказка ДОСТАВЛЯЕТ запас именно этого набора: без него на «в своей ли зоне» не ответить")
    _, out, _ = fire("vpm-fact-gate.py", edit("tests/quick/test_invariants.py", new_string="x = 1\n"))
    ok(not out, "правка без новых проверок — переименование или чистка, дверь не открывается")
    _, out, _ = fire("vpm-fact-gate.py",
                     edit("tests/quick/test_ещё_один.py", tool="Write", content=check))
    ok("РОЖДЕНИЕ" in reason(out),
       "у несуществующего набора дверь другая: рождение судится «не плодить», а не запасом зоны")
    gate2 = {"__name__": "не-главный", "__file__": str(HOOKS / "vpm-fact-gate.py")}
    exec(compile((HOOKS / "vpm-fact-gate.py").read_text(encoding="utf-8"),
                 str(HOOKS / "vpm-fact-gate.py"), "exec"), gate2)
    ok("ЗАПАС: новые хуки" in gate2["zone_row"]("tests/quick/test_hooks.py"),
       "запас берётся из СВОЕГО столбца каталога: столбцов пять, и «Зачем» стоит перед ним")
    ok("scenarios/" in gate2["zone_row"]("tests/scenarios/media_ops.yaml"),
       "зона объявлена КАТАЛОГУ, а не файлу — подсказка находит хозяина сценариев, а не врёт «зоны нет»")
    ok("НЕТ" in gate2["zone_row"]("tests/quick/test_безымянный.py"),
       "набора нет в каталоге зон — подсказка говорит это прямо, а не молчит про отсутствие строки")
    ok(gate2["suite_growth"]("tests/quick/test_hooks.py", check),
       "предикат роста узнаёт свой случай сам, не полагаясь на порядок дверей в verdict")
    ok(not gate2["suite_growth"]("tests/scenarios/ещё_не_рождённый.yaml", "- call: x\n"),
       "файла ещё нет — это рождение, а не рост: расширять нечего, и запаса у него не бывает")

    print("§9а дверь коммита")
    _, out, _ = fire("vpm-fact-gate.py", bash('git commit -m "правка" --no-verify'))
    ok(decision(out) == "deny", "коммит с флагом, снимающим проверки, — отказ")
    ok("Поставить" in reason(out), "отказ говорит, ЧЕМ его закрыть, а не только что нельзя")
    gate3: dict = {"__name__": "не-главный", "__file__": str(HOOKS / "vpm-fact-gate.py")}
    exec(compile((HOOKS / "vpm-fact-gate.py").read_text(encoding="utf-8"),
                 str(HOOKS / "vpm-fact-gate.py"), "exec"), gate3)
    door = gate3["commit_door"]
    стоит = Path(tempfile.mkdtemp(prefix="vpm-дверь-"))
    (стоит / ".git" / "hooks").mkdir(parents=True)
    (стоит / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    ok(door('git commit -m "правка"', root=стоит) == "",
       "дверь на месте — обычный коммит не трогаем")
    ok("Двери нет вовсе" in door('git commit -m "правка"', root=Path(tempfile.mkdtemp())),
       "дерева без двери коммит не покидает — на свежем клоне её нет, и это неотличимо от чистого")
    основной = Path(tempfile.mkdtemp(prefix="vpm-дверь-репо-"))
    среда = {"HOME": str(основной), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(["git", "init", "-q"], cwd=основной, env=среда, timeout=60, check=True)
    subprocess.run(["git", "-c", "user.name=н", "-c", "user.email=н@н", "commit", "-q",
                    "--allow-empty", "-m", "пусто"], cwd=основной, env=среда, timeout=60, check=True)
    (основной / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    отросток = основной / "ветка"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(отросток)],
                   cwd=основной, env=среда, timeout=60, check=True)
    ok(gate3["door_path"](отросток).exists(),
       "в worktree дверь ищется у git, а не по `.git/hooks` рядом — там `.git` файл, а хуки общие")
    ok(door("git log -n 5", root=ROOT) == "",
       "не коммит вовсе — соседняя команда с похожим флагом не обвиняется")

    print("§9в красный приносит с собой готовую регрессию")
    запись = Path(tempfile.mkdtemp(prefix="vpm-запись-")) / "trail-проба.jsonl"
    отказ = {"ts": 1.0, "scenario": "проба", "step": 1, "tool": "fs_read_file",
             "args": {"path": "../секрет"}, "ok": False, "code": "PATH_ESCAPE",
             "message": "выход за периметр", "level": "tool"}
    запись.write_text(json.dumps(отказ, ensure_ascii=False) + "\n", encoding="utf-8")
    сценарий = gate["produced_scenario"](запись, ROOT)
    ok(сценарий.startswith("- scenario:") and "fs_read_file" in сценарий,
       "отказ из записи превращается в готовый сценарий — регрессия пишется в момент отказа")
    зелёная = запись.with_name("trail-зелёная.jsonl")
    зелёная.write_text(json.dumps({**отказ, "ok": True, "code": ""}, ensure_ascii=False) + "\n",
                       encoding="utf-8")
    ok("нет воспроизводимого отказа" in gate["produced_scenario"](зелёная, ROOT),
       "в зелёной записи воспроизводить нечего — так и сказано, а не выдуман сценарий")

    print("§9б память под версиями судится тем же гейтом")
    склад = Path(tempfile.mkdtemp(prefix="vpm-склад-"))
    среда2 = {"HOME": str(склад), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(["git", "init", "-q"], cwd=склад, env=среда2, timeout=60, check=True)
    (склад / "MEMORY.md").write_text("- [Есть](есть.md)\n", encoding="utf-8")
    ok("MEMORY.md" in gate["memory_uncommitted"](склад),
       "правка памяти без коммита названа — иначе она живёт до первой чистки")
    subprocess.run(["git", "-c", "user.name=н", "-c", "user.email=н@н", "add", "-A"],
                   cwd=склад, env=среда2, timeout=60, check=True)
    subprocess.run(["git", "-c", "user.name=н", "-c", "user.email=н@н", "commit", "-q", "-m", "п"],
                   cwd=склад, env=среда2, timeout=60, check=True)
    ok(gate["memory_uncommitted"](склад) == "", "закоммиченная память тревоги не поднимает")
    ok(gate["memory_uncommitted"](Path(tempfile.mkdtemp())) == "",
       "каталог без git — улики нет, а не тревога: чужое отсутствие не наша находка")

    print("§9в гейт поставки: реестр правлен, а замер не подписан")
    дерево = Path(tempfile.mkdtemp(prefix="vpm-улика-"))
    subprocess.run(["git", "init", "-q", str(дерево)], check=True)
    (дерево / "docs" / "roadmap").mkdir(parents=True)
    (дерево / "docs" / "roadmap" / "02_findings.md").write_text("| F1 |\n", encoding="utf-8")
    (дерево / "tests" / ".journal").mkdir(parents=True)
    ok(gate["unsigned_measure"](дерево).strip().endswith("02_findings.md"),
       "реестр правлен, улик за день ноль — гейт называет ФАЙЛ, а не общую фразу")
    день = __import__("datetime").date.today().strftime("%Y%m%d")
    (дерево / "tests" / ".journal" / f"stamps-{день}.jsonl").write_text(
        json.dumps({"role": "УЛИКА", "what": "замер"}, ensure_ascii=False) + "\n", encoding="utf-8")
    ok(not gate["unsigned_measure"](дерево),
       "улика за день есть — упрёка нет")
    пусто = Path(tempfile.mkdtemp(prefix="vpm-без-журнала-"))
    subprocess.run(["git", "init", "-q", str(пусто)], check=True)
    ok(not gate["unsigned_measure"](пусто),
       "каталога журнала нет (свежий клон, CI) — улики нет, а не обвинение")

    print("§11 сторож намерений: остаток переживает сессию")
    намер: dict = {"__name__": "не-главный", "__file__": str(HOOKS / "vpm-intent-guard.py")}
    exec(compile((HOOKS / "vpm-intent-guard.py").read_text(encoding="utf-8"),
                 str(HOOKS / "vpm-intent-guard.py"), "exec"), намер)
    дерево = Path(tempfile.mkdtemp(prefix="vpm-намерение-"))
    (дерево / "tests" / ".journal").mkdir(parents=True)
    обещание = {"last_assistant_message": "Остальное доделаю завтра, вернёмся к этому."}
    ok("остаток не записан" in намер["судить_конец"](обещание, дерево),
       "сессия кончается обещанием, а подписи ОСТАТОК нет — отказ")
    ok(not намер["судить_конец"]({"last_assistant_message": "Всё закрыто, гейт зелёный."}, дерево),
       "без обещания упрёка нет — сторож не наказывает за законченную работу")
    сегодня = __import__("datetime").date.today().strftime("%Y%m%d")
    дом_хвостов(дерево).write_text(
        json.dumps({"role": "ОСТАТОК", "ts": __import__("time").time(), "key": "aaaa",
                    "what": "хвост", "cmd": "grep -n x y"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    ok(not намер["судить_конец"](обещание, дерево),
       "обещание записано подписью — упрёка нет: хвост переживёт сессию")
    ok("aaaa" in намер["показать_остатки"](дерево) and "продолжить" in намер["показать_остатки"](дерево),
       "начало сессии показывает хвост с ключом и командой продолжения")
    ok(намер["показать_остатки"](Path(tempfile.mkdtemp())) == "",
       "хвостов нет — молчим, а не печатаем пустую шапку")

    print("§11б остаток БЛОКИРУЕТ запись, пока не признан")
    хвост = Path(tempfile.mkdtemp(prefix="vpm-хвост-"))
    (хвост / "tests" / ".journal").mkdir(parents=True)
    день2 = __import__("datetime").date.today().strftime("%Y%m%d")
    жур = дом_хвостов(хвост)
    жур.write_text(json.dumps({"role": "ОСТАТОК", "ts": __import__("time").time(), "key": "bbbb",
                               "what": "хвост", "cmd": "grep -n x y"}, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    пишет = {"tool_name": "Write", "tool_input": {"file_path": "core/x.py"}}
    ok("не признан" in намер["судить_правку"](пишет, хвост),
       "запись при непризнанном остатке запрещена — вариант 2, а не показ после работы")
    отказ = намер["судить_правку"](пишет, хвост)
    ok("--closes bbbb" in отказ and "<ключ>" not in отказ,
       "ключ подставлен во ВСЕ формы признания: переписывать его руками больше не надо")
    ok(отказ.count("--closes bbbb") == 3,
       "подставлен трижды — выбор «закрыть · отложить · взять» остаётся за ИИ, а не сделан за него")
    двое = Path(tempfile.mkdtemp(prefix="vpm-двое-"))
    (двое / "tests" / ".journal").mkdir(parents=True)
    время = __import__("time").time()
    дом_хвостов(двое).write_text("\n".join(
        json.dumps({"role": "ОСТАТОК", "ts": t, "key": k, "what": k, "cmd": "grep -n x y"},
                   ensure_ascii=False) for k, t in (("млад", время), ("стар", время - 86400))) + "\n",
        encoding="utf-8")
    ok("--closes стар" in намер["судить_правку"](пишет, двое),
       "ключ берётся у СТАРШЕГО остатка, а не у последнего записанного")
    ok(not намер["судить_правку"]({"tool_name": "Read", "tool_input": {}}, хвост),
       "чтение проходит: чтобы признать хвост, на него надо посмотреть")
    ok(not намер["судить_правку"](
        {"tool_name": "Bash", "tool_input": {"command": "grep -n x y.py 2>&1"}}, хвост),
       "`2>&1` — перенаправление ПОТОКА, а не запись: поймано на собственной команде сторожа")
    ok(намер["судить_правку"](
        {"tool_name": "Bash", "tool_input": {"command": "echo x > core/y.py"}}, хвост),
       "перенаправление В ФАЙЛ дерева — запись, и она запрещена")
    with жур.open("a", encoding="utf-8") as дописать:
        дописать.write(json.dumps({"role": "ОТЛОЖЕН", "ts": __import__("time").time(),
                                   "key": "cccc", "closes": "bbbb"}, ensure_ascii=False) + "\n")
    ok(not намер["судить_правку"](пишет, хвост),
       "хвост отложен подписью — запрет снят: признание есть, и оно на диске")
    sys.path.insert(0, str(ROOT / "scripts" / "guards"))
    import _stamp as _s
    ok(len(_s.tails(хвост)) == 1 and _s.tails(хвост)[0]["отложен"],
       "но из ПОКАЗА отложенный не исчез — он не закрыт, исчез только запрет")
    with жур.open("a", encoding="utf-8") as дописать:
        дописать.write(json.dumps({"role": "ВЕРДИКТ", "ts": __import__("time").time(),
                                   "key": "dddd", "closes": "bbbb"}, ensure_ascii=False) + "\n")
    ok(not _s.tails(хвост), "закрыт вердиктом — ушёл и из показа тоже")

    взят = Path(tempfile.mkdtemp(prefix="vpm-вработе-"))
    (взят / "tests" / ".journal").mkdir(parents=True)
    жур2 = дом_хвостов(взят)
    жур2.write_text(json.dumps({"role": "ОСТАТОК", "ts": __import__("time").time(), "key": "eeee",
                                "what": "хвост, который берут", "cmd": "grep -n x y"},
                               ensure_ascii=False) + "\n", encoding="utf-8")
    with жур2.open("a", encoding="utf-8") as дописать:
        дописать.write(json.dumps({"role": "В РАБОТЕ", "ts": __import__("time").time(),
                                   "key": "ffff", "closes": "eeee"}, ensure_ascii=False) + "\n")
    ok(not намер["судить_правку"](пишет, взят) and len(_s.tails(взят)) == 1
       and _s.tails(взят)[0]["в работе"],
       "«в работе» снимает запрет, а хвост остаётся в показе")
    ok(not _s.tails(взят)[0]["отложен"],
       "«в работе» не закрывает хвост — закрытие только вердиктом")

    print("§12 сторож дисциплины: механизм, который пылится, назван")
    дисц: dict = {"__name__": "не-главный", "__file__": str(HOOKS / "vpm-discipline-guard.py")}
    exec(compile((HOOKS / "vpm-discipline-guard.py").read_text(encoding="utf-8"),
                 str(HOOKS / "vpm-discipline-guard.py"), "exec"), дисц)
    старый = Path(tempfile.mkdtemp(prefix="vpm-след-")) / "vpm-trace"
    старый.mkdir()
    давно = time.time() - 30 * 86400
    (старый / "_рождение.json").write_text(json.dumps({"ts": давно}), encoding="utf-8")
    (старый / "vpm-fact-gate.py.json").write_text(
        json.dumps({"last": давно, "count": 1}), encoding="utf-8")
    имена = дисц["объявленные"]()
    ok(имена and "vpm-intent-guard.py" in имена and not any(n.startswith("_") for n in имена),
       "объявленные читаются из settings.json, помощники в счёт не идут")
    пыль = дисц["пылящиеся"](ROOT, 7.0, старый)
    ok(any("vpm-fact-gate.py" in s and "30 дней" in s for s in пыль),
       "сторож, молчащий 30 дней, назван с возрастом")
    ok(any("ни разу" in s for s in пыль),
       "сторож, не срабатывавший НИ РАЗУ, назван отдельно — это не то же, что «давно»")
    свежий = Path(tempfile.mkdtemp(prefix="vpm-след-новый-")) / "vpm-trace"
    свежий.mkdir()
    (свежий / "_рождение.json").write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    ok(not дисц["пылящиеся"](ROOT, 7.0, свежий),
       "след моложе срока — улики нет, а не обвинение всем сразу")
    ok(not дисц["пылящиеся"](ROOT, 7.0, Path("/нет/такого/следа")),
       "следа нет вовсе (чужая машина, CI) — молчим")

    print("§12а след переживает одновременную запись: улику о срабатывании нельзя терять")
    трасса: dict = {"__name__": "не-главный"}
    exec(compile((HOOKS / "_trace.py").read_text(encoding="utf-8"),
                 str(HOOKS / "_trace.py"), "exec"), трасса)
    гнездо = Path(tempfile.mkdtemp(prefix="vpm-след-гонка-")) / "vpm-trace"
    имена = [f"хук-{i}.py" for i in range(6)]
    with ThreadPoolExecutor(max_workers=12) as пул:
        list(пул.map(lambda имя: [трасса["mark"](имя, гнездо) for _ in range(40)], имена))
    счёт = {и: (трасса["след"](гнездо).get(и) or {}).get("count") for и in имена}
    ok(all(c == 40 for c in счёт.values()),
       "шесть хуков по 40 отметок разом — ни одна не потеряна")
    ok(трасса["рождение"](гнездо), "дата рождения следа пережила гонку — иначе отсрочка встаёт заново")
    (гнездо / "хук-0.py.json").write_bytes(b"{\xd1")
    try:                                       # мусор не вправе ронять читателя — иначе слепнет всё
        живые = [и for и, з in трасса["след"](гнездо).items() if з]
    except Exception as беда:                  # noqa: BLE001 — предмет проверки и есть «что угодно»
        живые = [f"чтение упало: {беда!r}"]
    ok(живые == sorted(имена[1:]) and трасса["рождение"](гнездо),
       "рваная запись уносит только свой файл, а не весь след с рождением")
    ok(трасса["seen"]("нет-такого.py", гнездо) == 0,
       "не срабатывавший сторож — ноль, а не отказ чтения")

    # Один и тот же хук ДВАЖДЫ разом — не выдумка: параллельные вызовы инструментов будят его
    # столько раз, сколько команд. Здесь проверяется не счёт (последний писатель законно
    # затирает), а два свойства: отметка не теряется молча и читатель не видит рванья.
    один = Path(tempfile.mkdtemp(prefix="vpm-след-один-")) / "vpm-trace"
    рвань, стоп = [], []
    def читатель():
        # Существование спрашивается ДО чтения: спросив после, ловишь собственный первый заход,
        # случившийся раньше создания файла, — проверка мигала именно на этом.
        while not стоп:
            файлик = один / "хук.py.json"
            было = файлик.exists()
            if было and not трасса["_прочесть"](файлик):
                рвань.append(1)
    with ThreadPoolExecutor(max_workers=9) as пул:
        глаз = пул.submit(читатель)
        отметки = list(пул.map(lambda _: [трасса["mark"]("хук.py", один) for _ in range(40)],
                               range(8)))
        стоп.append(1)
        глаз.result()
    ok(all(all(партия) for партия in отметки),
       "восемь писателей одного файла — ни одной молча потерянной отметки")
    ok(not рвань, "читатель ни разу не увидел рваную запись: подмена файла атомарна")

    print("§10 форма того, что правка оставила в дереве")
    inv: dict = {"__name__": "не-главный", "__file__": str(HOOKS / "vpm-invariants.py")}
    exec(compile((HOOKS / "vpm-invariants.py").read_text(encoding="utf-8"),
                 str(HOOKS / "vpm-invariants.py"), "exec"), inv)
    tree = Path(tempfile.mkdtemp(prefix="vpm-форма-"))
    git = {"HOME": str(tree), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(["git", "init", "-q"], cwd=tree, env=git, timeout=60, check=True)
    subprocess.run(["git", "-c", "user.name=н", "-c", "user.email=н@н", "commit", "-q",
                    "--allow-empty", "-m", "пусто"], cwd=tree, env=git, timeout=60, check=True)
    (tree / "правка_скриптом.py").write_text("import yaml\n\n\ndef f():\n    return Declaration(1)\n",
                                             encoding="utf-8")
    (tree / "сломан.py").write_text("def f(:\n    pass\n", encoding="utf-8")
    (tree / "здоровый.py").write_text("import json\n\n\ndef f():\n    return json.dumps({})\n",
                                      encoding="utf-8")
    (tree / "заметка.md").write_text("правка документа формой не судится\n", encoding="utf-8")
    dirty = inv["dirty_python"](tree)
    ok({p.name for p in dirty} == {"правка_скриптом.py", "сломан.py", "здоровый.py"},
       f"правка скриптом видна — файлы берутся из ДЕРЕВА, а не из события  → {sorted(p.name for p in dirty)}")
    ruff = inv["linter"](ROOT)
    notes = inv["form_complaints"](dirty, root=tree, ruff=ruff)
    ok(any("не компилируется" in n for n in notes),
       "сломанный синтаксис — жалоба сразу, а не на следующем чужом прогоне")
    ok(any("F821" in n for n in notes) and any("F401" in n for n in notes),
       "имя без импорта и незакрытый импорт названы кодом линтера — это и есть немой промах")
    ok(inv["form_complaints"]([tree / "здоровый.py"], root=tree, ruff=ruff) == [],
       "здоровый файл — молчание, ведь сторожа, который кричит на чистом, выключают целиком")
    silent = inv["form_complaints"]([tree / "здоровый.py"], root=tree, ruff=None)
    ok(len(silent) == 1 and "не судились" in silent[0],
       "линтера нет — сторож говорит «не судил», а не выдаёт непроверенное за чистое")
    ok(inv["event_python"]({"tool_input": {"file_path": str(ROOT / "docs/roadmap/02_findings.md")}}) == [],
       "документ формой не судится — у прозы нет ни компиляции, ни линтера")

    print("§13 читатель правила «цикл до кода»: коммит в зону цикла требует вердикта")
    цикл_дер = Path(tempfile.mkdtemp(prefix="vpm-цикл-"))
    окр = {"HOME": str(цикл_дер), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(["git", "init", "-q"], cwd=цикл_дер, env=окр, timeout=60, check=True)
    (цикл_дер / "tests" / ".journal").mkdir(parents=True)
    (цикл_дер / "core").mkdir()
    (цикл_дер / "core" / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "core/x.py"], cwd=цикл_дер, env=окр, timeout=60, check=True)
    коммит = {"tool_name": "Bash", "tool_input": {"command": "git commit -m правка"}}
    ok("зону цикла" in намер["судить_цикл"](коммит, цикл_дер),
       "коммит в зону цикла без свежего вердикта запрещён")

    проза = Path(tempfile.mkdtemp(prefix="vpm-проза-"))
    окр2 = {"HOME": str(проза), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(["git", "init", "-q"], cwd=проза, env=окр2, timeout=60, check=True)
    (проза / "tests" / ".journal").mkdir(parents=True)
    (проза / "заметка.md").write_text("проза\n", encoding="utf-8")
    subprocess.run(["git", "add", "заметка.md"], cwd=проза, env=окр2, timeout=60, check=True)
    ok(not намер["судить_цикл"](коммит, проза),
       "коммит вне зоны цикла проходит — сравнивать там нечего")

    правка_мс = (цикл_дер / "core" / "x.py").stat().st_mtime
    жур3 = цикл_дер / "tests" / ".journal" / "stamps-20260101.jsonl"
    жур3.write_text(json.dumps({"kind": "цикл", "role": "ВЕРДИКТ", "ts": правка_мс - 100,
                                "key": "aaaa", "what": "старый"}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    ok("зону цикла" in намер["судить_цикл"](коммит, цикл_дер),
       "вердикт старее правки запрет не снимает")
    with жур3.open("a", encoding="utf-8") as дописать:
        дописать.write(json.dumps({"kind": "цикл", "role": "ВЕРДИКТ", "ts": правка_мс + 10,
                                   "key": "bbbb", "what": "свежий"}, ensure_ascii=False) + "\n")
    ok(not намер["судить_цикл"](коммит, цикл_дер),
       "вердикт цикла свежее правки снимает запрет")

    машинерия = Path(tempfile.mkdtemp(prefix="vpm-машин-"))
    окр3 = {"HOME": str(машинерия), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(["git", "init", "-q"], cwd=машинерия, env=окр3, timeout=60, check=True)
    (машинерия / "tests" / ".journal").mkdir(parents=True)
    (машинерия / ".claude" / "hooks").mkdir(parents=True)
    (машинерия / ".claude" / "hooks" / "vpm-проба.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=машинерия, env=окр3, timeout=60, check=True)
    ok("зону цикла" in намер["судить_цикл"](коммит, машинерия),
       "правка хука тоже в зоне цикла — машинерию не судит ни один сценарий")

    жур3.unlink()
    в_теле = {"tool_name": "Bash",
              "tool_input": {"command": "cat <<'EOF' > сборка.sh\ngit commit -m x\nEOF"}}
    ok(not намер["судить_цикл"](в_теле, цикл_дер),
       "слова коммита в теле heredoc запретом не считаются")
    в_описании = {"tool_name": "Bash", "tool_input": {"command":
                  ".venv/bin/python scripts/guards/_stamp.py --what 'ловит git commit в тексте'"}}
    ok(not намер["судить_цикл"](в_описании, цикл_дер),
       "слова коммита внутри аргумента запретом не считаются")

    print("§11 тандем ЗНАЕТ карту потоков и больные места приёмки")
    карта = tree / "routes.yaml"
    карта.write_text("""- route: reaction_to_client
  what: код отказа и рецепт
  means: клиент не знает, чинить самому или звать человека
  hops:
    - at: core/reactions/reactions.py
    - at: server.py
  proof:
    scenario: tables_destructive.yaml#ed1
""", encoding="utf-8")
    поток = inv["flows"](["core/reactions/reactions.py"], routes=карта)
    ok(len(поток) == 1 and "reaction_to_client" in поток[0] and "tables_destructive" in поток[0],
       "правка рубежа: назван поток, что через него течёт и каким прогоном это видно")
    ok(not inv["flows"](["core/поиск/чужое.py"], routes=карта),
       "файл не рубеж — тандем молчит, а не пересказывает всю карту")
    ok(not inv["flows"](["core/reactions/reactions.py"], routes=tree / "нет-карты.yaml"),
       "карты нет вовсе (чужой репозиторий) — улики нет, и это не «потоков ноль»")

    объявление = tree / "сценарии.yaml"
    объявление.write_text("""роды:
  правка-сервера:
    про: тронуто дерево сервера
    когда: ['*.py']
сценарии:
- имя: файл-без-объявленной-зоны
  улики: [правка-сервера]
  состояние: судится
  больно: b
  ломается: l
  доказать: cmd
  журнал: docs/roadmap/21_acceptance_plan.md
""", encoding="utf-8")
    совет = inv["acceptance"](["core/движок/узел.py"], scenarios=объявление)
    ok(len(совет) == 1 and "файл-без-объявленной-зоны" in совет[0] and "--совет" in совет[0],
       "по роду улики названо больное место и команда, которой поднять доказательство")
    ok(not inv["acceptance"](["README.md"], scenarios=объявление),
       "род улики не выведен — тандем молчит, а не советует наугад")
    ok(not inv["acceptance"](["core/движок/узел.py"], scenarios=tree / "нет-сценариев.yaml"),
       "объявления сценариев нет — улики нет, и это не «сценариев ноль»")

    print("§14 новорождённый сторож: отсрочка ТОЛЬКО в хуке")
    свои = inv["newborn"]
    молодые, взрослые = свои(["scripts/guards/новичок.py — сторожа не судит ни один набор",
                              "core/reactions/reactions.py:12 — копия объявления в коде"], ROOT)
    ok(len(молодые) == 1 and "новичок.py" in молодые[0] and len(взрослые) == 1,
       "находка про файл, которого git не знает, отделена от находки про файл под git")
    ok(свои(["core/reactions/reactions.py:12 — копия объявления"], ROOT)[0] == [],
       "файл под git новорождённым не считается — отсрочки ему нет")
    ok(свои(["общая жалоба без единого имени файла"], ROOT)[1],
       "строка без имени файла остаётся отказом: молчать о непонятном нельзя")
    # Каталог ВНЕ любого репозитория: `tree` набора сам под git, и git ответил бы за него.
    без_git = Path(tempfile.mkdtemp(prefix="vpm-нет-git-"))
    ok(свои(["scripts/guards/новичок.py — нет дома"], без_git)[1],
       "git не ответил (чужое дерево) — отказ остаётся: улики о возрасте нет")


    print("§7а роспись сторожей приезжает в контекст и читается С ДИСКА")
    вывод = subprocess.run(["bash", str(HOOKS / "vpm-skill-bundle.sh")], capture_output=True,
                           text=True, cwd=str(ROOT), timeout=60)
    контекст = (json.loads(вывод.stdout)["hookSpecificOutput"]["additionalContext"]
                if вывод.stdout.strip() else "")
    сторожа = sorted(p.name for p in (ROOT / "scripts" / "guards").glob("*.py")
                     if not p.name.startswith("_"))
    пропущены = [и for и in сторожа if f"`{и}`" not in контекст]
    # Ярлык — имя, а не замер: подставленное число делает его нестабильным, и цикл не узнаёт
    # проверку в объявлении. Промолчавшие печатаются строкой ниже, когда они есть.
    if пропущены:
        print(f"    не названы: {пропущены}")
    ok(not пропущены,
       "каждый сторож с диска назван в росписи старта — иначе завтра его не найдут")
    ok("`quality_advisor.py`" not in контекст,
       "переименованного сторожа роспись назвать не может: она читается с диска, а не из памяти")
    ok("--приёмка" in контекст and "--задача" in контекст,
       "названы две двери: кого звать по задаче и что уже судится")

    print("§15 дверь человека: запрет сторожа остатков снимает не ИИ")
    просьба = ('.venv/bin/python scripts/guards/_permit.py --запрос VPM_INTENT_GUARD '
               '--почему "правлю сам механизм остатков"')
    _, out, _ = fire("vpm-permit.py", bash(просьба))
    ok(decision(out) == "ask" and "решение человека" in reason(out),
       "просьба снять запрет выносится человеку вопросом да/нет, а не исполняется молча")
    _, out, _ = fire("vpm-permit.py", bash("VPM_INTENT" + "_GUARD=off .venv/bin/pytest -q"))
    ok(decision(out) == "deny", "снятие своей рукой отклонено и показывает дверь")
    _, out, _ = fire("vpm-permit.py", bash("echo 'VPM_INTENT" + "_GUARD=off' >> заметка.md"))
    ok(not out, "рассказ о выключателе в кавычках — данные, а не команда (поймано на себе же)")
    _, out, _ = fire("vpm-permit.py", bash("ls -la"))
    ok(not out, "чужая команда двери не касается")

    дом = tempfile.mkdtemp(prefix="vpm-permit-home-")
    прежний, os.environ["HOME"] = os.environ.get("HOME", ""), дом
    sys.path.insert(0, str(ROOT / "scripts" / "guards"))
    import _permit
    корень = Path(tempfile.mkdtemp(prefix="vpm-permit-root-"))
    ok(not _permit.снят("VPM_INTENT_GUARD", корень), "без разрешения запрет стоит")
    _permit.выдать("VPM_INTENT_GUARD", "правлю сам механизм остатков", корень)
    ok(_permit.снят("VPM_INTENT_GUARD", корень), "после «да» человека запрет снят")
    ok(not _permit.снят("VPM_INTENT_GUARD", Path(tempfile.mkdtemp())),
       "разрешение без своей подписи в журнале не действует: подделка файла не проходит")
    запись = json.loads(_permit.файл("VPM_INTENT_GUARD").read_text(encoding="utf-8"))
    _permit.файл("VPM_INTENT_GUARD").write_text(
        json.dumps({**запись, "истекает": time.time() - 1}), encoding="utf-8")
    ok(not _permit.снят("VPM_INTENT_GUARD", корень), "разрешение истекает само: «снял и ушёл» не вечно")

    # Замер на дереве, где сторож ОБЯЗАН отказать: в боевом все хвосты признаны, и там прибор
    # молчит независимо от переменной — ноль отказов означал бы «не мерил», а не «выключен».
    хвостатое = Path(tempfile.mkdtemp(prefix="vpm-хвост-"))
    (хвостатое / "tests" / ".journal").mkdir(parents=True)
    (хвостатое / "scripts" / "guards").mkdir(parents=True)
    for имя in ("_stamp.py", "_permit.py"):
        shutil.copy(ROOT / "scripts" / "guards" / имя, хвостатое / "scripts" / "guards" / имя)
    дом_хвостов(хвостатое).write_text(
        json.dumps({"ts": time.time(), "key": "aaaa", "kind": "проба", "role": "ОСТАТОК",
                    "what": "хвост для замера", "expected": "закроется", "cmd": "ls",
                    "closes": "", "detail": {}}, ensure_ascii=False) + "\n", encoding="utf-8")
    правка = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": "проба",
              "tool_input": {"file_path": str(хвостатое / "core" / "engine.py")}}
    _, слепой, _ = fire("vpm-intent-guard.py", правка, home=tempfile.mkdtemp(),
                        CLAUDE_PROJECT_DIR=str(хвостатое))
    ok(decision(слепой) == "deny", "непризнанный остаток запрещает запись — прибор мерит")
    _, с_рубильником, _ = fire("vpm-intent-guard.py", правка, home=tempfile.mkdtemp(),
                               CLAUDE_PROJECT_DIR=str(хвостатое), **{"VPM_INTENT" + "_GUARD": "off"})
    ok(decision(с_рубильником) == "deny",
       "переменная среды сторожа остатков больше не выключает: рубильник мёртв")
    _permit.выдать("VPM_INTENT_GUARD", "правлю сам механизм остатков", хвостатое)
    _, с_разрешением, _ = fire("vpm-intent-guard.py", правка, home=дом,
                               CLAUDE_PROJECT_DIR=str(хвостатое))
    ok(not с_разрешением, "после разрешения человека сторож пропускает правку")
    os.environ["HOME"] = прежний


    print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
    for fail in _fails:
        print(f"  ✗ {fail}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
