// Unit harness for the component's effect logic, NOT a React DOM/browser test.
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AnalysisJob } from './types';

const harness = vi.hoisted(() => ({
  cursor: 0, slots: [] as any[], effects: [] as (() => unknown)[],
  snapshot: undefined as AnalysisJob | undefined,
}));
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useState: (initial: unknown) => {
    const index = harness.cursor++;
    return [index === 0 ? {phase: 'idle', error: '', snapshot: harness.snapshot} : initial, vi.fn()];
  },
  useRef: (initial: unknown) => {
    const index = harness.cursor++;
    return harness.slots[index] ??= {current: initial};
  },
  useEffect: (effect: () => unknown, deps: unknown[]) => {
    const index = harness.cursor++;
    const previous = harness.slots[index];
    if (!previous || deps.some((value, i) => !Object.is(value, previous[i]))) harness.effects.push(effect);
    harness.slots[index] = deps;
  },
}));
vi.mock('./api', () => ({request: vi.fn(() => {throw Error('Network forbidden in isolated test');})}));
vi.mock('./pollAnalysis', async importOriginal => ({
  ...await importOriginal<typeof import('./pollAnalysis')>(),
  watchAnalysis: vi.fn(() => vi.fn()),
}));
import { AnalysisTracking } from './AnalysisTracking';
import { request } from './api';

function snapshot(status: string, updated = '2026-10-29T12:00:00Z'): AnalysisJob {
  return {asset_id: 'qa:DEMO3', as_of: updated, queue_position: null, queue_position_reason: null,
    result_analysis: null, job: {id: 'qa-job', kind: 'analyse', status, created_at: updated, updated_at: updated}};
}
function render(onResult: () => void, revision = 0) {
  harness.cursor = 0;
  AnalysisTracking({assetId: 'qa:DEMO3', busy: false, revision, onResult, openJobs: vi.fn(), resultLoading: false, resultError: false});
  harness.effects.splice(0).forEach(effect => effect());
}
beforeEach(() => {harness.slots = []; harness.effects = []; harness.snapshot = undefined; vi.clearAllMocks();});
describe('Terminal notification effect with isolated hook harness', () => {
  it.each(['paused', 'failed', 'blocked', 'cancelled', 'partial', 'succeeded'])('refreshes once after running → %s, including rerender/revision', status => {
    const refresh = vi.fn();
    harness.snapshot = snapshot('running'); render(refresh);
    expect(refresh).not.toHaveBeenCalled();
    harness.snapshot = snapshot(status); render(refresh); render(refresh, 1);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(request).not.toHaveBeenCalled();
  });
  it('refreshes a new terminal version using the latest callback', () => {
    const old = vi.fn(), latest = vi.fn();
    harness.snapshot = snapshot('paused'); render(old);
    render(latest, 1); expect(latest).not.toHaveBeenCalled();
    harness.snapshot = snapshot('paused', '2026-10-29T12:01:00Z'); render(latest);
    expect(old).toHaveBeenCalledTimes(1); expect(latest).toHaveBeenCalledTimes(1);
  });
  it('ignores a stale snapshot from another asset and missing jobs', () => {
    const refresh = vi.fn();
    harness.snapshot = {...snapshot('failed'), asset_id: 'qa:OTHER3'}; render(refresh);
    harness.snapshot = {...snapshot('failed'), job: null}; render(refresh);
    expect(refresh).not.toHaveBeenCalled();
  });
});
