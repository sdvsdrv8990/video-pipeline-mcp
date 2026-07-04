"""
core/contracts/error_detail.py — ErrorDetail + Recovery

## Назначение
Обёртка ошибки для Claude. Содержит код (из server_reactions.yaml),
ПОЛНЫЙ текст ошибки (от провайдера/сервера) и подсказку recovery.

## 4 уровня анализа

### 1. Код
- Recovery — вспомогательная модель (определяется ПЕРЕД ErrorDetail)
- ErrorDetail — основная модель (использует Recovery)
- Порядок: Recovery → ErrorDetail (зависимости снизу вверх)

### 2. Поведение
- Claude получает ErrorDetail при ЛЮБОЙ ошибке
- Код → маппинг на server_reactions.yaml (класс реакции)
- Полный текст → анализ причины (модерация/таймаут/лимит)
- Recovery → подсказка что делать (retry/смена/человек)

### 3. Поток данных
```
Провайдер/сервер → исключение → сервер ловит → маппит код
→ собирает ErrorDetail → кладёт в ToolResult.error → Claude
```

### 4. Долгосрочный (6 мес)
- Claude накопит знание "какие ошибки бывают у каких провайдеров"
- raw_response позволит анализировать паттерны ошибок
- Это знание пойдёт в project_memory.md
- Через 6 мес: меньше ложных retry, быстрое распознавание

## Порядок полей (причина → следствие)
1. code — идентификатор ошибки (первичный ключ)
2. message — полный текст (содержимое)
3. recovery — что делать (связь с поведением Claude)
4. raw_response — оригинал API (мета для анализа)

## Как будет меняться
- Добавятся новые code в server_reactions.yaml (не в код)
- recovery может расширяться (новые suggested_tool)
- raw_response будет заполняться чаще (провайдеры возвращают больше)

## Какие регрессии возможны
- Изменение формата code → сломает маппинг server_reactions.yaml
- Удаление recovery → Claude не будет знать что делать при ошибке
- Изменение message → Claude может неправильно интерпретировать причину
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
    "AUTH_REQUIRED", "AUTH_FAILED", "DEFAULT", "UNKNOWN_ERROR",
}


class ErrorDetail(BaseModel):
    """Детали ошибки для Claude.

    Attributes:
        code: Код ошибки из server_reactions.yaml (D4: валидируется)
        reaction_class: Класс реакции (ai_recoverable/server_recoverable/human_required/integrity/unknown)
        message: ПОЛНЫЙ текст ошибки от провайдера/сервера
        recovery: Подсказка что делать дальше
        raw_response: Оригинальный ответ API (для анализа, D23: секреты маскируются)
    """
    code: str
    reaction_class: str = "unknown"
    message: str
    recovery: Recovery
    raw_response: dict | None = None

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
        sanitized = {}
        for key, value in v.items():
            if any(s in key.lower() for s in SENSITIVE_KEYS):
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, dict):
                sanitized[key] = cls._sanitize_raw_response(value)
            else:
                sanitized[key] = value
        return sanitized
