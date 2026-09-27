import assert from 'node:assert/strict';
import test from 'node:test';
import {
  createQuickWorkoutController,
  quickWorkoutPanel,
  downloadWorkout,
} from '../src/quick-workout.js';

function result(running = false) {
  return {
    sport: running ? 'running' : 'cycling',
    open_duration: running,
    plan: {
      ...(running ? { sport: 'running' } : {}),
      day: '2026-09-25',
      title: 'Easy ride',
      rationale: 'Recovery',
      caveats: [],
      decision: 'workout',
    },
    duration_s: 1800,
    filename: running ? null : 'quick-workout-2026-09-25.zwo',
    zwo: running ? null : '<?xml version="1.0"?><workout_file/>',
    garmin_filename: running ? 'quick-run-2026-09-25.fit' : null,
    fit_base64: running ? fitData().toString('base64') : null,
  };
}
function harness(post) {
  const state = {};
  const downloads = [];
  let renders = 0;
  const controller = createQuickWorkoutController(
    state,
    post,
    () => ++renders,
    (x) => downloads.push(x),
  );
  return { state, downloads, controller, renders: () => renders };
}

test('one click requests and downloads one validated workout', async () => {
  let calls = 0;
  const h = harness(async (path, signal) => {
    ++calls;
    assert.equal(path, '/quick-workout');
    assert.ok(signal instanceof AbortSignal);
    return result();
  });
  await h.controller.generate();
  assert.equal(calls, 1);
  assert.equal(h.downloads.length, 1);
  assert.equal(h.state.quickWorkout.busy, false);
  assert.equal(h.state.quickWorkout.result.plan.title, 'Easy ride');
});

test('rest is displayed without any file download', async () => {
  const r = result();
  Object.assign(r, {
    filename: null,
    zwo: null,
    garmin_filename: null,
    fit_base64: null,
    duration_s: 0,
  });
  r.plan.decision = 'rest';
  const h = harness(async () => r);
  await h.controller.generate();
  assert.equal(h.downloads.length, 0);
  const html = quickWorkoutPanel(h.state);
  assert.match(html, /Rest today/);
  assert.doesNotMatch(html, /0 minutes/);
});

function fitData() {
  const bytes = Buffer.alloc(20);
  bytes[0] = 14;
  bytes.writeUInt32LE(4, 4);
  bytes.write('.FIT', 8);
  bytes[15] = 255;
  return bytes;
}

test('Garmin requests a running prescription and downloads binary FIT bytes', async () => {
  const h = harness(async (path) => {
    assert.equal(path, '/quick-workout/run');
    return result(true);
  });
  await h.controller.generate('fit');
  assert.equal(h.downloads.length, 1);
  assert.equal(h.downloads[0].format, 'fit');
  assert.deepEqual(h.downloads[0].fit_bytes, new Uint8Array(fitData()));
  assert.match(quickWorkoutPanel(h.state), /LAP press/);
});

test('Garmin rejects malformed files and unknown formats make no request', async () => {
  for (const change of [
    { garmin_filename: '../bad.fit' },
    { fit_base64: '!' },
    { fit_base64: 'A'.repeat(65537) },
    { fit_base64: Buffer.alloc(20).toString('base64') },
    { fit_base64: fitData().subarray(0, 19).toString('base64') },
  ]) {
    const h = harness(async () => ({ ...result(true), ...change }));
    await h.controller.generate('fit');
    assert.equal(h.downloads.length, 0);
    assert.ok(h.state.quickWorkout.error);
  }
  let calls = 0;
  const h = harness(async () => {
    calls++;
    return result(true);
  });
  await h.controller.generate('unknown');
  assert.equal(calls, 0);
});

test('Garmin rest has no file; inconsistent rest payload is rejected', async () => {
  const r = result(true);
  r.plan.decision = 'rest';
  Object.assign(r, {
    duration_s: 0,
    filename: null,
    zwo: null,
    garmin_filename: null,
    fit_base64: null,
  });
  const h = harness(async () => r);
  await h.controller.generate('fit');
  assert.equal(h.downloads.length, 0);
  assert.equal(h.state.quickWorkout.error, null);
  r.fit_base64 = 'unexpected';
  await h.controller.generate('fit');
  assert.ok(h.state.quickWorkout.error);
});

test('both format buttons are disabled during either request and on loading/auth error', () => {
  for (const state of [
    { quickWorkout: { busy: true, format: 'fit' } },
    { loading: true },
    { authError: true },
  ]) {
    const html = quickWorkoutPanel(state);
    assert.match(html, /data-format="zwo"/);
    assert.match(html, /data-format="fit"/);
    assert.equal((html.match(/ disabled/g) || []).length, 2);
  }
});

test('download helper preserves binary bytes and chooses the FIT filename', async (t) => {
  let blob;
  const a = { style: {}, click() {} };
  t.mock.method(URL, 'createObjectURL', (value) => {
    blob = value;
    return 'blob:test';
  });
  t.mock.method(URL, 'revokeObjectURL', () => {});
  t.mock.method(globalThis, 'setTimeout', (callback) => {
    callback();
    return 0;
  });
  const previous = globalThis.document;
  globalThis.document = { createElement: () => a, body: { appendChild() {}, removeChild() {} } };
  try {
    downloadWorkout({
      format: 'fit',
      garmin_filename: 'quick-run-2026-09-25.fit',
      fit_bytes: new Uint8Array(fitData()),
    });
    assert.equal(a.download, 'quick-run-2026-09-25.fit');
    assert.equal(blob.type, 'application/octet-stream');
    assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), new Uint8Array(fitData()));
  } finally {
    if (previous === undefined) delete globalThis.document;
    else globalThis.document = previous;
  }
});

test('duplicate clicks use one request and cancellation suppresses late results', async () => {
  let resolve,
    capturedSignal,
    calls = 0;
  const h = harness((path, signal) => {
    capturedSignal = signal;
    ++calls;
    return new Promise((done) => {
      resolve = done;
    });
  });
  const first = h.controller.generate();
  const second = h.controller.generate();
  assert.equal(calls, 1);
  h.controller.cancel();
  const rendered = h.renders();
  assert.equal(capturedSignal.aborted, true);
  resolve(result());
  await Promise.all([first, second]);
  assert.equal(h.downloads.length, 0);
  assert.equal(h.renders(), rendered);
  assert.equal(h.state.quickWorkout.result, null);
});

test('stale context displays sync guidance and raw errors stay private', async () => {
  const h = harness(async () => {
    throw Object.assign(new Error('private detail'), { status: 409 });
  });
  await h.controller.generate();
  assert.match(h.state.quickWorkout.error, /sync/);
  assert.doesNotMatch(h.state.quickWorkout.error, /private detail/);
  assert.equal(h.downloads.length, 0);
});

test('invalid filenames never trigger a download', async () => {
  const h = harness(async () => ({ ...result(), filename: '../bad.zwo' }));
  await h.controller.generate();
  assert.ok(h.state.quickWorkout.error);
  assert.equal(h.downloads.length, 0);
});

test('panel escapes provider text and disables an in-flight action', () => {
  const r = result();
  r.plan.title = '<script>bad</script>';
  r.plan.caveats = ['<img onerror="bad">'];
  const html = quickWorkoutPanel({ quickWorkout: { busy: true, result: r } });
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<script>|<img/);
  assert.match(html, /disabled/);
});

test('a cycling response cannot be downloaded by the running button', async () => {
  const h = harness(async () => result());
  await h.controller.generate('fit');
  assert.equal(h.downloads.length, 0);
  assert.ok(h.state.quickWorkout.error);
});

test('running requires explicit open duration and cannot carry a cycling export', async () => {
  for (const change of [{ open_duration: false }, { zwo: '<workout_file/>' }]) {
    const h = harness(async () => ({ ...result(true), ...change }));
    await h.controller.generate('fit');
    assert.equal(h.downloads.length, 0);
    assert.ok(h.state.quickWorkout.error);
  }
});
