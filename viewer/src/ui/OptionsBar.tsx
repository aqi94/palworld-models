import { useState } from 'react';
import { LightingPanel, type Options } from './OptionsSection';
import { LightingSelect } from './LightingSelect';
import { CameraPanel } from './CameraPanel';
import type { CameraOrbit } from '../scene/ViewerScene';

interface Props {
  game: string;
  gameName: string;
  title: string;
  options: Options;
  onChange: (patch: Partial<Options>) => void;
  panelsHidden: boolean;
  onTogglePanels: () => void;
  camera: {
    orbit: CameraOrbit | null;
    onChange: (patch: Partial<CameraOrbit>) => void;
    onRotate: (axis: 'yaw' | 'pitch' | 'roll', deg: number) => void;
    onFit: () => void;
    ranges: () => { center: [number, number, number]; radius: number; dMin: number; dMax: number } | null;
  };
}

type Pop = 'light' | 'camera' | null;
const readPop = (): Pop => {
  try {
    if (localStorage.getItem('lightOpen') === '1') return 'light';
    if (localStorage.getItem('camOpen') === '1') return 'camera';
  } catch { /* ignore */ }
  return null;
};

/** Photoshop-style options bar across the top: domain switcher, current model, and the settings tinkered with most (lighting, shading, albedo, background). */
export function OptionsBar({ game, gameName, title, options, onChange, panelsHidden, onTogglePanels, camera }: Props) {
  // one popover at a time (they share the spot under the bar)
  const [pop, setPop] = useState<Pop>(readPop);
  const toggle = (which: Exclude<Pop, null>) => setPop((cur) => {
    const n = cur === which ? null : which;
    try { localStorage.setItem('lightOpen', n === 'light' ? '1' : '0'); localStorage.setItem('camOpen', n === 'camera' ? '1' : '0'); } catch { /* ignore */ }
    return n;
  });
  return (
    <header className="optionsbar">
      <a className="btn small" href={import.meta.env.BASE_URL} title="All games">&#9664; Games</a>
      <a className="btn small" href={`${import.meta.env.BASE_URL}${game}`} title="Thumbnail grid for this game">&#9638; {gameName || game}</a>
      <strong className="ob-title">{title}</strong>
      <span className="spacer" />
      <div className="light-menu">
        <button className={'btn small' + (pop === 'camera' ? ' primary' : '')} onClick={() => toggle('camera')} title="Camera settings" aria-expanded={pop === 'camera'}>&#127909;&#xFE0E;</button>
        <button className={'btn small' + (pop === 'light' ? ' primary' : '')} onClick={() => toggle('light')} title="Light settings" aria-expanded={pop === 'light'}>&#9728;</button>
        {pop === 'light' && <div className="light-pop"><LightingPanel options={options} onChange={onChange} /></div>}
        {pop === 'camera' && <div className="light-pop cam-pop"><CameraPanel {...camera} /></div>}
      </div>
      <label className="ob-field">Lighting
        <LightingSelect env={options.env} onPick={(env) => onChange({ env })} />
      </label>
      <label className="ob-field">Shading
        <select className="text" value={options.shading} onChange={(e) => onChange({ shading: e.target.value as Options['shading'] })}>
          <option value="game">Game</option><option value="pbr">PBR</option>
        </select>
      </label>
      <label className="ob-field">Albedo
        <select className="text" value={options.albedo} onChange={(e) => onChange({ albedo: e.target.value as Options['albedo'] })}>
          <option value="baked">Baked</option><option value="raw">Raw</option><option value="off">Off</option>
        </select>
      </label>
      <label className="ob-field">BG
        <input type="color" value={options.background} onChange={(e) => onChange({ background: e.target.value })} />
      </label>
      <button className={'btn small' + (panelsHidden ? ' primary' : '')} onClick={onTogglePanels} title="Hide / show panels (Tab)">Panels</button>
    </header>
  );
}
