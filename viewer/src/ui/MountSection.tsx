import type { ClipInfo } from '../types';

interface Props {
  palName: string;
  on: boolean;
  /** the Pal's clips that make sense under a rider (ClipInfo.mountOk) */
  clips: ClipInfo[];
  clip: string;
  onToggle: (v: boolean) => void;
  onClip: (name: string) => void;
}

/** Boss page: include the Pal this boss rides (always at its tower size, as in the game) and which of its mount-compatible clips plays. */
export function MountSection({ palName, on, clips, clip, onToggle, onClip }: Props) {
  return (
    <>
      <label className="check">
        <input type="checkbox" checked={on} onChange={(e) => onToggle(e.target.checked)} /> Include mount ({palName})
      </label>
      {on && clips.length > 0 && (
        <div className="var-group">
          <select className="text" value={clip} onChange={(e) => onClip(e.target.value)} aria-label="Mount animation">
            <option value="">Auto (follow the boss)</option>
            {clips.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </div>
      )}
    </>
  );
}
