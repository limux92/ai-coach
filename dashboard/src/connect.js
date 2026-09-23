import { GoogleAuthProvider, signInWithPopup, signOut } from 'firebase/auth';

// The destination is fetched from the server's validated OAuth request, never
// accepted as a browser URL parameter. Approval is always an explicit click.
export async function connectChat(root, auth) {
  const request = new URLSearchParams(location.search).get('request');
  if (!/^[A-Za-z0-9_-]{43}$/.test(request || '')) throw new Error('Invalid login request');
  const endpoint = `/oauth/consent?request=${encodeURIComponent(request)}`;
  const response = await fetch(endpoint, { cache: 'no-store', credentials: 'same-origin' });
  if (!response.ok) throw new Error('Expired login request');
  const details = await response.json();
  root.innerHTML = `<main class="setup-state"><h1>Connect your training data</h1>
    <p id="client-description"></p><p>This connection can read your training summaries and workouts. It cannot change your plans or start imports.</p>
    <button class="button primary" id="approve-chat">Sign in with Google and allow access</button>
    <button class="button" id="cancel-chat">Cancel</button><p role="alert" id="login-error"></p></main>`;
  root.querySelector('#client-description').textContent = `${details.client_name} will receive access at ${details.redirect_host}.`;
  root.querySelector('#cancel-chat').addEventListener('click', () => location.assign('/dashboard/'));
  root.querySelector('#approve-chat').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const provider = new GoogleAuthProvider();
      provider.setCustomParameters({ prompt: 'select_account' });
      const { user } = await signInWithPopup(auth, provider);
      const result = await fetch(endpoint, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { Authorization: `Bearer ${await user.getIdToken()}` } });
      if (!result.ok) throw new Error('denied');
      const { redirect } = await result.json();
      location.assign(redirect);
    } catch {
      await signOut(auth).catch(() => {});
      root.querySelector('#login-error').textContent = 'Access was not granted. Use the authorized owner account, or restart the connection from your chat client.';
      button.disabled = false;
    }
  });
}
