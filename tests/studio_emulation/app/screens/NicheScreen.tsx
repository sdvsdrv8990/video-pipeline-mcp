import { useEffect, useState } from "react";

import { listNiches, type Niche } from "../mcp/client";
import { Button } from "../primitives/Button";
import { Card } from "../primitives/Card";
import { tokens } from "../tokens";

export function NicheScreen({ onOpen }: { onOpen: (id: string) => void }) {
  const [niches, setNiches] = useState<Niche[]>([]);

  useEffect(() => {
    listNiches().then(setNiches);
  }, []);

  return (
    <main data-component="NicheScreen" style={{ padding: tokens.space.lg, background: tokens.color.ground }}>
      {niches.map((niche) => (
        <Card key={niche.id} variant="niche" title={niche.title}>
          <Button variant="primary" label="Открыть" onPress={() => onOpen(niche.id)} />
        </Card>
      ))}
    </main>
  );
}
