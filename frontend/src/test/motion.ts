/** Makes `window.matchMedia` answer the reduced-motion query (and nothing else) the way a person's setting would. */
export function setReducedMotion(reduced: boolean) {
  window.matchMedia = ((query: string) =>
    ({
      matches: query.includes("prefers-reduced-motion") ? reduced : false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList) as typeof window.matchMedia;
}

/** Makes `matchMedia` say the device has a mouse or trackpad (and reduced motion, as the suite runs), so focus may return to the composer. */
export function setFinePointer() {
  window.matchMedia = ((query: string) =>
    ({
      matches: query.includes("prefers-reduced-motion") || (query.includes("hover: hover") && query.includes("pointer: fine")),
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList) as typeof window.matchMedia;
}
