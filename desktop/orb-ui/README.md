# ULTRON orb UI (blue fork)

The interface of ULTRON's desktop window. It is a fork of
[SAGAR-TAMANG/ultron-by-sagar-builds](https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds)
(MIT, see `LICENSE`), changed in these ways:

- recoloured from amber to blue (scene palette, shaders, sprites, HUD CSS)
- webcam hand-tracking removed (ULTRON is voice-only; no camera code is shipped)
- `OrbSceneApi.setState()` and `window.ultronSetState()` added: the desktop window pushes the
  assistant's state (off / starting / standby / muted / listening / thinking / speaking) in, and
  the orb changes glow, spin speed and core pulse. The speaking pulse is a rhythm pattern, not
  the real audio level.
- `?embed=1` hides the page's own HUD so the desktop app can draw its buttons on top
- static export (`output: "export"`), served by the desktop app from a loopback-only server

## Build

```bash
cd desktop/orb-ui
npm install --ignore-scripts
npm run build          # writes out/ ; the desktop window loads it
```

`ULTRON_ORB=native` makes the window use the older QPainter orb instead. The window also falls
back to it by itself if the page reports that WebGL is unavailable.

Standalone preview in a browser: `npm run dev`, then open http://localhost:3000.
