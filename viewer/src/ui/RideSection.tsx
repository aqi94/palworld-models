import type { ClipInfo, IndexModel } from '../types';

interface Props {
  riders: IndexModel[];
  rider: string;
  clips: ClipInfo[];
  clip: string;
  /** Pal's size when it carries a tower boss (null = same as normal) */
  towerScale: number | null;
  towerSize: boolean;
  onRider: (id: string) => void;
  onClip: (name: string) => void;
  onTowerSize: (v: boolean) => void;
}

/** A tower boss riding this Pal: who, which of their clips (Auto follows the mount) and whether the Pal is shown at its tower size. */
export function RideSection({ riders, rider, clips, clip, towerScale, towerSize, onRider, onClip, onTowerSize }: Props) {
  return (
    <>
      <div className="var-group">
        <div className="label">Rider</div>
        <select className="text" value={rider} onChange={(e) => onRider(e.target.value)} aria-label="Rider">
          <option value="">None</option>
          {riders.map((m) => <option key={m.id} value={m.id}>{m.displayName}</option>)}
        </select>
      </div>
      {rider && clips.length > 0 && (
        <div className="var-group">
          <select className="text" value={clip} onChange={(e) => onClip(e.target.value)} aria-label="Rider animation">
            <option value="">Auto (ride idle)</option>
            {clips.filter((c) => c.category === 'ride').map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </div>
      )}
      {!rider && towerScale != null && towerScale !== 1 && (  // with a rider the Pal is always at tower size
        <label className="check">
          <input type="checkbox" checked={towerSize} onChange={(e) => onTowerSize(e.target.checked)} /> Tower size (×{towerScale})
        </label>
      )}
    </>
  );
}
