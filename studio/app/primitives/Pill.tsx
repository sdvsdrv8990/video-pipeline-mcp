// Таблетка фильтра: выбор одного значения из ряда. Невыбранная всегда одинакова, выбранная
// красится РОДОМ фильтра — действие синее, ниша бирюзовая. В дизайне это различие уже было,
// но повторялось разметкой в каждой ветке тернарника.

export type PillTone = "accent" | "niche";

const выбрана: Record<PillTone, string> = {
  accent: "bg-accent-solid text-ink-strong",
  niche: "border border-niche-line bg-niche-ground text-niche-soft",
};

const покой = "border border-line bg-surface text-ink-muted hover:bg-surface-hover hover:text-ink";

export function Pill({ tone, label, count, selected, onPress }: {
  tone: PillTone;
  label: string;
  count: number;
  selected: boolean;
  onPress: () => void;
}) {
  return (
    <button
      data-component="Pill"
      data-tone={tone}
      data-selected={selected}
      type="button"
      onClick={onPress}
      className={`flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-all ${
        selected ? `font-semibold ${выбрана[tone]}` : `font-medium ${покой}`
      }`}
    >
      <span>{label}</span>
      <span className="font-mono text-nano opacity-75">({count})</span>
    </button>
  );
}
