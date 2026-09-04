import { tokens } from "../tokens";

export type CardVariant = "niche" | "channel" | "video";

export function Card({ variant, title, children }: {
  variant: CardVariant;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <section
      data-component="Card"
      data-variant={variant}
      style={{
        padding: tokens.space.md,
        borderRadius: tokens.radius.card,
        fontFamily: tokens.font.family,
        fontSize: tokens.font.body,
        color: tokens.color.ink,
        background: tokens.color.ground,
      }}
    >
      <h2 style={{ fontSize: tokens.font.head, marginBottom: tokens.space.sm }}>{title}</h2>
      {children}
    </section>
  );
}
