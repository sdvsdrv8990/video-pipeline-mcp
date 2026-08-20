"""core/uniqueness/uniqueness_core.py — локальный расчёт уникальности (воркстрим A7.2).

## Назначение
Меряет, насколько наполнение уникально относительно прошлых применений ТОЙ ЖЕ структуры:
n-gram-шинглы по словам, локально, без внешнего API.

## Границы
Ответ всегда несёт `readiness` (`full` / `partial` с перечнем нехватки / `empty`): пустой вход
даёт честный ноль из декларации, а не «100% уникально». Окно шингла, метрика, пороги и состав
композиции — `config/uniqueness.yaml`; активные типы фрагментов и веса — ДАННЫЕ канала, где
`enabled=false` гасит поведение, а не структуру. Добавить тип фрагмента = строка в данных.
"""

from typing import ClassVar
import re
from pathlib import Path

import yaml


class UniquenessError(Exception):
    """Ошибка расчёта в формате контракта (код из server_reactions.yaml)."""

    def __init__(self, code: str, message: str, reason: str = "", suggested_tool: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+", re.UNICODE)


class UniquenessEngine:
    """Расчёт уникальности по декларации `config/uniqueness.yaml`."""

    def __init__(self, config_file: str | Path):
        self.config_file = Path(config_file)
        self._cfg: dict | None = None
        self._mtime: float = 0.0

    # ═══ Декларация ═══

    @property
    def config(self) -> dict:
        """Параметры расчёта. Битую декларацию не глушим — иначе она тихо перестанет работать (D2)."""
        if not self.config_file.exists():
            raise UniquenessError(
                "TEMPLATE_NOT_FOUND", f"Нет декларации расчёта: {self.config_file.name}",
                reason="Заведи config/uniqueness.yaml — параметры расчёта живут там, не в коде.")
        mtime = self.config_file.stat().st_mtime
        if self._cfg is None or mtime != self._mtime:
            try:
                data = yaml.safe_load(self.config_file.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                raise UniquenessError("SCHEMA_INVALID", f"Битый {self.config_file.name}: {e}",
                                      reason="Почини YAML — без него расчёт не имеет параметров.") from e
            self._cfg, self._mtime = data, mtime
        return self._cfg

    # ═══ Похожесть текста ═══

    def shingles(self, text: str) -> set[tuple[str, ...]]:
        """Множество шинглов из слов. Размер окна и нормализация — из декларации."""
        ng = self.config.get("ngram") or {}
        size = int(ng.get("size", 4))
        norm = text or ""
        if ng.get("lowercase", True):
            norm = norm.lower()
        if ng.get("strip_punctuation", True):
            norm = _PUNCT_RE.sub(" ", norm)
        words = [w for w in _SPACE_RE.split(norm) if w]
        if len(words) < size:
            # Текст короче окна — сам себе единственный шингл, иначе он был бы «пустым»
            # и любая короткая фраза считалась бы уникальной.
            return {tuple(words)} if words else set()
        return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}

    def similarity(self, text: str, corpus: list[str]) -> float:
        """Максимальная похожесть текста на элементы корпуса, 0..1. Метрика — из декларации."""
        mine = self.shingles(text)
        if not mine:
            return 0.0
        metric = (self.config.get("ngram") or {}).get("metric", "jaccard")
        best = 0.0
        for other in corpus:
            theirs = self.shingles(other)
            if not theirs:
                continue
            common = len(mine & theirs)
            base = len(mine | theirs) if metric == "jaccard" else len(mine)
            if base:
                best = max(best, common / base)
        return best

    # ═══ Оценки и готовность ═══

    def _active_profile(self, profile_rows: list[dict], spec: dict) -> list[dict]:
        """Активные типы фрагментов и веса ИЗ ДАННЫХ: `enabled=false` гасит тип («тихий столбец»)."""
        type_col = spec.get("type_column", "fragment_type")
        enabled_col = spec.get("enabled_column", "enabled")
        weight_col = spec.get("weight_column", "niche_weight")
        active = []
        for row in profile_rows or []:
            if not row.get(enabled_col):
                continue
            try:
                weight = float(row.get(weight_col) or 0)
            except (TypeError, ValueError):
                weight = 0.0
            active.append({"type": row.get(type_col), "weight": weight})
        return active

    def compute(self, text: str = "", corpus: list[str] | None = None,
                fragments: dict | None = None, profile_rows: list[dict] | None = None,
                compensation_history: int = 0) -> dict:
        """Посчитать оценки и сказать, ХВАТИЛО ЛИ данных.

        `fragments` — {тип_фрагмента: [идентификаторы применений]}; уникальность типа = доля
        неповторяющихся. Что считать, какие входы обязательны и как складывать — из декларации.
        """
        cfg = self.config
        corpus = corpus or []
        empty_value = float(cfg.get("empty_input", 0.0))
        inputs = {"text": bool((text or "").strip()), "corpus": bool(corpus),
                  "fragments": bool(fragments), "profile": bool(profile_rows)}

        scores: dict[str, float] = {}
        missing: dict[str, list[str]] = {}
        fragment_gaps: list[str] = []
        fragment_scores: dict[str, float] = {}
        for name, spec in (cfg.get("scores") or {}).items():
            lack = [need for need in (spec.get("needs") or []) if not inputs.get(need)]
            if lack:
                missing[name] = lack
                continue
            if "profile" in (spec.get("needs") or []):
                active = self._active_profile(profile_rows or [], spec.get("profile") or {})
                scores[name], gaps, per_type = self._fragment_score(fragments or {}, active, empty_value)
                fragment_scores.update(per_type)
                if gaps:
                    # Включённый тип без применений — это ОТСУТСТВИЕ данных, а не нулевая
                    # уникальность. Молчаливый ноль сделал бы «нечего мерить» неотличимым
                    # от «повторено целиком» — ровно та подмена, которой быть не должно.
                    fragment_gaps.extend(gaps)
            else:
                # Уникальность = 1 − похожесть на прошлые применения той же структуры.
                scores[name] = round(1.0 - self.similarity(text, corpus), 4)

        if not scores:
            readiness = "empty"
        elif missing or fragment_gaps:
            readiness = "partial"
        else:
            readiness = "full"
        composed, weights_used = self._compose(scores, cfg, empty_value)
        thresholds = cfg.get("thresholds") or {}
        alert, alert_sources = self._alert(composed, scores, fragment_scores, thresholds, readiness)
        compensation = self._compensation(composed, scores, fragment_scores, thresholds,
                                          readiness, cfg, compensation_history)
        return {
            "readiness": readiness,
            "scores": scores,
            "missing_inputs": missing,
            "fragment_gaps": sorted(set(fragment_gaps)),
            "composed": composed,
            "composition_weights": weights_used,
            "fragment_scores": fragment_scores,
            "alert": alert,
            "alert_sources": alert_sources,
            "compensation": compensation,
            "thresholds": {k: thresholds.get(k) for k in ("alert_below", "critical_below")},
        }

    # Тяжесть уровней: сравниваем сигналы, чтобы выбрать худший.
    _SEVERITY: ClassVar[dict[str | None, int]] = {None: 0, "alert": 1, "critical": 2}

    @staticmethod
    def _level(value: float, th: dict) -> str | None:
        """Уровень сигнала для одного числа по его порогам."""
        if "critical_below" in th and value < float(th["critical_below"]):
            return "critical"
        if "alert_below" in th and value < float(th["alert_below"]):
            return "alert"
        return None

    def _checks(self, composed: float, scores: dict, fragment_scores: dict,
                thresholds: dict) -> list[tuple[str, float, dict]]:
        """Все уровни, на которых считается сигнал: композиция, оценки, КАЖДЫЙ тип фрагмента.

        Заспамленный фон обязан быть виден сам по себе, а не только в среднем по сцене.
        """
        top = {k: thresholds[k] for k in ("alert_below", "critical_below") if k in thresholds}
        per_score = thresholds.get("per_score") or {}
        per_type = thresholds.get("per_fragment_type") or {}
        out = [("composed", composed, top)]
        for name, value in scores.items():
            if name in per_score:
                out.append((name, value, {**top, **(per_score[name] or {})}))
        for ftype, value in fragment_scores.items():
            if per_type:
                own = per_type.get(ftype) if isinstance(per_type.get(ftype), dict) else {}
                base = {k: v for k, v in per_type.items() if not isinstance(v, dict)}
                out.append((f"fragment:{ftype}", value, {**top, **base, **(own or {})}))
        return out

    def _compensation(self, composed: float, scores: dict, fragment_scores: dict,
                      thresholds: dict, readiness: str, cfg: dict, history: int) -> dict:
        """Что прошло за счёт соседей — и не пора ли требовать новую сцену.

        Уникальный текст и уникальный фон могут закрыть собой заспамленные фрагменты: итог
        проходит. Это разрешено, но записывается — приём, которым «проходят уникальность»,
        опасен при повторении, и после объявленного числа раз требование ужесточается.
        """
        spec = cfg.get("compensation") or {}
        if readiness == "empty" or not spec:
            return {"active": False, "items": [], "history": history, "escalated": False}
        top_level = self._level(composed, {k: thresholds[k] for k in
                                           ("alert_below", "critical_below") if k in thresholds})
        weak = [name for name, value, th in self._checks(composed, scores, fragment_scores, thresholds)
                if name != "composed"
                and self._SEVERITY[self._level(value, th)] > self._SEVERITY[top_level]]
        if not weak:
            return {"active": False, "items": [], "history": history, "escalated": False}
        escalate_after = int(spec.get("escalate_after", 0) or 0)
        return {"active": True, "items": sorted(weak), "history": history,
                "escalated": bool(escalate_after) and history >= escalate_after,
                "masked_level": top_level}

    def _alert(self, composed: float, scores: dict, fragment_scores: dict, thresholds: dict,
               readiness: str) -> tuple[str | None, list[str]]:
        """Худшая оценка решает (владелец S22).

        Порог проверяется на композиции И на каждой объявленной оценке; берём самый тяжёлый
        уровень и называем, ЧТО его дало — иначе «critical» не подсказывает, где чинить.
        """
        if readiness == "empty":
            return None, []
        checks = self._checks(composed, scores, fragment_scores, thresholds)
        worst, sources = None, []
        for name, value, th in checks:
            level = self._level(value, th)
            if self._SEVERITY[level] > self._SEVERITY[worst]:
                worst, sources = level, [name]
            elif level is not None and level == worst:
                sources.append(name)
        return worst, sources

    def _fragment_score(self, fragments: dict, active: list[dict],
                        empty_value: float) -> tuple[float, list[str], dict]:
        """Взвешенная уникальность по типам фрагментов + типы БЕЗ данных.

        Выключенный тип не входит вовсе («тихий столбец»). Включённый, но не применённый —
        входит в список пробелов и считается по объявленному пустому значению; вес его при
        этом из знаменателя убирается, иначе отсутствие данных занижало бы оценку соседей.
        """
        if not active:
            return empty_value, [], {}
        gaps = [item["type"] for item in active if not (fragments.get(item["type"]) or [])]
        measured = [item for item in active if fragments.get(item["type"])]
        total_weight = sum(item["weight"] for item in measured)
        per_type = {item["type"]: round(len(set(fragments[item["type"]])) / len(fragments[item["type"]]), 4)
                    for item in measured}
        if not measured or total_weight <= 0:
            return empty_value, gaps, per_type
        acc = sum(per_type[item["type"]] * item["weight"] for item in measured)
        return round(acc / total_weight, 4), gaps, per_type

    def _compose(self, scores: dict, cfg: dict, empty_value: float) -> tuple[float, dict]:
        """Итог по объявленным весам. Считаем только по посчитанным слагаемым, веса пере-нормируем:
        иначе отсутствие одной оценки молча занижало бы итог, выдавая неполноту за низкое качество."""
        comp = (cfg.get("composition") or {}).get("full_scene_uniqueness") or {}
        used = {name: float(w) for name, w in comp.items() if name in scores}
        total = sum(used.values())
        if not used or total <= 0:
            return empty_value, {}
        value = sum(scores[name] * w for name, w in used.items()) / total
        return round(value, 4), used
