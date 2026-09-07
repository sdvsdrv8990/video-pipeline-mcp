// Бирка: короткая подпись в рамке. В дизайне владельца она нарисована шесть раз — ниша на
// карточке, «без ниши», личная сетка, сетка конкурентов, активный фильтр, имя ниши в строке.
// Здесь это ОДИН примитив, а различие тонов объявлено таблицей.

export type BadgeTone = "niche" | "muted" | "own" | "rival";

const тон: Record<BadgeTone, string> = {
  niche: "border-niche-line bg-niche-ground text-niche-soft",
  muted: "border-line-strong bg-surface text-ink-muted",
  own: "border-own-line bg-own-ground text-own-soft",
  rival: "border-rival-line bg-rival-ground text-rival-soft",
};

export function Badge({ tone, label, mono, onPress }: {
  tone: BadgeTone;
  label: string;
  mono?: boolean;
  onPress?: () => void;
}) {
  const вид = `rounded border px-2 py-0.5 text-micro font-medium ${тон[tone]} ${
    mono ? "font-mono" : ""
  }`;
  // Нажимаемая бирка — настоящая кнопка, а не span с обработчиком: иначе она недоступна с
  // клавиатуры, и это тот самый XSS-соседний долг, который студия обязана не заводить.
  if (!onPress) {
    return <span data-component="Badge" data-tone={tone} className={вид}>{label}</span>;
  }
  return (
    <button
      data-component="Badge"
      data-tone={tone}
      type="button"
      onClick={onPress}
      className={`${вид} cursor-pointer transition-colors hover:brightness-125`}
    >
      {label}
    </button>
  );
}
