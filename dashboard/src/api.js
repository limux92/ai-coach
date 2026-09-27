import { getIdToken } from 'firebase/auth';

export function createApi(getAuthInstance) {
  async function api(path, signal, method = 'GET') {
    const auth = getAuthInstance();
    let token;
    try {
      if (!auth.currentUser) throw new Error();
      token = await getIdToken(auth.currentUser);
    } catch {
      const e = new Error('Your sign-in has expired. Sign in again to reconnect.');
      e.auth = true;
      throw e;
    }
    const response = await fetch(`/dashboard/api${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
      signal,
    });
    if (!response.ok) {
      const messages = {
        401: 'Your sign-in has expired. Sign in again to reconnect.',
        403: 'This account does not have access to this private training space.',
        409: 'This workout’s summary is available, but detailed samples are not available. Its original FIT file is preserved.',
        429: 'The service is busy. Please try again shortly.',
        502: 'The training database is temporarily unavailable. Your saved data is safe.',
      };
      const error = new Error(
        messages[response.status] ||
          'The training service could not complete this request. Please try again.',
      );
      error.status = response.status;
      error.auth = response.status === 401;
      throw error;
    }
    return response.json();
  }

  async function fetchPages(path, oldest, newest, signal) {
    const rows = [],
      cursors = new Set();
    let cursor;
    for (let page = 0; page < 100; page++) {
      const params = new URLSearchParams({ oldest, newest, limit: '50' });
      if (cursor) params.set('after', cursor);
      const result = await api(`${path}?${params}`, signal);
      if (!Array.isArray(result.items))
        throw new Error('The training service returned an unexpected response. Please retry.');
      rows.push(...result.items);
      if (!result.next_cursor) return rows;
      if (cursors.has(result.next_cursor))
        throw new Error('The workout list could not finish loading. Please retry.');
      cursor = result.next_cursor;
      cursors.add(cursor);
    }
    throw new Error('This date range contains too many workouts. Please choose a smaller view.');
  }
  return { api, fetchPages, post: (path, signal) => api(path, signal, 'POST') };
}
