import test from 'node:test';
import assert from 'node:assert/strict';
import { bindEvents } from '../src/events.js';

test('bindEvents handles sport filter, view switching, and calendar mode', () => {
  const listeners = {};
  globalThis.document = {
    addEventListener: (type, fn) => {
      listeners[type] = fn;
    },
  };

  const state = {
    view: 'overview',
    sport: 'all',
    zone: 1,
    calendarMode: 'month',
    today: '2026-09-23',
    month: '2026-09-01',
    week: '2026-08-31',
  };

  let shellCalled = 0;
  let loadDataCalled = 0;
  const actions = {
    shell: () => {
      shellCalled++;
    },
    loadData: () => {
      loadDataCalled++;
    },
  };

  bindEvents(state, null, actions);

  const click = (dataset, tagName = 'button') => {
    listeners.click({
      target: {
        tagName,
        dataset,
        closest: (selector) => {
          if (selector.includes('button') && tagName === 'button') {
            return { dataset, tagName };
          }
          if (selector.includes('[data-action]') && dataset.action) {
            return { dataset, tagName };
          }
          return null;
        },
      },
    });
  };

  // Test sport filter
  click({ sport: 'run' });
  assert.equal(state.sport, 'run');
  assert.equal(state.zone, 0);
  assert.equal(shellCalled, 1);

  // Test view switch to calendar
  click({ view: 'calendar' });
  assert.equal(state.view, 'calendar');
  assert.equal(shellCalled, 2);

  // Test calendar mode switch to week
  click({ mode: 'week' });
  assert.equal(state.calendarMode, 'week');
  assert.equal(shellCalled, 3);

  // Test open intervals modal
  click({ action: 'open-intervals' }, 'span');
  assert.equal(state.showIntervalsModal, true);
  assert.equal(shellCalled, 4);

  // Test day click switches to calendar week and loads data
  click({ day: '2026-09-15' });
  assert.equal(state.view, 'calendar');
  assert.equal(state.calendarMode, 'week');
  assert.equal(state.month, '2026-09-01');
  assert.equal(state.week, '2026-09-14');
  assert.equal(loadDataCalled, 1);
});

test('bindEvents safely handles omitted shell without throwing', () => {
  const listeners = {};
  globalThis.document = {
    addEventListener: (type, fn) => {
      listeners[type] = fn;
    },
  };

  const state = { view: 'overview', sport: 'all' };
  bindEvents(state, null, {});

  const click = (dataset) => {
    listeners.click({
      target: {
        tagName: 'button',
        dataset,
        closest: () => ({ dataset, tagName: 'button' }),
      },
    });
  };

  assert.doesNotThrow(() => {
    click({ sport: 'cycle' });
    click({ view: 'calendar' });
  });
  assert.equal(state.sport, 'cycle');
  assert.equal(state.view, 'calendar');
});
