import { useEffect, useState } from "react";

/** Follows a CSS media query. Where `matchMedia` is missing it reports false. */
export function useMediaQuery(query: string): boolean {
  const read = () => (typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false);
  const [matches, setMatches] = useState(read);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}
