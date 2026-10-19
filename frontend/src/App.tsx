import { useEffect, useState } from "react";
import { cancelJob, createJob, getHealth, Health, Job, JobSettings, listJobs, watchJob } from "./api";

const DEFAULTS: JobSettings = {
  width: 768,
  height: 512,
  max_scenes: 4,
  motion: "kenburns",
  style: "cinematic, highly detailed, soft lighting",
  clip_seconds: 3,
};

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [prompt, setPrompt] = useState("");
  const [settings, setSettings] = useState<JobSettings>(DEFAULTS);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const poll = () => getHealth().then(setHealth).catch(() => setHealth(null));
    poll();
    const t = setInterval(poll, 5000);
    listJobs().then(setJobs).catch(() => {});
    return () => clearInterval(t);
  }, []);

  const upsert = (job: Job) =>
    setJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)].sort((a, b) => b.created - a.created));

  async function submit() {
    setError(null);
    try {
      const job = await createJob(prompt, settings);
      upsert(job);
      const stop = watchJob(job.id, (j) => {
        upsert(j);
        if (!["queued", "running"].includes(j.state)) stop();
      });
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <main>
      <header>
        <h1>Happica</h1>
        <StatusPill health={health} />
      </header>

      <section className="card">
        <label htmlFor="prompt">Describe your video — one line per scene</label>
        <textarea
          id="prompt"
          rows={5}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={"A lighthouse on a cliff at sunset\nWaves crashing against the rocks\nSeagulls flying over the sea"}
        />
        <label className="style">
          Style (added to every scene)
          <input value={settings.style} onChange={(e) => setSettings({ ...settings, style: e.target.value })} />
        </label>
        <div className="row">
          <label>
            Size
            <select
              value={`${settings.width}x${settings.height}`}
              onChange={(e) => {
                const [width, height] = e.target.value.split("x").map(Number);
                setSettings({ ...settings, width, height });
              }}
            >
              <option value="768x512">768×512 (landscape)</option>
              <option value="512x768">512×768 (portrait)</option>
              <option value="512x512">512×512 (square)</option>
            </select>
          </label>
          <label>
            Scenes
            <input
              type="number"
              min={1}
              max={12}
              value={settings.max_scenes}
              onChange={(e) => setSettings({ ...settings, max_scenes: Number(e.target.value) })}
            />
          </label>
          <label>
            Motion
            <select
              value={settings.motion}
              onChange={(e) => setSettings({ ...settings, motion: e.target.value as JobSettings["motion"] })}
            >
              <option value="none">Images only</option>
              <option value="kenburns">Pan &amp; zoom (fast)</option>
              <option value="ltx">AI motion — LTX-Video (slow)</option>
            </select>
          </label>
          <label>
            Seconds per scene
            <select
              value={settings.clip_seconds}
              onChange={(e) => setSettings({ ...settings, clip_seconds: Number(e.target.value) })}
            >
              {[2, 3, 4, 5, 6].map((s) => (
                <option key={s} value={s}>{s}s</option>
              ))}
            </select>
          </label>
          <button disabled={!prompt.trim()} onClick={submit}>
            Generate
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </section>

      <section>
        <h2>Jobs</h2>
        {jobs.length === 0 && <p className="muted">No jobs yet.</p>}
        {jobs.map((job) => (
          <JobCard key={job.id} job={job} onCancel={() => cancelJob(job.id).then(upsert)} />
        ))}
      </section>
    </main>
  );
}

function StatusPill({ health }: { health: Health | null }) {
  if (!health) return <span className="pill bad">Backend offline</span>;
  if (health.comfyui !== "ok") return <span className="pill warn">ComfyUI offline</span>;
  return <span className="pill ok">Ready · {health.devices?.join(", ")}</span>;
}

function JobCard({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const active = job.state === "queued" || job.state === "running";
  return (
    <article className="card job">
      <div className="row between">
        <strong className={`state ${job.state}`}>{job.state}</strong>
        <span className="muted">{new Date(job.created * 1000).toLocaleString()}</span>
      </div>
      <p className="prompt">{job.prompt}</p>
      {job.state === "running" && (
        <div className="progress">
          <span>{job.stage}</span>
          <progress value={job.progress} max={1} />
        </div>
      )}
      {job.error && <p className="error">{job.error}</p>}
      {job.result?.video && <video src={`/outputs/${job.result.video}`} controls />}
      {!!job.result?.images.length && (
        <div className="grid">
          {job.result.images.map((src, i) => (
            <figure key={src}>
              <a href={`/outputs/${src}`} target="_blank" rel="noreferrer">
                <img src={`/outputs/${src}`} alt={job.result!.scenes[i]} />
              </a>
              <figcaption>{job.result!.scenes[i]}</figcaption>
            </figure>
          ))}
        </div>
      )}
      {job.result?.seed !== undefined && <p className="muted">Seed {job.result.seed}</p>}
      {active && <button className="secondary" onClick={onCancel}>Cancel</button>}
    </article>
  );
}
