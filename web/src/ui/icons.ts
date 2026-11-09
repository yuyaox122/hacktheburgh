// Line icons in the style of SF Symbols (16px grid, 1.6 stroke, currentColor). Inline SVG strings for template HTML.

const svg = (body: string, vb = "0 0 16 16") =>
  `<svg class="ic" viewBox="${vb}" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

export const icon = {
  play: `<svg class="ic" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M4.5 2.8v10.4c0 .6.6.9 1.1.6l8.2-5.2a.7.7 0 0 0 0-1.2L5.6 2.2c-.5-.3-1.1 0-1.1.6Z" fill="currentColor"/></svg>`,
  pause: `<svg class="ic" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="3.5" y="2.5" width="3" height="11" rx="1" fill="currentColor"/><rect x="9.5" y="2.5" width="3" height="11" rx="1" fill="currentColor"/></svg>`,
  step: svg(`<path d="M3 3.5v9l6.5-4.5L3 3.5Z" fill="currentColor" stroke="none"/><path d="M12.5 3.5v9"/>`),
  reset: svg(`<path d="M3 8a5 5 0 1 0 1.6-3.7"/><path d="M3 2.5v3h3"/>`),
  layers: svg(`<path d="M8 2 1.8 5.2 8 8.4l6.2-3.2L8 2Z"/><path d="m1.8 8 6.2 3.2L14.2 8"/><path d="m1.8 10.8 6.2 3.2 6.2-3.2"/>`),
  explode: svg(`<rect x="3" y="2" width="10" height="3" rx="1"/><rect x="3" y="11" width="10" height="3" rx="1"/><path d="M8 6.5v3M6.5 8h3" opacity=".9"/>`),
  fit: svg(`<path d="M2.5 5.5v-2a1 1 0 0 1 1-1h2M10.5 2.5h2a1 1 0 0 1 1 1v2M13.5 10.5v2a1 1 0 0 1-1 1h-2M5.5 13.5h-2a1 1 0 0 1-1-1v-2"/>`),
  search: svg(`<circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/>`),
  sparkle: svg(`<path d="M8 1.8c.4 2.9 1.4 3.9 4.2 4.2-2.8.4-3.8 1.4-4.2 4.2-.4-2.8-1.4-3.8-4.2-4.2C6.6 5.7 7.6 4.7 8 1.8Z" fill="currentColor" stroke="none"/><path d="M12.6 10.2c.2 1.2.6 1.6 1.8 1.8-1.2.2-1.6.6-1.8 1.8-.2-1.2-.6-1.6-1.8-1.8 1.2-.2 1.6-.6 1.8-1.8Z" fill="currentColor" stroke="none"/>`),
  more: `<svg class="ic" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="3.5" cy="8" r="1.3" fill="currentColor"/><circle cx="8" cy="8" r="1.3" fill="currentColor"/><circle cx="12.5" cy="8" r="1.3" fill="currentColor"/></svg>`,
  close: svg(`<path d="m4 4 8 8M12 4l-8 8"/>`),
  chevron: svg(`<path d="m4.5 6.5 3.5 3.5 3.5-3.5"/>`),
  cube: svg(`<path d="M8 1.8 13.5 5v6L8 14.2 2.5 11V5L8 1.8Z"/><path d="M2.5 5 8 8.2 13.5 5M8 8.2v6"/>`),
  code: svg(`<path d="m5.5 4.5-3.5 3.5 3.5 3.5M10.5 4.5 14 8l-3.5 3.5"/>`),
  info: svg(`<circle cx="8" cy="8" r="6"/><path d="M8 7.2v3.6"/><circle cx="8" cy="5" r=".4" fill="currentColor"/>`),
  arrowIn: svg(`<path d="M13 8H3M7 4 3 8l4 4"/>`),
  arrowOut: svg(`<path d="M3 8h10M9 4l4 4-4 4"/>`),
  present: svg(`<rect x="1.8" y="2.5" width="12.4" height="8.5" rx="1.5"/><path d="M8 11v2.5M5.5 13.5h5"/>`),
  textSize: svg(`<path d="M2 12.5 5 4.5l3 8M3.1 10h3.8M9.5 12.5l2.25-6 2.25 6M10.3 10.5h2.9"/>`),
  wrap: svg(`<path d="M2.5 4h11M2.5 8h9a2 2 0 0 1 0 4H8.5M2.5 12h3"/><path d="m10 10.5-1.5 1.5 1.5 1.5"/>`),
  upload: svg(`<path d="M8 10.5V2.8M4.8 6 8 2.8 11.2 6"/><path d="M2.5 10.5v1.7a1.3 1.3 0 0 0 1.3 1.3h8.4a1.3 1.3 0 0 0 1.3-1.3v-1.7"/>`),
  logo: `<svg class="logo" viewBox="0 0 120 120" width="24" height="24" aria-hidden="true"><rect width="120" height="120" rx="20" fill="#0E0F12"/><path d="M60 60L104 84L60 108L16 84Z" fill="#2A2D33"/><path d="M60 44L104 68L60 92L16 68Z" fill="#E8893A"/><path d="M60 20L104 44L60 68L16 44Z" fill="#0E0F12" stroke="#C6FF3D" stroke-width="5" stroke-linejoin="round"/><path d="M60 32L82 44L60 56L38 44Z" fill="#C6FF3D"/></svg>`,
};
