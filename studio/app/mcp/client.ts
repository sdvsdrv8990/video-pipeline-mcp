// Единственная дверь студии к серверу. Сегодня за дверью НИЧЕГО НЕТ: транспорт к MCP не написан,
// и функции отдают посевные строки, чтобы экран было на чём судить.
//
// Стуб ЧЕСТНЫЙ: `mounted` остаётся false, пока дверь не подключена к серверу, и это
// единственное место, которое придётся переписать, — экраны уже ждут обещание, а не массив.

import type { Channel, Niche, Project } from "../types";

export const mounted = false;

const проекты: Project[] = [
  {
    id: "prj-ai",
    title: "Нейросети для всех",
    description: "Обзоры моделей, сравнения и разборы для широкой аудитории.",
    nicheId: "niche-ai",
    networks: [
      { id: "net-ai-own", projectId: "prj-ai", name: "Личная сетка", type: "personal" },
      { id: "net-ai-rivals", projectId: "prj-ai", name: "Конкуренты", type: "competitors" },
    ],
  },
  {
    id: "prj-money",
    title: "Финансы без воды",
    description: "Короткие ролики о личных финансах и инвестициях для новичков.",
    nicheId: "niche-money",
    networks: [
      { id: "net-money-own", projectId: "prj-money", name: "Личная сетка", type: "personal" },
    ],
  },
  {
    id: "prj-lab",
    title: "Лаборатория форматов",
    description: "Проверка гипотез по обложкам, длине и первым пяти секундам.",
    nicheId: null,
    networks: [],
  },
];

const ниши: Niche[] = [
  { id: "niche-ai", name: "Нейросети", subscribersCount: "412K" },
  { id: "niche-money", name: "Финансы", subscribersCount: "128K" },
];

const каналы: Channel[] = [
  { id: "ch-ai-1", projectId: "prj-ai", networkId: "net-ai-own", name: "AI Разбор", isOwn: true, nicheId: "niche-ai" },
  { id: "ch-ai-2", projectId: "prj-ai", networkId: "net-ai-rivals", name: "NeuroDaily", isOwn: false, nicheId: "niche-ai" },
  { id: "ch-ai-3", projectId: "prj-ai", networkId: "net-ai-rivals", name: "ML Weekly", isOwn: false, nicheId: "niche-ai" },
  { id: "ch-money-1", projectId: "prj-money", networkId: "net-money-own", name: "Деньги просто", isOwn: true, nicheId: "niche-money" },
];

export function listProjects(): Promise<Project[]> {
  return Promise.resolve(проекты);
}

export function listNiches(): Promise<Niche[]> {
  return Promise.resolve(ниши);
}

export function listChannels(): Promise<Channel[]> {
  return Promise.resolve(каналы);
}
