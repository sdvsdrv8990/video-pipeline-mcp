// Кнопка действия. Три тона покрывают все действия первого экрана: главное действие,
// приглушённое рядом с ним и действие-ссылка внутри текста.

export type ButtonTone = "primary" | "quiet" | "link";

const тон: Record<ButtonTone, string> = {
  primary: "rounded-lg bg-accent-solid px-4 py-2 text-ink-strong hover:bg-accent-hover",
  quiet: "rounded-lg border border-line-strong bg-surface px-3 py-2 text-ink-soft hover:bg-surface-hover",
  link: "text-niche underline underline-offset-2 hover:text-niche-soft",
};

export function Button({ tone, label, icon, onPress }: {
  tone: ButtonTone;
  label: string;
  icon?: React.ReactNode;
  onPress: () => void;
}) {
  return (
    <button
      data-component="Button"
      data-tone={tone}
      type="button"
      onClick={onPress}
      className={`inline-flex cursor-pointer items-center gap-1.5 text-xs font-semibold transition-colors ${тон[tone]}`}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
