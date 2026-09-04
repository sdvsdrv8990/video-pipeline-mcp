import { tokens } from "../tokens";

export type ButtonVariant = "primary" | "ghost";

export function Button({ variant, label, onPress }: {
  variant: ButtonVariant;
  label: string;
  onPress: () => void;
}) {
  return (
    <button
      data-component="Button"
      data-variant={variant}
      onClick={onPress}
      style={{
        padding: `${tokens.space.xs} ${tokens.space.sm}`,
        fontFamily: tokens.font.family,
        fontSize: tokens.font.body,
        color: variant === "primary" ? tokens.color.ground : tokens.color.ink,
        background: variant === "primary" ? tokens.color.accent : tokens.color.ground,
        transitionDuration: tokens.duration.enter,
      }}
    >
      {label}
    </button>
  );
}
