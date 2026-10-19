import { useState } from "react";
import { isActive, Job, outputUrl } from "./api";

interface Props {
  job: Job;
  onCancel: () => void;
  onRerun: (keepSeed: boolean) => void;
  onEdit: () => void;
  onDelete: () => void;
  onRegenerate: (index: number, prompt: string) => void;
}

export default function JobDetail({ job, onCancel, onRerun, onEdit, onDelete, onRegenerate }: Props) {
  const active = isActive(job);
  const result = job.result;

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

      {result?.video && <video key={job.updated} src={outputUrl(job, result.video)} controls />}

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
            <button className="secondary danger" onClick={onDelete}>Delete</button>
          </>
        )}
      </div>

      {!!result?.images.length && (
        <div className="grid">
          {result.images.map((src, i) => (
            <Scene
              key={src}
              job={job}
              index={i}
              src={src}
              prompt={result.scenes[i]}
              disabled={active}
              onRegenerate={(p) => onRegenerate(i, p)}
            />
          ))}
        </div>
      )}
      {result?.seed !== undefined && <p className="muted">Seed {result.seed}</p>}
    </article>
  );
}

interface SceneProps {
  job: Job;
  index: number;
  src: string;
  prompt: string;
  disabled: boolean;
  onRegenerate: (prompt: string) => void;
}

function Scene({ job, index, src, prompt, disabled, onRegenerate }: SceneProps) {
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
    </figure>
  );
}
