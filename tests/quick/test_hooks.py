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

    подпись = gate["sign_refusal"]("Гейт поставки: прогон задетых сценариев не уложился в срок")
    ok(подпись.startswith("⟦vpm ") and "ОТКАЗ" in подпись.splitlines()[0],
       "отказ гейта несёт подпись с ролью — скопированный в другую сессию, он говорит, чем был")
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

    print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
    for fail in _fails:
        print(f"  ✗ {fail}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
