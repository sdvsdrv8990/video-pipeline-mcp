// Типы книги в том виде, в каком их показывает студия. Хозяин объявления — листы сервера
// (config/templates/tables/*.schema.yaml); здесь лежит ровно то, что читает экран, и не больше.

export interface Niche {
  id: string;
  name: string;
  subscribersCount?: string;
}

export interface ChannelNetwork {
  id: string;
  projectId: string;
  name: string;
  type: "personal" | "competitors";
}

export interface Project {
  id: string;
  title: string;
  description: string;
  nicheId: string | null;
  networks: ChannelNetwork[];
}

export interface Channel {
  id: string;
  projectId: string;
  networkId: string;
  name: string;
  isOwn: boolean;
  nicheId?: string | null;
}
