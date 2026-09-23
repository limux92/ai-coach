import { monthStart, monthEnd, category, dayOf, formatDate } from './data.js';

export const selectedRows = (rows, state) =>
  state.sport === 'all' ? rows : rows.filter((row) => category(row) === state.sport);

export const inWindow = (rows, start, end) =>
  rows.filter((row) => dayOf(row) >= start && dayOf(row) <= end);

export const monthRows = (state) =>
  inWindow(selectedRows(state.workouts, state), monthStart(state.month), monthEnd(state.month));

export const prettyMonth = (month) =>
  formatDate(month, { day: undefined, month: 'long', year: 'numeric' });
