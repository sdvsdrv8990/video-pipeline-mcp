"""tools/montage — сборка видео: сцена из книги становится проверенным файлом.

Зовёт: ToolContext, SceneBook, FfmpegEngine, RenderLedger, Files.
Зовут: server.

Группа отделена от медиа по предмету: там генерация ассетов — кто и чем рисует, здесь монтаж —
что из чего собирается. Обёртка тонкая: замысел собирает ядро, исполняет провайдер, а строку
результата пишет приёмка, а не рука.
"""

from pathlib import Path

from core.contracts import Fact, ToolResult
from core.engine import Engine
from core.jobs import Tasks
from core.montage import RenderLedger, SceneBook, SceneScript, SceneTemplate
from core.paths import Files
from core.providers.ffmpeg import FfmpegEngine, SceneSpec
from core.providers.ffmpeg.engine import PLACE as RENDER_PLACE
from tools._context import ANNOTATIONS_MODIFY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы montage в движке."""

    book = SceneBook(ctx.state_manager, ctx.config_path, ctx.resolve)
    ledger = RenderLedger(ctx.state_manager, ctx.config_path, ctx.id_generator)
    script = SceneScript(book)
    templates = SceneTemplate(book, ctx.id_generator)
    tasks = Tasks(engine)

    def _output(table: str, scene_id: str, stage: str, container: str, given: str) -> Path:
        """Куда лечь файлу: названное вызовом или объявленное место внутри сущности видео."""
        if given:
            return ctx.resolve(given)
        spec = book.config["output"]
        name = str(spec["name"]).format(scene_id=scene_id, render_stage=stage)
        return ctx.resolve(f"{table.strip('/')}/{spec['dir']}/{name}.{container}")

    async def montage_render_scene(table: str, scene_id: str, profile_id: str = "",
                                   output: str = "", subtitles: str = "") -> "ToolResult":
        """Собрать сцену в видеофайл по строкам её элементов и звука — заданием узла заданий.

        Берёт слои из листа элементов (положение, размер, время появления, движение), дорожки из
        листа звука, параметры кодирования из профиля рендера канала. Сцена, профиль, команда и советы
        судятся в вызове — по книге на момент вызова; рендер, приёмка ffprobe и строка результата —
        работа задания (`RENDER_PLACE`). Ответ — статус задания и путь к итогу. Сцена без звука — обычный
        случай, а не отказ.
        """
        ok, profile = ctx.safe(lambda: book.profile(table, profile_id))
        if not ok:
            return profile
        profile, report = profile
        target = book.config["target"]
        stage = str(target["stage"])
        ok, path = ctx.safe(lambda: _output(table, scene_id, stage, profile.container, output))
        if not ok:
            return path
        subs = None
        if subtitles:
            ok, subs = ctx.safe(lambda: ctx.resolve(subtitles))
            if not ok:
                return subs
        ok, built = ctx.safe(lambda: book.scene(table, scene_id, path, profile, subs))
        if not ok:
            return built
        spec, counts = built
        render = FfmpegEngine()
        ok, prepared = ctx.safe(lambda: render.build(spec))
        if not ok:
            return prepared
        argv, span = prepared

        def work(pulse) -> dict:
            """Рендер, приёмка и строка результата; путь клиенту — дверью корня, а не `relative_to` (ссылки ФС)."""
            outcome = render.execute(spec, argv, span, pulse, owner=pulse.record.task.task_id)   # остаток — свой у задания
            relative = Files(ctx.workspace_path).name(path)
            written = ledger.record(table, {
                "stage": stage, "profile": report["profile_id"], "format_label": report["profile_id"],
                "file_path": relative, "file_name": path.name, "status": str(target["status_ready"]),
                "duration": int(round(outcome.duration_sec)), "verified": True})
            return {"file_path": relative, "duration_sec": outcome.duration_sec,
                    "frame": f"{outcome.width}x{outcome.height}", "file_verified": True,
                    "render_row_id": written["row_id"], "facts": [{"type": "RenderCompleted", "data": {
                        "scene_id": scene_id, "render_id": written["row_id"], "file_path": relative,
                        "duration_sec": outcome.duration_sec, "profile_id": report["profile_id"],
                        "elements": counts["elements"], "audio": counts["audio"]}}]}

        params = {"table": table, "scene_id": scene_id, "profile_id": report["profile_id"], "output": output,
                  "subtitles": subtitles}
        ok, entry = ctx.safe(lambda: tasks.begin("montage_render_scene", params, RENDER_PLACE, work,
                                                  key={"argv": argv, "inputs": _versions(spec)}))
        if not ok:
            return entry
        task = tasks.settle(entry["task_id"], 0).get("task") or {}
        ok, state = ctx.safe(lambda: book.variants_state(table, scene_id))
        state = state if ok else {"advice_key": ""}
        advice = (ctx.advice.get(state["advice_key"], table=table, scene_id=scene_id,
                                 channel=state.get("channel") or table)
                  if state.get("advice_key") else [])
        return ToolResult(status="success", data={
            **({"recommendations": advice} if advice else {}),
            "variants_enabled": state.get("variants_enabled"),
            "table": table, "scene_id": scene_id, "subtitles_mode": profile.subtitles_mode, **report, **counts,
            **task,
            "next": (f"Рендер идёт заданием сервера. Итог и ход — опрос task_status(task_ids=['{entry['task_id']}']): "
                     "ответ сразу, повторяй до completed; в итоге — путь файла, принятого ffprobe, и строка листа "
                     "рендеров. wait=true держит вызов до смены статуса — только клиенту без короткого срока вызова. "
                     "Ненужный или слишком долгий рендер снимает task_cancel."),
        }, facts=_subject(task, {"task_id": entry["task_id"], "table": table, "scene_id": scene_id,
                                 "profile_id": report["profile_id"]}))


    async def montage_scene_script(table: str, scene_id: str, profile_id: str = "",
                                   path: str = "") -> "ToolResult":
        """Выдать чистый скрипт сцены: требования канала подтянуты, наполнения нет.

        Формат кадра, кодек, режим субтитров и состояние тумблера вариативности приходят из
        книги канала — выяснять их самому не нужно. Свой скрипт писать по-прежнему можно:
        шаблон это удобство, а источником правды в обоих случаях остаётся книга.
        """
        ok, resolved = ctx.safe(lambda: book.profile(table, profile_id))
        if not ok:
            return resolved
        profile, report = resolved
        ok, text = ctx.safe(lambda: script.render(table, scene_id, profile, report))
        if not ok:
            return text
        relative = path or script.target(table, scene_id)
        ok, target = ctx.safe(lambda: ctx.resolve(relative))
        if not ok:
            return target
        # Та же дверь, что у любого файла: политика судит тип, размер и содержимое до первого байта.
        ok, denied = ctx.safe(lambda: Files(ctx.workspace_path).replace(target, text, policy=ctx.write_policy))
        if not ok:
            return denied
        return ToolResult(status="success", data={
            "table": table, "scene_id": scene_id, "file_path": relative,
            "bytes": len(text.encode("utf-8")), "variants_enabled": book.variants_state(
                table, scene_id).get("variants_enabled"),
            "executed": False, **report,
        }, facts=[Fact(type="FileCreated", data={"path": relative, "kind": "scene_script"})])


    async def montage_generate_scene(table: str, scene_id: str, template_id: str,
                                     time_offset: float = 0.0, z_offset: int = 0) -> "ToolResult":
        """Разложить сцену по заготовке: строки шаблона канала дописываются в книгу видео.

        Собрать несколько шаблонов в одну сцену — это вызвать несколько раз со смещением по
        времени и порядку слоёв: строки складываются, а не спорят. Дальше сцена правится как
        любые строки книги, и рендер собирает её тем же montage_render_scene.
        """
        ok, applied = ctx.safe(lambda: templates.apply(table, scene_id, template_id,
                                                       time_offset, z_offset))
        if not ok:
            return applied
        ok, state = ctx.safe(lambda: book.variants_state(table, scene_id))
        advice = []
        if ok and state.get("advice_key"):
            advice = ctx.advice.get(state["advice_key"], table=table, scene_id=scene_id,
                                    channel=state.get("channel") or table)
        return ToolResult(status="success", data={
            **({"recommendations": advice} if advice else {}),
            "table": table, "scene_id": scene_id, "template_id": template_id,
            "elements": len(applied["elements"]), "audio": len(applied["audio"]),
            "row_ids": applied["elements"] + applied["audio"],
            "time_offset": time_offset, "z_offset": z_offset,
            "templates_available": templates.names(table),
        }, facts=[Fact(type="RowAppended", data={
            "sheet": "scene", "scene_id": scene_id, "template_id": template_id,
            "rows": len(applied["elements"]) + len(applied["audio"])})])

    engine.register(
        name="montage_generate_scene",
        title="Монтаж: разложить сцену по заготовке",
        description=(
            "Берёт шаблон сцены из книги канала и дописывает его СТРОКАМИ в книгу выбранного "
            "видео: слои в лист элементов, дорожки в лист звука, всё под указанным scene_id. "
            "Шаблон — это строки, а не файл: он правится в редакторе как любые данные, и потому "
            "несколько шаблонов собираются в одну сцену простым повторным вызовом со смещением по "
            "времени (time_offset) и порядку слоёв (z_offset). "
            "Что разложено — обычные строки: дальше двигай и меняй их через table_set, а собирай "
            "через montage_render_scene. Список доступных заготовок приходит в ответе."),
        input_schema={"type": "object", "properties": {
            "table": {"type": "string", "description": "Путь сущности видео в workspace"},
            "scene_id": {"type": "string", "description": "Под каким ID сцены разложить строки"},
            "template_id": {"type": "string", "description": "Какую заготовку применить"},
            "time_offset": {"type": "number", "default": 0.0,
                            "description": "Сдвиг времён заготовки, сек (для второго шаблона подряд)"},
            "z_offset": {"type": "integer", "default": 0,
                         "description": "Сдвиг порядка слоёв (чтобы второй шаблон лёг поверх первого)"},
        }, "required": ["table", "scene_id", "template_id"]},
        handler=montage_generate_scene, group="montage", annotations=ANNOTATIONS_MODIFY)

    engine.register(
        name="montage_scene_script",
        title="Монтаж: чистый скрипт сцены с требованиями канала",
        description=(
            "Отдаёт ГОТОВЫЙ скелет скрипта сцены, в который уже подтянуты требования канала: "
            "разрешение, соотношение сторон, частота кадров, кодек, контейнер, режим субтитров и "
            "состояние вариативности персонажа (позы и эмоции дорожками — или нет). Наполнения в "
            "нём нет намеренно: ассеты, тайминги и звук это твоё решение, и они кладутся строками "
            "книги, а не строками кода. "
            "Писать свой скрипт по-прежнему можно — шаблон удобство, а не обязанность; книга "
            "остаётся источником правды в обоих случаях, поэтому логика не разойдётся. "
            "Сервер файл ТОЛЬКО СОЗДАЁТ и не исполняет: запуск чужого кода сервером означал бы "
            "выполнение того, что написал не он."),
        input_schema={"type": "object", "properties": {
            "table": {"type": "string", "description": "Путь сущности видео в workspace"},
            "scene_id": {"type": "string", "description": "Для какой сцены скелет"},
            "profile_id": {"type": "string", "description": "Профиль рендера (пусто → выбор видео или единственный)"},
            "path": {"type": "string", "description": "Куда положить (пусто → объявленное место внутри видео)"},
        }, "required": ["table", "scene_id"]},
        handler=montage_scene_script, group="montage", annotations=ANNOTATIONS_MODIFY)

    engine.register(
        name="montage_render_scene",
        title="Монтаж: собрать сцену в видеофайл",
        description=(
            "Собирает одну сцену в видео по данным книги: слои берутся из листа элементов сцены "
            "(ассет, положение, размер, слой по глубине, время появления и ухода, движение по "
            "точкам), дорожки — из листа звука (обрезка, громкость и её кривая, темп, затухания), "
            "а чем кодировать — из профиля рендера канала, который видео выбирает столбцом "
            "render_profile. Двигать и подгонять НЕ нужно вызовами: положение, размер, тайминг, "
            "громкость и скорость — это значения в строке, и правятся они обычным table_set, "
            "после чего сцену пересобирают этим же инструментом. "
            "Сцена, профиль и команда судятся сразу — отказ входа приходит этим же вызовом. Сам рендер идёт "
            "заданием сервера: ответ — статус задания (task_id); ход (кадр из обещанных, доля) и итог — "
            "опросом task_status (wait=true — только клиенту без короткого срока вызова); долгий или ненужный "
            "рендер снимает "
            "task_cancel, срока у рендера нет. Результат принимается ffprobe: длительность и размер кадра "
            "сверяются с замыслом, и только тогда в лист рендеров ложится строка с file_verified; итог "
            "задания — «файл годен», а не «файл создан». Повтор того же вызова при тех же входах — то же "
            "задание, второй рендер не идёт. "
            "Сцена без музыки и звуков собирается нормально — пустой звук не отказ. Снятие фона "
            "нейросетью делает media_generate ДО монтажа: ffmpeg режет фон только по цвету."),
        input_schema={"type": "object", "properties": {
            "table": {"type": "string", "description": "Путь сущности видео в workspace (её книга и данные)"},
            "scene_id": {"type": "string", "description": "Какую сцену собирать (ключ строк листа элементов)"},
            "profile_id": {"type": "string", "description": "Профиль рендера (пусто → выбор видео или единственный)"},
            "output": {"type": "string", "description": "Куда положить файл (пусто → объявленное место внутри видео)"},
            "subtitles": {"type": "string", "description": "Путь к .srt (пусто → без субтитров); режим задаёт профиль"},
        }, "required": ["table", "scene_id"]},
        handler=montage_render_scene, group="montage", annotations=ANNOTATIONS_MODIFY)


def _subject(task: dict, started: dict) -> list[Fact]:
    """Ровно один факт-предмет вызова: задание кончилось к ответу — факты его результата (RenderCompleted); иначе —
    RenderStarted: предмет — задание, его статус наблюдает task_status."""
    if task.get("status") == "completed":
        return [Fact(**fact) for fact in task["result"]["facts"]]
    return [Fact(type="RenderStarted", data=started)]


def _versions(spec: SceneSpec) -> list[list]:
    """Версия каждого входа сцены — размер, время правки (нс) и узел файла: перегенерированный ассет — новая работа, а не
    старый файл (AD-21). Хеш содержимого гигабайтного видео на каждый вызов дорог. Предел: на ФС с грубым временем
    правки (FAT — 2 с) замена файла того же размера на том же узле в те же секунды не видна."""
    inputs = [*(layer.asset_path for layer in spec.layers), *(track.asset_path for track in spec.audio),
              *([spec.subtitles_path] if spec.subtitles_path else [])]
    return [[str(path), seen.st_size, seen.st_mtime_ns, seen.st_ino]
            for path in dict.fromkeys(inputs) for seen in (path.stat(),)]
