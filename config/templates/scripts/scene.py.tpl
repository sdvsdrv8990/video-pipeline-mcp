"""
Сцена {{SCENE_TITLE}} — шаблон, выданный сервером.

Требования подтянуты из книги канала (профиль {{PROFILE_TITLE}}, источник: {{PROFILE_SOURCE}}):
править их здесь не нужно, они приедут заново, если профиль канала сменится. Твоя работа —
наполнение: какие слои, где, когда и с каким звуком. Значения ниже кладутся в строки книги
({{ELEMENTS_SHEET}} / {{AUDIO_SHEET}}), а рендер собирает кадр по ним — правка одной ячейки
не требует переписывать этот файл.
"""

# ═══ ТРЕБОВАНИЯ КАНАЛА ═══
SCENE_ID = {{SCENE_ID}}
PROFILE_ID = {{PROFILE_ID}}
RESOLUTION = {{RESOLUTION}}
ASPECT_RATIO = {{ASPECT_RATIO}}
FPS = {{FPS}}
CODEC = {{CODEC}}
CONTAINER = {{CONTAINER}}
SUBTITLES_MODE = {{SUBTITLES_MODE}}

# ═══ ВАРИАТИВНОСТЬ ПЕРСОНАЖА ═══
# {{VARIANTS_NOTE}}
VARIANTS_ENABLED = {{VARIANTS_ENABLED}}
SLOTS = {{SLOTS}}

# ═══ НАПОЛНЕНИЕ (пусто намеренно: ассеты, тайминги и звук — твоё решение) ═══
# Форма строки слоя:
{{ELEMENT_SHAPE}}
ELEMENTS: list[dict] = []

# Форма строки звука (сцена без звука — обычный случай, а не отказ):
{{AUDIO_SHAPE}}
AUDIO: list[dict] = []


def scene() -> dict:
    """Замысел сцены одним словарём: его и кладут в книгу."""
    return {"scene_id": SCENE_ID, "profile_id": PROFILE_ID,
            "elements": ELEMENTS, "audio": AUDIO}
