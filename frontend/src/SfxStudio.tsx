import { useEffect, useState } from "react";
import { cancelJob, createSfx, deleteJob, Health, isActive, Job, MusicResult, rerunJob, SfxSettings } from "./api";
import TrackPlayer, { fmtTime } from "./TrackPlayer";

export const SFX_DEFAULTS: SfxSettings = { prompt: "", duration: 10, loop: false, enhance: true, variations: 2 };

// One click fills the description; loop is on for ambiences that sit under a video
const QUICK: { label: string; prompt: string; loop: boolean }[] = [
  { label: "Sea waves", prompt: "gentle ocean waves rolling onto a sandy beach", loop: true },
  { label: "Night", prompt: "summer night in the countryside, crickets and distant frogs", loop: true },
  { label: "Buddhist temple", prompt: "buddhist temple, slow bronze bell, monks chanting softly", loop: true },
  { label: "Falling leaves", prompt: "dry autumn leaves falling and rustling in a light wind", loop: true },
  { label: "Rain on window", prompt: "steady rain on a window, soft indoor ambience", loop: true },
  { label: "Campfire", prompt: "crackling campfire with popping embers", loop: true },
  { label: "Thunderstorm", prompt: "heavy rain with rolling distant thunder", loop: true },
  { label: "Forest birds", prompt: "morning forest with songbirds and a light breeze", loop: true },
  { label: "City street", prompt: "busy city street, traffic, distant horns, footsteps", loop: true },
  { label: "Door creak", prompt: "old wooden door slowly creaking open", loop: false },
  { label: "Footsteps", prompt: "footsteps walking on crunchy gravel", loop: false },
  { label: "Whoosh", prompt: "fast cinematic whoosh transition", loop: false },
];
const DURATIONS = [3, 5, 10, 20, 30, 45];

interface Props {
  jobs: Job[];
  health: Health | null;
  upsert: (job: Job) => void;
  remove: (id: string) => void;
  act: (fn: () => Promise<unknown>) => void;
  error: string | null;
}

export default function SfxStudio({ jobs, health, upsert, remove, act, error }: Props) {
  const [settings, setSettings] = useState<SfxSettings>(load);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(settings));
    } catch {} // storage blocked: settings just won't be remembered
  }, [settings]);

  const set = (patch: Partial<SfxSettings>) => setSettings((s) => ({ ...s, ...patch }));
  const offline = health?.music !== undefined && health.music !== "ok";
  const estimate = 15 + settings.variations * settings.duration * 0.8 + (settings.enhance ? 8 : 0);

  return (
    <>
      <section className="card" id="sfx-form">
        <label htmlFor="sfx-prompt">Describe the sound</label>
        <textarea
          id="sfx-prompt"
          rows={3}
          value={settings.prompt}
          onChange={(e) => set({ prompt: e.target.value })}
          placeholder="gentle ocean waves rolling onto a sandy beach, distant seagulls"
        />
        <div className="suggestion-group quick">
          {QUICK.map((q) => (
            <button key={q.label} className="chip small" onClick={() => set({ prompt: q.prompt, loop: q.loop })}>
              {q.label}
            </button>
          ))}
        </div>

        <div className="row">
          <label>
            Length
            <select value={settings.duration} onChange={(e) => set({ duration: Number(e.target.value) })}>
              {DURATIONS.map((d) => (
                <option key={d} value={d}>
                  {d} s
                </option>
              ))}
            </select>
          </label>
          <label className="check" title="Blends the end into the start so it repeats without a seam — for ambiences under videos">
            <input type="checkbox" checked={settings.loop} onChange={(e) => set({ loop: e.target.checked })} />
            Seamless loop
          </label>
          <label className="check" title="The local AI turns a short idea into a detailed sound description">
            <input type="checkbox" checked={settings.enhance} onChange={(e) => set({ enhance: e.target.checked })} />
            AI adds detail
          </label>
          <label>
            Takes
            <select value={settings.variations} onChange={(e) => set({ variations: Number(e.target.value) })}>
              <option value={2}>2 variations</option>
              <option value={1}>1</option>
            </select>
          </label>
          <div className="generate">
            <button
              disabled={offline || settings.prompt.trim().length < 2}
              onClick={() => act(async () => upsert(await createSfx(settings)))}
            >
              Create sound
            </button>
            {settings.prompt.trim() && <span className="muted">about {Math.max(1, Math.round(estimate / 10) * 10)} s</span>}
          </div>
        </div>
        <p className="muted">
          Ambiences (waves, rain, night) work best as a <strong>seamless loop</strong>; one-shots (a door, a whoosh) without.
        </p>
        {offline && <p className="error">The audio engine (ComfyUI on port 8189) isn't running — restart with scripts/start.sh.</p>}
        {error && <p className="error">{error}</p>}
      </section>

      <section>
        <h2>Sound library</h2>
        {jobs.length === 0 && <p className="muted">No sounds yet — pick one of the quick ideas above to start.</p>}
        {jobs.map((job) => {
          const s = { ...SFX_DEFAULTS, ...(job.settings as Partial<SfxSettings>) };
          const result = job.result as unknown as (MusicResult & { prompt_used?: string }) | null;
          const active = isActive(job);
          return (
            <article key={job.id} className="card song">
              <div className="row between">
                <div>
                  <strong>{s.prompt}</strong>
                  <div className="muted">
                    {fmtTime(s.duration)} · {s.loop ? "seamless loop" : "one-shot"}
                    {result?.prompt_used && result.prompt_used !== s.prompt ? ` · AI description: “${result.prompt_used}”` : ""}
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
              {result?.tracks?.map((t, i) => (
                <TrackPlayer
                  key={t.mp3}
                  job={job}
                  track={t}
                  loop={s.loop}
                  label={result.tracks.length > 1 ? `Take ${i + 1}` : "Sound"}
                  downloadName={`${s.prompt.slice(0, 40)} - ${i + 1}.mp3`}
                />
              ))}
              <div className="actions">
                {active ? (
                  <button className="secondary" onClick={() => act(async () => upsert(await cancelJob(job.id)))}>Cancel</button>
                ) : (
                  <>
                    <button className="secondary" onClick={() => act(async () => upsert(await rerunJob(job.id)))}>Re-run</button>
                    <button
                      className="secondary"
                      onClick={() => {
                        setSettings(s);
                        document.getElementById("sfx-form")?.scrollIntoView({ behavior: "smooth" });
                      }}
                    >
                      Edit &amp; re-run
                    </button>
                    <button
                      className="secondary danger"
                      onClick={() =>
                        confirm("Delete this sound and its files?") &&
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

const KEY = "happica.sfx";

function load(): SfxSettings {
  try {
    return { ...SFX_DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") };
  } catch {
    return SFX_DEFAULTS;
  }
}
