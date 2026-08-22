"""
tests/quick/test_ffmpeg_filters.py — словарь переходов/фильтров сверяется с бинарём ffmpeg.

Standalone-прогон:  python tests/quick/test_ffmpeg_filters.py
Проверяет: каждое объявленное имя разрешается в существующий фильтр/токен установленного ffmpeg,
словарь закрыт (незнакомый вход в фильтрограф не проходит), параметры — валидная JSON Schema и
дефолт ей удовлетворяет, словарь не является копией списка из бинаря.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "config" / "ffmpeg_filters.yaml"

_checks = 0
_fails = []


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


D = yaml.safe_load(CFG.read_text(encoding="utf-8"))
TRANS = D["transitions"]["by_name"]
FILTERS = D["filters"]["by_name"]

print("── структура словаря ──")
ok(D["transitions"]["video_filter"] == "xfade", "видеопереход исполняет xfade")
ok(D["transitions"]["audio_filter"] == "acrossfade", "звук на стыке сшивает acrossfade")
ok("cut" in TRANS and TRANS["cut"]["transition"] is None,
   "склейка без перехода объявлена именем `cut`, а не пустым значением")

# Закрытость — не стилистика: значения приходят из книги и попадают в строку фильтров ffmpeg.
for name, spec in FILTERS.items():
    ok(spec["params"].get("additionalProperties") is False,
       f"{name}: словарь параметров закрыт (additionalProperties: false)")

print("\n── параметры: схема и её собственный дефолт ──")
for name, spec in FILTERS.items():
    schema = spec["params"]
    try:
        Draft202012Validator.check_schema(schema)
        ok(True, f"{name}: params — валидная JSON Schema")
    except Exception as exc:
        ok(False, f"{name}: params не является валидной JSON Schema — {exc}")
        continue
    # Дефолт, не проходящий собственную схему, — обещание, которое движок нарушит на первом вызове.
    defaults = {k: v["default"] for k, v in schema["properties"].items() if "default" in v}
    errors = sorted(Draft202012Validator(schema).iter_errors(defaults), key=lambda e: e.path)
    ok(not errors, f"{name}: дефолты удовлетворяют своей же схеме"
       + (f" — {errors[0].message}" if errors else ""))

print("\n── сверка с установленным ffmpeg ──")
FFMPEG = shutil.which("ffmpeg")
if not FFMPEG:
    # Пропуск с ПРИЧИНОЙ: рендер живёт на машине владельца, а не на раннере CI. Структурные
    # проверки выше отработали и без бинаря.
    print("  ⤼ ffmpeg не найден в PATH — сверка с бинарём пропущена (не отказ)")
else:
    _ver = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    print(f"  ({_ver})")

    def _filter_exists(name: str) -> bool:
        out = subprocess.run([FFMPEG, "-hide_banner", "-h", f"filter={name}"],
                             capture_output=True, text=True)
        return "Unknown filter" not in (out.stdout + out.stderr)

    def _xfade_tokens() -> set[str]:
        """Значения enum-опции `transition`: имя + числовой код + флаги."""
        out = subprocess.run([FFMPEG, "-hide_banner", "-h", "filter=xfade"],
                             capture_output=True, text=True).stdout
        tokens = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[1].lstrip("-").isdigit() and parts[2].startswith(".."):
                tokens.add(parts[0])
        return tokens

    ok(_filter_exists("xfade"), "xfade есть в бинаре")
    ok(_filter_exists("acrossfade"), "acrossfade есть в бинаре")

    _tokens = _xfade_tokens()
    ok(len(_tokens) > 10, f"список переходов вычитан из бинаря ({len(_tokens)} шт.)")

    _declared = {n: s["transition"] for n, s in TRANS.items() if s["transition"] is not None}
    for name, token in _declared.items():
        ok(token in _tokens, f"переход `{name}` → xfade:{token} существует в этом ffmpeg")

    for name, spec in FILTERS.items():
        ok(_filter_exists(spec["filter"]), f"фильтр `{name}` → {spec['filter']} существует в этом ffmpeg")

    # Копия чужого списка стареет молча и не имеет читателя. Объявляем СВОЁ подмножество.
    ok(len(_declared) < len(_tokens),
       f"словарь не копия бинаря: объявлено {len(_declared)} из {len(_tokens)} доступных")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
