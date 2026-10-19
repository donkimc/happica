import { useEffect, useState } from "react";
import { Health, Job, JobSettings, listVoices, Voice } from "./api";
import MusicPicker from "./MusicPicker";
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
  onPrompt: (p: string) => void;
  onSettings: (s: JobSettings) => void;
  onSubmit: () => void;
  error: string | null;
}

export default function GenerateForm({ prompt, settings, health, library, onPrompt, onSettings, onSubmit, error }: Props) {
  const [voices, setVoices] = useState<Voice[]>([]);
  useEffect(() => {
    listVoices().then(setVoices).catch(() => {});
  }, []);

  const set = (patch: Partial<JobSettings>) => onSettings({ ...settings, ...patch });
  const llm = settings.scene_mode === "llm";
  const llmUnavailable = llm && health?.ollama !== undefined && health.ollama !== "ok";
  const byLanguage = voices.reduce<Record<string, Voice[]>>((acc, v) => {
    (acc[v.language] ??= []).push(v);
    return acc;
  }, {});

  const sceneCount = llm
    ? settings.max_scenes
    : Math.min(settings.max_scenes, prompt.split("\n").filter((l) => l.trim()).length);

  return (
    <section className="card" id="generate">
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
      <textarea id="prompt" rows={5} value={prompt} onChange={(e) => onPrompt(e.target.value)} placeholder={PLACEHOLDERS[settings.scene_mode]} />
      {llmUnavailable && (
        <p className="error">
          {health?.ollama === "down"
            ? "Ollama isn't running — start the Ollama app to use story mode."
            : `The model ${health?.llm_model} isn't installed — run: ollama pull ${health?.llm_model}`}
        </p>
      )}

      <label className="style">
        Style (added to every scene)
        <input value={settings.style} onChange={(e) => set({ style: e.target.value })} />
      </label>

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
        <label>
          Motion
          <select value={settings.motion} onChange={(e) => set({ motion: e.target.value as JobSettings["motion"] })}>
            <option value="none">Images only</option>
            <option value="kenburns">Pan &amp; zoom (fast)</option>
            <option value="ltx">AI motion — LTX-Video (slow)</option>
          </select>
        </label>
        <label>
          {settings.narrate ? "Min seconds per scene" : "Seconds per scene"}
          <select value={settings.clip_seconds} onChange={(e) => set({ clip_seconds: Number(e.target.value) })}>
            {[2, 3, 4, 5, 6].map((s) => (
              <option key={s} value={s}>
                {s}s
              </option>
            ))}
          </select>
        </label>
      </div>

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
                    {v.name} ({v.gender})
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

      <MusicPicker value={settings} library={library} narrated={settings.narrate} onChange={set} />

      <div className="row">
        <div className="generate">
          <button disabled={!prompt.trim() || llmUnavailable} onClick={onSubmit}>
            Generate
          </button>
          {prompt.trim() && <span className="muted">{formatDuration(estimateSeconds(settings, sceneCount))}</span>}
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
