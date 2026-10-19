import { Job, MusicResult, MusicSettings } from "./api";

export interface MusicChoice {
  music: "off" | "auto" | "library";
  music_track: string;
  music_title: string;
  music_volume: number;
}

interface Props {
  value: MusicChoice;
  library: Job[]; // music jobs
  narrated: boolean;
  onChange: (patch: Partial<MusicChoice>) => void;
}

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

/** Every finished take in the Music Studio library, newest first. */
export function libraryTracks(library: Job[]) {
  return library
    .filter((j) => j.state === "done" && j.result)
    .flatMap((j) => {
      const s = j.settings as Partial<MusicSettings>;
      const tracks = (j.result as unknown as MusicResult).tracks;
      return tracks.map((t, i) => ({
        value: t.flac,
        title: s.title || "Untitled song",
        label: `${s.title || "Untitled song"}${tracks.length > 1 ? ` — take ${i + 1}` : ""} (${fmt(t.duration)}${s.instrumental ? "" : ", vocals"})`,
      }));
    });
}

export default function MusicPicker({ value, library, narrated, onChange }: Props) {
  const tracks = libraryTracks(library);
  return (
    <div className="row music-picker">
      <label>
        Background music
        <select
          value={value.music}
          onChange={(e) => {
            const music = e.target.value as MusicChoice["music"];
            const first = tracks[0];
            onChange(music === "library" && !value.music_track && first ? { music, music_track: first.value, music_title: first.title } : { music });
          }}
        >
          <option value="off">Off</option>
          <option value="auto">Auto — AI scores the story</option>
          <option value="library" disabled={tracks.length === 0}>
            From Music library{tracks.length === 0 ? " (empty)" : ""}
          </option>
        </select>
      </label>
      {value.music === "library" && (
        <label className="grow">
          Song
          <select
            value={value.music_track}
            onChange={(e) => onChange({ music_track: e.target.value, music_title: tracks.find((t) => t.value === e.target.value)?.title ?? "" })}
          >
            {tracks.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {value.music !== "off" && (
        <label className="volume">
          Music level {Math.round(value.music_volume * 100)}%{narrated ? " (under the voice)" : ""}
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={value.music_volume}
            onChange={(e) => onChange({ music_volume: Number(e.target.value) })}
          />
        </label>
      )}
    </div>
  );
}
