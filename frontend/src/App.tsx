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
import MusicStudio from "./MusicStudio";

type Tab = "video" | "music";
const tabFromHash = (): Tab => (location.hash === "#music" ? "music" : "video");

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [settings, setSettings] = useState<JobSettings>(loadSettings);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(tabFromHash);
  const watching = useRef(new Map<string, () => void>());

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);

  // Only one song plays at a time: starting any <audio> or <video> pauses the others
  useEffect(() => {
    const onPlay = (e: Event) =>
      document.querySelectorAll<HTMLMediaElement>("audio, video").forEach((m) => m !== e.target && m.pause());
    document.addEventListener("play", onPlay, true);
    return () => document.removeEventListener("play", onPlay, true);
  }, []);

  useEffect(() => {
    const poll = () => getHealth().then(setHealth).catch(() => setHealth(null));
    poll();
    const t = setInterval(poll, 5000);
    listJobs()
      .then((list) => {
        setJobs(list);
        setSelectedId((id) => id ?? list.find((j) => j.kind === "video")?.id ?? null);
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
  useEffect(() => {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch {} // private mode or blocked storage: settings just won't be remembered
  }, [settings]);

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

  const videoJobs = jobs.filter((j) => j.kind === "video");
  const musicJobs = jobs.filter((j) => j.kind === "music");
  const selected = videoJobs.find((j) => j.id === selectedId);
  const switchTab = (t: Tab) => {
    setError(null);
    setTab(t);
    history.replaceState(null, "", t === "music" ? "#music" : location.pathname);
  };

  return (
    <main>
      <header>
        <div className="brand">
          <h1>Happica</h1>
          <nav className="tabs" aria-label="Studio">
            <button className={tab === "video" ? "on" : ""} aria-current={tab === "video"} onClick={() => switchTab("video")}>
              Video
            </button>
            <button className={tab === "music" ? "on" : ""} aria-current={tab === "music"} onClick={() => switchTab("music")}>
              Music
            </button>
          </nav>
        </div>
        <div className="header-right">
          <a className="guide-link" href="/guide.html" target="_blank" rel="noreferrer">
            How to use
          </a>
          <StatusPill health={health} />
        </div>
      </header>

      {tab === "music" ? (
        <MusicStudio
          jobs={musicJobs}
          health={health}
          upsert={upsert}
          remove={(id) => setJobs((prev) => prev.filter((j) => j.id !== id))}
          act={act}
          error={error}
        />
      ) : (
      <>
      <GenerateForm
        prompt={prompt}
        settings={settings}
        health={health}
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
            // Story mode: the original story. Line mode: the current scene prompts, so edits made
            // while regenerating single scenes carry over.
            const story = selected.settings.scene_mode === "llm";
            setPrompt((!story && selected.result?.scenes.join("\n")) || selected.prompt);
            const { seed: _seed, ...rest } = selected.settings; // edited re-runs get a fresh seed
            setSettings({ ...DEFAULTS, ...rest });
            document.getElementById("generate")?.scrollIntoView({ behavior: "smooth" });
          }}
          onDelete={() =>
            confirm("Delete this job and its files?") &&
            act(async () => {
              await deleteJob(selected.id);
              setJobs((prev) => prev.filter((j) => j.id !== selected.id));
              setSelectedId(videoJobs.find((j) => j.id !== selected.id)?.id ?? null);
            })
          }
          onRegenerate={(index, p) => act(async () => upsert(await regenerateScene(selected.id, index, p)))}
        />
      )}

      <section>
        <h2>History</h2>
        <JobGallery jobs={videoJobs} selectedId={selectedId} onSelect={setSelectedId} />
      </section>
      </>
      )}
    </main>
  );
}

const SETTINGS_KEY = "happica.settings";

/** Last-used settings, so a reload doesn't reset the form. */
function loadSettings(): JobSettings {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}") };
  } catch {
    return DEFAULTS;
  }
}

function StatusPill({ health }: { health: Health | null }) {
  if (!health) return <span className="pill bad">Backend offline</span>;
  if (health.comfyui !== "ok") return <span className="pill warn">ComfyUI offline</span>;
  const llm = health.ollama === "ok" ? "" : " · story mode off";
  return (
    <span className={`pill ${llm ? "warn" : "ok"}`} title={`Ollama: ${health.ollama} (${health.llm_model})`}>
      Ready · {health.devices?.join(", ")}
      {llm}
    </span>
  );
}
