"""
core/providers/catalog.py — какие модели вообще бывают и какие из них можно поставить.

## Зачем не «список в документации»
Модели появляются и устаревают быстрее, чем правится любой файл в репозитории. Поэтому список
берётся ЖИВЫМ из каталогов (индекс голосов piper, Hugging Face по задаче, реестр LiteLLM), а
здесь — только правила отбора: где смотреть и что считать лишним. Отсев объявлен в
`config/providers.yaml → local.sources.*.filters`, значит меняется без правки кода.

## Онлайн и локально — разные вопросы
- **Онлайн:** какие модели вообще умеет вызвать шлюз и каким эндпоинтом. Ответ уже есть офлайн,
  в реестре LiteLLM: режим (`image_generation`/`audio_speech`), провайдер, цена. Ключ для СПИСКА
  не нужен — он нужен для вызова.
- **Локально:** что можно скачать и запустить на этой машине. Здесь важны размер, лицензия и
  формат весов.

## Установка = исполняемое содержимое
`.bin/.ckpt/.pt` — это pickle: его загрузка ВЫПОЛНЯЕТ код из файла. Поэтому ставим только
форматы, кодом не являющиеся (`.safetensors`, `.onnx`), из объявленных источников и не больше
объявленного размера. Популярность модели тут не аргумент.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .declaration import Declaration
from .resolver import ProviderError


class ModelCatalog:
    """Живые каталоги моделей + правила отбора и установки из декларации."""

    def __init__(self, config_file: str | Path, models_dir: str | Path):
        self.config_file = Path(config_file)
        self.models_dir = Path(models_dir)
        self._decl = Declaration(
            config_file, ProviderError, "провайдеров",
            "Заведи config/providers.yaml — где искать модели, объявлено там.")

    @property
    def config(self) -> dict:
        return self._decl.data

    @property
    def local(self) -> dict:
        return self.config.get("local") or {}

    def source(self, kind: str) -> dict:
        src = (self.local.get("sources") or {}).get(kind)
        if not src:
            declared = ", ".join(sorted((self.local.get("sources") or {}))) or "(ничего)"
            raise ProviderError(
                "TEMPLATE_NOT_FOUND", f"Для вида '{kind}' не объявлен каталог моделей.",
                reason=f"Объявлены: {declared}. Добавь источник в config/providers.yaml → "
                       "local.sources, если нужен ещё один вид.")
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
        import yaml
        if not self.inventory_file.exists():
            return []
        data = yaml.safe_load(self.inventory_file.read_text(encoding="utf-8")) or {}
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
        """Модели, известные серверу, с отметкой, лежат ли веса на диске."""
        out = []
        for entry in self.entries():
            path = self.models_dir / str(entry.get("path") or entry.get("dest") or "")
            files = [f for f in path.rglob("*") if f.is_file()] if path.is_dir() else (
                [path] if path.is_file() else [])
            out.append({"id": entry.get("id"), "kind": entry.get("kind"), "repo": entry.get("repo"),
                        "path": str(entry.get("path") or entry.get("dest") or ""),
                        "present": bool(files),
                        "mb": round(sum(f.stat().st_size for f in files) / 1e6, 1),
                        "params_total": int(entry.get("params_total") or 0),
                        "params_by_dtype": dict(entry.get("params_by_dtype") or {}),
                        "params_active": int(entry.get("params_active") or 0)})
        return out

    # ═══ Что доступно (живые каталоги) ═══

    def available(self, kind: str, limit: int = 20, **overrides) -> list[dict]:
        """Свежий список моделей вида `kind` по объявленным фильтрам."""
        src = self.source(kind)
        filters = {**(src.get("filters") or {}), **{k: v for k, v in overrides.items() if v not in (None, "")}}
        how = str(src.get("kind") or "")
        if how == "piper_index":
            return self._from_piper(src, filters, limit)
        if how == "hf_hub":
            return self._from_hf(src, filters, limit)
        raise ProviderError(
            "SCHEMA_INVALID", f"Неизвестный вид каталога: '{how}'.",
            reason="Поддерживаются piper_index и hf_hub — как читать каталог, это код, "
                   "а какой каталог смотреть, объявляется в config/providers.yaml.")

    def _from_piper(self, src: dict, filters: dict, limit: int) -> list[dict]:
        """Индекс голосов piper: одна запись = голос со своими файлами и размерами."""
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(src["repo"], src.get("index", "voices.json"))
        index = json.loads(Path(path).read_text(encoding="utf-8"))
        language = str(filters.get("language") or "")
        max_mb = float(filters.get("max_mb") or 0)
        rows = []
        for name, voice in index.items():
            if language and voice.get("language", {}).get("code") != language:
                continue
            mb = round(sum(f.get("size_bytes", 0) for f in (voice.get("files") or {}).values()) / 1e6, 1)
            if max_mb and mb > max_mb:
                continue
            rows.append({"id": name, "kind": "tts", "repo": src["repo"], "mb": mb,
                         "language": voice.get("language", {}).get("code", ""),
                         "quality": voice.get("quality", ""),
                         "files": sorted(voice.get("files") or {}),
                         "speakers": voice.get("num_speakers", 1)})
        rows.sort(key=lambda r: (r["language"], r["id"]))
        return rows[:limit] if limit else rows          # limit=0 — «без ограничения», а не «ничего»

    def _from_hf(self, src: dict, filters: dict, limit: int) -> list[dict]:
        """Каталог Hugging Face по задаче: отбираем живое, открытое и влезающее на эту машину."""
        from huggingface_hub import HfApi

        api = HfApi()
        # expand отдаёт число параметров ВМЕСТЕ со списком: иначе пришлось бы делать
        # по запросу на модель, а без параметров вопрос «влезет ли» не решается.
        kwargs = {"pipeline_tag": src.get("pipeline_tag"), "sort": "downloads",
                  "limit": max(limit * 4, 40),
                  "expand": ["safetensors", "downloads", "gated", "tags"]}
        if src.get("library"):
            kwargs["filter"] = src["library"]
        min_downloads = int(filters.get("min_downloads") or 0)
        rows = []
        for model in api.list_models(**kwargs):
            downloads = int(model.downloads or 0)
            if downloads < min_downloads:
                continue
            if filters.get("gated") is False and model.gated:
                continue
            st = getattr(model, "safetensors", None)
            by_dtype = dict(getattr(st, "parameters", None) or {})
            rows.append({"id": model.id, "kind": "image", "repo": model.id,
                         "downloads": downloads, "likes": int(model.likes or 0),
                         "gated": bool(model.gated), "mb": None,
                         "params_total": int(getattr(st, "total", 0) or 0),
                         "params_by_dtype": by_dtype,
                         # Активные параметры объявляет не всякая модель (это про MoE). Нет —
                         # значит нет: выдуманное число здесь хуже честного пропуска.
                         "params_active": self._active_params(model),
                         "license": (model.tags or []) and next(
                             (t.split(":", 1)[1] for t in model.tags if t.startswith("license:")), "")})
            if len(rows) >= limit:
                break
        return rows

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
        raise ProviderError(
            "LOCAL_MODEL_MISSING", f"Источник '{repo}' не объявлен разрешённым.",
            reason=f"Ставим только из объявленных источников: {', '.join(allow)}. "
                   "Список правится в config/providers.yaml → local.install.allow_sources; "
                   "веса — исполняемое содержимое, и брать их откуда попало сервер не станет.")

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

    def check_size(self, total_mb: float) -> None:
        limit = float(self.install_rules.get("max_total_mb") or 0)
        if limit and total_mb > limit:
            raise ProviderError(
                "LOCAL_MODEL_MISSING",
                f"Модель весит {round(total_mb)} МБ при объявленном пределе {round(limit)} МБ.",
                reason="Подними local.install.max_total_mb в config/providers.yaml, если такой "
                       "размер осмыслен на этой машине, или возьми модель полегче.")


    def with_fit(self, rows: list[dict], hardware: dict | None = None) -> list[dict]:
        """Дописать к строкам вердикт «влезет ли на эту машину»."""
        from .hardware import FitEstimator, probe

        hw = hardware or probe(self.models_dir, self.local.get("gpu"))
        est = FitEstimator(self.local.get("fit") or {})
        pool, idle = est.pool(hw), est.idle_gpu(hw)
        out = []
        for row in rows:
            need = est.bytes_for(row.get("params_by_dtype") or {}, row.get("params_total") or 0)
            if not need and row.get("mb"):
                # Число параметров источник не сообщил — тогда мерим тем, что известно: размером
                # весов на диске. Это грубее, но лучше, чем промолчать про пригодность вовсе.
                need = int(float(row["mb"]) * 1e6 * est.overhead)
            fit = est.verdict(need, pool["available_mb"], pool["device"])
            if idle and need:
                # Карта на машине есть, но её память расчёту недоступна: показываем, что было бы
                # на ней, и чем это перекрыто, — иначе «no» на ОЗУ выглядит приговором железу.
                fit["on_gpu"] = {**est.verdict(need, idle["vram_free_mb"], idle["name"]),
                                 "usable": False, "blocked_by": idle["why"]}
            out.append({**row, "fit": fit})
        return out

    # ═══ Установка ═══

    def install(self, model_id: str, kind: str, progress=lambda _m: None) -> dict:
        """Поставить модель: проверить источник, формат и размер, скачать, записать в опись."""
        src = self.source(kind)
        self.check_repo(src.get("repo") or "huggingface.co")
        how = str(src.get("kind") or "")
        entry = (self._install_piper(model_id, src, progress) if how == "piper_index"
                 else self._install_hf(model_id, src, progress))
        self._remember(entry)
        return entry

    def _install_piper(self, model_id: str, src: dict, progress) -> dict:
        from huggingface_hub import hf_hub_download

        candidates = [v for v in self.available("tts", limit=0, language="") if v["id"] == model_id]
        if not candidates:
            raise ProviderError(
                "LOCAL_MODEL_MISSING", f"Голоса '{model_id}' нет в каталоге {src['repo']}.",
                reason="Посмотри доступные: python scripts/models.py local --kind tts.",
                suggested_tool="media_models")
        voice = candidates[0]
        keep, refused = self.allowed_files(voice["files"])
        self.check_size(voice["mb"])
        dest = Path(str(src.get("dest") or "tts"))
        target_dir = self.models_dir / dest
        target_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        for remote in keep:
            progress(f"тяну {Path(remote).name}")
            got = hf_hub_download(src["repo"], remote)
            shutil.copyfile(got, target_dir / Path(remote).name)
        model_file = next((f for f in keep if f.endswith(".onnx")), keep[0] if keep else "")
        return {"id": model_id, "kind": "tts", "repo": src["repo"],
                "path": str(dest / Path(model_file).name), "mb": voice["mb"], "refused": refused}

    def _install_hf(self, model_id: str, src: dict, progress) -> dict:
        from huggingface_hub import model_info, snapshot_download

        info = model_info(model_id, files_metadata=True)
        if getattr(info, "gated", False):
            raise ProviderError(
                "LOCAL_MODEL_MISSING", f"Модель '{model_id}' закрыта (gated) — нужен доступ автора.",
                reason="Выбери открытую модель: python scripts/models.py local --kind image.")
        names = [s.rfilename for s in (info.siblings or [])]
        keep, refused = self.allowed_files(names)
        sizes = {s.rfilename: (s.size or 0) for s in (info.siblings or [])}
        # Половинная точность и полная — одни и те же веса; берём одну, иначе диск съедается вдвое.
        variant = [n for n in keep if ".fp16." in n]
        chosen = variant + [n for n in keep if not n.endswith(".safetensors")] if variant else keep
        total_mb = round(sum(sizes.get(n, 0) for n in chosen) / 1e6, 1)
        self.check_size(total_mb)
        dest = Path(str(src.get("dest") or "img")) / model_id.split("/")[-1]
        progress(f"тяну {len(chosen)} файлов, ~{round(total_mb)} МБ")
        snapshot_download(repo_id=model_id, local_dir=str(self.models_dir / dest),
                          allow_patterns=chosen or None)
        st = getattr(info, "safetensors", None)
        return {"id": model_id.split("/")[-1], "kind": "image", "repo": model_id,
                "path": str(dest), "mb": total_mb, "refused": refused,
                "variant": "fp16" if variant else "",
                # Число параметров кладём в опись сразу: потом оно понадобится, чтобы судить
                # о пригодности железа, а спрашивать сеть ради уже стоящей модели незачем.
                "params_total": int(getattr(st, "total", 0) or 0),
                "params_by_dtype": dict(getattr(st, "parameters", None) or {}),
                "params_active": self._active_params(info)}

    def install_declared(self, entry: dict, progress=lambda _m: None) -> dict:
        """Поставить модель, объявленную в конфиге: тянем ровно то, что там перечислено.

        Отличие от `install`: источник и файлы уже названы владельцем в декларации, каталог
        живых моделей не опрашивается — клон репозитория должен подниматься и без сети к нему.
        """
        import shutil

        from huggingface_hub import hf_hub_download, snapshot_download

        self.check_repo(str(entry.get("repo") or ""))
        dest = self.models_dir / str(entry.get("dest") or "")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if entry.get("snapshot"):
            progress(f"тяну снимок {entry.get('repo')}")
            snapshot_download(repo_id=str(entry["repo"]), local_dir=str(dest),
                              allow_patterns=entry.get("allow") or None)
        else:
            dest.mkdir(parents=True, exist_ok=True)
            keep, _ = self.allowed_files(list(entry.get("files") or []))
            for remote in keep:
                progress(f"тяну {Path(remote).name}")
                got = hf_hub_download(str(entry["repo"]), remote)
                # Плоско по имени: piper ждёт .onnx и .onnx.json рядом, без вложенности репозитория.
                shutil.copyfile(got, dest / Path(remote).name)
        path = self.models_dir / str(entry.get("path") or entry.get("dest") or "")
        files = [f for f in path.rglob("*") if f.is_file()] if path.is_dir() else (
            [path] if path.is_file() else [])
        return {"id": entry.get("id"), "kind": entry.get("kind"), "repo": entry.get("repo"),
                "path": str(entry.get("path") or entry.get("dest") or ""),
                "mb": round(sum(f.stat().st_size for f in files) / 1e6, 1)}

    def _remember(self, entry: dict) -> None:
        """Дописать в опись, заменив прежнюю запись того же имени (второй копии не заводим)."""
        import yaml
        rows = [r for r in self.inventory() if str(r.get("id")) != str(entry["id"])]
        rows.append({k: v for k, v in entry.items() if k != "refused"})
        self.inventory_file.parent.mkdir(parents=True, exist_ok=True)
        self.inventory_file.write_text(yaml.safe_dump(
            {"models": rows}, allow_unicode=True, sort_keys=False), encoding="utf-8")


class OnlineCatalog:
    """Какие модели умеет вызвать шлюз и каким эндпоинтом. Ключ для СПИСКА не нужен.

    С ключом список можно уточнить у самого провайдера (`live`): реестр шлюза знает то, что знал
    на день сборки, и новая модель появляется у провайдера раньше. Тогда роли делятся так —
    провайдер говорит, ЧТО ему доступно сегодня, реестр добавляет к именам смысл (режим, цену).
    """

    MODES = {"image": "image_generation", "tts": "audio_speech", "stt": "audio_transcription"}

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
        url = str(decl.get("url") or "")
        if not url.lower().startswith("https://"):
            # Ключ в открытом канале виден любому посреднику — это отказ, а не предупреждение.
            raise ProviderError(
                "DOWNLOAD_FORBIDDEN", f"Адрес списка моделей не https: {url}",
                reason="С ключом ходим только по https — иначе его увидит любой посредник.")
        try:
            import httpx
            headers = self._auth_headers(str(decl.get("auth") or "bearer"), api_key)
            response = httpx.get(url, headers=headers, timeout=30, follow_redirects=False)
        except Exception as e:                              # noqa: BLE001 — сеть/таймаут
            raise ProviderError(
                "PROVIDER_FAILED", f"Провайдер не ответил на запрос списка моделей: {e}",
                reason="Сеть или сам провайдер недоступны. Список из реестра шлюза остаётся "
                       "доступен без ключа.") from e
        if response.status_code == 401 or response.status_code == 403:
            raise ProviderError(
                "AUTH_FAILED", f"Провайдер отклонил ключ при запросе списка ({response.status_code}).",
                reason="Ключ недействителен или у него нет прав. Заменить может только владелец: "
                       "python scripts/set_provider_key.py --channel <канал> --provider " + provider)
        if response.status_code >= 400:
            raise ProviderError(
                "PROVIDER_FAILED", f"Провайдер ответил {response.status_code} на запрос списка.",
                reason="Повтори позже; список из реестра шлюза доступен и без обращения к нему.")
        items = response.json()
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
