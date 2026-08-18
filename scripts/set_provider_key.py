"""
scripts/set_provider_key.py — внести или заменить ключ провайдера. Только руками, только владельцем.

## Назначение
Двери «сохрани ключ» у модели нет намеренно: инструмент сервера означал бы, что значение хоть
на мгновение живёт в диалоге. Здесь оно вводится в терминале владельца, шифруется в памяти и
уходит на диск уже зашифрованным.

## Границы
Ввод скрыт (`getpass`), в подтверждении печатается только отпечаток — запись экрана и лог
сессии ключа не содержат. Новый ключ занимает место старого у того же провайдера: два
действующих ключа на одного поставщика дают два ответа на вопрос «каким сейчас платят».
"""

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.secrets import ChannelSecrets, SecretError    # noqa: E402 — после правки sys.path

WORKSPACE = ROOT / "workspace"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Ключи провайдеров канала (значение не печатается)")
    ap.add_argument("--channel", required=True, help="Путь канала относительно workspace/")
    ap.add_argument("--provider", default="", help="Имя провайдера из строки канала")
    ap.add_argument("--list", action="store_true", help="Показать, какие ключи стоят (без значений)")
    ap.add_argument("--remove", action="store_true", help="Убрать ключ провайдера")
    args = ap.parse_args(argv[1:])

    box = ChannelSecrets(WORKSPACE, ROOT)
    try:
        if args.list or not args.provider:
            rows = box.status(args.channel)
            if not rows:
                print(f"Канал {args.channel}: ключей нет.")
                return 0
            print(f"Канал {args.channel}:")
            print(f"  {'провайдер':<24}{'ключ':<10}{'отпечаток':<24}обновлён")
            for r in rows:
                # Поле значения намеренно пустое: на экране ключа не бывает даже под звёздочками.
                print(f"  {r['provider']:<24}{'—':<10}{r['fingerprint']:<24}{r['updated']}")
            return 0

        if args.remove:
            print(f"Ключ {args.provider} {'убран' if box.remove(args.channel, args.provider) else 'и так не стоял'}.")
            return 0

        print(f"Канал:     {args.channel}")
        print(f"Провайдер: {args.provider}")
        value = getpass.getpass("Ключ (ввод скрыт, на экране не появится): ")
        if not value.strip():
            print("Пусто — ничего не сохранено.")
            return 1
        report = box.set(args.channel, args.provider, value)
        del value                                        # значение больше не нужно ни здесь, ни где-либо
        print(f"✓ {report['provider']}: ключ сохранён зашифрованным, {report['fingerprint']}"
              + (f" (заменил {report['replaced']})" if report["replaced"] else ""))
        print("  Значение не печаталось и в переписку не попадало. Сервер отдаёт наружу только отпечаток.")
        return 0
    except SecretError as e:
        print(f"✗ {e.code}: {e.message}\n  {e.reason}")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
