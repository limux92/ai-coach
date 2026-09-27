import { renderDrawer as paintDrawer } from './views/workout.js';

export function createWorkoutController(state, drawerRoot, api) {
  let focusBeforeDrawer;
  const renderDrawer = () => paintDrawer(drawerRoot, state);
  async function openDrawer(id, planned = false) {
    const row = (planned ? state.plans : state.workouts).find((r) => r.id === id);
    if (!row) return;
    focusBeforeDrawer = document.activeElement;
    const d = {
      id,
      row,
      planned,
      loading: !planned,
      samples: [],
      sampleTotal: 0,
      nextOffset: null,
      samplesLoading: !planned,
      sampleField: 'heart_rate',
    };
    state.drawer = d;
    renderDrawer();
    drawerRoot.querySelector('[data-action="close-drawer"]:is(button)').focus();
    if (planned) return;
    await Promise.all([
      api(`/workouts/${encodeURIComponent(id)}`)
        .then((detail) => {
          if (state.drawer !== d) return;
          d.row = { ...row, ...detail };
        })
        .catch((error) => {
          d.error = error.message;
        })
        .finally(() => {
          if (state.drawer !== d) return;
          d.loading = false;
          renderDrawer();
        }),
      loadSamples(d, 0),
    ]);
  }

  async function loadSamples(d, offset) {
    if (state.drawer !== d) return;
    d.samplesLoading = true;
    renderDrawer();
    try {
      const result = await api(
        `/workouts/${encodeURIComponent(d.id)}/samples?${new URLSearchParams({ fields: 'timestamp,heart_rate,power,distance', limit: '500', offset: String(offset) })}`,
      );
      if (state.drawer !== d) return;
      d.samples.push(...result.items);
      d.sampleTotal = result.total;
      d.nextOffset = result.next_offset;
    } catch (error) {
      if (state.drawer === d) d.sampleError = error.message;
    } finally {
      if (state.drawer === d) {
        d.samplesLoading = false;
        renderDrawer();
      }
    }
  }

  function closeDrawer() {
    state.drawer = null;
    renderDrawer();
    focusBeforeDrawer?.focus();
  }
  return { openDrawer, loadSamples, closeDrawer, renderDrawer };
}
