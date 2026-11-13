"use client";

import { useEffect, useRef, useState } from "react";
import { createOrbScene, type OrbSceneApi, type OrbState } from "@/lib/orbScene";

// Modified from the MIT-licensed ultron-by-sagar-builds orb UI: recoloured blue, the webcam
// hand-tracking removed (ULTRON is voice-only), and a small state bridge added so the desktop
// window can tell the orb what the assistant is doing.

declare global {
  interface Window {
    ultronSetState?: (state: string) => void;
    ultronSetLevel?: (level: number) => void;
    ultronFailed?: boolean;
  }
}

const STATES: OrbState[] = ["off", "starting", "standby", "muted", "listening", "thinking", "speaking"];
const LABEL: Record<OrbState, string> = {
  off: "OFFLINE",
  starting: "STARTING",
  standby: "STANDBY",
  muted: "MUTED",
  listening: "LISTENING",
  thinking: "THINKING",
  speaking: "SPEAKING",
};

export default function JarvisOrb() {
  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<OrbSceneApi | null>(null);
  const [state, setState] = useState<OrbState>("standby");
  const [embedded, setEmbedded] = useState(false);

  useEffect(() => {
    setEmbedded(new URLSearchParams(window.location.search).has("embed"));
    const container = containerRef.current;
    if (!container) return;
    let scene: OrbSceneApi;
    try {
      scene = createOrbScene(container);
    } catch {
      // No WebGL: tell the desktop window so it can fall back to its native orb.
      window.ultronFailed = true;
      return;
    }
    sceneRef.current = scene;
    window.ultronSetState = (s: string) => {
      const next = STATES.includes(s as OrbState) ? (s as OrbState) : "standby";
      scene.setState(next);
      setState(next);
    };
    window.ultronSetLevel = (l: number) => {
      scene.setLevel(l);
      document.body.dataset.level = String(Math.round(l * 1000) / 1000);
    };
    return () => {
      delete window.ultronSetState;
      delete window.ultronSetLevel;
      scene.dispose();
      sceneRef.current = null;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      switch (e.key) {
        case "+":
        case "=":
          sceneRef.current?.zoomIn();
          break;
        case "-":
        case "_":
          sceneRef.current?.zoomOut();
          break;
        case "r":
        case "R":
          sceneRef.current?.resetView();
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className={embedded ? "embed" : undefined}>
      <div ref={containerRef} className="orb-root" />

      <div className="overlay-vignette" />
      <div className="overlay-grain" />
      <div className="overlay-scanlines" />

      <div className="hud hud-title">U.L.T.R.O.N.</div>
      <div className="hud hud-state" data-testid="state">
        {LABEL[state]}
      </div>

      <div className="hud hud-hint">
        <div>
          <span className="key">DRAG</span> spin&nbsp;&nbsp;
          <span className="key">SCROLL</span> zoom&nbsp;&nbsp;
          <span className="key">R</span> reset&nbsp;&nbsp;
          <span className="key">+/−</span> zoom
        </div>
      </div>

      <div className="hud hud-controls">
        <div className="hud-row">
          <button type="button" className="hud-btn" onClick={() => sceneRef.current?.zoomIn()} aria-label="Zoom in">
            +
          </button>
          <button type="button" className="hud-btn" onClick={() => sceneRef.current?.zoomOut()} aria-label="Zoom out">
            −
          </button>
          <button type="button" className="hud-btn" onClick={() => sceneRef.current?.resetView()}>
            RESET
          </button>
        </div>
      </div>
    </div>
  );
}
