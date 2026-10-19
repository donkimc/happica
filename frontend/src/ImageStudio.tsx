import { useEffect, useState } from "react";
import { cancelJob, createImages, deleteJob, Health, ImageSettings, isActive, Job, outputUrl, rerunJob } from "./api";

export const IMAGE_DEFAULTS: ImageSettings = { prompt: "", style: "", width: 768, height: 512, count: 4 };

const STYLES = ["photorealistic, 35mm photo", "watercolor illustration", "cinematic, dramatic lighting", "anime style", "3d render, soft clay", "oil painting", "pencil sketch", "flat vector illustration"];
const SIZES: [number, number, string][] = [
  [768, 512, "768×512 landscape"],
  [512, 768, "512×768 portrait"],
  [512, 512, "512×512 square"],
  [1024, 1024, "1024×1024 square (slower)"],
];

interface Props {
  jobs: Job[];
  health: Health | null;
  upsert: (job: Job) => void;
  remove: (id: string) => void;
  act: (fn: () => Promise<unknown>) => void;
  error: string | null;
}

export default function ImageStudio({ jobs, health, upsert, remove, act, error }: Props) {
  const [settings, setSettings] = useState<ImageSettings>(load);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(settings));
    } catch {} // storage blocked: settings just won't be remembered
  }, [settings]);
  const set = (patch: Partial<ImageSettings>) => setSettings((s) => ({ ...s, ...patch }));
  const offline = health !== null && health.comfyui !== "ok";
  const big = settings.width * settings.height > 600_000;

  return (
    <>
      <section className="card" id="image-form">
        <label htmlFor="image-prompt">Describe the image</label>
        <textarea
          id="image-prompt"
          rows={3}
          value={settings.prompt}
          onChange={(e) => set({ prompt: e.target.value })}
          placeholder="a cozy reading nook by a rainy window, warm lamp light, plants"
        />
        <label className="style">
          Style
          <input value={settings.style} placeholder="watercolor illustration" onChange={(e) => set({ style: e.target.value })} />
        </label>
        <div className="suggestion-group quick">
          {STYLES.map((s) => (
            <button key={s} className={`chip small ${settings.style === s ? "on" : ""}`} onClick={() => set({ style: s })}>
              {s}
            </button>
          ))}
        </div>
        <div className="row">
          <label>
            Size
            <select
              value={`${settings.width}x${settings.height}`}
              onChange={(e) => {
                const [width, height] = e.target.value.split("x").map(Number);
                set({ width, height });
              }}
            >
              {SIZES.map(([w, h, label]) => (
                <option key={label} value={`${w}x${h}`}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Images
            <select value={settings.count} onChange={(e) => set({ count: Number(e.target.value) })}>
              {[1, 2, 3, 4].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <div className="generate">
            <button disabled={offline || settings.prompt.trim().length < 2} onClick={() => act(async () => upsert(await createImages(settings)))}>
              Create images
            </button>
            {settings.prompt.trim() && <span className="muted">about {Math.round((25 + settings.count * (big ? 15 : 5)) / 5) * 5} s</span>}
          </div>
        </div>
        {error && <p className="error">{error}</p>}
      </section>

      <section>
        <h2>Image library</h2>
        {jobs.length === 0 && <p className="muted">No images yet.</p>}
        {jobs.map((job) => {
          const s = { ...IMAGE_DEFAULTS, ...(job.settings as Partial<ImageSettings>) };
          const active = isActive(job);
          const images = (job.result?.images ?? []) as string[];
          return (
            <article key={job.id} className="card song">
              <div className="row between">
                <div>
                  <strong>{s.prompt}</strong>
                  <div className="muted">
                    {s.width}×{s.height}
                    {s.style ? ` · ${s.style}` : ""}
                  </div>
                </div>
                <span className={`state ${job.state}`}>{job.state === "done" ? "" : job.state}</span>
              </div>
              {active && (
                <div className="progress">
                  <span>{job.stage || "waiting"}</span>
                  <progress value={job.progress} max={1} />
                </div>
              )}
              {job.error && <p className="error">{job.error}</p>}
              {images.length > 0 && (
                <div className="grid">
                  {images.map((src, i) => (
                    <figure key={src}>
                      <a href={outputUrl(job, src)} target="_blank" rel="noreferrer">
                        <img src={outputUrl(job, src)} alt={`${s.prompt} — ${i + 1}`} />
                      </a>
                      <figcaption>
                        <span>#{i + 1}</span>
                        <a className="link-small" href={outputUrl(job, src)} download={`${s.prompt.slice(0, 40)} ${i + 1}.png`}>
                          Download
                        </a>
                      </figcaption>
                    </figure>
                  ))}
                </div>
              )}
              <div className="actions">
                {active ? (
                  <button className="secondary" onClick={() => act(async () => upsert(await cancelJob(job.id)))}>Cancel</button>
                ) : (
                  <>
                    <button className="secondary" onClick={() => act(async () => upsert(await rerunJob(job.id)))}>More like this</button>
                    <button
                      className="secondary"
                      onClick={() => {
                        setSettings(s);
                        document.getElementById("image-form")?.scrollIntoView({ behavior: "smooth" });
                      }}
                    >
                      Edit &amp; re-run
                    </button>
                    <button
                      className="secondary danger"
                      onClick={() =>
                        confirm("Delete these images?") &&
                        act(async () => {
                          await deleteJob(job.id);
                          remove(job.id);
                        })
                      }
                    >
                      Delete
                    </button>
                  </>
                )}
              </div>
            </article>
          );
        })}
      </section>
    </>
  );
}

const KEY = "happica.images";

function load(): ImageSettings {
  try {
    return { ...IMAGE_DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") };
  } catch {
    return IMAGE_DEFAULTS;
  }
}
