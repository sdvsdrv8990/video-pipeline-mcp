"""
tools/montage — сборка видео: сцена из книги становится проверенным файлом.

Группа отдельная от `media`: там генерация ассетов (кто и чем рисует), здесь монтаж (что из чего
собирается). Обёртка тонкая — замысел собирает `core/montage`, исполняет `core/providers/ffmpeg`,
а строку результата пишет приёмка, а не рука.
"""

from pathlib import Path

from core.contracts import Fact, ToolResult
from core.engine import Engine
from core.montage import RenderLedger, SceneBook, SceneScript, SceneTemplate
from core.providers.ffmpeg import FfmpegEngine
from tools._context import ANNOTATIONS_MODIFY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы montage в движке."""

    book = SceneBook(ctx.state_manager, ctx.config_path, ctx.resolve)
    ledger = RenderLedger(ctx.state_manager, ctx.config_path, ctx.id_generator)
    script = SceneScript(book)
    templates = SceneTemplate(book, ctx.id_generator)

    def _output(table: str, scene_id: str, stage: str, container: str, given: str) -> Path:
        """Куда лечь файлу: названное вызовом или объявленное место внутри сущности видео."""
        if given:
            return ctx.resolve(given)
        spec = book.config["output"]
        name = str(spec["name"]).format(scene_id=scene_id, render_stage=stage)
        return ctx.resolve(f"{table.strip('/')}/{spec['dir']}/{name}.{container}")

    async def montage_render_scene(table: str, scene_id: str, profile_id: str = "",
                                   output: str = "", subtitles: str = "") -> "ToolResult":
        """Собрать сцену в видеофайл по строкам её элементов и звука.

        Берёт слои из листа элементов (положение, размер, время появления, движение), дорожки из
        листа звука, параметры кодирования из профиля рендера канала — и отдаёт файл, ПРОВЕРЕННЫЙ
        ffprobe. Приёмка внутри: длительность и кадр сверяются с замыслом, а строка результата
        пишется только после этого. Сцена без звука — обычный случай, а не отказ.
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
        timeout = int((book.config.get("render") or {}).get("timeout_sec") or 1800)
        ok, outcome = ctx.safe(lambda: FfmpegEngine(timeout_sec=timeout).render_scene(spec))
        if not ok:
            return outcome
        relative = str(path.relative_to(ctx.workspace_path))
        ok, written = ctx.safe(lambda: ledger.record(table, {
            "stage": stage, "profile": report["profile_id"], "format_label": report["profile_id"],
            "file_path": relative, "file_name": path.name, "status": str(target["status_ready"]),
            "duration": int(round(outcome.duration_sec)), "verified": True}))
        if not ok:
            return written
        ok, state = ctx.safe(lambda: book.variants_state(table, scene_id))
        state = state if ok else {"advice_key": ""}
        advice = (ctx.advice.get(state["advice_key"], table=table, scene_id=scene_id,
                                 channel=state.get("channel") or table)
                  if state.get("advice_key") else [])
        return ToolResult(status="success", data={
            **({"recommendations": advice} if advice else {}),
            "variants_enabled": state.get("variants_enabled"),
            "table": table, "scene_id": scene_id, "file_path": relative,
            "duration_sec": outcome.duration_sec, "frame": f"{outcome.width}x{outcome.height}",
            "file_verified": True, "render_row_id": written["row_id"],
            "subtitles_mode": profile.subtitles_mode, **report, **counts,
        }, facts=[Fact(type="RenderCompleted", data={
            "scene_id": scene_id, "render_id": written["row_id"], "file_path": relative,
            "duration_sec": outcome.duration_sec, "profile_id": report["profile_id"],
            "elements": counts["elements"], "audio": counts["audio"]})])


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
        def _allowed() -> None:
            # Та же дверь, что у любого файла: политика проверяет и тип, и содержимое.
            ctx.write_policy.check(relative)
            ctx.write_policy.check_content(relative, text)

        ok, denied = ctx.safe(_allowed)
        if not ok:
            return denied
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
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
            "Результат принимается ffprobe внутри вызова: длительность и размер кадра сверяются с "
            "замыслом, и только тогда в лист рендеров ложится строка с file_verified. Успех здесь "
            "означает «файл годен», а не «файл создан». "
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
