import { EXPORT_PRESETS, MAX_EXPORT_PX, type ExportOptions } from '../scene/exportOptions';
import type { GifExportOptions } from '../scene/ViewerScene';

interface Props {
  value: ExportOptions;
  onChange: (patch: Partial<ExportOptions>) => void;
  onExport: () => void;
  onClose: () => void;
  top: number;
  gif: GifExportOptions;
  onGifChange: (patch: Partial<GifExportOptions>) => void;
  onGif: () => void;
  /** null = idle, else a progress label. */
  gifProgress: string | null;
}

const num = (v: string, min: number, max: number, fallback: number) => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : fallback;
};

/** Popover next to the toolbox PNG button: canvas size, scale, framing, padding, background, trim. */
export function ExportPanel({ value: o, onChange, onExport, onClose, top, gif: g, onGifChange, onGif, gifProgress }: Props) {
  const solid = o.background !== 'transparent';
  const framed = o.framing !== 'current';
  return (
    <div className="export-panel" style={{ top, maxHeight: `calc(100vh - ${top + 8}px)` }} role="dialog" aria-label="Export">
      <div className="export-head"><b>Export</b><button className="tool-btn" title="Close" onClick={onClose}>×</button></div>
      <div className="export-cols">
      <div className="export-col">
      <b>PNG</b>
      <label>Preset
        <select value="" onChange={(e) => { const p = EXPORT_PRESETS.find((x) => x.label === e.target.value); if (p) onChange(p.opts); }}>
          <option value="">Choose…</option>
          {EXPORT_PRESETS.map((p) => <option key={p.label} value={p.label}>{p.label}</option>)}
        </select>
      </label>
      <label>Size (px)
        <span className="export-row">
          <input type="number" min={1} max={MAX_EXPORT_PX} value={o.width} onChange={(e) => onChange({ width: num(e.target.value, 1, MAX_EXPORT_PX, o.width) })} />
          ×
          <input type="number" min={1} max={MAX_EXPORT_PX} value={o.height} onChange={(e) => onChange({ height: num(e.target.value, 1, MAX_EXPORT_PX, o.height) })} />
        </span>
      </label>
      <label>Scale
        <select value={o.scale} onChange={(e) => onChange({ scale: Number(e.target.value) })}>
          {[1, 2, 3, 4].map((s) => <option key={s} value={s}>{s}×</option>)}
        </select>
      </label>
      <label>Framing
        <select value={o.framing} onChange={(e) => onChange({ framing: e.target.value as ExportOptions['framing'] })}>
          <option value="current">Current view</option>
          <option value="fit">Fit model</option>
          <option value="idle">Idle ortho (frame 0)</option>
        </select>
      </label>
      <label>FOV {o.fov}°
        <input type="range" min={1} max={60} step={1} value={o.fov} disabled={!framed} onChange={(e) => onChange({ fov: Number(e.target.value) })} />
      </label>
      <label title="Idle: fraction of model height above/below. Trim: fraction of the smaller canvas side.">Padding {(o.margin * 100).toFixed(1)}%
        <input type="range" min={0} max={0.3} step={0.005} value={o.margin} disabled={o.framing !== 'idle' && !o.trim} onChange={(e) => onChange({ margin: Number(e.target.value) })} />
      </label>
      <label className="export-check"><input type="checkbox" checked={o.trim} onChange={(e) => onChange({ trim: e.target.checked })} />
        Trim to subject (scale to fit, feet-aligned)</label>
      <label className="export-check"><input type="checkbox" checked={solid} onChange={(e) => onChange({ background: e.target.checked ? '#808080' : 'transparent' })} />
        Solid background
        {solid && <input type="color" value={o.background} onChange={(e) => onChange({ background: e.target.value })} />}
      </label>
      <div className="export-foot">
        <span>{Math.min(MAX_EXPORT_PX, o.width * o.scale)} × {Math.min(MAX_EXPORT_PX, o.height * o.scale)} px</span>
        <button className="export-go" onClick={onExport}>Export</button>
      </div>
      </div>
      <div className="export-col export-section">
      <b>Animated GIF</b>
      <span className="export-note">Current clip from the current camera, drawn as the view shows it (outline style included), transparent background.</span>
      <label title="Subject height on the first frame (Showdown sprites: Pikachu ~55, Charizard ~100)">Subject height (px)
        <input type="number" min={16} max={1024} value={g.height} onChange={(e) => onGifChange({ height: num(e.target.value, 16, 1024, g.height) })} />
      </label>
      <label>Frame rate
        <span className="export-row">
          <input type="number" min={5} max={60} value={g.fps} onChange={(e) => onGifChange({ fps: num(e.target.value, 5, 60, g.fps) })} /> fps,
          <input type="number" min={10} max={200} step={10} value={g.delayMs} onChange={(e) => onGifChange({ delayMs: num(e.target.value, 10, 200, g.delayMs) })} /> ms/frame
        </span>
      </label>
      <label title="Palette size including transparency (Showdown: 16)">Colours {g.colors}
        <input type="range" min={4} max={256} step={1} value={g.colors} onChange={(e) => onGifChange({ colors: Number(e.target.value) })} />
      </label>
      <div className="export-foot">
        <span>{gifProgress !== null ? `Rendering ${gifProgress}` : `${(1000 / g.delayMs).toFixed(0)} fps playback`}</span>
        <button className="export-go" disabled={gifProgress !== null} onClick={onGif}>Export GIF</button>
      </div>
      </div>
      </div>
    </div>
  );
}
