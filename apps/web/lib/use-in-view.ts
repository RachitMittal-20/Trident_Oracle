"use client";

import { useEffect, useRef, useState } from "react";

/**
 * True once the element has entered the viewport, and stays true forever
 * after -- charts must "animate once on first view, not on every
 * re-render" (a later re-render of the page must not replay the draw-in),
 * so this deliberately never flips back to false once it's seen the
 * element intersect.
 */
export function useInView<T extends Element>(): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (inView) return;
    const node = ref.current;
    if (!node) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setInView(true);
          observer.disconnect();
        }
      },
      { threshold: 0.2 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [inView]);

  return [ref, inView];
}
