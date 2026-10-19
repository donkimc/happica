import { JobSettings } from "./api";

export const DEFAULTS: JobSettings = {
  width: 768,
  height: 512,
  max_scenes: 4,
  motion: "kenburns",
  style: "cinematic, highly detailed, soft lighting",
  clip_seconds: 3,
};

interface Props {
  prompt: string;
  settings: JobSettings;
  onPrompt: (p: string) => void;
  onSettings: (s: JobSettings) => void;
  onSubmit: () => void;
  error: string | null;
}

export default function GenerateForm({ prompt, settings, onPrompt, onSettings, onSubmit, error }: Props) {
  const set = (patch: Partial<JobSettings>) => onSettings({ ...settings, ...patch });
  return (
    <section className="card" id="generate">
      <label htmlFor="prompt">Describe your video — one line per scene</label>
      <textarea
        id="prompt"
        rows={5}
        value={prompt}
        onChange={(e) => onPrompt(e.target.value)}
        placeholder={"A lighthouse on a cliff at sunset\nWaves crashing against the rocks\nSeagulls flying over the sea"}
      />
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
          Scenes
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
          Seconds per scene
          <select value={settings.clip_seconds} onChange={(e) => set({ clip_seconds: Number(e.target.value) })}>
            {[2, 3, 4, 5, 6].map((s) => (
              <option key={s} value={s}>
                {s}s
              </option>
            ))}
          </select>
        </label>
        <button disabled={!prompt.trim()} onClick={onSubmit}>
          Generate
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
