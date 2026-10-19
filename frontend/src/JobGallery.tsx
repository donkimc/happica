import { isActive, Job, outputUrl } from "./api";

interface Props {
  jobs: Job[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export default function JobGallery({ jobs, selectedId, onSelect }: Props) {
  if (jobs.length === 0) return <p className="muted">No videos yet — describe one above.</p>;
  return (
    <div className="gallery">
      {jobs.map((job) => {
        const thumb = job.result?.images[0];
        return (
          <button
            key={job.id}
            className={`tile ${job.id === selectedId ? "selected" : ""}`}
            onClick={() => onSelect(job.id)}
          >
            <div className="thumb">
              {thumb ? <img src={outputUrl(job, thumb)} alt="" /> : <span className="muted">{job.state}</span>}
              <span className={`badge ${job.state}`}>{job.result?.video ? "video" : job.state}</span>
            </div>
            {isActive(job) && <progress value={job.progress} max={1} />}
            <span className="tile-title">{job.prompt.split("\n")[0]}</span>
            <span className="muted">
              {job.result?.scenes.length ?? "–"} scenes · {new Date(job.created * 1000).toLocaleDateString()}
            </span>
          </button>
        );
      })}
    </div>
  );
}
