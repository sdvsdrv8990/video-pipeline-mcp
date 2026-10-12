"""tests/scenarios/steps.py — шаги-помощники: то, чего вызовом инструмента не выразить."""

import ast
import json
import os
import re
import socket
import threading
import time
from pathlib import Path

import yaml

from core.declaration import declarations_root


def remove_path(srv, path: str) -> dict:
    """Файл исчез МИМО инструментов — так его сносит человек в проводнике, а не сервер."""
    target = Path(srv.workspace) / path
    if not target.exists():
        return {"ok": False, "code": "STEP_TARGET_ABSENT", "message": f"нечего убирать: {path}"}
    target.unlink()
    return {"ok": True, "data": {"removed": path}}


def workspace_path(srv, path: str = "") -> dict:
    """Абсолютный путь в рабочей области: команде репозитория относительный ни о чём не говорит.

    Пустой путь — сам корень области: сторож с `--root` судит ДЕРЕВО, а не отдельный файл.
    """
    target = Path(srv.workspace) / path if path else Path(srv.workspace)
    if not target.exists():
        return {"ok": False, "code": "FILE_NOT_FOUND", "message": f"в рабочей области нет: {path}"}
    return {"ok": True, "data": {"path": str(target)}}


def branch_state(srv, niche: str) -> dict:
    """Снимок ВЕТКИ ниши: что в ней заведено и что сервер о ней говорит.

    Проекция, а не суждение: обход и фильтрация по префиксу — ветвление, которому в объявлении не
    место; судит уже сценарий своим `expect`. Книги считаются отдельно от структурных типов —
    материализация книг не является состоянием структуры.
    """
    root = f"niches/{niche}"
    mine = [e for e in (srv.rpc.call_tool("structure_find", {"limit": 500})["data"] or {}).get("found", [])
            if e["path"] == root or e["path"].startswith(root + "/")]
    status = srv.rpc.call_tool("structure_status", {})["data"] or {}

    def names(key):
        return sorted(e["name"] for e in status.get(key, []) if e.get("path", "").startswith(root + "/"))

    return {"ok": True, "data": {
        "types": sorted({e["type"] for e in mine if e["type"] != "table_file"}),
        "books": len([e for e in mine if e["type"] == "table_file"]),
        "orphans": names("orphans"),
        "childless": names("our_channels_without_competitor")}}


def busy_server_still_answers(srv, heavy: str, heavy_args: dict,
                              budget_sec: float = 1.0) -> dict:
    """Пока идёт тяжёлый вызов, лёгкий обязан получить ответ.

    Двумя потоками, иначе эффект не наблюдаем вовсе. `overlapped` защищает от пустого прохода:
    если тяжёлый успел закончиться раньше лёгкого, быстрый ответ ничего не доказывает.
    """
    marks: dict = {}

    def heavy_call():
        started = time.time()
        srv.rpc.call_tool(heavy, heavy_args)
        marks["heavy_sec"] = round(time.time() - started, 2)
        marks["heavy_end"] = time.time()

    worker = threading.Thread(target=heavy_call)
    worker.start()
    time.sleep(0.4)                                  # даём тяжёлому реально начаться
    started = time.time()
    tools = srv.rpc.tools_list()
    light_end = time.time()
    worker.join()

    return {"ok": True, "data": {
        "light_sec": round(light_end - started, 2),
        "heavy_sec": marks.get("heavy_sec", 0.0),
        "tools_seen": len(tools) > 0,
        "overlapped": light_end < marks.get("heavy_end", 0.0),
        "within_budget": (light_end - started) < budget_sec,
    }}


def busy_while_held(srv, tool: str, args: dict | None = None) -> dict:
    """Второй вызов того же класса, пока первый держит место (приспособление `hold_slot`), — исход шага; после снятия
    флага место свободно. Флаг снимается всегда (`finally`), держатель живёт не дольше сервера сценария."""
    flag = srv.workspace / ".hold" / tool
    held = flag.parent / f"{tool}.held"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    holder = threading.Thread(target=srv.rpc.call_tool, args=(tool, args or {}))
    answer: dict = {}
    caller = threading.Thread(target=lambda: answer.update(srv.rpc.call_tool(tool, args or {})))
    holder.start()
    try:
        for _ in range(600):
            if held.exists():
                break
            time.sleep(0.05)
        caller.start()
        caller.join(30)       # «занято» приходит сразу; дольше ждёт только сервер, который место не судит
    finally:
        flag.unlink(missing_ok=True)
        holder.join()
        if caller.ident:
            caller.join()
    second = answer
    after = srv.rpc.call_tool(tool, args or {})
    return {"ok": not second["is_error"], "code": second["code"],
            "data": {"held": held.exists(), "freed_after": after["code"] != second["code"]}}


def seed_runner_token(srv, token: str) -> dict:
    """Токен «живого раннера» в его файле состояния (`local.runner.state_file` от корня проекта) — у копии деклараций
    сервера сценария; без копии (`MCP_CONFIG`) отказ: файл состояния лёг бы в рабочее дерево проекта."""
    config = srv.extra_env.get("MCP_CONFIG")
    if not config:
        return {"ok": False, "code": "SCHEMA_INVALID", "message": "засев токена раннера — только у сервера с копией деклараций"}
    providers = yaml.safe_load((Path(config) / "providers.yaml").read_text(encoding="utf-8"))
    state = Path(config).parent / providers["local"]["runner"]["state_file"]
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"token": token}), encoding="utf-8")
    return {"ok": True, "data": {"state_file": state.name}}


def decoy_beyond_installs(srv, marker: str) -> dict:
    """Приманка суда пути установки: файл состояния с меткой вне каталога установок сервера (в его рабочей области) и
    id, которые к нему ведут, — относительный от каталога установок и абсолютный. Прочти сервер приманку — метка уйдёт
    клиенту в статусе; каталог установок — как его строит сервер: `local.models_dir` от родителя деклараций."""
    config = declarations_root({**os.environ, **srv.extra_env})
    providers = yaml.safe_load((config / "providers.yaml").read_text(encoding="utf-8"))
    installs = config.parent / providers["local"]["models_dir"] / "installs"
    decoy = Path(srv.workspace) / "приманка"
    decoy.with_suffix(".json").write_text(json.dumps(
        {"install_id": marker, "model_id": marker, "kind": "tts", "phase": "done", "started": 0, "heartbeat": 0},
        ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "data": {"relative": os.path.relpath(decoy, installs), "absolute": str(decoy)}}


def task_node_ids(srv) -> dict:
    """id заданий, которые засеяло приспособление `task_node` в хранилище узла сервера сценария: последняя строка его
    журнала `MCP_LOGS/task_node.jsonl` — {interrupted, stalled, broken}. Журнала нет — отказ: приспособление не стояло."""
    journal = Path(srv.logs) / "task_node.jsonl"
    lines = journal.read_text(encoding="utf-8").splitlines() if journal.is_file() else []
    if not lines:
        return {"ok": False, "code": "SCHEMA_INVALID",
                "message": f"журнала приспособления task_node нет ({journal.name}) — в given.fixtures нет task_node"}
    return {"ok": True, "data": json.loads(lines[-1])}


def trail_says(srv, tool: str = "", level: str = "", code: str = "") -> dict:
    """Что сервер записал о вызове `tool` в свой след.

    Наблюдать след через инструмент нельзя — ни один его не читает, а сценарий обязан судить по
    тому, что видно снаружи. Поэтому шаг читает файл сервера напрямую.

    Принимается только запись СВОЕГО сервера — с момента его старта. Окно по часам не годится:
    файлы следа переживают прогон, и на замере выключенный след зеленел на записи прошлого прогона.
    """
    directory = srv.journals.trail.directory  # след своего сервера — под его корнем журналов, как его строит сервер
    fresh = float(getattr(srv, "started", 0.0)) or time.time()
    files = sorted(directory.glob("trail-*.jsonl"), key=lambda f: f.stat().st_mtime,
                   reverse=True) if directory else []
    for path in files[:3]:
        rows = [r for r in path.read_text(encoding="utf-8", errors="replace").splitlines() if r.strip()]
        for row in reversed(rows):
            try:
                entry = json.loads(row)
            except ValueError:
                continue
            if float(entry.get("ts") or 0) < fresh:
                continue
            if tool and entry.get("tool") != tool:
                continue
            if level and entry.get("level") != level:
                continue
            if code and entry.get("code") != code:
                continue
            return {"ok": True, "data": {"tool": entry.get("tool", ""), "code": entry.get("code"),
                                         "level": entry.get("level", ""), "rpc": entry.get("rpc", ""),
                                         "ok_flag": entry.get("ok"), "args": entry.get("args") or {},
                                         "step": entry.get("step")}}
    искали = ", ".join(f"{k}={v}" for k, v in
                       (("tool", tool), ("level", level), ("code", code)) if v) or "любую запись"
    return {"ok": False, "code": "STEP_TRAIL_RECORD_ABSENT",
            "message": f"в следе сервера нет СВЕЖЕЙ записи ({искали}): "
                       f"сервер не пишет то, чем воспроизводят"}


def speak_garbage(srv, payload: str, ждать: bool = True) -> dict:
    """Сказать серверу то, что HTTP не является. Ниже этого уровня наблюдать уже нечего.

    Инструментом такое не выразить: предмет проверки — реакция на байты, до всякого разбора запроса.
    """
    conn = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
    try:
        conn.sendall(payload.encode("utf-8", errors="replace"))
        answer = conn.recv(200).decode("utf-8", errors="replace") if ждать else ""
    except OSError as exc:
        return {"ok": False, "code": "CONNECTION_FAILED", "message": str(exc)}
    finally:
        conn.close()
    return {"ok": True, "data": {"первая_строка": answer.split("\r\n")[0]}}


def hold_connections(srv, count: int, seconds: float = 2.5) -> dict:
    """Открыть соединения и молчать. Так выглядят скан портов и медленное исчерпание."""
    held = []
    try:
        for _ in range(int(count)):
            held.append(socket.create_connection(("127.0.0.1", srv.port), timeout=5))
        time.sleep(float(seconds))
    except OSError as exc:
        return {"ok": False, "code": "CONNECTION_FAILED", "message": str(exc)}
    finally:
        for conn in held:
            conn.close()
    return {"ok": True, "data": {"держали": len(held)}}


def _в_шапке(правка):
    """Правка докстринга модуля внутри целого текста: шапку правят, код не трогают."""
    def применить(текст: str) -> str:
        шапка = ast.get_docstring(ast.parse(текст), clean=False) or ""
        return текст.replace(шапка, правка(шапка), 1)
    return применить


def _шаги_через_from(текст: str) -> str:
    return re.sub(r"(?m)^(import _stamp\b.*)$", r"\1\nfrom _stamp import строка_шага",
                  текст, count=1).replace("_stamp.строка_шага(", "строка_шага(")


def _подпись_через_from(текст: str) -> str:
    return re.sub(r"(?m)^(import _verdict\b.*)$", r"\1\nfrom _verdict import close",
                  текст, count=1).replace("_verdict.close(", "close(")


# Преобразования снимка объявлены СПИСКОМ, а не свободной командой: шаг, принимающий любую строку
# правки, стал бы оболочкой, а её у объявлений нет намеренно. Каждое имя описывает, что именно
# ломает форму источника — и потому читается как условие сценария, а не как чужой код.
ПРАВКИ = {
    "как-есть": lambda текст: текст,
    # Разметку таблицы сдвигает один лишний пробел — так выглядит правка чужой рукой или
    # переформатирование редактором. Разбор источника обязан от этого ослепнуть вслух.
    "сдвиг-разметки": lambda текст: текст.replace("\n| ", "\n|  "),
    # Половина строк исчезла: так выглядит сломанный разбор или потерянный кусок файла.
    "половина-строк": lambda текст: "\n".join(
        текст.splitlines()[:max(2, len(текст.splitlines()) // 2)]) + "\n",
    # Правки ШАПКИ судьи: каждая — одно условие беды паспорта, которое сторож текста обязан назвать.
    "шаг-выдуман": _в_шапке(lambda ш: ш.replace("расписывает «", "расписывает «выдуманный шаг → ", 1)),
    "шаг-не-назван": _в_шапке(lambda ш: re.sub(r"расписывает «[^»→]+→\s*", "расписывает «", ш, count=1)),
    "улика-не-названа": _в_шапке(lambda ш: re.sub(r"(будит )[^,·\n]+,\s*", r"\1", ш, count=1)),
    "подпись-вырезана": _в_шапке(lambda ш: re.sub(r"\s*·\s*(?:не )?подписывает.*?(?=\.\n|\.$)", "", ш,
                                                   count=1, flags=re.S)),
    "адресат-выдуман": _в_шапке(lambda ш: ш.replace("Зовёт: ", "Зовёт: _выдуманный_модуль, ", 1)),
    "источник-выдуман": _в_шапке(lambda ш: ш.replace("читает ", "читает docs/выдуманный.md, ", 1)),
    "источник-прозой": _в_шапке(lambda ш: ш.replace("читает ", "читает журнал подписей, ", 1)),
    "stdin-выдуман": _в_шапке(lambda ш: ш.replace("читает ", "читает stdin, ", 1)),
    "git-через-импорт": _в_шапке(lambda ш: ш.replace("читает ", "читает git, ", 1)),
    "роспись-без-рода": _в_шапке(lambda ш: re.sub(r"расписывает «[^»]*»", "расписывает", ш, count=1)),
    "событие-вырезано": _в_шапке(lambda ш: re.sub(r",\s*событие [A-Za-z]+", "", ш, count=1)),
    "момент-вырезан": _в_шапке(lambda ш: re.sub(
        r",\s*(?:гейт CI|pre-commit|гейт поставки|наряд|рука|цикл|запись)\b", "", ш, count=1)),
    "паспорт-вырезан": _в_шапке(lambda ш: re.sub(r"(?ms)^Паспорт:.*?(?=^\s*$|^Зовёт:)", "", ш, count=1)),
    "шапка-вырезана": _в_шапке(lambda ш: ""),
    "ключ-в-прозе": _в_шапке(lambda ш: ш.rstrip() + "\n\nсессии: число удаляем, способ меряем.\n"),
    "пересказ-декларации": _в_шапке(
        lambda ш: ш.rstrip() + "\n\nпорог: три\nпотолок: сто\nокно: час\nрежим: строгий\n"),
    "вызывающий-выдуман": _в_шапке(lambda ш: ш.replace("Зовут: ", "Зовут: выдуманный_судья, ", 1)),
    "вызывающие-вырезаны": _в_шапке(lambda ш: re.sub(r"(?m)^Зовут:.*\n?", "", ш, count=1)),
    # `_context` импортирует core/excel — тёзку tools/excel: по короткому имени это вызывающий, по модулю нет.
    "вызывающий-тёзка": _в_шапке(lambda ш: ш.replace("Зовут: ", "Зовут: _context, ", 1)),
    "только-роль": _в_шапке(lambda ш: ш.split("\n", 1)[0] + "\n"),
    "вердикт-без-ключа": _в_шапке(lambda ш: ш.rstrip() + "\n\nПоймано на себе при первом прогоне.\n"),
    "вердикт-с-ключом": _в_шапке(lambda ш: ш.rstrip() + "\n\nПоймано на себе при первом прогоне ⟦vpm d496⟧.\n"),
    "поведение-настоящим": _в_шапке(lambda ш: ш.rstrip() + "\n\nКраснеет в обе стороны.\n"),
    "вердикт-отрицанием": _в_шапке(lambda ш: ш.rstrip() + "\n\nСостояние объявлено, но не доказано командой.\n"),
    "вердикт-отрицание-через-слово": _в_шапке(lambda ш: ш.rstrip() + '\n\nЭто не было доказано ни одним прогоном.\n'),
    "вердикт-отрицание-на-переносе": _в_шапке(lambda ш: ш.rstrip() + '\n\nСостояние объявлено, но не\nдоказано командой.\n'),
    "вердикт-условное": _в_шапке(lambda ш: ш.rstrip() + '\n\nБыло бы доказано, если бы ключ стоял.\n'),
    "вердикт-модальное": _в_шапке(lambda ш: ш.rstrip() + '\n\nДолжно быть подтверждено командой повтора.\n'),
    "вердикт-будущее": _в_шапке(lambda ш: ш.rstrip() + '\n\nБудет доказано следующим прогоном.\n'),
    "вердикт-вопрос": _в_шапке(lambda ш: ш.rstrip() + '\n\nДоказано ли это прогоном?\n'),
    "вердикт-цитата-слова": _в_шапке(lambda ш: ш.rstrip() + '\n\nСлово «доказано» без ключа — класс вердикта.\n'),
    "вердикт-что-поймано": _в_шапке(lambda ш: ш.rstrip() + '\n\nОтчёт: что заплачено и что поймано.\n'),
    "вердикт-лексема-доказано": _в_шапке(lambda ш: ш.rstrip() + '\n\nДоказано подсадкой.\n'),
    "вердикт-лексема-подтверждено": _в_шапке(lambda ш: ш.rstrip() + '\n\nПодтверждено на живом сервере.\n'),
    "вердикт-лексема-проверено-мутацией": _в_шапке(lambda ш: ш.rstrip() + '\n\nПроверено мутацией.\n'),
    "вердикт-лексема-покраснела": _в_шапке(lambda ш: ш.rstrip() + '\n\nПокраснела на первом прогоне.\n'),
    "вердикт-лексема-замер-показал": _в_шапке(lambda ш: ш.rstrip() + '\n\nЗамер показал рост.\n'),
    "вердикт-заглавными": _в_шапке(lambda ш: ш.rstrip() + '\n\nПОЙМАНО НА СЕБЕ.\n'),
    "вердикт-бы-в-соседней-клаузе": _в_шапке(lambda ш: ш.rstrip() + '\n\nИначе сторож объявил бы «двери нет» (поймано циклом).\n'),
    "вердикт-нет-через-тире": _в_шапке(lambda ш: ш.rstrip() + '\n\nПоймано красным CI — локально ffmpeg есть, у раннера нет.\n'),
    "вердикт-не-в-соседней-клаузе": _в_шапке(lambda ш: ш.rstrip() + '\n\nШаг молчал, не называя условия: поймано первым прогоном.\n'),
    "вердикт-модальность-в-соседней-клаузе": _в_шапке(lambda ш: ш.rstrip() + '\n\nПроверка должна идти первой, покраснела на втором запуске.\n'),
    "вердикт-вопрос-в-другом-предложении": _в_шапке(lambda ш: ш.rstrip() + '\n\nПокраснела на втором запуске. Почему не раньше?\n'),
    "вердикт-будущее-в-соседней-клаузе": _в_шапке(lambda ш: ш.rstrip() + '\n\nБудет шумно, но поймано мутацией.\n'),
    "вердикт-ключ-в-соседнем-блоке": lambda текст: текст + '\n# поймано на себе\nзаглушка_условия = 1\n# ⟦vpm d496⟧\n',
    "вердикт-слово-в-кавычках-кода": _в_шапке(lambda ш: ш.rstrip() + '\n\nПоймано на `себе`.\n'),
    "вердикт-ключ-строкой-ниже": _в_шапке(lambda ш: ш.rstrip() + '\n\nПоймано на себе.\nКлюч ⟦vpm d496⟧.\n'),
    "запуск-в-шапке": _в_шапке(lambda ш: ш.rstrip() + "\n\n    python3 scripts/guards/findings_count.py --check\n"),
    "из-кода-никто": _в_шапке(lambda ш: re.sub(r"(?m)^Зовут:.*$", "Зовут: рука, из кода никто.", ш, count=1)),
    # Роль и правдивый вызывающий ffmpeg/probe: engine зовёт его ОТНОСИТЕЛЬНЫМ импортом.
    "зовут-engine": _в_шапке(lambda ш: ш.split("\n", 1)[0] + "\n\nЗовут: engine.\n"),
    "шаги-через-from": _шаги_через_from,
    "шаги-через-псевдоним": lambda т: re.sub(r"(?m)^import _stamp\b", "import _stamp as _с",
                                             т, count=1).replace("_stamp.строка_шага(", "_с.строка_шага("),
    "подпись-через-from": _подпись_через_from,
    "шаги-через-from-отрицаются": lambda т: _шаги_через_from(
        _в_шапке(lambda ш: re.sub(r"расписывает «[^»]*»", "шагов не расписывает", ш, count=1))(т)),
    "подпись-через-from-отрицается": lambda т: _подпись_через_from(
        _в_шапке(lambda ш: ш.replace("подписывает через _verdict", "не подписывает", 1))(т)),
    "вердикт-решёткой": lambda т: т.rstrip("\n") + "\n\n# Поймано на себе при первом прогоне.\n",
    "вердикт-решёткой-ключ-ниже": lambda т: т.rstrip("\n") + "\n\n# Поймано на себе при первом прогоне.\n# ⟦vpm d496⟧\n",
    "вердикт-решёткой-ключ-через-пустую": lambda т: т.rstrip("\n") + "\n\n# Поймано на себе при первом прогоне.\n\n# ⟦vpm d496⟧\n",
    "вердикт-решёткой-отрицанием": lambda т: т.rstrip("\n") + "\n\n# Не было доказано ни одним прогоном.\n",
    "вердикт-в-строке-yaml": lambda т: т.rstrip("\n") + '\n\nпример_проверки: "адрес # Поймано на себе при первом прогоне."\n',
    "вердикт-хвостом-yaml": lambda т: т.rstrip("\n") + "\n\nпример_проверки: 1  # Поймано на себе при первом прогоне.\n",
    "вердикт-в-heredoc": lambda т: т.rstrip("\n") + "\n\ncat <<'EOF'\n# Поймано на себе при первом прогоне.\nEOF\n",
}


def снимок_источника(srv, источник: str, куда: str, правка: str = "как-есть") -> dict:
    """Живой файл репозитория кладётся в рабочую область КОМАНДОЙ, а не копией в объявлении.

    Улика (строки реестра, маршрутов, объявлений) не переносится в текст сценария: она стареет там
    молча — сдвиг разметки на один пробел ослепил разбор, а набор со своей копией остался зелёным
    ⟦vpm ec31⟧. Сценарий называет ИСТОЧНИК и ПРАВКУ, а содержимое всегда берётся живое.
    """
    корень = Path(__file__).resolve().parents[2]
    живой = корень / источник
    if not живой.is_file():
        return {"ok": False, "code": "STEP_SOURCE_ABSENT",
                "message": f"источника нет: {источник} — снимать нечего"}
    if правка not in ПРАВКИ:
        return {"ok": False, "code": "STEP_UNKNOWN_EDIT",
                "message": f"правка `{правка}` не объявлена; известны {sorted(ПРАВКИ)}"}
    цель = Path(srv.workspace) / куда
    цель.parent.mkdir(parents=True, exist_ok=True)
    текст = живой.read_text(encoding="utf-8")
    новый = ПРАВКИ[правка](текст)
    # Условие, не нашедшее места, уехало бы в прогон нетронутым источником — и сценарий судил бы не то.
    if правка != "как-есть" and новый == текст:
        return {"ok": False, "code": "STEP_EDIT_NOT_APPLIED",
                "message": f"правка `{правка}` не нашла места в {источник}: условие не подсажено"}
    цель.write_text(новый, encoding="utf-8")
    return {"ok": True, "data": {"path": str(цель), "строк": len(новый.splitlines()),
                                 "правка": правка, "источник": источник}}


STEPS = {"remove_path": remove_path, "branch_state": branch_state,
         "снимок_источника": снимок_источника,
         "workspace_path": workspace_path,
         "busy_server_still_answers": busy_server_still_answers, "busy_while_held": busy_while_held,
         "seed_runner_token": seed_runner_token, "decoy_beyond_installs": decoy_beyond_installs,
         "task_node_ids": task_node_ids,
         "trail_says": trail_says, "speak_garbage": speak_garbage,
         "hold_connections": hold_connections}
