import { useState } from "react";
import { CaptionStyle, Job, JobSettings, ScriptScene, SfxCue, SongBlock } from "./api";
import { libraryTracks } from "./MusicPicker";

interface Props {
  title: string;
  settings: JobSettings;
  onTitle: (t: string) => void;
  onSettings: (patch: Partial<JobSettings>) => void;
  library?: Job[]; // Music library, for the song
  artCharacters?: Job[]; // cartoon characters made from reference art
}

const MAX_SCENES = 16;
const blank = (): ScriptScene => ({ image_prompt: "", narration: "", sfx: [] });

/** Turn a story file (the autohappymusica format, or the API's own script) into editor fields. */
export function parseStory(text: string): { title?: string; patch: Partial<JobSettings> } {
  const data = JSON.parse(text);
  const scenes = (Array.isArray(data) ? data : data.scenes ?? data.script) as Record<string, unknown>[];
  if (!Array.isArray(scenes) || !scenes.length) throw new Error("No scenes found — expected a \"scenes\" list");
  const cues = (sfx: unknown): SfxCue[] =>
    (Array.isArray(sfx) ? sfx : sfx ? [sfx] : []).map((c) => (typeof c === "string" ? { prompt: c } : (c as SfxCue)));
  const script: ScriptScene[] = scenes.slice(0, MAX_SCENES).map((s) => ({
    image_prompt: String(s.image_prompt ?? s.image ?? s.prompt ?? ""),
    narration: String(s.narration ?? s.text ?? ""),
    motion: s.motion_hint ? String(s.motion_hint) : typeof s.motion === "string" && !["kenburns", "ltx", "none"].includes(s.motion) ? s.motion : "",
    motion_mode: (s.motion_mode ?? (["kenburns", "ltx", "none"].includes(String(s.motion)) ? s.motion : null)) as ScriptScene["motion_mode"],
    sfx: cues(s.sfx),
  }));
  const patch: Partial<JobSettings> = { script, scene_mode: "script", narrate: script.some((s) => s.narration.trim()) };
  if (!Array.isArray(data)) {
    if (data.characters) patch.characters = data.characters;
    if (data.style) patch.style = data.style;
    if (data.voice) patch.voice = data.voice;
    if (["kenburns", "ltx", "none"].includes(data.motion)) patch.motion = data.motion;
  }
  return { title: Array.isArray(data) ? undefined : data.title, patch };
}

/** My script: write every scene yourself — picture, narration, motion and sound effects. Nothing is rewritten by AI. */
export default function ScriptEditor({ title, settings, onTitle, onSettings, library = [], artCharacters = [] }: Props) {
  const script = settings.script?.length ? settings.script : [blank()];
  const characters = Object.entries(settings.characters ?? {});
  const [pasting, setPasting] = useState(false);
  const [pasted, setPasted] = useState("");
  const [error, setError] = useState<string | null>(null);

  const setScript = (next: ScriptScene[]) => onSettings({ script: next });
  const update = (i: number, patch: Partial<ScriptScene>) => setScript(script.map((s, k) => (k === i ? { ...s, ...patch } : s)));
  const move = (i: number, d: number) => {
    const next = [...script];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setScript(next);
  };
  const setCharacters = (rows: [string, string][]) => onSettings({ characters: Object.fromEntries(rows) });
  const updateCue = (i: number, k: number, patch: Partial<SfxCue>) =>
    update(i, { sfx: script[i].sfx.map((c, j) => (j === k ? { ...c, ...patch } : c)) });

  return (
    <div className="script">
      <div className="row tight-top">
        <label className="grow">
          Title
          <input value={title} maxLength={200} placeholder="아기도깨비 똘이와 요술 방망이" onChange={(e) => onTitle(e.target.value)} />
        </label>
        <button className="secondary inline" onClick={() => setPasting((p) => !p)}>
          {pasting ? "Close" : "Paste a story file (JSON)"}
        </button>
      </div>
      {pasting && (
        <div className="paste">
          <textarea rows={6} value={pasted} placeholder='{"title": "…", "characters": {…}, "scenes": [{"image": "…", "narration": "…", "sfx": "…"}]}'
                    onChange={(e) => setPasted(e.target.value)} />
          <button
            disabled={!pasted.trim()}
            onClick={() => {
              try {
                const { title: t, patch } = parseStory(pasted);
                if (t) onTitle(t);
                onSettings(patch);
                setPasting(false);
                setPasted("");
                setError(null);
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              }
            }}
          >
            Fill in the script
          </button>
        </div>
      )}
      {error && <p className="error">{error}</p>}

      <fieldset className="characters-list">
        <legend>Characters — their look is spelled out in every picture that names them</legend>
        {characters.map(([name, look], i) => (
          <div className="row tight" key={i}>
            <input className="name" value={name} placeholder="Ttori" maxLength={40}
                   onChange={(e) => setCharacters(characters.map((c, k) => (k === i ? [e.target.value, c[1]] : c)))} />
            <input className="grow" value={look} placeholder="a tiny baby dokkaebi goblin with sky-blue skin, one yellow horn…" maxLength={300}
                   onChange={(e) => setCharacters(characters.map((c, k) => (k === i ? [c[0], e.target.value] : c)))} />
            <button className="link" onClick={() => setCharacters(characters.filter((_, k) => k !== i))}>Remove</button>
          </div>
        ))}
        <button className="link" disabled={characters.length >= 12} onClick={() => setCharacters([...characters, ["", ""]])}>
          + Add character
        </button>
      </fieldset>

      {artCharacters.length > 0 && (
        <fieldset>
          <legend>Cartoon characters — scenes that name one are drawn from its pictures</legend>
          <div className="row tight">
            {artCharacters.map((c) => {
              const on = (settings.art_characters ?? []).includes(c.id);
              return (
                <label className="check" key={c.id}>
                  <input type="checkbox" checked={on} disabled={!on && (settings.art_characters ?? []).length >= 4}
                    onChange={() => onSettings({ art_characters: on ? (settings.art_characters ?? []).filter((x) => x !== c.id) : [...(settings.art_characters ?? []), c.id] })} />
                  {c.settings.name as string}
                </label>
              );
            })}
          </div>
        </fieldset>
      )}

      {script.map((scene, i) => (
        <fieldset className="script-scene" key={i}>
          <legend>
            Scene {i + 1}
            <span className="scene-tools">
              <button className="link" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">↑</button>
              <button className="link" disabled={i === script.length - 1} onClick={() => move(i, 1)} aria-label="Move down">↓</button>
              <button className="link" disabled={script.length === 1} onClick={() => setScript(script.filter((_, k) => k !== i))}>Remove</button>
            </span>
          </legend>
          <label>
            Picture (English)
            <textarea rows={2} value={scene.image_prompt} maxLength={1000} placeholder="Ttori holding a golden magic club on a hill at sunrise"
                      onChange={(e) => update(i, { image_prompt: e.target.value })} />
          </label>
          <label>
            Narration (spoken exactly as written)
            <textarea rows={2} value={scene.narration} maxLength={600} placeholder="깊은 산속 작은 마을에 아기도깨비 똘이가 살았어요."
                      onChange={(e) => update(i, { narration: e.target.value })} />
          </label>
          <div className="row tight">
            <label>
              Motion
              <select value={scene.motion_mode ?? ""} onChange={(e) => update(i, { motion_mode: (e.target.value || null) as ScriptScene["motion_mode"] })}>
                <option value="">Same as the video</option>
                <option value="kenburns">Pan &amp; zoom</option>
                <option value="parallax">Parallax</option>
                <option value="ltx">AI motion (slow)</option>
                <option value="none">Still picture</option>
              </select>
            </label>
            {(scene.motion_mode ?? settings.motion) === "ltx" && (
              <label className="grow">
                What moves
                <input value={scene.motion ?? ""} maxLength={300} placeholder="the club sparkles, camera slowly pushes in"
                       onChange={(e) => update(i, { motion: e.target.value })} />
              </label>
            )}
          </div>
          {scene.sfx.map((cue, k) => (
            <div className="row tight cue" key={k}>
              <input className="grow" value={cue.prompt} maxLength={300} placeholder="magical sparkle chime"
                     onChange={(e) => updateCue(i, k, { prompt: e.target.value })} />
              <label className="small">
                at
                <input type="number" min={0} max={60} step={0.1} value={cue.at ?? 0.15} onChange={(e) => updateCue(i, k, { at: Number(e.target.value) })} />
              </label>
              <label className="small">
                sec
                <input type="number" min={1} max={10} step={0.5} value={cue.duration ?? 3} onChange={(e) => updateCue(i, k, { duration: Number(e.target.value) })} />
              </label>
              <button className="link" onClick={() => update(i, { sfx: scene.sfx.filter((_, j) => j !== k) })}>Remove</button>
            </div>
          ))}
          <button className="link" disabled={scene.sfx.length >= 4} onClick={() => update(i, { sfx: [...scene.sfx, { prompt: "" }] })}>
            + Sound effect
          </button>
        </fieldset>
      ))}
      <button className="secondary inline" disabled={script.length >= MAX_SCENES} onClick={() => setScript([...script, blank()])}>
        + Add scene
      </button>
      <span className="muted"> {script.length} of {MAX_SCENES} scenes</span>
      <SongOptions settings={settings} library={library} onSettings={onSettings} />
      <ExportOptions settings={settings} onSettings={onSettings} />
    </div>
  );
}

/** A sung song from the Music library: under the narration, as a sung intro and outro, or as the whole story. */
function SongOptions({ settings, library, onSettings }: { settings: JobSettings; library: Job[]; onSettings: Props["onSettings"] }) {
  const tracks = libraryTracks(library.filter((j) => j.kind === "music"));
  const song = settings.song ?? null;
  const set = (patch: Partial<SongBlock>) => onSettings({ song: { placement: "intro_outro", track: "", ...song, ...patch } });
  return (
    <fieldset>
      <legend>Song</legend>
      <div className="row tight">
        <label className="grow">
          Song from your Music library
          <select value={song?.track ?? ""} onChange={(e) => (e.target.value ? set({ track: e.target.value }) : onSettings({ song: null }))}>
            <option value="">None</option>
            {tracks.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </label>
        {song?.track && (
          <label>
            How it plays
            <select value={song.placement} onChange={(e) => set({ placement: e.target.value as SongBlock["placement"] })}>
              <option value="intro_outro">Sung intro &amp; outro, instrumental under the story</option>
              <option value="bed">Under the narration</option>
              <option value="sung_story">Sung story (no narration)</option>
            </select>
          </label>
        )}
      </div>
      {song?.track && song.placement !== "bed" && (
        <p className="muted">Sung lyrics are timed to the singing and shown as yellow subtitles (takes ~1 min the first time a song is used).</p>
      )}
    </fieldset>
  );
}

/** Caption look and the final export size. */
function ExportOptions({ settings, onSettings }: { settings: JobSettings; onSettings: Props["onSettings"] }) {
  const style: CaptionStyle = settings.caption_style ?? {};
  const setStyle = (patch: Partial<CaptionStyle>) => onSettings({ caption_style: { ...style, ...patch } });
  const portrait = settings.height > settings.width;
  const target = portrait ? 1920 : 1080;
  return (
    <fieldset>
      <legend>Captions &amp; export</legend>
      <div className="row tight">
        <label>
          Caption font
          <select value={style.font ?? "rounded"} onChange={(e) => setStyle({ font: e.target.value as CaptionStyle["font"] })}>
            <option value="rounded">Rounded (Jua)</option>
            <option value="handwritten">Handwritten (Gamja Flower)</option>
            <option value="system">Plain (Apple SD Gothic Neo)</option>
          </select>
        </label>
        <label>
          Size
          <select value={style.font_size_scale ?? 1} onChange={(e) => setStyle({ font_size_scale: Number(e.target.value) })}>
            <option value={0.85}>Small</option>
            <option value={1}>Normal</option>
            <option value={1.2}>Large</option>
          </select>
        </label>
        <label>
          Position
          <select value={style.position ?? "bottom"} onChange={(e) => setStyle({ position: e.target.value as CaptionStyle["position"] })}>
            <option value="bottom">Bottom</option>
            <option value="lower_third">Lower third</option>
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={!!style.box} onChange={(e) => setStyle({ box: e.target.checked })} />
          Dark box
        </label>
      </div>
      <div className="row tight">
        <label className="check">
          <input type="checkbox" checked={!!settings.output_height} onChange={(e) => onSettings({ output_height: e.target.checked ? target : null })} />
          Export at {portrait ? "1080×1920" : "1620×1080"}
        </label>
        {!!settings.output_height && (
          <label>
            Enlarge with
            <select value={settings.upscale ?? "fast"} onChange={(e) => onSettings({ upscale: e.target.value as "fast" | "ai" })}>
              <option value="fast">Fast resize (seconds)</option>
              <option value="ai">AI upscale (~1 s per frame)</option>
            </select>
          </label>
        )}
      </div>
    </fieldset>
  );
}
