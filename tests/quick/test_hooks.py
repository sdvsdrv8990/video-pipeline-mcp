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
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".claude" / "hooks"

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

    print("§5 рубильники сторожей живы")
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
    (дерево / "tests" / ".journal" / f"stamps-{сегодня}.jsonl").write_text(
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
    жур = хвост / "tests" / ".journal" / f"stamps-{день2}.jsonl"
    жур.write_text(json.dumps({"role": "ОСТАТОК", "ts": __import__("time").time(), "key": "bbbb",
                               "what": "хвост", "cmd": "grep -n x y"}, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    пишет = {"tool_name": "Write", "tool_input": {"file_path": "core/x.py"}}
    ok("не признан" in намер["судить_правку"](пишет, хвост),
       "запись при непризнанном остатке запрещена — вариант 2, а не показ после работы")
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
    жур2 = взят / "tests" / ".journal" / f"stamps-{день2}.jsonl"
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
    старый = Path(tempfile.mkdtemp(prefix="vpm-след-")) / "trace.json"
    давно = __import__("time").time() - 30 * 86400
    старый.write_text(json.dumps({"_рождение": давно,
                                  "vpm-fact-gate.py": {"last": давно, "count": 1}},
                                 ensure_ascii=False), encoding="utf-8")
    имена = дисц["объявленные"]()
    ok(имена and "vpm-intent-guard.py" in имена and not any(n.startswith("_") for n in имена),
       "объявленные читаются из settings.json, помощники в счёт не идут")
    пыль = дисц["пылящиеся"](ROOT, 7.0, старый)
    ok(any("vpm-fact-gate.py" in s and "30 дней" in s for s in пыль),
       "сторож, молчащий 30 дней, назван с возрастом")
    ok(any("ни разу" in s for s in пыль),
       "сторож, не срабатывавший НИ РАЗУ, назван отдельно — это не то же, что «давно»")
    свежий = Path(tempfile.mkdtemp(prefix="vpm-след-новый-")) / "trace.json"
    свежий.write_text(json.dumps({"_рождение": __import__("time").time()}), encoding="utf-8")
    ok(not дисц["пылящиеся"](ROOT, 7.0, свежий),
       "след моложе срока — улики нет, а не обвинение всем сразу")
    ok(not дисц["пылящиеся"](ROOT, 7.0, Path("/нет/такого/следа.json")),
       "следа нет вовсе (чужая машина, CI) — молчим")

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

    print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
    for fail in _fails:
        print(f"  ✗ {fail}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
