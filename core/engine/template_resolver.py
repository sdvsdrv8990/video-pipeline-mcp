"""core/engine/template_resolver.py — чьи шаблоны берём: проекта или сервера.

## Назначение
Отвечает по АДРЕСУ создания: есть ли выше по дереву каталог `.templates/` — берём его,
нет — серверный `config/templates/`. Памяти у резолвера нет, два одинаковых вызова дают
одинаковый ответ.

## Границы
Поиск идёт ТОЛЬКО внутри `workspace/` и только вверх до его корня (G17). Материализация по
найденным шаблонам всё равно идёт через `write_policy` (F72/F73) — послаблений резолвер не даёт.
"""

from pathlib import Path

# Имя каталога с шаблонами проекта. Зеркалит серверный `config/templates/`: те же подкаталоги,
# тот же формат — копия правится, а не переписывается заново.
PROJECT_TEMPLATES_DIR = ".templates"
WORKSPACE_SUBDIR = "workspace"
TABLES_SUBDIR = "tables"


class TemplateResolver:
    """Какие каталоги шаблонов действуют для конкретного адреса создания."""

    def __init__(self, workspace_path: str | Path, config_path: str | Path):
        self.workspace = Path(workspace_path).resolve()
        self.config = Path(config_path)

    def server_dirs(self) -> dict:
        return {"workspace_dir": self.config / "templates" / WORKSPACE_SUBDIR,
                "tables_dir": self.config / "templates" / TABLES_SUBDIR}

    def project_dir(self, target_path: str) -> Path | None:
        """Ближайший `.templates/` вверх от адреса создания, не выходя из workspace."""
        try:
            start = (self.workspace / (target_path or "")).resolve()
        except OSError:
            return None
        if not (start == self.workspace or self.workspace in start.parents):
            return None                       # адрес вне рабочей области — шаблонов проекта нет
        node = start
        while True:
            candidate = node / PROJECT_TEMPLATES_DIR
            if candidate.is_dir():
                return candidate
            if node == self.workspace:
                return None
            node = node.parent

    def resolve(self, target_path: str = "", mode: str = "default") -> dict:
        """Каталоги шаблонов для адреса. `mode=custom` ищет проектные, иначе серверные.

        Возвращает и фактический источник: клиент должен видеть, ЧЬИ шаблоны сработали,
        иначе «custom» молча выглядел бы как «default».
        """
        server = self.server_dirs()
        if mode != "custom":
            return {**server, "source": "server", "project_dir": None}
        found = self.project_dir(target_path)
        if found is None:
            # Своих шаблонов нет — работаем на серверных, но говорим об этом (не выдаём за custom).
            return {**server, "source": "server_fallback", "project_dir": None}
        dirs = {}
        for key, sub, fallback in (("workspace_dir", WORKSPACE_SUBDIR, server["workspace_dir"]),
                                   ("tables_dir", TABLES_SUBDIR, server["tables_dir"])):
            own = found / sub
            dirs[key] = own if own.is_dir() else fallback      # частичная копия допустима
        partial = [k for k in dirs if dirs[k] == server[k]]
        return {**dirs, "source": "project", "project_dir": found,
                "fell_back": partial}
