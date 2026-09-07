// Нажимаемая поверхность: карточка проекта и строка ниши в дизайне отличаются только раскладкой
// содержимого и цветом отклика, а рамка, фон, тень и подъём под курсором у них одни.

export type PanelTone = "accent" | "niche";

const отклик: Record<PanelTone, string> = {
  accent: "hover:border-accent hover:-translate-y-0.5 hover:shadow-xl",
  niche: "hover:border-niche-line",
};

export function Panel({ tone, label, onOpen, children }: {
  tone: PanelTone;
  label: string;
  onOpen: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      data-component="Panel"
      data-tone={tone}
      type="button"
      title={label}
      onClick={onOpen}
      className={`group w-full cursor-pointer rounded-xl border border-line bg-surface text-left shadow-lg transition-all duration-200 hover:bg-surface-hover ${отклик[tone]}`}
    >
      {children}
    </button>
  );
}
