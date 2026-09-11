"""core/engine — движок: реестр инструментов, шаблоны структуры и материализация книг."""

from .engine import Engine
from .template_engine import TemplateEngine, TemplateError
from .table_materializer import TableMaterializer, TableMaterializerError
from .template_resolver import TemplateResolver

__all__ = ["Engine", "TemplateEngine", "TemplateError", "TableMaterializer", "TableMaterializerError",
           "TemplateResolver"]
