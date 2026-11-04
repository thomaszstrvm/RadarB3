export type MotionPreference = 'system' | 'reduce';
export const MOTION_PREFERENCE_KEY = 'radar-motion';

export function readMotionPreference(storage?: Pick<Storage, 'getItem'>): MotionPreference {
  try {
    return (storage ?? window.localStorage).getItem(MOTION_PREFERENCE_KEY) === 'reduce' ? 'reduce' : 'system';
  } catch {
    return 'system';
  }
}

export function readAppliedMotionPreference(root?: Pick<HTMLElement, 'dataset'>): MotionPreference {
  const applied = root?.dataset.motion;
  return applied === 'system' || applied === 'reduce' ? applied : readMotionPreference();
}

/** Apply immediately; persistence failure must not undo the accessible choice. */
export function applyMotionPreference(preference: MotionPreference, root: HTMLElement) {
  root.dataset.motion = preference;
}

export function saveMotionPreference(preference: MotionPreference, storage?: Pick<Storage, 'setItem'>): boolean {
  try {
    (storage ?? window.localStorage).setItem(MOTION_PREFERENCE_KEY, preference);
    return true;
  } catch {
    return false;
  }
}
