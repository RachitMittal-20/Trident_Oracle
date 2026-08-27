"use client";

import { useEffect, useRef, useState } from "react";
import type { JSAnimation, Timeline } from "animejs";

/**
 * Duration tokens (ms) and easing tokens, per CLAUDE.md:
 * "Max 600ms, except the one-time pipeline rail draw on mount.
 *  easeOutExpo for entrances, easeInOutQuad for state changes."
 */
export const DURATION = {
  fast: 180,
  base: 320,
  slow: 600,
} as const;

export const EASING = {
  entrance: "easeOutExpo",
  stateChange: "easeInOutQuad",
} as const;

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);

    const handleChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener("change", handleChange);
    return () => query.removeEventListener("change", handleChange);
  }, []);

  return reduced;
}

type Revertible = JSAnimation | Timeline;

/**
 * The single entry point for anime.js calls in this app (CLAUDE.md: "every
 * anime.js call gated behind a prefers-reduced-motion check"). `factory` runs
 * an animate()/createTimeline() call and returns the resulting instance;
 * under reduced motion, `withMotion` skips the factory entirely and returns
 * null, so no anime.js call is ever made.
 */
export function withMotion<T extends Revertible>(
  reducedMotion: boolean,
  factory: () => T,
): T | null {
  if (reducedMotion) {
    return null;
  }
  return factory();
}

/**
 * Runs `factory` inside an effect and reverts whatever anime.js instance it
 * returns on unmount, so no component needs to remember cleanup itself.
 * Pass null (e.g. from withMotion under reduced motion) to skip entirely.
 */
export function useAnimeTimeline(
  factory: () => Revertible | null,
  deps: React.DependencyList,
): void {
  const factoryRef = useRef(factory);
  factoryRef.current = factory;

  useEffect(() => {
    const instance = factoryRef.current();
    return () => {
      instance?.revert();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deps is caller-provided
  }, deps);
}
