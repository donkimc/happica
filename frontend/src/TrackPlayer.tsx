import { useRef, useState } from "react";
import { Job, outputUrl, Track } from "./api";

export const fmtTime = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

interface Props {
  job: Job;
  track: Track;
  label: string;
  downloadName: string;
  loop?: boolean; // preview seamless loops as loops
}

/** Play button, clickable waveform and MP3 download for one generated audio take. */
export default function TrackPlayer({ job, track, label, downloadName, loop = false }: Props) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const src = outputUrl(job, track.mp3);
  const played = time / (track.duration || 1);

  function seek(e: React.MouseEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const a = audio.current;
    if (!a) return;
    a.currentTime = ((e.clientX - box.left) / box.width) * track.duration;
    if (a.paused) a.play();
  }

  return (
    <div className="track">
      <button
        className="play"
        aria-label={playing ? `Pause ${label}` : `Play ${label}`}
        onClick={() => (audio.current?.paused ? audio.current.play() : audio.current?.pause())}
      >
        {playing ? "❚❚" : "▶"}
      </button>
      <div className="track-main">
        <div className="track-meta">
          <span>{label}</span>
          <span className="muted">
            {fmtTime(time)} / {fmtTime(track.duration)}
          </span>
        </div>
        {/* SVG with a stretched viewBox, so all bars fit any width (one unit per bar, 70% bar / 30% gap) */}
        <svg
          className="wave"
          viewBox={`0 0 ${track.peaks.length} 100`}
          preserveAspectRatio="none"
          onClick={seek}
          role="slider"
          aria-label={`Seek ${label}`}
          aria-valuenow={Math.round(time)}
          aria-valuemax={Math.round(track.duration)}
        >
          {track.peaks.map((p, i) => {
            const h = Math.max(6, p * 100);
            return <rect key={i} x={i + 0.15} y={(100 - h) / 2} width={0.7} height={h} className={i / track.peaks.length < played ? "on" : ""} />;
          })}
        </svg>
      </div>
      <a className="download" href={src} download={downloadName}>
        MP3
      </a>
      <audio
        ref={audio}
        src={src}
        preload="none"
        loop={loop}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
      />
    </div>
  );
}
