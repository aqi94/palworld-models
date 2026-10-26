# Palworld Model Viewer

Vite + React + TypeScript + three.js viewer for the models produced by `pipeline/` into `../out/`.
Adapted from the Pokémon model viewer in the sibling project; the Palworld-specific parts are `src/data/pal.ts`
(Paldeck labels, element chips, search) and `src/ui/Disclaimer.tsx`.

    npm install
    npm run dev        # http://localhost:5180  (serves /models/* from ../out/)
    npm run build      # type-check + production build into dist/
    npm run preview    # serves dist/ on :4180; /models/* is also mapped to ../out/ by the preview middleware

For a static deployment, copy `out/` to `dist/models`, or host the GLBs elsewhere and set `VITE_GLB_BASE`
(see `../tools/deploy_pages.py`).

Routes: `/palworld` grid, `/palworld/<CodeName>` viewer (e.g. `/palworld/SheepBall`). Search accepts English names,
code names, `#029` and element names. Eye/mouth expressions appear under Appearance > Variations.
Keys: Left/Right previous/next clip, P pause, T time scale, F fit, 1-5 camera views.
