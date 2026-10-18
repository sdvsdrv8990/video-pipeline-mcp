"""core/providers/catalog.py — какие модели вообще бывают и какие из них можно поставить.

Список берётся ЖИВЫМ из чужих каталогов, здесь только правила отбора и установки, и отсев объявлен
— меняется без правки кода. Онлайн-список отвечает офлайн: ключ нужен для ВЫЗОВА, не для
перечисления. Запрет pickle-форматов весов не вкусовщина: их загрузка ВЫПОЛНЯЕТ код из файла,
поэтому ставится только то, что кодом не является.
"""

from typing import ClassVar
import json
import logging
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

from filelock import Timeout

from core.contracts import ContractError
from core.declaration import Declaration
from core.engine.engine import incident
from core.declaration_schemas import (MODEL_FETCH, MODEL_INSTALL, ONLINE_ANSWER, AnswerPolicy, FetchPolicy, InstallPolicy,
                                      ProviderUrl)
from core.paths import Files
from .download import Fetcher, answers, file_digest
from .resolver import ProviderError

log = logging.getLogger(__name__)


class ModelCatalog:
    """Живые каталоги моделей + правила отбора и установки из декларации."""

    # Вердикт диска (`judge`) → код отказа: у каждой причины свой рецепт в server_reactions.yaml.
    REFUSALS: ClassVar[dict[str, str]] = {
        "incomplete": "MODEL_INCOMPLETE", "corrupt": "MODEL_CORRUPT", "unverified": "MODEL_UNVERIFIED"}
    # Сеть загрузчика (MF-16): None — настоящий транспорт; подставной видит каждый запрос. Атрибут класса — каталог
    # строит и сервер, и CLI, подмена доходит до любого из них.
    transport: ClassVar = None
    resolve: ClassVar = staticmethod(socket.getaddrinfo)

    def __init__(self, config_file: str | Path, models_dir: str | Path):
        self.config_file = Path(config_file)
        self.models_dir = Path(models_dir)
        self._decl = Declaration(
            config_file, ProviderError, "провайдеров",
            "Заведи config/providers.yaml — где искать модели, объявлено там.")
        self._revisions: dict[str, str] = {}   # repo → ревизия (спрашиваем один раз)

    @property
    def config(self) -> dict:
        return self._decl.data

    @property
    def local(self) -> dict:
        return self.config.get("local") or {}

    def source(self, kind: str) -> dict:
        src = (self.local.get("sources") or {}).get(kind)
        if not src:  # вид — свободный ввод клиента (в схеме инструмента перечня нет): не из объявленных — ошибка ввода
            raise ProviderError("VALIDATION_ERROR", errors=[
                {"field": "kind", "rule": "enum", "expected": sorted(self.local.get("sources") or {})}])
        return src

    # ═══ Что уже стоит ═══

    @property
    def inventory_file(self) -> Path:
        """Опись поставленного лежит РЯДОМ С ВЕСАМИ, а не в конфиге.

        Конфиг — рукописная декларация с объяснениями; машина, дописывающая в него строки,
        стёрла бы комментарии при первой же перезаписи. Опись описывает состояние диска,
        поэтому и живёт на диске, вне git — вместе с тем, что описывает.
        """
        return self.models_dir / "installed.yaml"

    def inventory(self) -> list[dict]:
        """Опись поставленного. Её отсутствие — «ничего не поставлено», а вот битость — отказ:
        пустой список вместо повреждённой описи выглядел бы как чистая машина."""
        data = Declaration(
            self.inventory_file, ProviderError, "описи установленного",
            "Опись пишет установка моделей; руками её не правят.").optional()
        return list(data.get("models") or [])

    def entries(self) -> list[dict]:
        """Что сервер знает о локальных моделях. Источник ОДИН — опись поставленного.

        Прежде рядом жил рукописный `local.catalog`, и одна и та же модель могла быть описана
        дважды по-разному. Теперь ставится всё одним путём (живой каталог → установка → опись),
        и вопрос «откуда эта модель взялась» имеет один ответ.
        """
        return self.inventory()

    def path_of(self, model_id: str) -> str:
        """Путь весов по имени модели (относительно каталога весов). Не нашли — пусто."""
        for entry in self.entries():
            if str(entry.get("id")) == str(model_id).strip():
                return str(entry.get("path") or entry.get("dest") or "")
        return ""

    def installed(self) -> list[dict]:
        """Модели, известные серверу, с готовностью по диску (`judge` по длине) и занятым местом."""
        out = []
        for entry in self.entries():
            path = self.models_dir / str(entry.get("path") or entry.get("dest") or "")
            files = [f for f in path.rglob("*") if f.is_file()] if path.is_dir() else (
                [path] if path.is_file() else [])
            out.append({"id": entry.get("id"), "kind": entry.get("kind"), "repo": entry.get("repo"),
                        "path": str(entry.get("path") or entry.get("dest") or ""),
                        "disk": self.judge(entry),
                        # Вариант весов — свойство ТОГО, ЧТО ЛЕЖИТ на диске, и его надо поставить
                        # в столбец строки: без него модель с файлами fp16 не поднимется вовсе.
                        "variant": str(entry.get("variant") or ""),
                        "mb": round(sum(f.stat().st_size for f in files) / 1e6, 1),
                        "params_total": entry.get("params_total"),
                        "params_by_dtype": dict(entry.get("params_by_dtype") or {}),
                        "params_active": int(entry.get("params_active") or 0)})
        return out

    def judge(self, entry: dict, deep: bool = False, mark=None) -> dict:
        """Вердикт по диску и план разницей: части нет или она короче — докачать, длиннее или отпечаток разошёлся — заменить.

        Ступень названа в ответе: «по длине» дёшева — для списка и подъёма модели; «по отпечатку» читает все байты —
        конец установки, pull и явная проверка. Запись без состава (поставлена до него) судить нечем — `unverified`.
        """
        parts = entry.get("parts") or []
        if not parts:
            return {"verdict": "unverified", "checked": "", "plan": [],
                    "why": "состава в описи нет — судить нечем; снять его с источника и сверить: "
                           f"python scripts/models.py pull {entry.get('id')}"}
        place = self.models_dir / str(entry.get("dest") or "")
        plan, blind = [], []
        for part in parts:
            path = place / part["path"]
            size = path.stat().st_size if path.is_file() else None
            if size is None or (part["size"] is not None and size < part["size"]):
                plan.append({**part, "job": "докачать"})
            elif part["size"] is not None and size > part["size"]:
                plan.append({**part, "job": "заменить"})
            elif deep and part["digest"]:
                if self._digest(path, part["algo"], mark) != part["digest"]:
                    plan.append({**part, "job": "заменить"})
            elif part["size"] is None:
                blind.append(part["path"])
        checked = "по отпечатку" if deep and all(p["digest"] for p in parts) else "по длине"
        names = ", ".join(p["path"] for p in plan)
        if any(p["job"] == "заменить" for p in plan):
            verdict, why = "corrupt", f"расходятся с составом {len(plan)} из {len(parts)} частей ({names}), проверено {checked}"
        elif plan:
            verdict, why = "incomplete", f"не хватает {len(plan)} из {len(parts)} частей ({names}), проверено {checked}"
        elif blind:
            verdict, why = "unverified", f"источник не назвал длину ({', '.join(blind)}) — судить нечем"
        else:
            verdict, why = "ready", f"все {len(parts)} частей на месте, проверено {checked}"
        return {"verdict": verdict, "checked": checked, "why": why, "plan": plan}

    @staticmethod
    def _digest(path: Path, algo: str, mark=None) -> str:
        """Отпечаток части для суда готовности — тем же счётом, что и загрузчик перед тем, как поставить часть."""
        return file_digest(path, algo, mark)

    def refusal(self, entry: dict, disk: dict) -> ProviderError:
        """Отказ кодом причины по вердикту диска: части не хватает, часть расходится, судить нечем.

        План и команда — в `message`: для кода из реестра клиенту доходит он, а рецепт берётся из реестра.
        """
        jobs = "; ".join(f"{p['job']} {p['path']}" for p in disk["plan"]) or "снять состав с источника"
        return ProviderError(
            self.REFUSALS[disk["verdict"]],
            f"Модель '{entry.get('id')}' не готова: {disk['why']}. План: {jobs}; выполнить: "
            f"python scripts/models.py pull {entry.get('id')}.")

    def installed_as(self, model_id: str, kind: str) -> dict | None:
        """Модель уже стоит той же ревизией и цела по длине — исход «уже стоит» без загрузки, потока и чтения весов (MF-17).

        Сначала диск (по длине, без сети), затем ревизия: закреплённая (`local.install.revisions`) сверяется с описью
        без сети, плавающая — одним запросом коммита к источнику. Ответ называет, чем судили: был ли поход в сеть.
        """
        piper = str(self.source(kind).get("kind") or "") == "piper_index"
        entry = next((e for e in self.inventory() if e.get("kind") == kind
                      and str(e.get("id") if piper else e.get("repo")) == model_id), None)
        if not entry or self.judge(entry)["verdict"] != "ready":
            return None
        repo = str(self.source(kind)["repo"] if piper else model_id)
        pinned = repo in (self.install_rules.get("revisions") or {})
        if not pinned:
            self._open_source(kind)
        if self.revision(repo) != entry.get("revision"):
            return None
        return {"outcome": "already_installed", "revision": entry.get("revision"),
                "checked": "по длине, ревизия закреплена" if pinned else "по длине, ревизия сверена с источником",
                "path": entry.get("path"), "mb": entry.get("mb")}

    # ═══ Что доступно (живые каталоги) ═══

    @property
    def cache_dir(self) -> Path:
        """Куда ложатся малые файлы источника (индекс голосов, карточки) — внутри каталога весов, по репозиторию и
        ревизии: «сколько весит проект» имеет один ответ, а новая ревизия не читает старый файл."""
        return self.models_dir / str(self.local.get("cache_dir") or ".cache")

    def revision(self, repo: str) -> str:
        """Состояние репозитория, к которому привязана загрузка.

        Без привязки веса тянутся «из того, что там сейчас»: файлы ОДНОЙ модели могут приехать
        из разных состояний репозитория, а переустановка через месяц — дать другие веса под тем
        же именем и молча. Ревизия спрашивается один раз на репозиторий и уходит в опись, чтобы
        поставленное можно было повторить и сверить.

        Объявленная ревизия сильнее вычисленной: `local.install.revisions.<repo>` в
        `config/providers.yaml` — это настоящий пин, который делает владелец.
        Источник не назвал ревизию — отказ кодом причины: закрыто автором, репозитория нет, источник не ответил.
        """
        if repo in self._revisions:
            return self._revisions[repo]
        declared = self.pinned(repo)
        if declared:
            self._revisions[repo] = str(declared)
            return self._revisions[repo]
        from huggingface_hub import HfApi

        try:
            sha = HfApi().repo_info(repo_id=repo, repo_type="model").sha
        except Exception as e:
            refusal = self._hub_refusal(repo, e)
            if refusal is None:
                raise
            raise refusal from e
        self._revisions[repo] = str(sha or "")
        return self._revisions[repo]

    def available(self, kind: str, limit: int = 20, describe: bool = False, **overrides) -> list[dict]:
        """Свежий список моделей вида `kind` по объявленным фильтрам."""
        src = self.source(kind)
        filters = {**(src.get("filters") or {}), **{k: v for k, v in overrides.items() if v not in (None, "")}}
        how = str(src.get("kind") or "")
        if how == "piper_index":
            return self._from_piper(src, filters, limit)
        if how == "hf_hub":
            return self._from_hf(src, filters, limit, kind, describe)
        raise ProviderError(
            "SCHEMA_INVALID", f"Неизвестный вид каталога: '{how}'.",
            reason="Поддерживаются piper_index и hf_hub — как читать каталог, это код, "
                   "а какой каталог смотреть, объявляется в config/providers.yaml.")

    def _from_piper(self, src: dict, filters: dict, limit: int) -> list[dict]:
        """Индекс голосов piper: одна запись = голос со своими файлами и размерами."""
        rows = []
        for name, voice in self._piper_voices(src, filters).items():
            rows.append({"id": name, "kind": "tts", "repo": src["repo"], "mb": self._voice_mb(voice),
                         "language": voice.get("language", {}).get("code", ""),
                         "quality": voice.get("quality", ""),
                         "files": sorted(voice.get("files") or {}),
                         "speakers": voice.get("num_speakers", 1)})
        rows.sort(key=lambda r: (r["language"], r["id"]))
        return rows[:limit] if limit else rows          # limit=0 — «без ограничения», а не «ничего»

    def _from_hf(self, src: dict, filters: dict, limit: int, kind: str,
                 describe: bool = False) -> list[dict]:
        """Каталог Hugging Face по задаче: отбираем живое, открытое и влезающее на эту машину."""
        from huggingface_hub import HfApi

        api = HfApi()
        # Задача задаётся либо pipeline_tag, либо тегами. У фона и апскейла pipeline_tag общий с
        # чужими задачами (сегментация одежды, img2img-диффузия), и один он отбирает не то.
        tags = [str(t) for t in (src.get("tags") or [])]
        if src.get("library"):
            tags.append(str(src["library"]))
        # expand отдаёт число параметров ВМЕСТЕ со списком: иначе пришлось бы делать
        # по запросу на модель, а без параметров вопрос «влезет ли» не решается. Свойства вне
        # expand хаб не присылает вовсе (huggingface_hub hf_api.py, list_models) — likes просится явно.
        kwargs = {"sort": "downloads", "limit": max(limit * 4, 40),
                  "expand": ["safetensors", "downloads", "gated", "tags", "likes"]}
        if src.get("pipeline_tag"):
            kwargs["pipeline_tag"] = src["pipeline_tag"]
        if tags:
            kwargs["filter"] = tags
        min_downloads = int(filters.get("min_downloads") or 0)
        want = [str(f).lower() for f in (src.get("require_formats") or [])]
        rows = []
        for model in api.list_models(**kwargs):
            if model.downloads < min_downloads:
                continue
            if filters.get("gated") is False and model.gated:
                continue
            st = getattr(model, "safetensors", None)
            by_dtype = dict(getattr(st, "parameters", None) or {})
            rows.append({"id": model.id, "kind": kind, "repo": model.id,
                         "downloads": model.downloads, "likes": model.likes,
                         "gated": bool(model.gated), "mb": None,
                         # Без safetensors (ONNX, GGUF) хаб числа параметров не называет — None, а не 0.
                         "params_total": getattr(st, "total", None),
                         "params_by_dtype": by_dtype,
                         # Активные параметры объявляет не всякая модель (это про MoE). Нет —
                         # значит нет: выдуманное число здесь хуже честного пропуска.
                         "params_active": self._active_params(model),
                         "license": (model.tags or []) and next(
                             (t.split(":", 1)[1] for t in model.tags if t.startswith("license:")), "")})
            # Требование формата и описание доспрашиваются по модели, и часть кандидатов отпадёт,
            # поэтому берём с запасом: обрезка до limit — уже после отсева.
            if len(rows) >= (limit * 2 if (want or describe) else limit):
                break
        if want or describe:
            rows = self._enrich(rows, want, describe, float(filters.get("max_mb") or 0),
                                float(filters.get("min_mb") or 0))
        return rows[:limit] if limit else rows

    def _enrich(self, rows: list[dict], want: list[str], describe: bool,
                max_mb: float = 0, min_mb: float = 0) -> list[dict]:
        """Дописать то, чего в списке нет: какой файл ляжет, сколько весит и что модель делает."""
        from concurrent.futures import ThreadPoolExecutor

        from core.capacity import Capacity

        with ThreadPoolExecutor(max_workers=Capacity().places("describe")) as pool:
            details = list(pool.map(lambda r: self._detail(r["id"], want, describe), rows))
        out = []
        for row, detail in zip(rows, details, strict=True):
            if want and not detail.get("file"):
                # Нужного формата в репозитории нет — установщик обязан её отклонить, значит
                # и предлагать её нельзя: список из того, что поставить НЕЛЬЗЯ, — обман.
                continue
            # Пустое поле карточки не затирает уже известное: лицензия ONNX-порта живёт в тегах,
            # а его card_data пуста — иначе найденное терялось бы по дороге.
            row = {**row, **{k: v for k, v in detail.items() if v not in (None, "")}}
            # min_mb: файл нужного формата нашёлся, но он посторонний — у LoRA-репозиториев рядом
            # лежит чужой детектор лиц на сотню килобайт, и по нему модель попадала в список.
            if self._outside(row["mb"], min_mb, max_mb):
                continue
            out.append(row)
        return out

    def _detail(self, model_id: str, want: list[str], describe: bool) -> dict:
        """Файлы модели, их размеры и описание — список моделей этого не отдаёт. Размер — сумма тех частей, что поставит
        установка (`_pick`), той же ревизии: закреплённой владельцем, иначе текущей; длина части по ступеням
        `_part_sizes`, не названа ни одной — `mb` пусто и `size_unknown` называет спрошенное."""
        from huggingface_hub import model_info

        try:
            info = model_info(model_id, revision=self.pinned(model_id) or None, files_metadata=True)
        except Exception as e:  # сеть/закрытый репозиторий
            # Не знаем — так и говорим: молча выкинуть модель значило бы соврать, что её нет. Причина — в журнал под номером.
            refusal = self._hub_refusal(model_id, e)
            code = refusal.code if refusal else ""
            return {"detail_error": code or "INTERNAL_ERROR", "event": incident(log, f"детали модели {model_id}", e, code)}
        picked, _, chosen = self._pick([s.rfilename for s in info.siblings or []], tuple(want))
        try:
            sizes, unknown = self._part_sizes(model_id, info, picked)
        except ContractError as e:  # суд адреса или отказ источника на HEAD: строка называет код, а не размер
            return {"detail_error": e.code}
        card_data = getattr(info, "card_data", None)
        card = card_data.to_dict() if card_data else {}
        base = card.get("base_model")
        base = str(base[0]) if isinstance(base, list) and base else str(base or "")
        out: dict[str, object] = {"file": chosen, "base_model": base, "mb": self._mb(sizes.values()) if chosen else None,
                                  "license": str(card.get("license") or "")}
        if unknown:
            out["size_unknown"] = unknown
        if describe:
            out["description"] = self.describe_model(model_id, base)
        return out

    def describe_model(self, repo: str, base: str = "") -> str:
        """Что модель делает, словами её автора. У ONNX-порта карточка вырождена — тогда базовая."""
        rules = self.local.get("describe") or {}
        text = self._prose(repo, rules)
        markers = [str(m).lower() for m in (rules.get("port_markers") or [])]
        if base and (not text or any(m in text.lower() for m in markers)):
            text = self._prose(base, rules) or text
        return text

    PROSE_SKIP = ("#", "!", "<", "|", "-", "*", ">", "[", "`", "=")

    def _prose(self, repo: str, rules: dict) -> str:
        """Первый связный абзац карточки: заголовки, картинки, таблицы и код описанием не считаем."""
        try:
            text = self._small(repo, str(rules.get("from") or "README.md"))
        except Exception:  # карточки может не быть
            return ""
        text = re.sub(r"^---.*?---", "", text, flags=re.S)  # фронт-маттер — не проза
        limit = int(rules.get("max_chars") or 240)
        picked: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(self.PROSE_SKIP):
                continue
            picked.append(line)
            if sum(len(p) for p in picked) >= limit:
                break
        return " ".join(picked)[:limit]

    def prefer(self, names: list[str]) -> str:
        """Какой из равнозначных файлов берём (один граф в разных разрядностях) — по декларации."""
        from fnmatch import fnmatch

        for pattern in (self.install_rules.get("prefer_files") or []):
            for name in names:
                if fnmatch(name, str(pattern)):
                    return name
        return ""

    ACTIVE_FIELDS = ("num_activated_params", "activated_params", "active_params",
                     "num_active_params")

    def _active_params(self, model) -> int:
        """Сколько параметров работает на один проход — если модель сама это объявляет.

        У плотной модели активны все, у MoE — часть, и разница определяет требования к памяти
        по-разному для загрузки и для счёта. Гадать по архитектуре нельзя: вычисленное «на глаз»
        число выглядит как факт. Поэтому берём только объявленное.
        """
        card = getattr(model, "cardData", None) or {}
        config = getattr(model, "config", None) or {}
        for source in (card, config):
            for field in self.ACTIVE_FIELDS:
                value = source.get(field) if isinstance(source, dict) else None
                if isinstance(value, (int, float)) and value > 0:
                    return int(value)
        return 0

    def _piper_index(self, src: dict) -> dict:
        """Индекс голосов piper из объявленной ревизии: имя голоса → язык, качество, файлы с размером и md5."""
        return json.loads(self._small(src["repo"], src.get("index", "voices.json")))

    @classmethod
    def _voice_mb(cls, voice: dict) -> float | None:
        """Сумма длин файлов голоса по индексу; файл без `size_bytes` — сумма неизвестна (None), а не меньше."""
        return cls._mb(f.get("size_bytes") for f in (voice.get("files") or {}).values())

    def _piper_voices(self, src: dict, filters: dict) -> dict:
        """Голоса индекса, прошедшие объявленный отсев (язык, предел размера): имя → запись индекса."""
        language, max_mb = str(filters.get("language") or ""), float(filters.get("max_mb") or 0)
        return {name: voice for name, voice in self._piper_index(src).items()
                if (not language or voice.get("language", {}).get("code") == language)
                and not self._outside(self._voice_mb(voice), 0, max_mb)}

    def pinned(self, repo: str) -> str:
        """Ревизия, объявленная владельцем (`local.install.revisions.<repo>`); не объявлена — пусто."""
        return str(((self.local.get("install") or {}).get("revisions") or {}).get(repo) or "")

    def _pick(self, names: list[str], want: tuple[str, ...]) -> tuple[list[str], list[dict], str]:
        """Что из файлов репозитория ставится — (части, отказ с причиной, главный файл); одна выборка для состава и для
        строки списка. Формат требуется — модель ОДИН граф (рядом те же веса в других разрядностях) и описания `.json`
        для адаптера; иначе половинная точность и полная — одни и те же веса, берётся одна, иначе диск съедается вдвое."""
        keep, refused = self.allowed_files(names)
        if want:
            graphs = [n for n in keep if n.lower().endswith(want)]
            chosen = self.prefer(graphs) or (graphs[0] if graphs else "")
            return ([chosen, *(n for n in keep if n.lower().endswith(".json"))] if chosen else []), refused, chosen
        fp16 = [n for n in keep if ".fp16." in n]
        picked = fp16 + [n for n in keep if not n.endswith(".safetensors")] if fp16 else keep
        return picked, refused, self.prefer(picked) or (picked[0] if picked else "")

    def _hub_refusal(self, repo: str, e: Exception) -> ProviderError | None:
        """Сбой хаба по репозиторию — отказ кодом причины: закрыто автором, репозитория нет, источник ответил ошибкой или
        не ответил; иное исключение — не сбой хаба (None)."""
        import httpx
        from huggingface_hub import constants
        from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

        host = urlparse(constants.ENDPOINT).hostname
        if isinstance(e, GatedRepoError):             # подкласс RepositoryNotFoundError: порядок от частного к общему
            return ProviderError("MODEL_GATED", facts={"model": repo})
        if isinstance(e, RepositoryNotFoundError):
            return ProviderError("MODEL_NOT_IN_CATALOG", facts={"model": repo, "source": host})
        if isinstance(e, HfHubHTTPError):
            return ProviderError("MODEL_REVISION_UNKNOWN",
                                 facts={"model": repo, "host": host, "status": e.response.status_code})
        if isinstance(e, (httpx.HTTPError, OSError)):  # сеть; офлайн-режим hf — ConnectionError; имя класса — в журнал
            return ProviderError("MODEL_SOURCE_UNREACHABLE", facts={"model": repo, "host": host})
        return None

    # ═══ Правила установки ═══

    @property
    def install_rules(self) -> dict:
        return self.local.get("install") or {}

    HF_ID = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")

    def check_repo(self, repo: str) -> None:
        """Источник объявлен? Модель — исполняемое содержимое, откуда угодно её не берём.

        Имя вида `org/name` — это идентификатор на Hugging Face; всё остальное считаем ссылкой
        и смотрим её хост. Перечислять разрешённые репозитории поимённо смысла нет: их тысячи,
        и ограничение должно стоять на ИСТОЧНИКЕ.
        """
        allow = [str(a).lower() for a in (self.install_rules.get("allow_sources") or [])]
        if not allow:
            return
        name = str(repo or "").strip()
        host = "huggingface.co" if self.HF_ID.fullmatch(name) else (urlparse(name).netloc or name).lower()
        if host in allow:
            return
        raise ProviderError("MODEL_SOURCE_FORBIDDEN", facts={"source": str(repo), "allowed": ", ".join(allow)})

    def allowed_files(self, names: list[str]) -> tuple[list[str], list[dict]]:
        """Разделить файлы на «можно ставить» и «отказ с причиной» по объявленным форматам."""
        allow = {str(s).lower() for s in (self.install_rules.get("allow_suffixes") or [])}
        deny = {str(s).lower() for s in (self.install_rules.get("deny_suffixes") or [])}
        keep, refused = [], []
        for name in names:
            suffix = "".join(Path(name).suffixes[-1:]).lower()
            if suffix in deny:
                refused.append({"file": name, "reason": (
                    f"формат {suffix} — это pickle: его загрузка выполняет код из файла, "
                    "поэтому такие веса не ставим даже у популярной модели")})
            elif allow and suffix not in allow:
                refused.append({"file": name, "reason": f"формат {suffix} не объявлен разрешённым"})
            else:
                keep.append(name)
        return keep, refused

    def check_size(self, total_mb: float | None, model_id: str, unknown: dict[str, str] | None = None) -> None:
        """Сумма частей против `max_total_mb`; сумма неизвестна — MODEL_SIZE_UNKNOWN с тем, что спрошено о каждой части
        без длины: «нет данных» — не «влезает»."""
        if total_mb is None:
            raise ProviderError("MODEL_SIZE_UNKNOWN", facts={
                "model": model_id, "parts": ", ".join(unknown or {}),
                "asked": "; ".join(f"{name}: {how}" for name, how in (unknown or {}).items())})
        limit = float(self.install_rules.get("max_total_mb") or 0)
        if limit and total_mb > limit:
            raise ProviderError("MODEL_TOO_LARGE", facts={"model": model_id, "mb": round(total_mb), "limit_mb": round(limit)})


    def место_под(self, model_id: str, kind: str, force: bool = False) -> dict:
        """Хватит ли ДИСКА под загрузку модели — и снимок, по которому её потом качают (`install_planned`).

        Состав снимается здесь один раз: ревизия и план (`judge` по длине — лежащее целым не считается; при явном
        `force` — все части), по которым судили место, и уходят в загрузку. Длина части плана не названа —
        MODEL_SIZE_UNKNOWN, место не прочитано — DISK_SPACE_UNKNOWN: «нет данных» — не «хватает» и не «нет места»;
        места меньше плана — MODEL_NO_DISK_SPACE с обоими числами, до загрузки. Имя того же вида уже принадлежит
        другому репозиторию — MODEL_NAME_TAKEN: модель ищут по имени, и вторая перезаписала бы первую.
        """
        from core.providers.hardware import probe

        self._open_source(kind)
        entry = self.manifest(model_id, kind)
        taken = next((e for e in self.inventory() if e.get("kind") == entry["kind"]
                      and str(e.get("id")) == str(entry["id"]) and e.get("repo") != entry["repo"]), None)
        if taken:
            raise ProviderError("MODEL_NAME_TAKEN",
                                facts={"model": entry["id"], "kind": entry["kind"], "held_by": str(taken.get("repo"))})
        plan = entry["parts"] if force else self.judge(entry)["plan"]
        свободно = probe(self.models_dir)["disk_free_mb"]
        if свободно is None:
            raise ProviderError("DISK_SPACE_UNKNOWN", facts={"model": model_id})
        нужно = round(sum(p["size"] for p in plan) / 1e6, 1)
        if свободно < нужно:
            raise ProviderError("MODEL_NO_DISK_SPACE", facts={"model": model_id, "need_mb": нужно, "free_mb": свободно})
        files = Files(self.models_dir)
        # Что лежало под именами частей до загрузки (inode): вставшую часть `publish` кладёт новым inode — без часов.
        marks = {rel: (files.place(rel).stat().st_ino if files.place(rel).is_file() else None)
                 for rel in (self.landing(entry, p) for p in plan)}
        return {"нужно_мб": нужно, "свободно_мб": float(свободно), "entry": entry, "plan": plan, "marks": marks,
                "force": force}

    def with_fit(self, rows: list[dict], hardware: dict | None = None) -> list[dict]:
        """Дописать к строкам вердикт «влезет ли на эту машину»."""
        from .hardware import FitEstimator, probe

        from .hardware import OVERSIZE_MODES, compute_device

        hw = hardware or probe(self.models_dir, self.local.get("gpu"))
        est = FitEstimator(self.local.get("fit") or {})
        pool, idle = est.pool(hw), est.idle_gpu(hw)
        # Вердикт считается в той разрядности, в какой модель поднимут на выбранном устройстве:
        # веса fp32 на диске занимают вдвое меньше, если грузятся в fp16 (так на карте).
        gpu_rules = self.local.get("gpu") or {}
        load = compute_device(gpu_rules, hw=hw)["dtype"]
        gpu_load = str((gpu_rules.get("dtype") or {}).get("gpu", ""))
        oversize = str(gpu_rules.get("oversize") or "error").lower()
        out = []
        for row in rows:
            pbd, ptotal = row.get("params_by_dtype") or {}, row.get("params_total")
            need = est.bytes_for(pbd, ptotal, as_dtype=load)
            if need is None and row.get("mb"):
                # Число параметров источник не сообщил — тогда мерим тем, что известно: размером
                # весов на диске. Это грубее, но лучше, чем промолчать про пригодность вовсе.
                need = int(float(row["mb"]) * 1e6 * est.overhead)
            fit = {**est.verdict(need, pool["available_mb"], pool["device"]), "dtype": load}
            if fit["verdict"] == "no" and oversize != "error":
                # «Не влезает» — не приговор: библиотека умеет держать на устройстве по одной
                # части. Молчать об этом значит советовать модель полегче там, где она не нужна.
                fit["oversize"] = {"mode": oversize, "note": OVERSIZE_MODES.get(
                    oversize, f"режим '{oversize}' не объявлен")}
            if idle and need is not None:
                # Карта на машине есть, но её память расчёту недоступна: показываем, что было бы
                # на ней, и чем это перекрыто, — иначе «no» на ОЗУ выглядит приговором железу.
                need_gpu = est.bytes_for(pbd, ptotal, as_dtype=gpu_load)
                need_gpu = need if need_gpu is None else need_gpu
                fit["on_gpu"] = {**est.verdict(need_gpu, idle["vram_free_mb"], idle["name"]),
                                 "dtype": gpu_load, "usable": False, "blocked_by": idle["why"]}
            out.append({**row, "fit": fit})
        return out

    # ═══ Установка ═══

    def restore(self, entry: dict, force: bool = False, progress=lambda _m: None) -> dict:
        """Восстановить модель описи по плану разницы: докачать недостающее, заменить расходящееся.

        Запись без состава (поставлена до него) получает состав с источника по имени и виду — диск сверяется с ним.
        """
        kind, fresh = str(entry.get("kind") or ""), not entry.get("parts")
        self._open_source(kind)
        if fresh:
            piper = str(self.source(kind).get("kind") or "") == "piper_index"
            entry = self.manifest(str(entry["id"] if piper else entry["repo"]), kind)
        disk = self.judge(entry, deep=True)
        plan = entry["parts"] if force else disk["plan"]
        if plan:
            return self._install(entry, plan, progress, force=force)
        if fresh:
            self._remember(entry)
        return {**entry, "disk": disk, "fetched": []}

    def _open_source(self, kind: str) -> None:
        """Источник вида разрешён декларацией — до первого запроса к источнику."""
        self.check_repo(self.source(kind).get("repo") or "huggingface.co")

    def _install(self, entry: dict, parts: list[dict], progress, mark=None, held=lambda: None, pulse=None,
                 force: bool = False) -> dict:
        """Скачать части плана, записать состав в опись и судить по отпечатку: расхождение — отказ кодом причины.

        Модель ставит одна установка на все процессы (MF-10): замок по владельцу её остатков. Занят — ждём, пока держатель
        не отпустит (умер — замок снимает ядро); с `pulse` — пока задание не снято (`pulse.cancelled`), снято — отказ
        снятия (`pulse.refusal`). Дождались — план пересуживается под замком по длине, как в `место_под`: части,
        поставленные держателем, второй раз не качаются (`force` — все части). `held()` — замок взят. Порядок замков:
        модель → опись. Состав ложится в опись и при отказе загрузчика: `pull` знает, что чинить, и докачивает свой
        остаток (MF-04). `mark()` — признак работы для наблюдателя: загрузка и сверка отмечают себя по ходу.
        """
        lock = Files(self.models_dir).lock(self.owner(entry))
        try:
            hold = lock.acquire(timeout=0)
        except Timeout:
            progress("жду замок модели: её ставит другая установка")
            try:
                hold = lock.acquire(timeout=-1, cancel_check=pulse.cancelled.is_set if pulse else None)
            except Timeout:
                raise pulse.refusal() from None
            if not force:
                parts = self.judge(entry)["plan"]
        with hold:
            held()
            try:
                self._pull(entry, parts, progress, mark)
            finally:
                self._remember(entry)
            disk = self.judge(entry, deep=True, mark=mark)
        if disk["verdict"] != "ready":
            raise self.refusal(entry, disk)
        return {**entry, "disk": disk, "fetched": [p["path"] for p in parts]}

    def _pull(self, entry: dict, parts: list[dict], progress, mark=None) -> None:
        """Части плана — одним загрузчиком туда, где их судит `judge`: свой остаток докачивается, отпечаток сверяется до
        того, как часть встанет под имя; заголовки источника уходят только на его хост."""
        fetcher, files, owner, headers = self.fetcher(mark), Files(self.models_dir), self.owner(entry), self._hf_headers()
        for part in parts:
            progress(f"тяну {part['path']}")
            fetcher.fetch(self._source_url(entry["repo"], entry["revision"], part["source"]), files,
                          self.landing(entry, part), owner, size=part["size"], digest=part["digest"] or "",
                          algo=part["algo"] or "sha256", headers=headers)

    def fetcher(self, mark=None) -> Fetcher[FetchPolicy]:
        """Загрузчик весов по объявленной политике `local.install.fetch` — объект схемы двери: битый раздел — отказ
        кодом двери с путём ключа, а не умолчание."""
        return Fetcher(self._decl.section(*MODEL_FETCH.section, schema=MODEL_FETCH.schema), transport=self.transport,
                       resolve=self.resolve, mark=mark, slot=MODEL_FETCH)

    def speed(self) -> dict:
        """Мера работы тела из политики загрузчика весов (NI-01): та же пара судит приём и признак работы установки."""
        policy = self.fetcher().policy
        return {"floor": policy.speed_floor_bytes_per_sec, "window": policy.speed_window_sec}

    def install_policy(self) -> InstallPolicy:
        """Раздел `local.install` — объект схемы двери: битый раздел — отказ кодом двери с путём ключа."""
        return self._decl.section(*MODEL_INSTALL.section, schema=MODEL_INSTALL.schema)

    @staticmethod
    def owner(entry: dict) -> str:
        """Владелец остатков модели — один на вид и имя: следующая установка той же модели докачивает (MF-04)."""
        return re.sub(r"[^\w-]", "_", f"{entry.get('kind')}-{entry.get('id')}")

    @staticmethod
    def landing(entry: dict, part: dict) -> str:
        """Куда ложится часть — от каталога весов: там же её судит `judge`."""
        return str(Path(str(entry.get("dest") or "")) / part["path"])

    def on_disk(self, место: dict) -> int:
        """Сколько байт плана уже легло: свои остатки частей и части, вставшие под имя после снимка (FS-10)."""
        entry, files, total = место["entry"], Files(self.models_dir), 0
        for part in место["plan"]:
            rel = self.landing(entry, part)
            remnant, target = files.remnant(rel, self.owner(entry)), files.place(rel)
            if remnant.is_file():
                total += remnant.stat().st_size
            elif target.is_file() and target.stat().st_ino != место["marks"].get(rel):
                total += target.stat().st_size
        return total

    @staticmethod
    def _source_url(repo: str, revision: str, name: str) -> str:
        from huggingface_hub import hf_hub_url
        return hf_hub_url(repo, name, revision=revision)

    @staticmethod
    def _hf_headers() -> dict:
        from huggingface_hub.utils import build_hf_headers
        return build_hf_headers()

    def _small(self, repo: str, name: str) -> str:
        """Малый файл источника той же ревизии (индекс голосов, карточка): лежит — без сети, нет — тем же
        загрузчиком."""
        revision, files = self.revision(repo), Files(self.cache_dir)
        rel = f"{repo}/{revision}/{name}"
        with files.lock(rel):           # остаток владельца "source" общий: второй писатель ждёт и читает файл первого
            if not files.place(rel).is_file():
                self.fetcher().fetch(self._source_url(repo, revision, name), files, rel, "source",
                                     headers=self._hf_headers())
        return files.place(rel).read_text(encoding="utf-8")

    def _remember(self, entry: dict) -> None:
        """Дописать в опись, заменив прежнюю запись того же имени (второй копии не заводим). Опись общая для всех моделей
        и процессов: «прочитать — изменить — записать» под её замком (FS-09), замена целиком через дверь слоя — обрыв
        оставляет прежнюю опись, а не половину (FS-06)."""
        import yaml
        files = Files(self.inventory_file.parent)
        with files.lock(self.inventory_file.name):
            rows = [r for r in self.inventory() if str(r.get("id")) != str(entry["id"])]
            rows.append({k: v for k, v in entry.items() if k != "refused"})
            files.replace(self.inventory_file.name, yaml.safe_dump({"models": rows}, allow_unicode=True, sort_keys=False))

    def manifest(self, model_id: str, kind: str) -> dict:
        """Состав модели: части с длиной и отпечатком из объявленного поля источника; весь отбор файлов — здесь.

        Отпечаток — целостность (обрыв, порча), а не подлинность: от подмены защищают https и суд адреса.
        Части отсортированы по пути: тот же ответ источника даёт тот же состав.
        """
        src = self.source(kind)
        if str(src.get("kind") or "") == "piper_index":
            return self._manifest_piper(model_id, src)
        return self._manifest_hf(model_id, src, kind)

    @staticmethod
    def _part(path: str, source: str, size, digest, algo) -> dict:
        """Часть состава; источник не назвал длину или отпечаток — они неизвестны (None), а не ноль и не пусто."""
        return {"path": path, "source": source, "size": None if size is None else int(size),
                "digest": digest or None, "algo": algo if digest else None}

    @staticmethod
    def _sibling_digest(sibling) -> tuple:
        """Отпечаток файла из ответа model_info: LFS — sha256 содержимого; иначе id git-блоба (sha1 от
        «blob <длина>\\0» и содержимого); нет ни того, ни другого — неизвестен."""
        lfs = getattr(sibling, "lfs", None)
        sha = getattr(lfs, "sha256", None) or (lfs.get("sha256") if isinstance(lfs, dict) else None)
        if sha:
            return sha, "sha256"
        blob = getattr(sibling, "blob_id", None)
        return (blob, "git-blob-sha1") if blob else (None, None)

    def _manifest_piper(self, model_id: str, src: dict) -> dict:
        voice = self._piper_voices(src, dict(src.get("filters") or {})).get(model_id)
        if not voice:
            raise ProviderError("MODEL_NOT_IN_CATALOG", facts={"model": model_id, "source": src["repo"]})
        files = voice.get("files") or {}
        keep, refused = self.allowed_files(sorted(files))
        mb = self._voice_mb(voice)
        self.check_size(mb, model_id, {n: "индекс голосов — без size_bytes" for n in sorted(files)
                                       if files[n].get("size_bytes") is None})
        dest = str(src.get("dest") or "tts")
        parts = sorted((self._part(Path(n).name, n, files[n].get("size_bytes"), files[n].get("md5_digest"), "md5")
                        for n in keep), key=lambda p: p["path"])
        graph = next((p["path"] for p in parts if p["path"].endswith(".onnx")), parts[0]["path"] if parts else "")
        return {"id": model_id, "kind": "tts", "repo": src["repo"], "revision": self.revision(src["repo"]),
                "dest": dest, "path": Files(self.models_dir).name(Path(dest) / graph), "mb": mb, "parts": parts,
                "refused": refused}

    def _manifest_hf(self, model_id: str, src: dict, kind: str) -> dict:
        from huggingface_hub import model_info

        rev = self.revision(model_id)
        # Список файлов и сами файлы обязаны быть из ОДНОГО состояния репозитория.
        info = model_info(model_id, revision=rev, files_metadata=True)
        if getattr(info, "gated", False):
            raise ProviderError("MODEL_GATED", facts={"model": model_id})
        siblings = {s.rfilename: s for s in (info.siblings or [])}
        name, want = model_id.split("/")[-1], tuple(str(f).lower() for f in (src.get("require_formats") or []))
        picked, refused, chosen = self._pick(list(siblings), want)
        if want and not chosen:
            raise ProviderError("MODEL_FORMAT_MISSING", facts={"model": model_id, "formats": ", ".join(want)})
        dest = Path(str(src.get("dest") or (kind if want else "img"))) / name
        sizes, unknown = self._part_sizes(model_id, info, picked)
        parts = sorted((self._part(Path(n).name if want else n, n, sizes[n],
                                   *self._sibling_digest(siblings[n])) for n in picked), key=lambda p: p["path"])
        mb = self._mb(sizes.values())
        self.check_size(mb, model_id, unknown)
        files = Files(self.models_dir)
        entry = {"id": name, "kind": kind, "repo": model_id, "revision": rev, "dest": files.name(dest), "mb": mb,
                 "parts": parts, "refused": refused}
        if want:
            return {**entry, "path": files.name(dest / Path(chosen).name), "file": Path(chosen).name}
        st = getattr(info, "safetensors", None)
        return {**entry, "path": entry["dest"], "variant": "fp16" if any(".fp16." in n for n in picked) else "",
                # Число параметров — в опись сразу: по нему судят о пригодности железа без запроса к сети.
                "params_total": getattr(st, "total", None),
                "params_by_dtype": dict(getattr(st, "parameters", None) or {}),
                "params_active": self._active_params(info)}

    def install_planned(self, место: dict, progress=lambda _m: None, mark=None, held=lambda: None, pulse=None) -> dict:
        """Поставить модель по снимку `место_под`: скачать его план, записать состав в опись и судить по отпечатку.

        Источник заново не спрашивается: качаются ровно та ревизия и тот план, по которым судили место. Замок модели
        держит другая установка — план после ожидания пересуживается под замком (`_install`), её части не качаются
        второй раз; `pulse` — суд тишины ожидания.
        """
        return self._install(место["entry"], место["plan"], progress, mark, held, pulse=pulse, force=место["force"])

    def _part_sizes(self, repo: str, info, names: list[str]) -> tuple[dict[str, int | None], dict[str, str]]:
        """Длина частей `names` по ступеням (MS-04): названная ответом `model_info(files_metadata)`, иначе HEAD без тела
        тем же загрузчиком той же ревизии, иначе неизвестна — None, а не ноль. Длину файла за переходом на CDN несёт
        заголовок, объявленный видом источника (`local.source_kinds.hf_hub.size_header`). Исход — (путь → байты или
        None, путь без длины → что спрошено)."""
        named = {s.rfilename: s.size for s in info.siblings or []}
        sizes: dict[str, int | None] = {n: None if named[n] is None else int(named[n]) for n in names}
        blind = [n for n in names if sizes[n] is None]
        if not blind:
            return sizes, {}
        header = self._decl.need("local", "source_kinds", "hf_hub", "size_header", kind=str)
        fetcher, headers, revision, unknown = self.fetcher(), self._hf_headers(), self.revision(repo), {}
        for name in blind:
            sizes[name], how = fetcher.length(self._source_url(repo, revision, name), headers, linked=header)
            if sizes[name] is None:
                unknown[name] = f"метаданные источника — без длины; {how}"
        return sizes, unknown

    @staticmethod
    def _mb(sizes) -> float | None:
        """Сумма длин в МБ; хоть одна длина неизвестна — сумма неизвестна (None), а не меньше."""
        known = list(sizes)
        return None if None in known else round(sum(known) / 1e6, 1)

    @staticmethod
    def _outside(mb: float | None, low: float, high: float) -> bool:
        """Сумма вне объявленных пределов отсева (0 — предела нет); неизвестная — не вне: «нет данных» — не «больше» и не
        «меньше», а установка откажет MODEL_SIZE_UNKNOWN."""
        return mb is not None and bool((high and mb > high) or (low and mb < low))


class OnlineCatalog:
    """Какие модели умеет вызвать шлюз и каким эндпоинтом. Ключ для СПИСКА не нужен.

    С ключом список можно уточнить у самого провайдера (`live`): реестр шлюза знает то, что знал
    на день сборки, и новая модель появляется у провайдера раньше. Тогда роли делятся так —
    провайдер говорит, ЧТО ему доступно сегодня, реестр добавляет к именам смысл (режим, цену).
    """

    MODES: ClassVar[dict[str, str]] = {
        "image": "image_generation", "tts": "audio_speech", "stt": "audio_transcription"}
    # Сеть ответов провайдера (MF-16): None — настоящий транспорт; подмена — на классе, ею же ходит спека модели.
    transport: ClassVar = None
    resolve: ClassVar = staticmethod(socket.getaddrinfo)

    def __init__(self, config_file: str | Path | None = None):
        self.config_file = Path(config_file) if config_file else None
        self._decl = Declaration(
            config_file or Path("providers.yaml"), ProviderError, "провайдеров",
            "Заведи config/providers.yaml — где спрашивать список моделей, объявлено там.")

    @property
    def config(self) -> dict:
        return (self._decl.data.get("online") or {}) if self.config_file else {}

    def _kind_of(self, model_id: str) -> str:
        """Вид ресурса по имени модели: в сыром списке провайдера режима вызова нет."""
        name = str(model_id).lower()
        for kind, patterns in (self.config.get("kinds") or {}).items():
            if any(str(p).lower() in name for p in patterns):
                return kind
        return ""

    def live(self, provider: str, api_key: str, kind: str = "") -> list[dict]:
        """Спросить провайдера, что ему доступно СЕГОДНЯ. Ключ уходит только на объявленный адрес."""
        decl = (self.config.get("live") or {}).get(provider)
        if not decl:
            known = ", ".join(sorted(self.config.get("live") or {})) or "(никто)"
            raise ProviderError(
                "PROVIDER_ADAPTER_MISSING", f"Для '{provider}' не объявлено, где спрашивать список.",
                reason=f"Объявлены: {known}. Добавь адрес в config/providers.yaml → online.live, "
                       "если нужен ещё один провайдер.")
        url = self._decl.need("online", "live", provider, "url", kind=ProviderUrl)
        if not url.lower().startswith("https://"):
            # Ключ в открытом канале виден любому посреднику — это отказ, а не предупреждение.
            raise ProviderError("DOWNLOAD_FORBIDDEN", facts={"host": urlparse(url).hostname or url, "allowed": "https"})
        headers = self._auth_headers(str(decl.get("auth") or "bearer"), api_key)
        items = self.fetcher(url).answer(
            url, headers, refused=lambda response: provider_refusal(response, provider, "запрос списка"))
        path = str(decl.get("items") or "")
        if path:
            items = items.get(path) or []
        id_field = str(decl.get("id_field") or "id")
        rows = []
        for item in items:
            name = str(item.get(id_field) or "") if isinstance(item, dict) else str(item)
            if not name:
                continue
            of_kind = self._kind_of(name)
            if kind and of_kind != kind:
                continue
            rows.append({"id": name, "provider": provider, "kind": of_kind, "source": "provider"})
        rows.sort(key=lambda r: r["id"])
        return rows

    @staticmethod
    def _auth_headers(scheme: str, api_key: str) -> dict:
        """Как провайдер принимает ключ — объявлено; в коде только две формы, обе безымянные."""
        if scheme.startswith("header:"):
            return {scheme.split(":", 1)[1]: api_key}
        return {"Authorization": f"Bearer {api_key}"}

    def merge(self, live_rows: list[dict], kind: str = "") -> list[dict]:
        """Список провайдера + смысл из реестра шлюза: режим, эндпоинт, цена.

        Модель, которой в реестре нет (свежая линейка), не выбрасывается — она помечается
        `known_to_gateway: false`. Иначе новинка была бы невидима именно тогда, когда нужна.
        """
        registry = {r["id"].split("/")[-1]: r for r in self.available(kind, limit=0)}
        out = []
        for row in live_rows:
            known = registry.get(row["id"])
            out.append({**row, "known_to_gateway": bool(known),
                        "mode": (known or {}).get("mode", ""),
                        "endpoints": (known or {}).get("endpoints", []),
                        "cost_per_image": (known or {}).get("cost_per_image"),
                        "cost_per_character": (known or {}).get("cost_per_character")})
        return out

    def available(self, kind: str = "", provider: str = "", limit: int = 30) -> list[dict]:
        try:
            import litellm
        except ImportError as e:                            # pragma: no cover — есть в зависимостях
            raise ProviderError(
                "PROVIDER_ADAPTER_MISSING", f"Шлюз онлайн-моделей недоступен: {e}",
                reason="Поставь зависимости сервера (pip install -e .).") from e
        want = self.MODES.get(kind, kind)
        rows = []
        for name, entry in (litellm.model_cost or {}).items():
            if want and entry.get("mode") != want:
                continue
            if provider and entry.get("litellm_provider") != provider:
                continue
            rows.append({
                "id": name, "provider": entry.get("litellm_provider", ""),
                "mode": entry.get("mode", ""),
                "endpoints": entry.get("supported_endpoints") or [],
                "cost_per_image": entry.get("output_cost_per_image"),
                "cost_per_character": entry.get("input_cost_per_character"),
            })
        rows.sort(key=lambda r: (r["provider"], r["id"]))
        return rows[:limit] if limit else rows

    def providers(self, kind: str = "") -> list[str]:
        """Кто из провайдеров вообще умеет этот вид ресурса — прежде чем заводить ключ."""
        return sorted({r["provider"] for r in self.available(kind, limit=0) if r["provider"]})

    def fetcher(self, url: str) -> Fetcher[AnswerPolicy]:
        """Загрузчик ответа провайдера в память — одна политика `online.answer` для списка (`live`) и спеки модели
        (`ModelSpec`); хост — хост объявленного адреса `url`."""
        return answers(self._decl, ONLINE_ANSWER, url, transport=self.transport, resolve=self.resolve)


def provider_refusal(response, provider: str, subject: str) -> ProviderError:
    """Отказ провайдера (ответ ≥400, тело прочитано дверью в пределе) его причиной — одна фраза на все обращения к
    провайдерам; `subject` — что у него просили («запрос списка», «вызов»): ключ отклонён (401, 403) — AUTH_FAILED с
    рецептом замены ключа, иначе — PROVIDER_FAILED с кодом и цитатой ответа."""
    status = response.status_code
    if status in (401, 403):
        return ProviderError(
            "AUTH_FAILED", f"Провайдер отклонил ключ в ответ на {subject} ({status}).",
            reason="Ключ недействителен или у него нет прав. Заменить может только владелец: "
                   f"python scripts/set_provider_key.py --channel <канал> --provider {provider}")
    return ProviderError(
        "PROVIDER_FAILED", f"Провайдер ответил {status} на {subject}: {response.text[:300]}",
        reason="Это ответ самого сервиса — в нём причина отказа (модерация, исчерпанный платный лимит, неподходящий "
               "файл); повтор не лечит её, пока причина не снята.")
