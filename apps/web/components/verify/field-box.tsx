"use client";

import { useEffect, useRef } from "react";
import { animate } from "animejs";

import { DURATION, EASING, useReducedMotion, withMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

export interface FieldBoxProps {
  fieldPath: string;
  bbox: { x: number; y: number; w: number; h: number };
  belowThreshold: boolean;
  drawIndex: number;
  isHovered: boolean;
  onHoverChange: (fieldPath: string | null) => void;
  onClick: (fieldPath: string) => void;
}

const BASE_OPACITY = 0.7;
const HOVER_OPACITY = 1;
const DRAW_STAGGER_MS = 40;
const DRAW_DURATION_MS = 260;
const BREATHE_DURATION_MS = 1200; // one direction; alternate+loop makes a full 2.4s cycle

export function FieldBox({
  fieldPath,
  bbox,
  belowThreshold,
  drawIndex,
  isHovered,
  onHoverChange,
  onClick,
}: FieldBoxProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const rectRef = useRef<SVGRectElement>(null);
  const reducedMotion = useReducedMotion();

  // Draw-in on mount: stroke-dashoffset from full to 0, staggered by
  // reading-order index. Depends on reducedMotion (not an empty array)
  // for the same reason components/pipeline/pipeline-rail.tsx's draw-in
  // does: useReducedMotion() must report `false` through initial
  // hydration even on a client that actually prefers reduced motion (SSR
  // can't know), so this effect's first run can see that stale guess --
  // an empty dependency array would freeze that stale value in the
  // closure forever instead of re-running once the real value arrives.
  useEffect(() => {
    const rect = rectRef.current;
    if (!rect) return;
    const length = rect.getTotalLength();
    rect.style.strokeDasharray = `${length}`;

    if (reducedMotion) {
      rect.style.strokeDashoffset = "0";
      return;
    }

    rect.style.strokeDashoffset = `${length}`;
    const animation = animate(rect, {
      strokeDashoffset: [length, 0],
      duration: DRAW_DURATION_MS,
      delay: drawIndex * DRAW_STAGGER_MS,
      ease: "easeOutExpo",
    });
    return () => {
      animation.complete();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- drawIndex/fieldPath don't change for a mounted box
  }, [reducedMotion]);

  // Breathing loop for low-confidence boxes -- the only looping animation
  // on this screen, deliberately, so the eye goes straight to it.
  useEffect(() => {
    if (!belowThreshold) return;
    const rect = rectRef.current;
    if (!rect) return;
    const animation = withMotion(reducedMotion, () =>
      animate(rect, {
        opacity: [1, 0.35],
        duration: BREATHE_DURATION_MS,
        alternate: true,
        loop: true,
        ease: "easeInOutQuad",
      }),
    );
    return () => {
      animation?.revert();
    };
  }, [belowThreshold, reducedMotion]);

  // Hover: scale to 1.06 and raise opacity, 180ms, bidirectional with the
  // matching field row (driven by the isHovered prop, not this element's
  // own :hover -- the field row can trigger this too).
  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    withMotion(reducedMotion, () =>
      animate(wrapper, {
        scale: isHovered ? 1.06 : 1,
        opacity: isHovered ? HOVER_OPACITY : BASE_OPACITY,
        duration: DURATION.fast,
        ease: EASING.stateChange,
      }),
    );
    if (reducedMotion) {
      wrapper.style.transform = `scale(${isHovered ? 1.06 : 1})`;
      wrapper.style.opacity = `${isHovered ? HOVER_OPACITY : BASE_OPACITY}`;
    }
  }, [isHovered, reducedMotion]);

  return (
    <div
      ref={wrapperRef}
      data-field-path={fieldPath}
      role="button"
      tabIndex={0}
      aria-label={`Highlight field ${fieldPath}`}
      className="absolute cursor-pointer rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      style={{
        left: `${bbox.x * 100}%`,
        top: `${bbox.y * 100}%`,
        width: `${bbox.w * 100}%`,
        height: `${bbox.h * 100}%`,
        opacity: BASE_OPACITY,
        transformOrigin: "center",
      }}
      onMouseEnter={() => onHoverChange(fieldPath)}
      onMouseLeave={() => onHoverChange(null)}
      onFocus={() => onHoverChange(fieldPath)}
      onBlur={() => onHoverChange(null)}
      onClick={() => onClick(fieldPath)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick(fieldPath);
        }
      }}
    >
      <svg className="absolute inset-0 h-full w-full overflow-visible">
        <rect
          ref={rectRef}
          x={1}
          y={1}
          width="calc(100% - 2px)"
          height="calc(100% - 2px)"
          rx={2}
          fill="none"
          strokeWidth={2}
          className={cn(belowThreshold ? "stroke-signal-warn" : "stroke-signal-clean")}
        />
      </svg>
    </div>
  );
}
