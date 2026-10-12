#!/usr/bin/env python3
"""tests/scenarios/test_scenarios.py — прогон объявленных сценариев против живого сервера.

Зовут: vpm-delivery-gate и reproduce.

Один файл сценариев = один сервер: область общая внутри файла (как у клиента), между файлами —
своя, и частота вызовов не копится через весь набор до бана по IP.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml

from tests.harness import live_server
from tests.harness.scenario import (FIXTURES, Journal, Runner, ScenarioError, Step, Vocabulary, _declarations, _fixtures,
                                    _network,
                                    _steps,
                                    род_сценария,
                                    load, scenario_files, server_env)
from tests.harness.scenario_map import MapRunner, analyse, load_map, plan
from tests.scenarios.steps import STEPS

HERE = Path(__file__).parent
JOURNAL_DIR = Path(__file__).resolve().parents[1] / ".journal"


def _language() -> list[str]:
    """Правила ЯЗЫКА объявления проверяются до сервера: кривое объявление обязано падать разбором.

    Шаг `run` исполняет команду репозитория, и его ожидание — код возврата, а не код реакции:
    сторож `ErrorDetail` не отдаёт, и реестровый код у него означал бы враньё в словаре.
    """
    vocab, bad = Vocabulary(), []

    def rejects(item: dict, because: str) -> None:
        try:
            _steps([item], vocab, "проба")
        except ScenarioError:
            return
        except Exception as exc:
            bad.append(f"проба уронила разбор ({because}): {type(exc).__name__}: {exc}")
            return
        bad.append(f"объявление принято, а не должно: {because}")

    def accepts(item: dict, because: str) -> None:
        try:
            _steps([item], vocab, "проба")
        except ScenarioError as exc:
            bad.append(f"объявление отвергнуто, а должно приниматься ({because}): {exc}")
        except Exception as exc:
            bad.append(f"проба уронила разбор ({because}): {type(exc).__name__}: {exc}")

    rejects({"run": "scripts/guards/invariants.py", "call": "fs_read_file", "expect": {"ok": True}},
            "и команда, и инструмент в одном шаге")
    rejects({"call": "fs_read_file", "expect": {"ok": False, "code": "FILE_NOT_FOUND", "exit": 1}},
            "`exit` у шага, который команду не запускает")
    rejects({"run": "scripts/guards/invariants.py", "expect": {"ok": False, "code": "FILE_NOT_FOUND"}},
            "реестровый код отказа у команды")
    rejects({"run": "scripts/guards/invariants.py", "expect": {"ok": True, "exit": 1}},
            "`exit: 1` при `ok: true` — нулевой код возврата и есть успех")
    rejects({"run": "scripts/guards/invariants.py", "expect": {"ok": False}},
            "отказ команды без кода возврата — «просто упало» не ожидание")
    accepts({"run": "scripts/guards/invariants.py", "with": {"args": ["--check"]},
             "expect": {"ok": True, "exit": 0}}, "полное объявление команды")

    def род_отвергнут(item: dict, because: str) -> None:
        try:
            род_сценария(item, "проба")
        except ScenarioError:
            return
        bad.append(f"род принят, а не должен: {because}")

    отказный = {"when": [{"call": "t", "expect": {"ok": False}}]}
    удачный = {"when": [{"call": "t", "expect": {"ok": True}}]}
    род_отвергнут({**отказный, "род": "положительный"},
                  "род положительный при шаге, ждущем отказа")
    род_отвергнут({**удачный, "род": "отрицательный"},
                  "род отрицательный, а отказа не ждёт ни один шаг")
    род_отвергнут({**удачный, "род": "деструктивный"}, "род не из словаря")
    for item, ждём, why in ((удачный, "положительный", "выведен из ожиданий: все шаги удачны"),
                            (отказный, "отрицательный", "выведен из ожиданий: шаг ждёт отказа"),
                            ({**отказный, "род": "разрушительный"}, "разрушительный",
                             "объявленное разрушение не подменяется выводом"),
                            ({**удачный, "род": "разрушительный"}, "разрушительный",
                             "разрушение не обязано ждать отказа: снос бывает и успешным")):
        вышло = род_сценария(item, "проба")
        if вышло != ждём:
            bad.append(f"род определён как `{вышло}`, ждали `{ждём}` ({why})")

    runner = Runner(None, None, {})
    for target, why in (("../секрет.txt", "цель вне репозитория"),
                        ("scripts/guards/нет-такого.py", "цели не существует")):
        try:
            runner._command(Step(index=1, run=target), {})
            bad.append(f"команда исполнена, а не должна: {why}")
        except ScenarioError:
            pass

    # Путь порчи судится по боевой декларации: ключ советов сам несёт точку, шаг-число — индекс списка.
    for spoils, accepted, why in (
            ({"recommendations.yaml": {"media_provider_status.choice.0.tool": 5}}, True, "ключ с точкой и индекс списка"),
            ({"recommendations.yaml": {"media_provider_status.choice.999.tool": 5}}, False, "индекса нет в списке"),
            ({"firewall.yaml": {"file_limits.нет_такой_ручки": 1}}, False, "ключа нет в разделе")):
        try:
            _declarations(spoils, "проба")
            if not accepted:
                bad.append(f"порча принята, а не должна: {why}")
        except ScenarioError as exc:
            if accepted:
                bad.append(f"порча отвергнута, а должна приниматься ({why}): {exc}")
        except Exception as exc:
            bad.append(f"порча уронила разбор ({why}): {type(exc).__name__}: {exc}")

    for raw, accepted, why in ((["no_crypto"], True, "приспособление из каталога"),
                               (["нет_такого"], False, "приспособления нет в каталоге"),
                               (["sitecustomize"], False, "точка входа — не приспособление"),
                               ("no_crypto", False, "строка вместо списка")):
        try:
            _fixtures(raw, "проба")
            if not accepted:
                bad.append(f"приспособление принято, а не должно: {why}")
        except ScenarioError as exc:
            if accepted:
                bad.append(f"приспособление отвергнуто, а должно приниматься ({why}): {exc}")

    route = {"host": "127.0.0.1", "method": "POST", "path": "/run", "replies": [{"status": 500}]}
    for net, fixtures, accepted, why in (({"routes": [route]}, ["net_double"], True, "сеть с двойником"),
                                         ({"routes": [route]}, [], False, "сеть без приспособления net_double"),
                                         ({"routes": [{**route, "replies": [{"status": 700}]}]}, ["net_double"], False,
                                          "ответ вне 100…599")):
        try:
            _network(net, fixtures, "проба")
            if not accepted:
                bad.append(f"сеть принята, а не должна: {why}")
        except ScenarioError as exc:
            if accepted:
                bad.append(f"сеть отвергнута, а должна приниматься ({why}): {exc}")

    refused = {"ok": False, "code": "PROVIDER_FAILED"}
    accepts({"call": "media_generate", "expect": {**refused, "net_tasks": 1}}, "счёт задач у вызова сервера")
    rejects({"run": "scripts/guards/invariants.py", "expect": {"ok": True, "exit": 0, "net_tasks": 0}},
            "счёт задач у команды — задачи создаёт сервер")
    rejects({"call": "media_generate", "expect": {**refused, "net_tasks": -1}}, "счёт задач меньше нуля")
    rejects({"call": "media_generate", "expect": {**refused, "net_tasks": True}}, "счёт задач логическим значением")
    rejects({"call": "media_generate", "repeat": 3, "expect": {**refused, "net_tasks": 1}},
            "счёт задач у ряда повторов сверялся бы с последним ответом")
    accepts({"call": "media_generate", "expect": {**refused, "detail": {"facts.host": "127.0.0.1"}}},
            "форма отказа у отказа вызова")
    rejects({"call": "media_generate", "expect": {"ok": True, "detail": {"code": "X"}}}, "форма отказа у успеха")
    rejects({"run": "scripts/guards/invariants.py", "expect": {"ok": False, "exit": 1, "detail": {"code": "X"}}},
            "форма отказа у команды — `ErrorDetail` она не отдаёт")
    rejects({"call": "media_generate", "expect": {**refused, "detail": {}}}, "пустая форма отказа — проверять нечего")
    rejects({"call": "media_generate", "expect": {**refused, "detail": ["facts.host"]}}, "форма отказа списком путей")
    accepts({"call": "media_generate", "expect": {**refused, "canary": "канарейка-проба"}}, "канарейка у вызова сервера")
    rejects({"run": "scripts/guards/invariants.py", "expect": {"ok": True, "exit": 0, "canary": "канарейка-проба"}},
            "канарейка у команды — ни ответа сервера, ни его журналов")
    rejects({"call": "media_generate", "expect": {**refused, "canary": " "}}, "пустая канарейка — есть в любом файле")
    rejects({"call": "media_generate", "expect": {**refused, "canary": 12345678}}, "канарейка числом")
    lost = {"in": "task", "code": "TASK_INTERRUPTED", "facts": {"tool": "сценарий"}}
    accepts({"call": "task_status", "expect": {"ok": True, "task_error": lost}}, "отказ задания у успешного вызова")
    accepts({"call": "task_status", "expect": {"ok": True, "task_error": {"in": "element", "code": "TASK_RECORD_INVALID",
                                                                          "class": "integrity"}}}, "отказ элемента с классом")
    rejects({"call": "task_status", "expect": {"ok": False, "code": "VALIDATION_ERROR", "task_error": lost}},
            "отказ задания у отказавшего вызова — элементов ответа нет")
    rejects({"run": "scripts/guards/invariants.py", "expect": {"ok": True, "exit": 0, "task_error": lost}},
            "отказ задания у команды")
    rejects({"call": "task_status", "expect": {"ok": True, "task_error": {"code": "TASK_INTERRUPTED"}}},
            "отказ задания без места — задание и элемент — разные исходы")
    rejects({"call": "task_status", "expect": {"ok": True, "task_error": {**lost, "in": "где-то"}}}, "место не из словаря")
    rejects({"call": "task_status", "expect": {"ok": True, "task_error": {**lost, "code": "НЕТ_ТАКОГО_КОДА"}}},
            "код вне реестра")
    rejects({"call": "task_status", "expect": {"ok": True, "task_error": {**lost, "status": "failed"}}}, "неизвестный ключ")
    rejects({"call": "task_status", "expect": {"ok": True, "task_error": {**lost, "facts": ["tool"]}}}, "факты списком")
    if род_сценария({"when": [{"call": "task_status", "expect": {"ok": True, "task_error": lost}}]}, "проба") != "отрицательный":
        bad.append("род сценария с отказом задания в элементе выведен не отрицательным")
    step = {"call": "media_generate", "expect": {**refused, "net_tasks": 1}}
    for given, accepted, why in (({"fixtures": ["net_double"], "network": {"routes": [route]}}, True, "счёт задач с двойником"),
                                 ({}, False, "счёт задач без двойника сети — ноль был бы пустым входом")):
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "проба.yaml"
            probe.write_text(yaml.safe_dump([{"scenario": "проба", "why": why, "given": given, "when": [step]}],
                                            allow_unicode=True), encoding="utf-8")
            try:
                load(probe, vocab)
                if not accepted:
                    bad.append(f"сценарий принят, а не должен: {why}")
            except ScenarioError as exc:
                if accepted:
                    bad.append(f"сценарий отвергнут, а должен приниматься ({why}): {exc}")

    # Приспособление, запускающее свой интерпретатор при импорте (как task_node), ставится в процесс ровно раз; цепочку
    # при наследовании обрывает сама проба на третьем звене.
    with tempfile.TemporaryDirectory() as tmp:
        marks = Path(tmp) / "marks"
        (Path(tmp) / "spawning_probe.py").write_text(
            "import os, subprocess, sys\nfrom pathlib import Path\nmarks = Path(os.environ['PROBE_MARKS'])\n"
            "with marks.open('a') as fh:\n    fh.write('звено\\n')\nif len(marks.read_text().split()) < 3:\n"
            "    subprocess.run([sys.executable, '-c', 'import os; print(os.environ.get(\"VPM_FIXTURES\"))'], check=True)\n",
            encoding="utf-8")
        env = {**os.environ, "PROBE_MARKS": str(marks), "VPM_FIXTURES": "spawning_probe",
               "PYTHONPATH": os.pathsep.join((str(FIXTURES), tmp))}
        done = subprocess.run([sys.executable, "-c", "import os; print(os.environ.get('VPM_FIXTURES'))"],
                              env=env, capture_output=True, text=True)
        links = len(marks.read_text(encoding="utf-8").split()) if marks.exists() else 0
        if (done.returncode, links, done.stdout.split()) != (0, 1, ["None", "None"]):
            bad.append(f"точка входа: приспособление поставлено {links} раз (ждали 1), среда ребёнка и сервера после "
                       f"загрузки {done.stdout.split()} (ждали без VPM_FIXTURES), код {done.returncode}: "
                       f"{done.stderr[-300:]}")
    return bad


def main() -> int:
    broken = _language()
    print(f"══ язык объявления: {'чисто' if not broken else str(len(broken)) + ' нарушений'} ══")
    for note in broken:
        print(f"  ✗ {note}")
    files = scenario_files(HERE)
    maps = sorted(HERE.glob("*.map.yaml"))
    if not files and not maps:
        print("Нет ни одного файла сценариев — пустой прогон зелёным не считается.")
        return 1

    vocab = Vocabulary()
    ran: set[str] = set()
    journal = Journal(JOURNAL_DIR / f"scenarios-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    total, fails = len(broken), list(broken)
    matched: set[str] = set()
    try:
        for path in files:
            try:
                scenarios = load(path, vocab)
            except ScenarioError as exc:
                print(f"\n══ {path.name}: ОБЪЯВЛЕНИЕ НЕ ЗАГРУЖЕНО ══\n  {exc}")
                total += 1
                fails.append(f"{path.name}: {exc}")
                continue
            # Отбор — список через запятую: точечный прогон части сценариев по ID.
            only = [part for part in os.environ.get("VPM_SCENARIO", "").split(",") if part]
            if only:
                scenarios = [s for s in scenarios if any(part in s.id for part in only)]
                matched |= {part for part in only for s in scenarios if part in s.id}
            if not scenarios:
                continue
            print(f"\n══ {path.name}: сценариев {len(scenarios)} ══")
            # Сценарий с порчей деклараций или приспособлением — на своём сервере (`server_env`); общий сервер файла — боевой.
            own = {s.id for s in scenarios if s.declarations or s.fixtures}
            groups = [({}, [s for s in scenarios if s.id not in own])] + [
                (server_env(s), [s]) for s in scenarios if s.id in own]
            for env, group in groups:
                if not group:
                    continue
                with live_server(env=env) as srv:
                    srv.rpc.timeout = 180.0        # предел КЛИЕНТА: под coverage дерево с книгами идёт дольше
                    runner = Runner(srv, journal, STEPS)
                    for scenario in group:
                        ran.add(scenario.id)
                        print(f"\n— {scenario.id}: {scenario.why}")
                        for check in runner.run(scenario):
                            total += 1
                            # Метка всегда с именем сценария: по ней сравниваются ДВА прогона
                            # (diff поведения), а шаговые метки в разных сценариях совпадают.
                            if check.ok:
                                print(f"  ✓ {check.scenario} · {check.label}")
                            else:
                                print(f"  ✗ {check.scenario} · {check.label}  → {check.detail}")
                                fails.append(f"{check.scenario} · {check.label} → {check.detail}")
        only = [part for part in os.environ.get("VPM_SCENARIO", "").split(",") if part]
        for path in maps:
            # Отбор применяется и к картам: иначе «точечный прогон» тянул всю карту целиком.
            if only and not any(part.split("#")[0] in path.stem for part in only):
                continue
            matched |= {part for part in only if part.split("#")[0] in path.stem}
            total, fails = _walk_map(path, vocab, journal, total, fails, ran)
    except BaseException:
        # Авария (сервер не поднялся, порт занят, харнесс упал) — это НЕ зелёный прогон.
        # Улика, объявляющая успех тому, чего не было, хуже отсутствия улики.
        journal.verdict(ok=False, total=total, failed=len(fails), scenarios=sorted(ran), crashed=True)
        journal.close()
        raise
    # Прогон, ничего не отобравший, — не зелёный, а неизмеренный: опечатка в имени иначе
    # выглядит как проверенная правка, и точечный прогон становится ритуалом.
    requested = [part for part in os.environ.get("VPM_SCENARIO", "").split(",") if part]
    for part in sorted(set(requested) - matched):
        total += 1
        fails.append(f"отбор `{part}` не совпал НИ С ОДНИМ сценарием или картой — прогон его не проверял")
    if not any(True for _ in ran):
        total += 1
        fails.append("прогон не выполнил ни одного сценария — измерять нечем, зелёным это быть не может")

    journal.verdict(ok=not fails, total=total, failed=len(fails), scenarios=sorted(ran))
    journal.close()

    print(f"\n{'=' * 50}\nРЕЗУЛЬТАТ: {total - len(fails)}/{total} прошло · журнал: {journal.path}")
    if fails:
        print("ПРОВАЛЫ:")
        for line in fails:
            print(f"  - {line}")
        return 1
    print("ВСЁ ЗЕЛЁНОЕ ✅")
    return 0


def _routes() -> dict[str, list[str]]:
    """Селекторы маршрутов: переход карты объявляет, ЧТО обязан донести, а список полей — у маршрута."""
    declared = ROOT / "tests" / "routes" / "routes.yaml"
    if not declared.exists():
        return {}
    return {r["route"]: list(r["proof"]["observe"])
            for r in yaml.safe_load(declared.read_text(encoding="utf-8"))
            if r.get("proof", {}).get("arrives")}


def _walk_map(path: Path, vocab: Vocabulary, journal: Journal, total: int, fails: list, ran: set):
    """Карта: сначала РАЗБОР (что из неё следует), потом обход, покрывающий каждый переход."""
    try:
        smap = load_map(path, vocab)
    except ScenarioError as exc:
        print(f"\n══ {path.name}: КАРТА НЕ ЗАГРУЖЕНА ══\n  {exc}")
        return total + 1, fails + [f"{path.name}: {exc}"]

    print(f"\n══ карта {smap.id}: {smap.why} ══")
    notes = analyse(smap)
    print(f"  состояний {len(smap.states)}, переходов {len(smap.transitions)}")
    for note in notes:
        print(f"  ⚠ {note}")
    paths = plan(smap)
    print(f"  обход покрывает каждый переход: путей {len(paths)}, шагов {sum(len(p) for p in paths)}")
    for i, route in enumerate(paths, 1):
        print(f"    путь{i}: " + " → ".join([smap.start] + [smap.transitions[e].target for e in route]))
    total += 1
    if notes:
        fails.append(f"{smap.id}: карта не сходится — {notes[0]}")

    walker = MapRunner(_routes())
    for number, route in enumerate(paths, 1):
        with live_server() as srv:            # свой сервер на путь: см. walk_path
            srv.rpc.timeout = 180.0
            ran.add(smap.id)                  # в улику попадает пройденное, а не запланированное
            for check in walker.walk_path(smap, route, number, Runner(srv, journal, STEPS)):
                total += 1
                if check.ok:
                    print(f"  ✓ {check.scenario} · {check.label}")
                else:
                    print(f"  ✗ {check.scenario} · {check.label}  → {check.detail}")
                    fails.append(f"{check.scenario} · {check.label} → {check.detail}")
    return total, fails


def test_scenarios_suite():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
