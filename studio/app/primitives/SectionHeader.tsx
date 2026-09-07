// Шапка раздела: подпись слева, органы управления разделом справа, черта снизу.
// В дизайне повторена дважды («Рабочие проекты», «Ниши») разметкой в обоих местах.

export function SectionHeader({ title, icon, children }: {
  title: string;
  icon?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div
      data-component="SectionHeader"
      className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-line-soft pb-4"
    >
      <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
        {icon}
        <span>{title}</span>
      </h2>
      {children}
    </div>
  );
}
