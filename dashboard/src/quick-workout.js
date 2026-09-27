import { escapeHTML as esc } from './data.js';

export function quickWorkoutPanel(state) {
  const busy = state.quickWorkout?.busy;
  const result = state.quickWorkout?.result;
  const error = state.quickWorkout?.error;
  const loading = state.loading;
  const authError = state.authError;
  const disabled = busy || loading || authError;
  const desc = `A session based on your recent training: cycling for Zwift or running for Garmin, with rest when appropriate.`;
  const duration =
    result?.format === 'fit'
      ? `${Math.round((result?.duration_s || 0) / 60)} min timed main set + warm-up and cool-down until LAP press`
      : `${Math.round((result?.duration_s || 0) / 60)} minutes · Uses your Zwift FTP`;
  const garminHelp =
    '<p>Run at the effort shown in each step. Press LAP to finish warm-up ' +
    'and again to finish cool-down. Transfer the .fit workout to a compatible Garmin device.</p>';
  const status = error ? `<div role="status" aria-live="polite">${esc(error)}</div>` : '';
  const resultHtml = result
    ? `<div class="panel">
        <h3 class="panel-heading">${esc(result.plan.title)}</h3>
        <p>${esc(result.plan.rationale)}</p>
        ${
          result.plan.caveats.length
            ? `<ul>${result.plan.caveats.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>`
            : ''
        }
        <p>${result.plan.decision === 'rest' ? 'Rest today · no workout file' : duration}</p>
        ${result.plan.decision === 'workout' && result.format === 'fit' ? garminHelp : ''}
      </div>`
    : '';
  return `<section class="panel quick-workout-panel" aria-label="Quick Workout" aria-busy="${busy ? 'true' : 'false'}">
    <h2 class="panel-heading">Quick Workout</h2>
    <p>${esc(desc)}</p>
    <div class="quick-workout-actions">
      <button data-action="quick-workout" data-format="zwo" class="button primary" ${disabled ? 'disabled' : ''}>
        ${busy && state.quickWorkout.format === 'zwo' ? 'Preparing workout…' : 'Quick Ride · Zwift'}
      </button>
      <button data-action="quick-workout" data-format="fit" class="button primary" ${disabled ? 'disabled' : ''}>
        ${busy && state.quickWorkout.format === 'fit' ? 'Preparing workout…' : 'Quick Run · Garmin'}
      </button>
    </div>
    ${status}
    <div aria-live="polite">${resultHtml}</div>
  </section>`;
}

export function createQuickWorkoutController(state, post, render, download = downloadWorkout) {
  let counter = 0;
  let activeAbort = null;
  const controller = {
    generate: generate,
    cancel: cancel,
  };

  function generate(format = 'zwo') {
    if (state.quickWorkout?.busy || !['zwo', 'fit'].includes(format)) return;
    counter++;
    const thisCount = counter;
    const abort = new AbortController();
    activeAbort = abort;
    state.quickWorkout = { busy: true, result: null, error: null, format };
    render();
    return (async () => {
      try {
        const data = await post(
          format === 'fit' ? '/quick-workout/run' : '/quick-workout',
          abort.signal,
        );
        if (counter !== thisCount) return;
        const { plan, duration_s, filename, zwo, garmin_filename, fit_base64 } = data;
        const running = format === 'fit';
        if (data.sport !== (running ? 'running' : 'cycling'))
          throw new Error('Wrong workout sport');
        if (!Number.isFinite(duration_s) || duration_s < 0 || duration_s > 5400)
          throw new Error('Invalid duration');
        if (
          !plan ||
          typeof plan.day !== 'string' ||
          typeof plan.title !== 'string' ||
          typeof plan.rationale !== 'string' ||
          !Array.isArray(plan.caveats) ||
          !plan.caveats.every((c) => typeof c === 'string') ||
          !['workout', 'rest'].includes(plan.decision)
        ) {
          throw new Error('Invalid plan');
        }
        let fit_bytes = null;
        if (plan.decision === 'workout') {
          if (
            running &&
            (plan.sport !== 'running' ||
              data.open_duration !== true ||
              zwo !== null ||
              filename !== null)
          )
            throw new Error('Invalid running prescription');
          if (
            format === 'zwo' &&
            (!zwo ||
              typeof zwo !== 'string' ||
              (!zwo.startsWith('<?xml') && !zwo.startsWith('<workout_file')) ||
              !filename ||
              !/^quick-workout-\d{4}-\d{2}-\d{2}\.zwo$/.test(filename))
          ) {
            throw new Error('Invalid workout data');
          }
          if (format === 'fit') {
            if (
              !garmin_filename ||
              typeof garmin_filename !== 'string' ||
              !/^quick-run-\d{4}-\d{2}-\d{2}\.fit$/.test(garmin_filename)
            ) {
              throw new Error('Invalid Garmin filename');
            }
            if (
              !fit_base64 ||
              typeof fit_base64 !== 'string' ||
              fit_base64.length === 0 ||
              fit_base64.length > 65536
            ) {
              throw new Error('Invalid FIT base64');
            }
            try {
              const decoded = atob(fit_base64);
              const bytes = new Uint8Array(decoded.length);
              for (let i = 0; i < decoded.length; i++) bytes[i] = decoded.charCodeAt(i);
              if (bytes.length < 16) throw new Error('FIT too short');
              if (bytes[0] !== 0x0e) throw new Error('FIT first byte');
              if (bytes[8] !== 46 || bytes[9] !== 70 || bytes[10] !== 73 || bytes[11] !== 84) {
                throw new Error('FIT signature missing');
              }
              const size = new DataView(bytes.buffer).getUint32(4, true);
              if (size + 16 !== bytes.length) throw new Error('FIT size mismatch');
              fit_bytes = bytes;
            } catch (e) {
              throw new Error('Invalid FIT data');
            }
          }
        } else {
          if (zwo !== null || filename !== null || garmin_filename !== null || fit_base64 !== null)
            throw new Error('Rest data invalid');
        }
        state.quickWorkout.result = {
          plan,
          duration_s,
          filename,
          zwo,
          garmin_filename: garmin_filename || null,
          fit_base64: fit_base64 || null,
          fit_bytes,
          format,
        };
        if (plan.decision === 'workout') download(state.quickWorkout.result);
      } catch (e) {
        if (counter !== thisCount) return;
        if (e.name === 'AbortError') return;
        let msg = 'Couldn’t prepare a workout. Please try again.';
        if (e.auth) msg = 'Your sign-in expired. Sign in again to prepare a workout.';
        else if (e.status === 409)
          msg = 'Your training data needs a fresh, complete sync before a recommendation.';
        else if (e.status === 503)
          msg =
            'Quick Workout is not available yet. The recommendation connection needs attention.';
        else if (e.status === 429) msg = 'Please wait before requesting another recommendation.';
        state.quickWorkout.error = msg;
      } finally {
        if (counter === thisCount) {
          state.quickWorkout.busy = false;
          render();
        }
      }
    })();
  }

  function cancel() {
    counter++;
    activeAbort?.abort();
    state.quickWorkout = { busy: false, result: null, error: null };
  }

  return controller;
}

export function downloadWorkout(result) {
  const garmin = result.format === 'fit';
  const blob = new Blob([garmin ? result.fit_bytes : result.zwo], {
    type: garmin ? 'application/octet-stream' : 'application/xml;charset=utf-8',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = garmin ? result.garmin_filename : result.filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  try {
    a.click();
  } finally {
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
