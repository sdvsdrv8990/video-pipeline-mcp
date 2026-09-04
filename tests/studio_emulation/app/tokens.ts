// Единственный источник размеров, шрифтов, отступов, цвета и длительностей студии.
// Компонент берёт значение ОТСЮДА; литерал внутри компонента — хардкод класса 2 (anti-hardcode).
export const tokens = {
  space: { xs: "4px", sm: "8px", md: "16px", lg: "24px" },
  font: { family: "Inter, system-ui, sans-serif", body: "14px", head: "20px" },
  radius: { card: "12px" },
  color: { ink: "#1a1a1a", ground: "#ffffff", accent: "#2f6fed" },
  duration: { enter: "160ms" },
} as const;
