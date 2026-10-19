import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  cancelJob,
  createMusic,
  deleteJob,
  draftSong,
  getMusicOptions,
  Health,
  isActive,
  Job,
  MusicOptions,
  MusicResult,
  MusicSettings,
  outputUrl,
  rerunJob,
} from "./api";
import TrackPlayer, { fmtTime } from "./TrackPlayer";

export const MUSIC_DEFAULTS: MusicSettings = {
  title: "",
  tags: "",
  lyrics: "",
  instrumental: false,
  duration: 60,
  bpm: 110,
  keyscale: "C major",
  timesignature: "4",
  language: "en",
  variations: 2,
};

const TAG_SUGGESTIONS: Record<string, string[]> = {
  Genre: ["pop", "lofi hip hop", "acoustic folk", "cinematic orchestral", "synthwave", "jazz", "indie rock", "ambient", "edm"],
  Mood: ["uplifting", "melancholic", "calm", "energetic", "dreamy", "epic"],
  Instruments: ["piano", "acoustic guitar", "strings", "warm synth pads", "808 bass", "soft drums"],
  Vocals: ["female vocals", "male vocals", "choir", "whispered vocals"],
};
const SECTIONS = ["intro", "verse", "pre-chorus", "chorus", "bridge", "outro"];
const DURATIONS = [30, 60, 90, 120, 180, 240];

const LANGUAGE_NAMES = new Intl.DisplayNames(["en"], { type: "language" });
const languageName = (code: string) => {
  try {
    return LANGUAGE_NAMES.of(code) ?? code;
  } catch {
    return code;
  }
};


/** ~35 s to load the models, then about 1.2 s of work per second of song, per variation (M2, 16 GB). */
const estimateSeconds = (s: MusicSettings) => 35 + s.variations * s.duration * 1.2;

interface Props {
  jobs: Job[];
  health: Health | null;
  upsert: (job: Job) => void;
  remove: (id: string) => void;
  act: (fn: () => Promise<unknown>) => void;
  error: string | null;
}

export default function MusicStudio({ jobs, health, upsert, remove, act, error }: Props) {
  const [settings, setSettings] = useState<MusicSettings>(loadSettings);
  const [options, setOptions] = useState<MusicOptions | null>(null);
  const lyricsRef = useRef<HTMLTextAreaElement>(null);
  const caret = useRef<number | null>(null);
  const [mode, setMode] = useState<"simple" | "custom">(() => (localStorage.getItem(MODE_KEY) === "custom" ? "custom" : "simple"));
  const [description, setDescription] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [draftedFrom, setDraftedFrom] = useState<string | null>(null);

  useEffect(() => {
    getMusicOptions().then(setOptions).catch(() => {});
  }, [health?.music]);
  useEffect(() => {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch {} // storage blocked: settings just won't be remembered
  }, [settings]);

  const set = (patch: Partial<MusicSettings>) => setSettings((s) => ({ ...s, ...patch }));
  const switchMode = (m: "simple" | "custom") => {
    setMode(m);
    try {
      localStorage.setItem(MODE_KEY, m);
    } catch {}
  };

  /** Ask the local LLM for a draft; review it in Custom mode, or create the song straight away. */
  function writeSong(createNow: boolean) {
    act(async () => {
      setDrafting(true);
      try {
        const draft = await draftSong(description, settings.instrumental, settings.duration);
        const next = { ...settings, ...draft };
        setSettings(next);
        setDraftedFrom(description);
        if (createNow) upsert(await createMusic(next));
        else switchMode("custom");
      } finally {
        setDrafting(false);
      }
    });
  }
  const offline = health?.music !== undefined && health.music !== "ok";
  const needsLyrics = !settings.instrumental && !settings.lyrics.trim();

  function addTag(tag: string) {
    const current = settings.tags.split(",").map((t) => t.trim()).filter(Boolean);
    if (!current.includes(tag)) set({ tags: [...current, tag].join(", ") });
  }

  function insertSection(name: string) {
    const ta = lyricsRef.current;
    const text = settings.lyrics;
    const at = ta?.selectionStart ?? text.length;
    const before = text.slice(0, at);
    const insert = `${before && !before.endsWith("\n") ? "\n" : ""}${before.trim() ? "\n" : ""}[${name}]\n`;
    caret.current = at + insert.length;
    set({ lyrics: before + insert + text.slice(at) });
  }

  // Put the cursor after an inserted section tag once React has written the new text into the textarea
  useLayoutEffect(() => {
    const ta = lyricsRef.current;
    if (caret.current === null || !ta) return;
    ta.focus();
    ta.setSelectionRange(caret.current, caret.current);
    caret.current = null;
  }, [settings.lyrics]);

  function edit(job: Job) {
    setSettings({ ...MUSIC_DEFAULTS, ...(job.settings as Partial<MusicSettings>) });
    setDraftedFrom(null);
    switchMode("custom");
    document.getElementById("music-form")?.scrollIntoView({ behavior: "smooth" });
  }

  return (
    <>
      <section className="card" id="music-form">
        <div className="segmented" role="radiogroup" aria-label="How to write the song">
          <button role="radio" aria-checked={mode === "simple"} className={mode === "simple" ? "on" : ""} onClick={() => switchMode("simple")}>
            Simple — describe it
          </button>
          <button role="radio" aria-checked={mode === "custom"} className={mode === "custom" ? "on" : ""} onClick={() => switchMode("custom")}>
            Custom — write it yourself
          </button>
        </div>

        {mode === "simple" && (
          <>
            <label htmlFor="song-idea">Describe the song — mood, genre, what it's about, any language</label>
            <textarea
              id="song-idea"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="A cozy rainy-day folk song about a lighthouse keeper waiting for the ships to come home"
            />
            <div className="row">
              <label className="check">
                <input type="checkbox" checked={settings.instrumental} onChange={(e) => set({ instrumental: e.target.checked })} />
                Instrumental
              </label>
              <label>
                Length
                <select value={settings.duration} onChange={(e) => set({ duration: Number(e.target.value) })}>
                  {DURATIONS.map((d) => (
                    <option key={d} value={d}>
                      {fmtTime(d)}
                    </option>
                  ))}
                </select>
              </label>
              <div className="generate">
                <button disabled={drafting || offline || description.trim().length < 3} onClick={() => writeSong(false)}>
                  {drafting ? "Writing…" : "Write song"}
                </button>
                <button className="secondary inline" disabled={drafting || offline || description.trim().length < 3} onClick={() => writeSong(true)}>
                  Write &amp; create
                </button>
              </div>
            </div>
            <p className="muted">
              {drafting
                ? "The AI is writing a title, style and lyrics — about 15–25 seconds."
                : "Write song lets you review and edit the lyrics before anything is generated. Write & create goes straight to two takes."}
            </p>
            {health?.ollama === "down" && <p className="error">Ollama isn't running — start the Ollama app, or use Custom mode.</p>}
          </>
        )}

        {mode === "custom" && (
        <>
        {draftedFrom && (
          <div className="draft-banner">
            <span>
              ✍️ Drafted by AI from “{draftedFrom.length > 80 ? draftedFrom.slice(0, 80) + "…" : draftedFrom}” — edit anything, then Create song.
            </span>
            <button className="link" disabled={drafting} onClick={() => writeSong(false)}>
              {drafting ? "Writing…" : "Write another"}
            </button>
          </div>
        )}
        <div className="row tight-top">
          <label className="grow">
            Title (optional)
            <input value={settings.title} placeholder="Untitled song" onChange={(e) => set({ title: e.target.value })} />
          </label>
          <label className="check">
            <input type="checkbox" checked={settings.instrumental} onChange={(e) => set({ instrumental: e.target.checked })} />
            Instrumental
          </label>
        </div>

        <label className="style">
          Style — genre, mood, instruments{settings.instrumental ? "" : ", vocals"}
          <input
            value={settings.tags}
            placeholder="indie pop, female vocals, acoustic guitar, warm, upbeat"
            onChange={(e) => set({ tags: e.target.value })}
          />
        </label>
        <div className="suggestions">
          {Object.entries(TAG_SUGGESTIONS)
            .filter(([group]) => !(settings.instrumental && group === "Vocals"))
            .map(([group, tags]) => (
              <div key={group} className="suggestion-group">
                <span className="muted">{group}</span>
                {tags.map((t) => (
                  <button key={t} className="chip small" onClick={() => addTag(t)}>
                    + {t}
                  </button>
                ))}
              </div>
            ))}
        </div>

        {!settings.instrumental && (
          <>
            <div className="row between lyrics-head">
              <label htmlFor="lyrics">Lyrics</label>
              <div className="section-buttons">
                {SECTIONS.map((s) => (
                  <button key={s} className="chip small" onClick={() => insertSection(s)}>
                    [{s}]
                  </button>
                ))}
              </div>
            </div>
            <textarea
              id="lyrics"
              ref={lyricsRef}
              rows={10}
              value={settings.lyrics}
              onChange={(e) => set({ lyrics: e.target.value })}
              placeholder={"[verse]\nMorning light on the harbor wall\nGulls are singing, I hear them call\n\n[chorus]\nSail away, sail away with me\nOver the silver and open sea"}
            />
          </>
        )}

        <div className="row">
          <label>
            Length
            <select value={settings.duration} onChange={(e) => set({ duration: Number(e.target.value) })}>
              {DURATIONS.map((d) => (
                <option key={d} value={d}>
                  {fmtTime(d)}
                </option>
              ))}
            </select>
          </label>
          <label>
            BPM
            <input type="number" min={30} max={300} value={settings.bpm} onChange={(e) => set({ bpm: Number(e.target.value) })} />
          </label>
          <label>
            Key
            <select value={settings.keyscale} onChange={(e) => set({ keyscale: e.target.value })}>
              {(options?.keys ?? [settings.keyscale]).map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
          <label>
            Time
            <select value={settings.timesignature} onChange={(e) => set({ timesignature: e.target.value })}>
              {(options?.timesignatures ?? [settings.timesignature]).map((t) => (
                <option key={t} value={t}>
                  {t}/4
                </option>
              ))}
            </select>
          </label>
          {!settings.instrumental && (
            <label>
              Lyrics language
              <select value={settings.language} onChange={(e) => set({ language: e.target.value })}>
                {(options?.languages ?? [settings.language])
                  .map((l) => [l, languageName(l)] as const)
                  .sort((a, b) => a[1].localeCompare(b[1]))
                  .map(([code, name]) => (
                    <option key={code} value={code}>
                      {name}
                    </option>
                  ))}
              </select>
            </label>
          )}
          <label>
            Takes
            <select value={settings.variations} onChange={(e) => set({ variations: Number(e.target.value) })}>
              <option value={2}>2 variations</option>
              <option value={1}>1</option>
            </select>
          </label>
          <div className="generate">
            <button
              disabled={offline || !settings.tags.trim() || needsLyrics}
              onClick={() => act(async () => upsert(await createMusic(settings)))}
            >
              Create song
            </button>
            {settings.tags.trim() && <span className="muted">about {Math.round(estimateSeconds(settings) / 60)} min</span>}
          </div>
        </div>
        </>
        )}
        {offline && <p className="error">The music engine (ComfyUI on port 8189) isn't running — restart with scripts/start.sh.</p>}
        {mode === "custom" && needsLyrics && settings.tags.trim() && <p className="muted">Add lyrics, or switch on Instrumental.</p>}
        {error && <p className="error">{error}</p>}
      </section>

      <section>
        <h2>Library</h2>
        {jobs.length === 0 && <p className="muted">No songs yet — describe a style above and create one.</p>}
        {jobs.map((job) => (
          <SongCard
            key={job.id}
            job={job}
            onCancel={() => act(async () => upsert(await cancelJob(job.id)))}
            onRerun={() => act(async () => upsert(await rerunJob(job.id)))}
            onEdit={() => edit(job)}
            onDelete={() =>
              confirm("Delete this song and its files?") &&
              act(async () => {
                await deleteJob(job.id);
                remove(job.id);
              })
            }
          />
        ))}
      </section>
    </>
  );
}

interface CardProps {
  job: Job;
  onCancel: () => void;
  onRerun: () => void;
  onEdit: () => void;
  onDelete: () => void;
}

function SongCard({ job, onCancel, onRerun, onEdit, onDelete }: CardProps) {
  const s = job.settings as Partial<MusicSettings>;
  const result = job.result as unknown as MusicResult | null;
  const active = isActive(job);
  const [showLyrics, setShowLyrics] = useState(false);

  return (
    <article className="card song">
      <div className="row between">
        <div>
          <strong>{s.title || "Untitled song"}</strong>
          <div className="muted">
            {s.tags} · {fmtTime(s.duration ?? 0)} · {s.bpm} BPM · {s.keyscale}
            {s.instrumental ? " · instrumental" : ""}
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

      {result?.tracks.map((t, i) => (
        <TrackPlayer
          key={t.mp3}
          job={job}
          track={t}
          label={result.tracks.length > 1 ? `Take ${i + 1}` : "Song"}
          downloadName={`${s.title || "song"} - ${result.tracks.length > 1 ? `Take ${i + 1}` : "Song"}.mp3`}
        />
      ))}

      <div className="actions">
        {active ? (
          <button className="secondary" onClick={onCancel}>Cancel</button>
        ) : (
          <>
            <button className="secondary" onClick={onRerun}>Re-run</button>
            <button className="secondary" onClick={onEdit}>Edit &amp; re-run</button>
            {!s.instrumental && s.lyrics && (
              <button className="secondary" onClick={() => setShowLyrics((v) => !v)}>
                {showLyrics ? "Hide lyrics" : "Lyrics"}
              </button>
            )}
            <button className="secondary danger" onClick={onDelete}>Delete</button>
          </>
        )}
      </div>
      {showLyrics && <pre className="lyrics">{s.lyrics}</pre>}
    </article>
  );
}

const SETTINGS_KEY = "happica.music";
const MODE_KEY = "happica.music.mode";

function loadSettings(): MusicSettings {
  try {
    return { ...MUSIC_DEFAULTS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}") };
  } catch {
    return MUSIC_DEFAULTS;
  }
}
