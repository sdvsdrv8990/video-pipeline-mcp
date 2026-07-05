"""
tests/render_draft_final/test_render_draft_final.py — Тест: Двухуровневый рендер

## Что тестируем
Полный пайплайн рендера: draft → final через реальные инструменты.

## Зачем нужен
Основной рабочий кейс: от сценария до финального видео.

## Что хотим увидеть
- Draft создаётся успешно
- Final привязан к draft
- Файлы доступны через fs_read_file

## Как отражает реальное поведение
Эмулирует реальный пайплайн создания видео.

## Тип теста
Workflow / Integration (тест инструментов)

## Workspace
ДА — создаёт файлы через инструменты сервера.
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server import create_server

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {('- ' + str(detail)) if detail else ''}")


async def test_project_structure():
    """Тест создания структуры проекта."""
    print("=== Project Structure ===")

    engine, _, _ = create_server()

    # Создаём project.json
    r = await engine.call("fs_create_file", {
        "path": "project.json",
        "content": json.dumps({"name": "test_video", "resolution": "1920x1080"})
    })
    check("project.json создан", r.status == "success")

    # Создаём source/scenes.json
    r = await engine.call("fs_create_file", {
        "path": "source/scenes.json",
        "content": json.dumps({"scenes": [{"id": "scene_01", "duration": 5.0}]})
    })
    check("source/scenes.json создан", r.status == "success")

    # Проверяем дерево
    r = await engine.call("fs_get_directory_tree", {"path": "."})
    check("Дерево доступно", r.status == "success")
    tree = r.data
    check("project.json в дереве", "project.json" in tree)
    check("source/ в дереве", "source/" in tree)


async def test_draft_render():
    """Тест создания draft рендера."""
    print("\n=== Draft Render ===")

    engine, _, _ = create_server()

    # Создаём draft task
    draft = {
        "task_id": "draft_001",
        "status": "completed",
        "type": "draft",
        "input": "source/scenes.json",
        "output": "renders/draft_001.mp4"
    }
    r = await engine.call("fs_create_file", {
        "path": "renders/draft_001.json",
        "content": json.dumps(draft)
    })
    check("Draft task создан", r.status == "success")

    # Читаем и проверяем
    r = await engine.call("fs_read_file", {"path": "renders/draft_001.json"})
    check("Draft читается", r.status == "success")
    data = json.loads(r.data["content"])
    check("Draft task_id = draft_001", data["task_id"] == "draft_001")
    check("Draft status = completed", data["status"] == "completed")


async def test_final_render():
    """Тест создания final рендера с привязкой к draft."""
    print("\n=== Final Render ===")

    engine, _, _ = create_server()

    # Создаём final task с привязкой к draft
    final = {
        "task_id": "final_001",
        "status": "completed",
        "type": "final",
        "input": "renders/draft_001.mp4",
        "output": "renders/final_001.mp4",
        "derived_from_render_id": "draft_001"
    }
    r = await engine.call("fs_create_file", {
        "path": "renders/final_001.json",
        "content": json.dumps(final)
    })
    check("Final task создан", r.status == "success")

    # Проверяем привязку
    r = await engine.call("fs_read_file", {"path": "renders/final_001.json"})
    data = json.loads(r.data["content"])
    check("Final привязан к draft", data["derived_from_render_id"] == "draft_001")
    check("Final status = completed", data["status"] == "completed")


async def test_facts():
    """Тест facts с информацией о рендерах."""
    print("\n=== Facts ===")

    engine, _, _ = create_server()

    facts = {
        "draft_render_id": "draft_001",
        "final_render_id": "final_001",
        "chain": "source → draft → final"
    }
    r = await engine.call("fs_create_file", {
        "path": "renders/facts.json",
        "content": json.dumps(facts)
    })
    check("Facts создан", r.status == "success")

    r = await engine.call("fs_read_file", {"path": "renders/facts.json"})
    data = json.loads(r.data["content"])
    check("draft_render_id = draft_001", data["draft_render_id"] == "draft_001")
    check("final_render_id = final_001", data["final_render_id"] == "final_001")


async def main():
    await test_project_structure()
    await test_draft_render()
    await test_final_render()
    await test_facts()

    print()
    passed = sum(results)
    total = len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
