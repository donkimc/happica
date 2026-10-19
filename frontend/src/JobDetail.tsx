import { useState } from "react";
import { isActive, Job, JobSettings, outputUrl, SoundFields } from "./api";
import { DEFAULTS } from "./GenerateForm";
import SoundPicker from "./MusicPicker";

interface Props {
  job: Job;
  onCancel: () => void;
  onRerun: (keepSeed: boolean) => void;
  onEdit: () => void;
  onDelete: () => void;
  onRegenerate: (index: number, prompt: string) => void;
  library: Job[];
  sfxLibrary: Job[];
  onChangeSound: (choice: SoundFields) => void;
  onNewAuto: (layer: "music" | "ambience") => void;
}

export default function JobDetail({ job, onCancel, onRerun, onEdit, onDelete, onRegenerate, library, sfxLibrary, onChangeSound, onNewAuto }: Props) {
  const active = isActive(job);
  const result = job.result;
  const settings = { ...DEFAULTS, ...job.settings } as JobSettings;
  const [musicOpen, setMusicOpen] = useState(false);
  const [choice, setChoice] = useState<SoundFields>(settings);
  const needsAuto = (layer: "music" | "ambience") =>
    choice[layer] === "auto" && (settings[layer] !== "auto" || !result?.[layer]);

  return (
    <article className="card detail">
      <div className="row between">
        <strong className={`state ${job.state}`}>{job.state}</strong>
        <span className="muted">{new Date(job.created * 1000).toLocaleString()}</span>
      </div>

      {active && (
        <div className="progress">
          <span>{job.stage || "waiting"}</span>
          <progress value={job.progress} max={1} />
        </div>
      )}
      {job.error && <p className="error">{job.error}</p>}

      {result?.video && (
        <video key={job.updated} src={outputUrl(job, result.video)} controls>
          {result.subtitles_vtt && (
            // Burned-in captions are already in the picture; only "toggleable" jobs show this track by default
            <track kind="subtitles" label="Narration" src={outputUrl(job, result.subtitles_vtt)}
                   default={job.settings.subtitles === "soft"} />
          )}
        </video>
      )}

      <div className="actions">
        {active ? (
          <button className="secondary" onClick={onCancel}>Cancel</button>
        ) : (
          <>
            <button className="secondary" onClick={() => onRerun(false)} title="Same prompt and settings, new seed">
              Re-run
            </button>
            {result?.seed !== undefined && (
              <button className="secondary" onClick={() => onRerun(true)} title={`Seed ${result.seed}`}>
                Re-run, same seed
              </button>
            )}
            <button className="secondary" onClick={onEdit}>Edit &amp; re-run</button>
            {result?.video && (
              <button
                className="secondary"
                onClick={() => {
                  setChoice(settings);
                  setMusicOpen((o) => !o);
                }}
              >
                Change sound
              </button>
            )}
            <button className="secondary danger" onClick={onDelete}>Delete</button>
          </>
        )}
      </div>

      {result?.music && settings.music !== "off" && (
        <p className="muted music-line">
          ♪ {result.music.title || "Untitled"} — {result.music.source === "auto" ? `scored for this video (${result.music.tags})` : "from your Music library"}
          {" · "}level {Math.round(settings.music_volume * 100)}%
          {settings.music === "auto" && !active && (
            <button className="link" onClick={() => onNewAuto("music")}>New score</button>
          )}
        </p>
      )}
      {result?.ambience && settings.ambience !== "off" && (
        <p className="muted music-line">
          ≋ Ambience: {result.ambience.title} {result.ambience.source === "library" ? "(Sound library)" : "(made for this video)"}
          {" · "}level {Math.round(settings.ambience_volume * 100)}%
          {settings.ambience === "auto" && !active && (
            <button className="link" onClick={() => onNewAuto("ambience")}>New ambience</button>
          )}
        </p>
      )}

      {musicOpen && !active && (
        <div className="music-panel">
          <SoundPicker
            value={choice}
            musicLibrary={library}
            sfxLibrary={sfxLibrary}
            narrated={!!result?.audio?.length}
            onChange={(patch) => setChoice((c) => ({ ...c, ...patch }))}
          />
          <div className="row tight">
            <button
              onClick={() => {
                onChangeSound(choice);
                setMusicOpen(false);
              }}
            >
              {needsAuto("music") ? "Score & re-mix (~1–2 min)" : needsAuto("ambience") ? "Make ambience & re-mix (~30 s)" : "Re-mix (a few seconds)"}
            </button>
            <button className="secondary" onClick={() => setMusicOpen(false)}>Cancel</button>
          </div>
          <p className="muted">Only the soundtrack changes — images, clips, narration and subtitles stay as they are.</p>
        </div>
      )}

      {!!result?.images.length && (
        <div className="grid">
          {result.images.map((src, i) => (
            <Scene
              key={src}
              job={job}
              index={i}
              src={src}
              prompt={result.scenes[i]}
              narration={result.narration?.[i]}
              disabled={active}
              onRegenerate={(p) => onRegenerate(i, p)}
            />
          ))}
        </div>
      )}
      <p className="muted">
        {result?.seed !== undefined && <>Seed {result.seed}</>}
        {result?.subtitles && (
          <>
            {" · "}
            <a href={outputUrl(job, result.subtitles)} download>
              Subtitles (.srt)
            </a>
          </>
        )}
      </p>
    </article>
  );
}

interface SceneProps {
  job: Job;
  index: number;
  src: string;
  prompt: string;
  narration?: string;
  disabled: boolean;
  onRegenerate: (prompt: string) => void;
}

function Scene({ job, index, src, prompt, narration, disabled, onRegenerate }: SceneProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(prompt);

  return (
    <figure>
      <a href={outputUrl(job, src)} target="_blank" rel="noreferrer">
        <img src={outputUrl(job, src)} alt={prompt} />
      </a>
      {editing ? (
        <div className="scene-edit">
          <textarea rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="row tight">
            <button
              disabled={!draft.trim()}
              onClick={() => {
                onRegenerate(draft.trim() === prompt ? "" : draft.trim());
                setEditing(false);
              }}
            >
              Regenerate
            </button>
            <button className="secondary" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <figcaption>
          <span>
            {index + 1}. {prompt}
          </span>
          <button
            className="link"
            disabled={disabled}
            onClick={() => {
              setDraft(prompt);
              setEditing(true);
            }}
          >
            Regenerate
          </button>
        </figcaption>
      )}
      {narration && narration !== prompt && <p className="narration">“{narration}”</p>}
    </figure>
  );
}
