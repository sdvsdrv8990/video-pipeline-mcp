"""
core/contracts/error_detail.py — ErrorDetail + Recovery

## Назначение
Обёртка ошибки для Claude. Содержит код (из server_reactions.yaml),
ПОЛНЫЙ текст ошибки (от провайдера/сервера) и подсказку recovery.
"""

from pydantic import BaseModel, field_validator


# ═══ ВСПОМОГАТЕЛЬНЫЕ (используются ниже) ═══

class Recovery(BaseModel):
    """Подсказка Claude: что делать при ошибке.

    Attributes:
        suggested_tool: Какой инструмент использовать (опционально)
        suggested_params: Параметры для suggested_tool (опционально)
        reason: Почему именно так (обязательно для понимания)
    """
    suggested_tool: str | None = None
    suggested_params: dict | None = None
    reason: str = ""


# ═══ ОСНОВНЫЕ (используют Recovery выше) ═══

# D4: реестр кодов ошибок (единый источник).
# Генерируется из server_reactions.yaml при старте; здесь — статический список
# для валидации. При добавлении нового кода — добавить сюда И в yaml.
KNOWN_ERROR_CODES = {
    "TOOL_NOT_FOUND", "VALIDATION_ERROR", "INTERNAL_ERROR",
    "PATH_ESCAPE", "MISSING_TARGET_FILE", "FILE_NOT_FOUND",
    "TABLE_NOT_FOUND", "STRUCTURE_INCOMPLETE",
    "FILE_EXISTS", "DIRECTORY_NOT_EMPTY", "TEMPLATE_NOT_FOUND",
    "NO_FRAGMENTS", "INVALID_EXTENSION",
    "PROVIDER_FAILED", "CONTENT_REJECTED", "LOCAL_INFERENCE_FAILED",
    "PROVIDER_NOT_CONFIGURED", "PROVIDER_EXHAUSTED",
    "PROVIDER_TIMEOUT", "DOWNLOAD_INCOMPLETE", "DOWNLOAD_FORBIDDEN",
    "PROVIDER_ADAPTER_MISSING", "LOCAL_MODEL_MISSING", "USAGE_UNIT_UNKNOWN",
    # F100: объявлены в server_reactions.yaml и реально бросаются, но выпали отсюда —
    # ErrorDetail предупреждал «код не в реестре» на штатных отказах поиска и пересчёта
    "PATH_NOT_FOUND", "QUERY_NOT_FOUND", "RECALC_UNAVAILABLE",
    # Reconcile (S24): пакет откачен целиком — половина сведения хуже отсутствия
    "RECONCILE_ROLLED_BACK",
    # Раннер (S24): строка канала просит считать вне сервера, а он не поднят
    "RUNNER_NOT_RUNNING",
    # Ключи провайдеров (S23): значение не выдаётся никогда, только отпечаток
    "SECRET_ACCESS_DENIED", "PROVIDER_KEY_MISSING", "SECRET_UNREADABLE",
    "SECRET_ENCRYPTION_UNAVAILABLE",
    "AUTH_REQUIRED", "AUTH_FAILED", "DEFAULT", "UNKNOWN_ERROR",
    # Таблицы: данные (Категория 3)
    "SHEET_NOT_FOUND", "COLUMN_NOT_FOUND", "ROW_NOT_FOUND",
    "COMPUTED_READONLY", "ENUM_VIOLATION", "INVALID_ACTION",
    # Таблицы: структура (Категория 2, excel_*)
    "WORKBOOK_NOT_FOUND", "SHEET_EXISTS", "LAST_SHEET",
    "COLUMN_EXISTS", "FORMULA_PROTECTED", "COLUMN_HAS_DEPENDENTS",
    # Структура: реестр связей / ORPHAN (Ф2)
    "UNLINKED_ENTITY", "ENTITY_NOT_FOUND",
    # Анализ данных
    "SHEET_COPY_ERROR",
    # Проверка целостности
    "DUPLICATE_ID",
    # Таблицы: материализация книг по декларации (A1′, фаза ТАБЛИЦЫ)
    "SCHEMA_INVALID",
    # Иерархия ID: цепочка по каталогу назначения (S18-g/S18-h)
    "DUPLICATE_PATH", "CHAIN_UNRESOLVED", "TEMPLATE_INVALID",
    # Целостность артефактов: подпись инстанса и отпечаток машины (S9)
    "FOREIGN_WRITE", "MACHINE_MISMATCH",
    # Тип файла запрещён к записи (S2)
    "FILE_TYPE_FORBIDDEN",
    # Подтверждение необратимой операции (S3)
    "CONFIRM_REQUIRED",
}


class ErrorDetail(BaseModel):
    """Детали ошибки для Claude."""

    code: str  # D4: валидируется против `config/server_reactions.yaml`
    reaction_class: str = "unknown"  # класс задаёт реестр, не эмитент
    message: str
    recovery: Recovery
    raw_response: dict | None = None  # D23: секреты маскируются до попадания сюда

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        """D4: предупреждаем если код не в реестре (но не блокируем)."""
        if v not in KNOWN_ERROR_CODES:
            import warnings
            warnings.warn(f"ErrorDetail.code='{v}' не в реестре KNOWN_ERROR_CODES", stacklevel=2)
        return v

    @field_validator("raw_response", mode="before")
    @classmethod
    def _sanitize_raw_response(cls, v: dict | None) -> dict | None:
        """D23: маскируем секреты в raw_response перед передачей Claude."""
        if v is None:
            return None
        SENSITIVE_KEYS = {"authorization", "api_key", "token", "set-cookie", "cookie", "secret", "password"}
        sanitized: dict = {}
        for key, value in v.items():
            if any(s in key.lower() for s in SENSITIVE_KEYS):
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, dict):
                sanitized[key] = cls._sanitize_raw_response(value)
            else:
                sanitized[key] = value
        return sanitized
