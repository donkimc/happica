import { Job, JobSettings, MusicResult, MusicSettings, SfxSettings } from "./api";

/** The soundtrack fields of a video job: background music and an ambience bed. */
export type SoundChoice = Pick<
  JobSettings,
  "music" | "music_track" | "music_title" | "music_volume" | "ambience" | "ambience_track" | "ambience_title" | "ambience_volume"
>;
type Layer = "music" | "ambience";

const LAYERS: Record<Layer, { label: string; auto: string; library: string; empty: string }> = {
  music: { label: "Background music", auto: "Auto — AI scores the story", library: "From Music library", empty: "Music library" },
  ambience: { label: "Ambience", auto: "Auto — AI picks the setting's sounds", library: "From Sound library", empty: "Sound library" },
};

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

/** Every finished take in a library (Music Studio songs or Sound FX), newest first. */
export function libraryTracks(library: Job[]) {
  return library
    .filter((j) => j.state === "done" && j.result)
    .flatMap((j) => {
      const tracks = (j.result as unknown as MusicResult).tracks ?? [];
      const title =
        j.kind === "sfx"
          ? (j.settings as Partial<SfxSettings>).prompt ?? "Sound"
          : (j.settings as Partial<MusicSettings>).title || "Untitled song";
      const extra = j.kind === "sfx" ? ((j.settings as Partial<SfxSettings>).loop ? ", loop" : "") : (j.settings as Partial<MusicSettings>).instrumental ? "" : ", vocals";
      return tracks.map((t, i) => ({
        value: t.flac,
        title,
        label: `${title.length > 48 ? title.slice(0, 48) + "…" : title}${tracks.length > 1 ? ` — take ${i + 1}` : ""} (${fmt(t.duration)}${extra})`,
      }));
    });
}

interface LayerProps {
  layer: Layer;
  value: SoundChoice;
  library: Job[];
  narrated: boolean;
  onChange: (patch: Partial<SoundChoice>) => void;
}

function LayerPicker({ layer, value, library, narrated, onChange }: LayerProps) {
  const text = LAYERS[layer];
  const tracks = libraryTracks(library);
  const mode = value[layer];
  const track = value[`${layer}_track`];
  const volume = value[`${layer}_volume`];
  return (
    <div className="row music-picker">
      <label>
        {text.label}
        <select
          value={mode}
          onChange={(e) => {
            const next = e.target.value as SoundChoice["music"];
            const first = tracks[0];
            onChange(
              next === "library" && !track && first
                ? { [layer]: next, [`${layer}_track`]: first.value, [`${layer}_title`]: first.title }
                : { [layer]: next },
            );
          }}
        >
          <option value="off">Off</option>
          <option value="auto">{text.auto}</option>
          <option value="library" disabled={tracks.length === 0}>
            {text.library}
            {tracks.length === 0 ? " (empty)" : ""}
          </option>
        </select>
      </label>
      {mode === "library" && (
        <label className="grow">
          {layer === "music" ? "Song" : "Sound"}
          <select
            value={track}
            onChange={(e) =>
              onChange({ [`${layer}_track`]: e.target.value, [`${layer}_title`]: tracks.find((t) => t.value === e.target.value)?.title ?? "" })
            }
          >
            {tracks.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {mode !== "off" && (
        <label className="volume">
          Level {Math.round(volume * 100)}%{narrated ? " (under the voice)" : ""}
          <input type="range" min={0} max={1} step={0.05} value={volume} onChange={(e) => onChange({ [`${layer}_volume`]: Number(e.target.value) })} />
        </label>
      )}
    </div>
  );
}

interface Props {
  value: SoundChoice;
  musicLibrary: Job[];
  sfxLibrary: Job[];
  narrated: boolean;
  onChange: (patch: Partial<SoundChoice>) => void;
}

/** Background music and ambience pickers for a video. */
export default function SoundPicker({ value, musicLibrary, sfxLibrary, narrated, onChange }: Props) {
  return (
    <>
      <LayerPicker layer="music" value={value} library={musicLibrary} narrated={narrated} onChange={onChange} />
      <LayerPicker layer="ambience" value={value} library={sfxLibrary} narrated={narrated} onChange={onChange} />
    </>
  );
}
