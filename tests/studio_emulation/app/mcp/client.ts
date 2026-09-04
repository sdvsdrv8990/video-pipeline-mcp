// Единственный слой обращения к серверу: компонент в сеть сам не ходит (project-rules §2).
export type Niche = { id: string; title: string; channels: number };

export async function listNiches(): Promise<Niche[]> {
  const answer = await fetch("/mcp", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call",
                           params: { name: "structure_list", arguments: { kind: "niche" } } }),
  });
  const result = await answer.json();
  return result.result?.data?.items ?? [];
}
