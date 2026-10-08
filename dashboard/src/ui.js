import { isNumber, number } from './data.js';

export const names = {
  run: 'Running',
  cycle: 'Cycling',
  other: 'Other sports',
  all: 'All activities',
};

const paths = {
  overview:
    '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  calendar:
    '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M16 3v4M8 3v4M3 11h18M8 15h2M14 15h2"/>',
  run: '<circle cx="14" cy="4" r="2"/><path d="m7 10 4-3 4 4 4 1M11 7l-2 7 4 3-2 5M9 14l-3 5H2"/>',
  cycle:
    '<circle cx="5" cy="16" r="4"/><circle cx="19" cy="16" r="4"/><path d="m5 16 5-9 5 9H5l-2-9h4M10 7h5l4 9M15 4h3"/>',
  other: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  arrow: '<path d="m9 5 7 7-7 7"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 11.5-2L20 8M4 16l2.5 3A7 7 0 0 0 18 17"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  distance: '<path d="M5 4h6v6H5zM13 14h6v6h-6zM8 10v5h5M11 7h5v7"/>',
  load: '<path d="m13 2-9 12h7l-1 8 10-12h-7l1-8Z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  logout: '<path d="M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M14 8l4 4-4 4M8 12h10"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
  heart:
    '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  mountain: '<path d="m2 20 8-16 5 10 3-6 4 12H2ZM7 10l3 2 3-2"/>',
};

export const icon = (name, extra = '') =>
  /* HTML */ `<svg
    class="icon ${extra}"
    aria-hidden="true"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.65"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    ${paths[name] || paths.other}
  </svg>`;

export const brand = /* HTML */ `<span class="brand-mark" aria-hidden="true"
  ><svg viewBox="0 0 40 40" fill="none">
    <path
      d="M8 29V12l12 11 12-11v17"
      stroke="currentColor"
      stroke-width="3.5"
      stroke-linecap="round"
      stroke-linejoin="round"
    /></svg
></span>`;

export const datum = (value, suffix = '') => (isNumber(value) ? `${number(value)}${suffix}` : '—');

export const spinner = '<span class="spinner" aria-hidden="true"></span>';
