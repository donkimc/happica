import { useEffect, useRef, useState } from "react";
import {
  cancelJob,
  createJob,
  deleteJob,
  getHealth,
  Health,
  isActive,
  Job,
  JobSettings,
  listJobs,
  regenerateScene,
  rerunJob,
  watchJob,
} from "./api";
import GenerateForm, { DEFAULTS } from "./GenerateForm";
import JobDetail from "./JobDetail";
import JobGallery from "./JobGallery";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [settings, setSettings] = useState<JobSettings>(DEFAULTS);
  const [error, setError] = useState<string | null>(null);
  const watching = useRef(new Map<string, () => void>());

  useEffect(() => {
    const poll = () => getHealth().then(setHealth).catch(() => setHealth(null));
    poll();
    const t = setInterval(poll, 5000);
    listJobs()
      .then((list) => {
        setJobs(list);
        setSelectedId((id) => id ?? list[0]?.id ?? null);
      })
      .catch(() => {});
    return () => clearInterval(t);
  }, []);

  // Keep one WebSocket open per queued/running job, whatever started it (new job, re-run, regenerate, page reload).
  useEffect(() => {
    for (const job of jobs) {
      if (isActive(job) && !watching.current.has(job.id)) {
        const close = watchJob(job.id, upsert, () => watching.current.delete(job.id));
        watching.current.set(job.id, close);
      }
    }
  }, [jobs]);
  useEffect(() => () => watching.current.forEach((close) => close()), []);

  function upsert(job: Job) {
    setJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)].sort((a, b) => b.created - a.created));
  }

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const select = (job: Job) => {
    upsert(job);
    setSelectedId(job.id);
  };

  const selected = jobs.find((j) => j.id === selectedId);

  return (
    <main>
      <header>
        <h1>Happica</h1>
        <StatusPill health={health} />
      </header>

      <GenerateForm
        prompt={prompt}
        settings={settings}
        onPrompt={setPrompt}
        onSettings={setSettings}
        onSubmit={() => act(async () => select(await createJob(prompt, settings)))}
        error={error}
      />

      {selected && (
        <JobDetail
          job={selected}
          onCancel={() => act(async () => upsert(await cancelJob(selected.id)))}
          onRerun={(keepSeed) => act(async () => select(await rerunJob(selected.id, keepSeed)))}
          onEdit={() => {
            // Current scene prompts, so edits made while regenerating single scenes carry over
            setPrompt(selected.result?.scenes.join("\n") || selected.prompt);
            const { seed: _seed, ...rest } = selected.settings; // edited re-runs get a fresh seed
            setSettings({ ...DEFAULTS, ...rest });
            document.getElementById("generate")?.scrollIntoView({ behavior: "smooth" });
          }}
          onDelete={() =>
            confirm("Delete this job and its files?") &&
            act(async () => {
              await deleteJob(selected.id);
              const rest = jobs.filter((j) => j.id !== selected.id);
              setJobs(rest);
              setSelectedId(rest[0]?.id ?? null);
            })
          }
          onRegenerate={(index, p) => act(async () => upsert(await regenerateScene(selected.id, index, p)))}
        />
      )}

      <section>
        <h2>History</h2>
        <JobGallery jobs={jobs} selectedId={selectedId} onSelect={setSelectedId} />
      </section>
    </main>
  );
}

function StatusPill({ health }: { health: Health | null }) {
  if (!health) return <span className="pill bad">Backend offline</span>;
  if (health.comfyui !== "ok") return <span className="pill warn">ComfyUI offline</span>;
  return <span className="pill ok">Ready · {health.devices?.join(", ")}</span>;
}
