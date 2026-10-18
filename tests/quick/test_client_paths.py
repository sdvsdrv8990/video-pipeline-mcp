#!/usr/bin/env python3
"""tests/quick/test_client_paths.py — путь, который сервер называет наружу, — от корня и через `/` на любой ОС.

Решение одно — `Files.name` (core/paths.py): факт отказа двери и текст реестра по нему, состав и опись моделей, отказ
весов, ответ раннера, расхождение реестра с диском. Windows мерится здесь, на Linux, двумя родами событий:
- узел — флейвором Windows: `Path` модуля узла подменён чистым путём Windows (разбор и склейка — как у WindowsPath,
  диска нет); законная пара — POSIX, где `\\` — часть имени и разделителем не становится;
- читатели — на настоящем диске: поле ответа — то, что узел ответил на этот файл (шпион узла), а не своя склейка.
Что флейвор не меряет (WindowsPath с диском: буква тома, UNC, регистр) — мерит CI на Windows.
"""
import asyncio
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import huggingface_hub
import yaml

import core.paths as paths_module
from core.contracts import ContractError
from core.engine import Engine
from core.ids import LinkRegistry
from core.ids.taxonomy import Taxonomy
from core.paths import Files, PathEscapeError
from core.providers.adapters import AdapterRegistry, MediaOutcome
from core.providers.catalog import ModelCatalog
from core.providers.resolver import ProviderError
from core.reactions import Reactions
from core.runner.service import RunnerService
from tests.harness.live_server import declarations_copy

_checks = 0
_fails: list[str] = []


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


class _Windows(PureWindowsPath):
    """Путь Windows без диска: разбор, склейка и `relative_to` — как у WindowsPath; раскрывать нечего."""

    def resolve(self, strict=False):
        return self


class _Posix(PurePosixPath):
    """Путь POSIX без диска: `\\` — часть имени на любой ОС раннера; раскрывать нечего."""

    def resolve(self, strict=False):
        return self


@contextmanager
def flavour(kind: type):
    """Модуль узла видит пути флейвора `kind` (Windows или POSIX) — на любой ОС раннера."""
    held = paths_module.Path
    paths_module.Path = kind
    try:
        yield
    finally:
        paths_module.Path = held


def windows():
    """Модуль узла видит пути, как на Windows."""
    return flavour(_Windows)


@contextmanager
def node_said():
    """Что узел ответил, пока шёл вызов: поле ответа читателя обязано быть одним из этих ответов."""
    said: list[str] = []
    real = Files.name

    def name(self, path):
        said.append(real(self, path))
        return said[-1]

    Files.name = name  # type: ignore[method-assign]
    try:
        yield said
    finally:
        Files.name = real  # type: ignore[method-assign]


def by_node(value, said: list[str]) -> bool:
    """Поле — тот самый объект, что вернул узел: своя склейка того же значения (на Linux равного) не проходит."""
    return any(value is s for s in said)


engine = Engine(reactions=Reactions(ROOT / "config" / "server_reactions.yaml"))

print("== 1. Узел на Windows: путь наружу — от корня и через / ==")
# (род, корень, путь, как назвать клиенту) — Windows-ввод: разделитель ОС в пути.
WINDOWS = [
    ("выход за корень, написан через \\", "C:/ws", "..\\..\\etc\\passwd", "../../etc/passwd"),
    ("выход за корень, написан через /", "C:/ws", "../../outside/evil", "../../outside/evil"),
    ("под корнем — от корня", "C:/ws", "C:\\ws\\ch\\.secrets\\keys.enc", "ch/.secrets/keys.enc"),
    ("сам корень — пусто", "C:/ws", "C:/ws", ""),
    ("абсолютный вне корня — текст клиента через /", "C:/ws", "D:\\other\\x.txt", "D:/other/x.txt"),
]
with windows():
    for kind, root, path, want in WINDOWS:
        got = Files(_Windows(root)).name(path)
        check(f"Windows, {kind}: {path!r} → {want!r}", got == want, repr(got))

print("== 2. Законная пара на POSIX: \\ — часть имени, разделителем не становится ==")
# Обновлено 2026-10-18 (CI f382d26 Windows): пара шла настоящим Path — на Windows «\\» там разделитель, случай невыполним;
# теперь — флейвор POSIX, как раздел 1 — флейвор Windows: меряет на любой ОС раннера.
with flavour(_Posix):
    for kind, path, want in (
            ("обратная косая в имени файла", "dir/a\\b.txt", "dir/a\\b.txt"),
            ("под корнем — от корня", "/ws/ch/keys.enc", "ch/keys.enc"),
            ("выход за корень", "../../etc/passwd", "../../etc/passwd")):
        got = Files(_Posix("/ws")).name(path)
        check(f"POSIX, {kind}: {path!r} → {want!r}", got == want, repr(got))

print("== 3. Причина в узле, следствие на двери: факт и текст отказа, которые видит клиент ==")
with windows():
    _win = Files(_Windows("C:/ws"))
    escaped = engine.refusal(PathEscapeError(_win.name("..\\..\\etc\\passwd"))).error
    try:
        _win.named("ch\\.secrets")
        secret = None
    except ContractError as e:
        secret = engine.refusal(e).error
check("PATH_ESCAPE: факт path и текст реестра — через /, без \\",
      escaped.facts.get("path") == "../../etc/passwd" and "«../../etc/passwd»" in escaped.message
      and "\\" not in escaped.message, (escaped.facts, escaped.message))
check("SECRET_ACCESS_DENIED (дверь named): факт path — от корня через /",
      secret is not None and secret.code == "SECRET_ACCESS_DENIED" and secret.facts.get("path") == "ch/.secrets",
      secret and (secret.code, secret.facts))

print("== 4. Читатели называют путь узлом: состав и опись моделей ==")
_work = Path(tempfile.mkdtemp(prefix="vpm-client-paths-cat-"))
_cfg = declarations_copy(root=_work) / "providers.yaml"
_cfg.write_text(yaml.safe_dump({"local": {
    "models_dir": "vendor/models",
    "hand_placed": "by_presence",
    "sources": {"tts": {"kind": "piper_index", "repo": "voices/piper", "dest": "tts"},
                "image": {"kind": "hf_hub", "dest": "img"},
                "bg_removal": {"kind": "hf_hub", "dest": "bg", "require_formats": [".onnx"]}},
    "install": {"allow_suffixes": [".safetensors", ".onnx", ".json"], "deny_suffixes": [".bin"],
                "max_total_mb": 100, "prefer_files": ["*model.onnx"],
                "revisions": {"org/bg": "rev-bg", "org/sd": "rev-sd", "voices/piper": "rev-voices"}}}},
    allow_unicode=True), encoding="utf-8")


def _sibling(name: str):
    return SimpleNamespace(rfilename=name, size=1000, lfs={"sha256": "0" * 64}, blob_id=None)


_repos = {"org/bg": ["onnx/model.onnx", "preprocessor_config.json"],
          "org/sd": ["model_index.json", "unet/diffusion.fp16.safetensors"]}
huggingface_hub.model_info = lambda repo_id, revision=None, files_metadata=False, **_kw: SimpleNamespace(
    gated=False, siblings=[_sibling(n) for n in _repos[repo_id]], safetensors=None, cardData=None, card_data=None)
_cat = ModelCatalog(_cfg, _work / "vendor" / "models")
_cat._piper_index = lambda src: {"ru_RU-test-low": {  # type: ignore[method-assign]
    "language": {"code": "ru_RU"}, "files": {"ru/test.onnx": {"size_bytes": 100, "md5_digest": "a" * 32},
                                             "ru/test.onnx.json": {"size_bytes": 10, "md5_digest": "b" * 32}}}}
for repo, kind, fields in (("org/bg", "bg_removal", {"dest": "bg/bg", "path": "bg/bg/model.onnx"}),
                           ("org/sd", "image", {"dest": "img/sd", "path": "img/sd"}),
                           ("ru_RU-test-low", "tts", {"path": "tts/test.onnx"})):
    with node_said() as said:
        try:
            made = _cat.manifest(repo, kind)
        except Exception as e:  # отказ — улика проверки, а не обрыв набора
            made = {"отказ": f"{type(e).__name__}: {e}"}
    for field, want in fields.items():
        check(f"состав {repo}: {field} — ответ узла ({want})",
              made.get(field) == want and by_node(made.get(field), said), (made.get(field), said, made.get("отказ")))

print("== 5. Читатели называют путь узлом: отказ весов, ответ раннера, расхождение реестра с диском ==")
try:
    with node_said() as said:
        AdapterRegistry(_cfg, _work).ready("руками/нет.onnx")
    missing = None
except ProviderError as e:
    missing = e
check("LOCAL_MODEL_MISSING: факт path — ответ узла, от корня весов",
      missing is not None and missing.code == "LOCAL_MODEL_MISSING"
      and missing.facts.get("path") == "руками/нет.onnx" and by_node(missing.facts.get("path"), said),
      missing and (missing.code, missing.facts, said))

_ws = _work / "ws"
_ws.mkdir()
_svc = RunnerService(_cfg, _work, _ws, token="t")


class _Drawn:
    """Адаптер без модели: кладёт файл туда, куда велели, и называет его."""

    def generate(self, media):
        media.target.write_bytes(b"\x89PNG")
        return MediaOutcome(files=[media.target])


async def _body():
    return {"kind": "image", "target": "out/pic.png", "input": "x"}

_svc.engine_for = lambda kind: "drawn"  # type: ignore[method-assign]
_svc.registry.load = lambda provider, kind: _Drawn()  # type: ignore[method-assign]
with node_said() as said:
    answer = asyncio.run(_svc.run(SimpleNamespace(headers={_svc.token_header: "t"}, json=_body)))
files = json.loads(answer.text).get("files")
check("раннер: files — ответ узла, от корня рабочей области (провод — по значению: иных вызовов узла в /run нет)",
      answer.status == 200 and files == ["out/pic.png"] and files == said, (answer.status, files, said))

_reg = LinkRegistry(_ws)
(_ws / "niches" / "мимо" / "channels" / "вглубь").mkdir(parents=True)
with node_said() as said:
    issues = _reg.check_integrity(Taxonomy(ROOT / "config" / "templates" / "workspace"))["issues"]
stray = [i["path"] for i in issues if i["type"] == "unregistered_path"]
check("реестр: unregistered_path корня и вложенного — ответ узла, от корня рабочей области",
      stray == ["niches/мимо", "niches/мимо/channels/вглубь"] and all(by_node(p, said) for p in stray),
      (stray, said))

print("== 6. Псевдоним корня: узел судит настоящий путь, а не написанный текст ==")
# Корень — ссылка A, путь пришёл ссылкой B на тот же каталог (на Windows — короткое имя RUNNER~1 того же каталога).
_real = Path(tempfile.mkdtemp(prefix="vpm-client-paths-alias-"))
(_real / "ws" / "sub").mkdir(parents=True)
(_real / "ws" / "sub" / "x.txt").write_text("x", encoding="utf-8")
(_real / "outside").mkdir()
(_real / "alias_a").symlink_to(_real / "ws", target_is_directory=True)
(_real / "alias_b").symlink_to(_real / "ws", target_is_directory=True)
(_real / "ws" / "link").symlink_to(_real / "outside", target_is_directory=True)
_aliased = Files(_real / "alias_a")
for kind, path, want in (
        ("путь вторым псевдонимом корня — от корня", _real / "alias_b" / "sub" / "x.txt", "sub/x.txt"),
        ("путь через свой корень — от корня", _real / "alias_a" / "sub" / "x.txt", "sub/x.txt"),
        ("ссылка внутри корня наружу — именем под корнем, а не настоящим путём",
         _real / "alias_a" / "link" / "y.txt", "link/y.txt"),
        ("абсолютный вне корня — текст клиента", _real / "outside" / "y.txt", (_real / "outside" / "y.txt").as_posix())):
    got = _aliased.name(path)
    check(f"псевдоним, {kind}: → {want!r}", got == want, repr(got))
denied = _aliased.unread(_real / "alias_b" / "sub" / "x.txt", PermissionError(13, "Permission denied"))
check("непрочитанное вторым псевдонимом: факт path — от корня, пути хоста в фактах нет",
      denied == {"path": "sub/x.txt", "reason": "EACCES"} and str(_real) not in str(denied), denied)

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
