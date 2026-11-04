import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MotionPreferenceControl } from './MotionPreferenceControl';
import { readAppliedMotionPreference, readMotionPreference, saveMotionPreference } from './motionPreference';

describe('local motion preference', () => {
  it('only recognises the supported opt-out value; absent/corrupt values follow the OS', () => {
    for (const value of [null, '', 'dark', '{broken', 'system']) {
      expect(readMotionPreference({getItem: () => value})).toBe('system');
    }
    expect(readMotionPreference({getItem: () => 'reduce'})).toBe('reduce');
  });
  it('does not break rendering or claim persistence when storage is blocked', () => {
    expect(readMotionPreference({getItem: () => {throw new Error('blocked');}})).toBe('system');
    expect(saveMotionPreference('reduce', {setItem: () => {throw new Error('blocked');}})).toBe(false);
    expect(renderToStaticMarkup(<MotionPreferenceControl/>)).toContain('Movimento da interface');
  });
  it('persists a choice that a subsequent visit can restore', () => {
    const values = new Map<string,string>();
    const storage = {setItem: (key: string, value: string) => {values.set(key,value);}, getItem: (key: string) => values.get(key) ?? null};
    expect(saveMotionPreference('reduce', storage)).toBe(true);
    expect(readMotionPreference(storage)).toBe('reduce');
    expect(saveMotionPreference('system', storage)).toBe(true);
    expect(readMotionPreference(storage)).toBe('system');
  });
  it('keeps the currently applied choice when revisiting settings after storage fails', () => {
    expect(saveMotionPreference('reduce', {setItem: () => {throw new Error('blocked');}})).toBe(false);
    expect(readAppliedMotionPreference({dataset: {motion: 'reduce'}})).toBe('reduce');
    expect(readAppliedMotionPreference({dataset: {motion: 'system'}})).toBe('system');
  });
});
