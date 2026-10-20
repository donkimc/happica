import { useEffect, useRef, useState } from "react";

export type Page = "tools" | "guide" | "keys";

interface Props {
  page: Page;
  onPage: (page: Page) => void;
  onLogout: () => void;
}

/** The ☰ menu: How to use, API keys, Log out. */
export default function Menu({ page, onPage, onLogout }: Props) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // Close on a click outside or Escape
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const choose = (action: () => void) => {
    setOpen(false);
    action();
  };

  return (
    <div className="menu" ref={box}>
      <button className="menu-button" aria-label="Menu" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span aria-hidden="true">☰</span>
      </button>
      {open && (
        <div className="menu-list" role="menu">
          <button role="menuitem" className={page === "guide" ? "on" : ""} onClick={() => choose(() => onPage("guide"))}>
            How to use
          </button>
          <button role="menuitem" className={page === "keys" ? "on" : ""} onClick={() => choose(() => onPage("keys"))}>
            API keys
          </button>
          <hr />
          <button role="menuitem" onClick={() => choose(onLogout)}>
            Log out
          </button>
        </div>
      )}
    </div>
  );
}

/** The user guide, inside the app. */
export function GuidePage({ onBack }: { onBack: () => void }) {
  return (
    <section className="page">
      <div className="row between page-head">
        <h2>How to use</h2>
        <button className="secondary inline" onClick={onBack}>← Back to the studio</button>
      </div>
      <iframe className="guide-frame" src="/guide.html" title="Happica guide" />
    </section>
  );
}
