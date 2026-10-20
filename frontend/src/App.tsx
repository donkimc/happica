import { useEffect, useRef, useState } from "react";
import {
  cancelJob,
  changeSound,
  createJob,
  deleteJob,
  getHealth,
  getSession,
  Health,
  logout,
  isActive,
  Job,
  JobSettings,
  listJobs,
  regenerateScene,
  regenerateSound,
  rerunJob,
  watchJob,
} from "./api";
import GenerateForm, { DEFAULTS } from "./GenerateForm";
import JobDetail from "./JobDetail";
import JobGallery from "./JobGallery";
import Login from "./Login";
import ApiKeys from "./ApiKeys";
import MusicStudio from "./MusicStudio";
import ImageStudio from "./ImageStudio";
import SfxStudio from "./SfxStudio";
import { SlideItem } from "./SlidesEditor";
import { libraryTracks } from "./MusicPicker";
import VoiceStudio from "./VoiceStudio";
import CharacterStudio from "./CharacterStudio";

type Tab = "video" | "musicvideo" | "image" | "music" | "sfx" | "voice" | "character";
const TABS: { id: Tab; label: string }[] = [
  { id: "image", label: "Images" },
  { id: "video", label: "Story Video" },
  { id: "music", label: "Music" },
  { id: "musicvideo", label: "Music Video" },
  { id: "sfx", label: "Sound FX" },
  { id: "voice", label: "Voiceover" },
  { id: "character", label: "Characters" },
];
// #slides (the old Slideshow tab) opens Story Video, where My pictures mode now lives
const tabFromHash = (): Tab => TABS.find((t) => `#${t.id}` === location.hash && t.id !== "video")?.id ?? "video";

export default function App() {
  const [authState, setAuthState] = useState<"checking" | "in" | "out">("checking");
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [settings, setSettings] = useState<JobSettings>(() =>
    location.hash === "#slides" ? { ...loadSettings(), scene_mode: "slides" } : loadSettings(),
  );
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(tabFromHash);
  const [showKeys, setShowKeys] = useState(false);
  const [slides, setSlides] = useState<SlideItem[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(SLIDES_KEY) ?? "[]");
    } catch {
      return [];
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(SLIDES_KEY, JSON.stringify(slides));
    } catch {} // storage blocked: the slide list just won't survive a reload
  }, [slides]);
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

  // Login: check the session once, and drop back to the login screen whenever the backend says 401
  useEffect(() => {
    getSession()
      .then((s) => setAuthState(s.logged_in ? "in" : "out"))
      .catch(() => setAuthState("out"));
    const onUnauthorized = () => setAuthState("out");
    addEventListener("happica:unauthorized", onUnauthorized);
    return () => removeEventListener("happica:unauthorized", onUnauthorized);
  }, []);

  useEffect(() => {
    if (authState !== "in") return;
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
  }, [authState]);

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

  // Story Video, Slideshow and Music Video share the video pipeline; each tab shows its own history
  const mvTab = tab === "musicvideo";
  const videoJobs = jobs.filter((j) => j.kind === "video" && (j.settings.scene_mode === "musicvideo") === mvTab);
  const musicJobs = jobs.filter((j) => j.kind === "music");
  const sfxJobs = jobs.filter((j) => j.kind === "sfx");
  const imageJobs = jobs.filter((j) => j.kind === "image");
  const characterJobs = jobs.filter((j) => j.kind === "character");
  const readyCharacters = characterJobs.filter((j) => j.state === "done");
  const selected = videoJobs.find((j) => j.id === selectedId);
  const switchTab = (t: Tab) => {
    setError(null);
    setTab(t);
    history.replaceState(null, "", t === "video" ? location.pathname : `#${t}`);
  };

  if (authState === "checking") return <main className="login-page" />;
  if (authState === "out") return <Login onLoggedIn={() => setAuthState("in")} />;

  return (
    <main>
      <header>
        <div className="brand">
          <h1>Happica</h1>
          <nav className="tabs" aria-label="Studio">
            {TABS.map((t) => (
              <button key={t.id} className={tab === t.id ? "on" : ""} aria-current={tab === t.id} onClick={() => switchTab(t.id)}>
                {t.label}
              </button>
            ))}
          </nav>
        </div>
        <div className="header-right">
          <a className="guide-link" href="/guide.html" target="_blank" rel="noreferrer">
            How to use
          </a>
          <StatusPill health={health} />
          <button className="link" onClick={() => setShowKeys((v) => !v)}>
            API keys
          </button>
          <button className="link" onClick={() => logout().finally(() => setAuthState("out"))}>
            Log out
          </button>
        </div>
      </header>

      {showKeys && <ApiKeys onClose={() => setShowKeys(false)} />}

      {tab === "character" ? (
        <CharacterStudio
          characters={characterJobs}
          images={imageJobs}
          upsert={upsert}
          remove={(id) => setJobs((prev) => prev.filter((j) => j.id !== id))}
          act={act}
          error={error}
        />
      ) : tab === "voice" ? (
        <VoiceStudio
          jobs={jobs.filter((j) => j.kind === "voice")}
          upsert={upsert}
          remove={(id) => setJobs((prev) => prev.filter((j) => j.id !== id))}
          act={act}
          error={error}
        />
      ) : tab === "image" ? (
        <ImageStudio
          characters={readyCharacters}
          jobs={imageJobs}
          health={health}
          upsert={upsert}
          remove={(id) => setJobs((prev) => prev.filter((j) => j.id !== id))}
          act={act}
          error={error}
        />
      ) : tab === "sfx" ? (
        <SfxStudio
          jobs={sfxJobs}
          health={health}
          upsert={upsert}
          remove={(id) => setJobs((prev) => prev.filter((j) => j.id !== id))}
          act={act}
          error={error}
        />
      ) : tab === "music" ? (
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
        library={musicJobs}
        sfxLibrary={sfxJobs}
        characters={readyCharacters}
        onPrompt={setPrompt}
        onSettings={setSettings}
        musicVideo={mvTab}
        slides={slides}
        onSlides={setSlides}
        imageLibrary={imageJobs}
        onSubmit={() =>
          act(async () =>
            select(
              mvTab
                ? await (async () => {
                    const track = settings.song_track && libraryTracks(musicJobs).some((t) => t.value === settings.song_track)
                      ? settings.song_track
                      : libraryTracks(musicJobs)[0]?.value ?? "";
                    const title = libraryTracks(musicJobs).find((t) => t.value === track)?.title ?? "song";
                    return createJob(`Music video: ${title}`, { ...settings, scene_mode: "musicvideo", song_track: track });
                  })()
                : settings.scene_mode === "slides"
                ? await createJob(
                    settings.theme?.trim().slice(0, 80) || slides.find((sl) => sl.text.trim())?.text.slice(0, 80) || `My pictures (${slides.length})`,
                    { ...settings, slides },
                  )
                : await createJob(prompt, settings),
            ),
          )
        }
        error={error}
      />

      {selected && (
        <JobDetail
          key={selected.id}
          job={selected}
          onCancel={() => act(async () => upsert(await cancelJob(selected.id)))}
          onRerun={(keepSeed) => act(async () => select(await rerunJob(selected.id, keepSeed)))}
          onEdit={() => {
            // Story mode: the original story. Line mode: the current scene prompts, so edits made
            // while regenerating single scenes carry over.
            const story = selected.settings.scene_mode === "llm";
            if (selected.settings.scene_mode === "slides") setSlides(selected.settings.slides ?? []);
            else if (selected.settings.scene_mode === "musicvideo") setPrompt(prompt);
            else setPrompt((!story && selected.result?.scenes.join("\n")) || selected.prompt);
            const { seed: _seed, ...rest } = selected.settings; // edited re-runs get a fresh seed
            // Music videos store their forced soundtrack; keep the user's own Story Video sound choices instead
            setSettings(
              rest.scene_mode === "musicvideo"
                ? (() => {
                    const { song_track, style, width, height, motion, clip_seconds } = { ...DEFAULTS, ...rest };
                    return { ...settings, song_track, style, width, height, motion, clip_seconds };
                  })()
                : { ...DEFAULTS, ...rest },
            );
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
          library={musicJobs}
          sfxLibrary={sfxJobs}
          onChangeSound={(choice) => act(async () => upsert(await changeSound(selected.id, choice)))}
          onNewAuto={(layer) => act(async () => upsert(await regenerateSound(selected.id, layer)))}
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
const SLIDES_KEY = "happica.slides";

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
