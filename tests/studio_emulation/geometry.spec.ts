// Геометрия судится ПРОГОНОМ браузера: наложение прямоугольников, выход текста за границу и
// уход компонента из видимой области — то, чего не видит ни текстовый разбор, ни снимок формы.
// Судим ГЕОМЕТРИЮ, а не красоту: «некрасиво» машина не берётся судить, и это объявленный предел.
import { expect, test } from "@playwright/test";

const НИШИ = {
  jsonrpc: "2.0", id: 1,
  result: { data: { items: [
    { id: "n1", title: "Разборы техники", channels: 3 },
    { id: "n2", title: "Короткие рецепты", channels: 5 },
    { id: "n3", title: "Очень длинное название ниши, которое проверяет перенос текста", channels: 1 },
  ] } },
};

test.beforeEach(async ({ page }) => {
  // Слой доступа настоящий, подменяется только ОТВЕТ сервера: иначе прогон судил бы пустой экран.
  await page.route("**/mcp", (route) => route.fulfill({ json: НИШИ }));
  await page.goto("/");
  await page.waitForSelector("[data-component]");
});

test("компоненты не накладываются друг на друга", async ({ page }) => {
  const наложения = await page.evaluate(() => {
    const узлы = [...document.querySelectorAll<HTMLElement>("[data-component]")];
    const беды: string[] = [];
    for (let i = 0; i < узлы.length; i++) {
      for (let j = i + 1; j < узлы.length; j++) {
        const [a, b] = [узлы[i], узлы[j]];
        if (a.contains(b) || b.contains(a)) continue;   // вложенность — не наложение
        const [ra, rb] = [a.getBoundingClientRect(), b.getBoundingClientRect()];
        const пересечение = Math.max(0, Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left))
          * Math.max(0, Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top));
        if (пересечение > 1) {
          беды.push(`${a.dataset.component} ↔ ${b.dataset.component}: ${Math.round(пересечение)}px²`);
        }
      }
    }
    return беды;
  });
  expect(наложения, "прямоугольники компонентов перекрываются").toEqual([]);
});

test("текст не выходит за границу своего контейнера", async ({ page }) => {
  const переполнения = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("[data-component]")]
      .filter((el) => el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)
      .map((el) => `${el.dataset.component}: ${el.scrollWidth}×${el.scrollHeight} в ${el.clientWidth}×${el.clientHeight}`));
  expect(переполнения, "содержимое не помещается в компонент").toEqual([]);
});

test("ни один компонент не ушёл из видимой области", async ({ page }) => {
  const ушедшие = await page.evaluate(() => {
    const ширина = document.documentElement.clientWidth;
    return [...document.querySelectorAll<HTMLElement>("[data-component]")]
      .map((el) => ({ имя: el.dataset.component, r: el.getBoundingClientRect() }))
      .filter(({ r }) => r.left < -1 || r.right > ширина + 1)
      .map(({ имя, r }) => `${имя}: ${Math.round(r.left)}…${Math.round(r.right)} при ширине ${ширина}`);
  });
  expect(ушедшие, "компонент вышел за границу окна").toEqual([]);
});
