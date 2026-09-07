import { useEffect, useState } from "react";

import { listChannels, listNiches, listProjects, mounted } from "../mcp/client";
import type { Channel, Niche, Project } from "../types";
import { ProjectsListView } from "./ProjectsListView";

export function App() {
  const [projects, задатьПроекты] = useState<Project[]>([]);
  const [channels, задатьКаналы] = useState<Channel[]>([]);
  const [niches, задатьНиши] = useState<Niche[]>([]);

  useEffect(() => {
    listProjects().then(задатьПроекты);
    listChannels().then(задатьКаналы);
    listNiches().then(задатьНиши);
  }, []);

  return (
    <div data-component="App" className="min-h-screen bg-ground">
      {!mounted && (
        <p className="border-b border-line-strong bg-rival-ground px-6 py-2 text-xs font-semibold text-rival-soft">
          Дверь к серверу не подключена: показаны посевные строки, а не книга.
        </p>
      )}
      <ProjectsListView
        projects={projects}
        channels={channels}
        niches={niches}
        onOpenProject={() => undefined}
        onCreateProject={() => undefined}
        onAddNiche={() => undefined}
      />
    </div>
  );
}
