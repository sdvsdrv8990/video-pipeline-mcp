import { useState } from "react";
import { ChevronRight, Layers, Network, Plus, X } from "lucide-react";

import { Badge } from "../primitives/Badge";
import { Button } from "../primitives/Button";
import { Panel } from "../primitives/Panel";
import { Pill } from "../primitives/Pill";
import { SectionHeader } from "../primitives/SectionHeader";
import type { Channel, Niche, Project } from "../types";

const ВСЕ = "all";

export function ProjectsListView({ projects, channels, niches, onOpenProject, onCreateProject, onAddNiche }: {
  projects: Project[];
  channels: Channel[];
  niches: Niche[];
  onOpenProject: (projectId: string) => void;
  onCreateProject: (nicheId: string | null) => void;
  onAddNiche: () => void;
}) {
  const [фильтр, задатьФильтр] = useState<string>(ВСЕ);

  const ниша = niches.find((n) => n.id === фильтр);
  const видимые = ниша ? projects.filter((p) => p.nicheId === ниша.id) : projects;

  return (
    <div data-component="ProjectsListView" className="mx-auto max-w-7xl space-y-10 px-6 py-8">
      <section>
        <SectionHeader title="Рабочие проекты">
          <span className="font-mono text-xs text-ink-muted">
            {видимые.length} из {projects.length}
          </span>
        </SectionHeader>

        {niches.length > 0 && (
          <div className="mb-5 flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-micro uppercase tracking-wider text-ink-muted">Ниша:</span>
            <Pill
              tone="accent"
              label="Все"
              count={projects.length}
              selected={фильтр === ВСЕ}
              onPress={() => задатьФильтр(ВСЕ)}
            />
            {niches.map((n) => (
              <Pill
                key={n.id}
                tone="niche"
                label={n.name}
                count={projects.filter((p) => p.nicheId === n.id).length}
                selected={фильтр === n.id}
                onPress={() => задатьФильтр(фильтр === n.id ? ВСЕ : n.id)}
              />
            ))}
          </div>
        )}

        {ниша && (
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-niche-line bg-surface px-4 py-2.5">
            <div className="flex items-center gap-2.5">
              <span className="text-xs text-ink-muted">Активный фильтр по нише:</span>
              <Badge tone="niche" label={ниша.name} />
              <span className="font-mono text-xs text-ink-faint">({видимые.length} проектов)</span>
            </div>
            <Button
              tone="quiet"
              label="Сбросить фильтр"
              icon={<X className="h-3.5 w-3.5" />}
              onPress={() => задатьФильтр(ВСЕ)}
            />
          </div>
        )}

        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
          {видимые.length === 0 && ниша ? (
            <div className="col-span-full space-y-3 rounded-xl border border-line bg-surface p-8 text-center">
              <h3 className="text-sm font-bold text-ink-strong">
                В нише «{ниша.name}» пока нет рабочих проектов
              </h3>
              <p className="mx-auto max-w-md text-xs text-ink-muted">
                Создайте первый проект в этой нише или сбросьте фильтр, чтобы увидеть все проекты.
              </p>
              <div className="flex items-center justify-center gap-3 pt-2">
                <Button
                  tone="primary"
                  label={`Создать проект в нише ${ниша.name}`}
                  icon={<Plus className="h-4 w-4" />}
                  onPress={() => onCreateProject(ниша.id)}
                />
                <Button
                  tone="quiet"
                  label={`Показать все проекты (${projects.length})`}
                  onPress={() => задатьФильтр(ВСЕ)}
                />
              </div>
            </div>
          ) : (
            видимые.map((project) => {
              const свои = channels.filter((c) => c.projectId === project.id);
              const проектнаяНиша = niches.find((n) => n.id === project.nicheId);
              return (
                <Panel
                  key={project.id}
                  tone="accent"
                  label="Открыть проект"
                  onOpen={() => onOpenProject(project.id)}
                >
                  <div className="flex h-full flex-col justify-between p-5">
                    <div>
                      <div className="mb-3 flex items-center justify-between gap-2">
                        <Badge
                          tone={проектнаяНиша ? "niche" : "muted"}
                          label={проектнаяНиша ? проектнаяНиша.name : "без ниши"}
                        />
                        <div className="font-mono text-micro text-ink-muted">
                          {свои.filter((c) => c.isOwn).length} кан. ·{" "}
                          {свои.filter((c) => !c.isOwn).length} конк.
                        </div>
                      </div>

                      <h3 className="mb-1.5 text-lg font-bold tracking-tight text-ink-strong transition-colors group-hover:text-accent">
                        {project.title}
                      </h3>
                      <p className="mb-4 line-clamp-2 text-xs leading-relaxed text-ink-muted">
                        {project.description}
                      </p>

                      {project.networks.length > 0 && (
                        <div className="mb-3 flex flex-wrap items-center gap-1.5">
                          {project.networks.map((net) => (
                            <Badge
                              key={net.id}
                              tone={net.type === "personal" ? "own" : "rival"}
                              label={net.name}
                              mono
                            />
                          ))}
                        </div>
                      )}
                    </div>

                    <div className="flex items-center justify-between border-t border-line-soft pt-3 text-micro text-ink-muted">
                      <span className="flex items-center gap-1.5 font-medium">
                        <Network className="h-3 w-3 text-accent" />
                        <span>{project.networks.length} сеток</span>
                      </span>
                      <span className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wider text-accent transition-colors group-hover:text-accent-soft">
                        <span>Открыть</span>
                        <ChevronRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-1" />
                      </span>
                    </div>
                  </div>
                </Panel>
              );
            })
          )}

          <button
            data-component="CreateProjectCard"
            type="button"
            onClick={() => onCreateProject(ниша ? ниша.id : null)}
            className="group min-h-40 cursor-pointer rounded-xl border border-dashed border-line-strong bg-ground-deep p-5 text-left transition-all hover:border-accent hover:bg-surface"
          >
            <div className="mb-1.5 flex items-center gap-2">
              <Plus className="h-4 w-4 text-accent transition-transform group-hover:scale-110" />
              <span className="text-sm font-semibold text-ink group-hover:text-ink-strong">
                Новый проект
              </span>
            </div>
            <p className="text-xs text-ink-muted">
              Карточка · {ниша ? `ниша «${ниша.name}»` : "ниша"} · конкуренты · сетки
            </p>
          </button>
        </div>
      </section>

      <section className="pt-4">
        <SectionHeader title="Ниши" icon={<Layers className="h-4 w-4 text-niche" />}>
          <Button
            tone="quiet"
            label="Добавить нишу"
            icon={<Plus className="h-3.5 w-3.5" />}
            onPress={onAddNiche}
          />
        </SectionHeader>

        <div className="space-y-2.5">
          {niches.map((n) => (
            <Panel key={n.id} tone="niche" label="Открыть карточку ниши" onOpen={() => задатьФильтр(n.id)}>
              <div className="flex items-center justify-between px-4 py-3">
                <Badge tone="niche" label={n.name} />
                <div className="flex items-center gap-4">
                  <div className="flex items-center gap-3 font-mono text-xs text-ink-muted">
                    <span>{projects.filter((p) => p.nicheId === n.id).length} проектов</span>
                    <span className="text-ink-faint">·</span>
                    <span>{channels.filter((c) => c.nicheId === n.id).length} каналов</span>
                    <span className="text-ink-faint">·</span>
                    <span>{n.subscribersCount ?? "0"} подписчиков</span>
                  </div>
                  <span className="flex items-center gap-1 pl-2 text-xs font-semibold uppercase tracking-wider text-niche transition-colors group-hover:text-niche-soft">
                    <span>Карточка ниши</span>
                    <ChevronRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                  </span>
                </div>
              </div>
            </Panel>
          ))}
        </div>
      </section>
    </div>
  );
}
