import { Dispatch, SetStateAction, useEffect, useState } from "react";
import { Health, Job, JobSettings, listVoices, Voice } from "./api";
import SoundPicker, { libraryTracks } from "./MusicPicker";
import SlidesEditor, { SlideItem } from "./SlidesEditor";
import { estimateSeconds, formatDuration, matches, PRESETS } from "./presets";

export const DEFAULTS: JobSettings = {
  width: 768,
  height: 512,
  max_scenes: 4,
  motion: "kenburns",
  style: "cinematic, highly detailed, soft lighting",
  clip_seconds: 3,
  scene_mode: "lines",
  narrate: false,
  voice: "af_heart",
  subtitles: "burn",
  music: "off",
  music_track: "",
  music_title: "",
  music_volume: 0.3,
  ambience: "off",
  ambience_track: "",
  ambience_title: "",
  ambience_volume: 0.35,
};

const PLACEHOLDERS = {
  lines: "A lighthouse on a cliff at sunset\nWaves crashing against the rocks\nSeagulls flying over the sea",
  llm: "An old fisherman rows out before dawn. He waits all morning but catches nothing. At noon a golden fish leaps beside his boat — he smiles and lets it go.",
};

interface Props {
  prompt: string;
  settings: JobSettings;
  health: Health | null;
  library: Job[];
  sfxLibrary: Job[];
  characters?: Job[];
  slidesMode?: boolean;
  slides?: SlideItem[];
  onSlides?: Dispatch<SetStateAction<SlideItem[]>>;
  imageLibrary?: Job[];
  musicVideo?: boolean;
  onPrompt: (p: string) => void;
  onSettings: (s: JobSettings) => void;
  onSubmit: () => void;
  error: string | null;
}

export default function GenerateForm({ prompt, settings, health, library, sfxLibrary, characters = [], slidesMode = false, slides = [], onSlides, imageLibrary = [], musicVideo = false, onPrompt, onSettings, onSubmit, error }: Props) {
  const [voices, setVoices] = useState<Voice[]>([]);
  useEffect(() => {
    listVoices().then(setVoices).catch(() => {});
  }, []);

  const set = (patch: Partial<JobSettings>) => onSettings({ ...settings, ...patch });
  const llm = !slidesMode && !musicVideo && settings.scene_mode === "llm";
  const songs = libraryTracks(library);
  const songTrack = settings.song_track && songs.some((t) => t.value === settings.song_track) ? settings.song_track : songs[0]?.value ?? "";
  const songSeconds = (() => {
    const job = library.find((j) => j.id === songTrack.split("/")[0]);
    return job?.result ? ((job.result as unknown as { tracks: { flac: string; duration: number }[] }).tracks.find((t) => t.flac === songTrack)?.duration ?? 30) : 30;
  })();
  const llmUnavailable = llm && health?.ollama !== undefined && health.ollama !== "ok";
  const byLanguage = voices.reduce<Record<string, Voice[]>>((acc, v) => {
    (acc[v.language] ??= []).push(v);
    return acc;
  }, {});

  const sceneCount = musicVideo
    ? Math.max(2, Math.min(16, Math.round(songSeconds / Math.max(2, settings.clip_seconds))))
    : slidesMode
    ? slides.length
    : llm
      ? settings.max_scenes
      : Math.min(settings.max_scenes, prompt.split("\n").filter((l) => l.trim()).length);
  const ready = musicVideo ? !!songTrack : slidesMode ? slides.length > 0 : prompt.trim().length > 0;

  return (
    <section className="card" id="generate">
      {musicVideo && (
        <div className="row tight-top">
          <label className="grow">
            Song from your Music library
            <select value={songTrack} onChange={(e) => set({ song_track: e.target.value })}>
              {songs.length === 0 && <option value="">Make a song in the Music tab first</option>}
              {songs.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          {songTrack && <audio key={songTrack} className="song-preview" src={`/outputs/${songTrack.replace(/\.flac$/, ".mp3")}`} controls preload="none" />}
        </div>
      )}

      {!slidesMode && !musicVideo && (
      <div className="presets" aria-label="Presets">
        {PRESETS.map((p) => (
          <button
            key={p.id}
            className={`chip ${matches(p, settings) ? "on" : ""}`}
            title={p.hint}
            onClick={() => onSettings({ ...settings, ...p.settings })}
          >
            {p.name}
          </button>
        ))}
      </div>
      )}

      {slidesMode && onSlides && <SlidesEditor slides={slides} onChange={onSlides} imageLibrary={imageLibrary} />}

      {!slidesMode && !musicVideo && (
      <>
      <div className="segmented" role="radiogroup" aria-label="How to split into scenes">
        <button
          role="radio"
          aria-checked={!llm}
          className={!llm ? "on" : ""}
          onClick={() => set({ scene_mode: "lines" })}
        >
          One line per scene
        </button>
        <button
          role="radio"
          aria-checked={llm}
          className={llm ? "on" : ""}
          onClick={() => set({ scene_mode: "llm", narrate: true })}
        >
          Story — AI writes the scenes
        </button>
      </div>

      <label htmlFor="prompt">
        {llm ? "Tell a story — the AI splits it into scenes and writes the narration" : "Describe your video — one line per scene"}
      </label>
      <textarea id="prompt" rows={5} value={prompt} onChange={(e) => onPrompt(e.target.value)} placeholder={PLACEHOLDERS[llm ? "llm" : "lines"]} />
      {llmUnavailable && (
        <p className="error">
          {health?.ollama === "down"
            ? "Ollama isn't running — start the Ollama app to use story mode."
            : `The model ${health?.llm_model} isn't installed — run: ollama pull ${health?.llm_model}`}
        </p>
      )}

      </>
      )}

      {!slidesMode && (
      <label className="style">
        {musicVideo ? "Visual style" : "Style (added to every scene)"}
        <input value={settings.style} onChange={(e) => set({ style: e.target.value })} />
      </label>
      )}

      {!slidesMode && !musicVideo && characters.length > 0 && (
        <div className="row">
          <label>
            Main character
            <select value={settings.character ?? ""} onChange={(e) => set({ character: e.target.value })}>
              <option value="">None</option>
              {characters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.settings.name as string}
                </option>
              ))}
            </select>
          </label>
          {settings.character && (
            <span className="muted character-hint">
              {llm
                ? `The AI writes ${characters.find((c) => c.id === settings.character)?.settings.name as string} into the story; scenes with them get their face.`
                : `Write ${characters.find((c) => c.id === settings.character)?.settings.name as string} in the lines where they appear (or nowhere, to have them in every scene).`}
            </span>
          )}
        </div>
      )}

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
            <option value="768x512">768×512 (landscape)</option>
            <option value="512x768">512×768 (portrait)</option>
            <option value="512x512">512×512 (square)</option>
          </select>
        </label>
        {!slidesMode && !musicVideo && (
        <label>
          {llm ? "Max scenes" : "Scenes"}
          <input
            type="number"
            min={1}
            max={12}
            value={settings.max_scenes}
            onChange={(e) => set({ max_scenes: Number(e.target.value) })}
          />
        </label>
        )}
        <label>
          Motion
          <select value={settings.motion} onChange={(e) => set({ motion: e.target.value as JobSettings["motion"] })}>
            <option value="none">Images only</option>
            <option value="kenburns">Pan &amp; zoom (fast)</option>
            <option value="ltx">AI motion — LTX-Video (slow)</option>
          </select>
        </label>
        <label>
          {musicVideo ? "Seconds per scene (about)" : settings.narrate ? "Min seconds per scene" : "Seconds per scene"}
          <select value={settings.clip_seconds} onChange={(e) => set({ clip_seconds: Number(e.target.value) })}>
            {[2, 3, 4, 5, 6].map((s) => (
              <option key={s} value={s}>
                {s}s
              </option>
            ))}
          </select>
        </label>
      </div>

      {!musicVideo && (
      <div className="row">
        <label className="check">
          <input type="checkbox" checked={settings.narrate} onChange={(e) => set({ narrate: e.target.checked })} />
          Narration
        </label>
        <label>
          Voice
          <select value={settings.voice} disabled={!settings.narrate} onChange={(e) => set({ voice: e.target.value })}>
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
        <label>
          Subtitles
          <select
            value={settings.subtitles}
            disabled={!settings.narrate}
            onChange={(e) => set({ subtitles: e.target.value as JobSettings["subtitles"] })}
          >
            <option value="burn">Burned in</option>
            <option value="soft">Toggleable track</option>
            <option value="off">Off</option>
          </select>
        </label>
      </div>
      )}

      {!musicVideo && (
        <SoundPicker value={settings} musicLibrary={library} sfxLibrary={sfxLibrary} narrated={settings.narrate} onChange={set} />
      )}

      <div className="row">
        <div className="generate">
          <button disabled={!ready || llmUnavailable} onClick={onSubmit}>
            Generate
          </button>
          {ready && (
            <span className="muted">
              {formatDuration(
                musicVideo
                  ? estimateSeconds({ ...settings, scene_mode: "llm", narrate: false, music: "off", ambience: "off" }, sceneCount) + 10
                  : estimateSeconds(settings, sceneCount) - (slidesMode ? 25 + 5 * sceneCount : 0),
              )}
              {musicVideo ? ` · ${sceneCount} scenes` : ""}
            </span>
          )}
        </div>
      </div>

      {settings.motion === "ltx" && (
        <p className="muted">AI motion renders each scene with LTX-Video on your GPU — about 6–7 minutes per scene.</p>
      )}
      {settings.narrate && settings.voice[0] !== "a" && settings.voice[0] !== "b" && (
        <p className="muted">Write the narration in the voice's language — the voice reads whatever text it gets.</p>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
