import { useEffect, useState } from "react";
import { cancelJob, createVoiceover, deleteJob, isActive, Job, listVoices, MusicResult, outputUrl, rerunJob, Voice, VoiceSettings } from "./api";
import TrackPlayer from "./TrackPlayer";

export const VOICE_DEFAULTS: VoiceSettings = { text: "", voice: "af_heart", speed: 1.0 };

interface Props {
  jobs: Job[];
  upsert: (job: Job) => void;
  remove: (id: string) => void;
  act: (fn: () => Promise<unknown>) => void;
  error: string | null;
}

export default function VoiceStudio({ jobs, upsert, remove, act, error }: Props) {
  const [settings, setSettings] = useState<VoiceSettings>(load);
  const [voices, setVoices] = useState<Voice[]>([]);
  useEffect(() => {
    listVoices().then(setVoices).catch(() => {});
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(settings));
    } catch {} // storage blocked: settings just won't be remembered
  }, [settings]);
  const set = (patch: Partial<VoiceSettings>) => setSettings((s) => ({ ...s, ...patch }));
  const byLanguage = voices.reduce<Record<string, Voice[]>>((acc, v) => {
    (acc[v.language] ??= []).push(v);
    return acc;
  }, {});
  const voiceName = (id: string) => voices.find((v) => v.id === id)?.name ?? id;
  const words = settings.text.trim() ? settings.text.trim().split(/\s+/).length : 0;

  return (
    <>
      <section className="card" id="voice-form">
        <label htmlFor="voice-text">Text to read — blank lines make paragraph pauses</label>
        <textarea
          id="voice-text"
          rows={8}
          maxLength={5000}
          value={settings.text}
          onChange={(e) => set({ text: e.target.value })}
          placeholder="Welcome to the channel. Today we're visiting a tiny village on the coast…"
        />
        <div className="row">
          <label>
            Voice
            <select value={settings.voice} onChange={(e) => set({ voice: e.target.value })}>
              {voices.length === 0 && <option value={settings.voice}>{settings.voice}</option>}
              {Object.entries(byLanguage).map(([language, vs]) => (
                <optgroup key={language} label={language}>
                  {vs.map((v) => (
                    <option key={v.id} value={v.id}>
                      {/^(Female|Male) /.test(v.name) ? v.name : `${v.name} (${v.gender})`}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <label className="volume">
            Speed {settings.speed.toFixed(2)}×
            <input type="range" min={0.6} max={1.5} step={0.05} value={settings.speed} onChange={(e) => set({ speed: Number(e.target.value) })} />
          </label>
          <div className="generate">
            <button disabled={!settings.text.trim()} onClick={() => act(async () => upsert(await createVoiceover(settings)))}>
              Create voiceover
            </button>
            {words > 0 && (
              <span className="muted">
                {words} words · ~{Math.max(1, Math.round(words / 2.6 / settings.speed))} s of speech
              </span>
            )}
          </div>
        </div>
        {settings.voice[0] !== "a" && settings.voice[0] !== "b" && (
          <p className="muted">Write in the voice's language — it reads whatever text it gets.</p>
        )}
        {error && <p className="error">{error}</p>}
      </section>

      <section>
        <h2>Voiceover library</h2>
        {jobs.length === 0 && <p className="muted">No voiceovers yet.</p>}
        {jobs.map((job) => {
          const s = { ...VOICE_DEFAULTS, ...(job.settings as Partial<VoiceSettings>) };
          const result = job.result as unknown as (MusicResult & { subtitles?: string }) | null;
          const active = isActive(job);
          return (
            <article key={job.id} className="card song">
              <div className="row between">
                <div>
                  <strong>{s.text.length > 90 ? s.text.slice(0, 90) + "…" : s.text}</strong>
                  <div className="muted">
                    {voiceName(s.voice)} · {s.speed.toFixed(2)}×
                    {result?.subtitles && (
                      <>
                        {" · "}
                        <a href={outputUrl(job, result.subtitles)} download>
                          Subtitles (.srt)
                        </a>
                      </>
                    )}
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
              {result?.tracks?.map((t) => (
                <TrackPlayer key={t.mp3} job={job} track={t} label="Voiceover" downloadName={`voiceover - ${voiceName(s.voice)}.mp3`} />
              ))}
              <div className="actions">
                {active ? (
                  <button className="secondary" onClick={() => act(async () => upsert(await cancelJob(job.id)))}>Cancel</button>
                ) : (
                  <>
                    <button
                      className="secondary"
                      onClick={() => {
                        setSettings(s);
                        document.getElementById("voice-form")?.scrollIntoView({ behavior: "smooth" });
                      }}
                    >
                      Edit &amp; re-run
                    </button>
                    <button className="secondary" onClick={() => act(async () => upsert(await rerunJob(job.id)))}>Re-run</button>
                    <button
                      className="secondary danger"
                      onClick={() =>
                        confirm("Delete this voiceover?") &&
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

const KEY = "happica.voice";

function load(): VoiceSettings {
  try {
    return { ...VOICE_DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") };
  } catch {
    return VOICE_DEFAULTS;
  }
}
