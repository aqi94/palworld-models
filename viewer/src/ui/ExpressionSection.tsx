interface Props {
  names: string[];
  values: Record<string, number>;
  onChange: (name: string, w: number) => void;
  onReset: () => void;
}

const label = (n: string) => n.replace(/_/g, ' ');

/** Facial expressions of a human: the head's morph targets (eye_close, eye_smile, mouth_open...), one weight slider each. They are shapes
 *  the game blends from gameplay code, not animation curves, so they combine freely (smile + open mouth). */
export function ExpressionSection({ names, values, onChange, onReset }: Props) {
  return (
    <div className="var-group">
      <div className="label">Expression <button className="btn small" onClick={onReset} disabled={!names.some((n) => values[n])}>Reset</button></div>
      {names.map((n) => (
        <label key={n} className="check">
          <span style={{ minWidth: '6.5em' }}>{label(n)}</span>
          <input type="range" min={0} max={1} step={0.01} value={values[n] ?? 0} aria-label={label(n)} onChange={(e) => onChange(n, Number(e.target.value))} />
        </label>
      ))}
    </div>
  );
}
