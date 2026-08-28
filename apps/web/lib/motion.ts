"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
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

function subscribeToReducedMotion(callback: () => void): () => void {
  const query = window.matchMedia("(prefers-reduced-motion: reduce)");
  query.addEventListener("change", callback);
  return () => query.removeEventListener("change", callback);
}

function getReducedMotionSnapshot(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function getReducedMotionServerSnapshot(): boolean {
  return false;
}

/**
 * useSyncExternalStore, not useState+useEffect: this value has to be
 * correct on the very first render an animation-driving effect
 * (useAnimeTimeline below -- ConfidenceBar, StatCard, StageCountBadge,
 * etc.) can see, not just eventually-consistent. A useState+useEffect
 * version defaults to `false` and only learns the real value inside its
 * own effect -- but passive effects across a commit all run with THAT
 * commit's props, so a consumer's effect still fires once with the stale
 * `false` guess, starts a real animation, and then this hook flips to
 * `true` and the consumer's cleanup calls .revert() on the animation it
 * just started, permanently snapping the element back to its
 * pre-animation state (nothing ever puts it back, since withMotion's
 * reduced-motion branch never runs a replacement animation). Even
 * useLayoutEffect here doesn't fix it -- it resolves sooner, but a
 * consumer's plain useEffect still gets a pass with the stale value
 * first. useSyncExternalStore is React's dedicated primitive for exactly
 * this "external value can differ between server and client snapshots"
 * case: it forces a synchronous re-check during commit/hydration itself,
 * before any passive effect runs, so every consumer's first real effect
 * run already sees the correct value.
 */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeToReducedMotion,
    getReducedMotionSnapshot,
    getReducedMotionServerSnapshot,
  );
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
 * Runs `factory` inside an effect and cleans up whatever anime.js instance
 * it returns, on unmount or whenever `deps` change again, so no component
 * needs to remember cleanup itself. Pass null (e.g. from withMotion under
 * reduced motion) to skip entirely.
 *
 * Cleanup calls .complete() (jump to the animation's end state), not
 * .revert() (jump back to its start). Every animation driven through this
 * hook animates *toward* a final, correct value -- a fade-in, a count-up,
 * a bar filling in -- never a repeating one (the one looping animation in
 * this app, FieldBox's low-confidence breathing, manages its own effect
 * directly, not through this hook). That matters because of exactly one
 * unavoidable race: useReducedMotion() (lib/motion.ts) must render `false`
 * on the server and through initial hydration even on a client that
 * actually prefers reduced motion, since SSR can't know that. A consumer's
 * effect here can therefore run once with that stale `false`, start a
 * real animation, and then re-run moments later once the real value
 * arrives -- if this cleanup reverted to the start, that would leave the
 * element permanently stuck mid-animation (nothing else ever puts it back,
 * since withMotion's reduced-motion branch never runs a replacement
 * animation). Completing instead means that stale first pass simply
 * resolves immediately to the correct final state -- which is exactly
 * what reduced motion wants anyway.
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
      instance?.complete();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deps is caller-provided
  }, deps);
}
