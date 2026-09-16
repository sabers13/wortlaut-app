import assert from 'node:assert';
import { describe, it } from 'node:test';
import {
  ALWAYS_SHOW_EXTRA_INFO_STORAGE_KEY,
  extraInfoOpenOnCardLoad,
  extraInfoOpenOnPreferenceChange,
  extraInfoOpenOnReveal,
  readAlwaysShowExtraInfo,
  writeAlwaysShowExtraInfo,
  type StorageLike,
} from './extra-info-preference.ts';

/** An in-memory Storage stand-in, with an optional throwing mode. */
class FakeStorage implements StorageLike {
  private store = new Map<string, string>();
  throwOnGet = false;
  throwOnSet = false;

  getItem(key: string): string | null {
    if (this.throwOnGet) throw new Error('storage access denied');
    return this.store.has(key) ? this.store.get(key)! : null;
  }

  setItem(key: string, value: string): void {
    if (this.throwOnSet) throw new Error('storage access denied');
    this.store.set(key, value);
  }
}

describe('readAlwaysShowExtraInfo', () => {
  it('defaults to false when nothing is stored', () => {
    const storage = new FakeStorage();
    assert.strictEqual(readAlwaysShowExtraInfo(storage), false);
  });

  it('reads a persisted "true" value back as true', () => {
    const storage = new FakeStorage();
    storage.setItem(ALWAYS_SHOW_EXTRA_INFO_STORAGE_KEY, 'true');
    assert.strictEqual(readAlwaysShowExtraInfo(storage), true);
  });

  it('reads a persisted "false" value back as false', () => {
    const storage = new FakeStorage();
    storage.setItem(ALWAYS_SHOW_EXTRA_INFO_STORAGE_KEY, 'false');
    assert.strictEqual(readAlwaysShowExtraInfo(storage), false);
  });

  it('falls back to false for an invalid stored value', () => {
    const storage = new FakeStorage();
    storage.setItem(ALWAYS_SHOW_EXTRA_INFO_STORAGE_KEY, 'yes-please');
    assert.strictEqual(readAlwaysShowExtraInfo(storage), false);
  });

  it('falls back to false when storage access throws', () => {
    const storage = new FakeStorage();
    storage.throwOnGet = true;
    assert.strictEqual(readAlwaysShowExtraInfo(storage), false);
  });

  it('falls back to false when no storage is available at all', () => {
    assert.strictEqual(readAlwaysShowExtraInfo(null), false);
    assert.strictEqual(readAlwaysShowExtraInfo(undefined), false);
  });
});

describe('writeAlwaysShowExtraInfo', () => {
  it('persists true so it can be read back', () => {
    const storage = new FakeStorage();
    writeAlwaysShowExtraInfo(storage, true);
    assert.strictEqual(readAlwaysShowExtraInfo(storage), true);
  });

  it('persists false so it can be read back', () => {
    const storage = new FakeStorage();
    writeAlwaysShowExtraInfo(storage, true);
    writeAlwaysShowExtraInfo(storage, false);
    assert.strictEqual(readAlwaysShowExtraInfo(storage), false);
  });

  it('does not throw when storage access fails', () => {
    const storage = new FakeStorage();
    storage.throwOnSet = true;
    assert.doesNotThrow(() => writeAlwaysShowExtraInfo(storage, true));
  });

  it('does not throw when no storage is available at all', () => {
    assert.doesNotThrow(() => writeAlwaysShowExtraInfo(null, true));
  });
});

describe('extra info open/closed state transitions', () => {
  it('a newly loaded card is always collapsed, before reveal is even possible', () => {
    assert.strictEqual(extraInfoOpenOnCardLoad(), false);
  });

  it('revealing a card with the preference OFF stays collapsed', () => {
    assert.strictEqual(extraInfoOpenOnReveal(false), false);
  });

  it('revealing a card with the preference ON opens automatically', () => {
    assert.strictEqual(extraInfoOpenOnReveal(true), true);
  });

  it('turning the preference ON while a card is revealed opens it immediately', () => {
    assert.strictEqual(
      extraInfoOpenOnPreferenceChange({ isRevealed: true, newPreference: true }),
      true,
    );
  });

  it('turning the preference OFF while a card is revealed collapses it immediately', () => {
    assert.strictEqual(
      extraInfoOpenOnPreferenceChange({ isRevealed: true, newPreference: false }),
      false,
    );
  });

  it('changing the preference before reveal never opens extra info', () => {
    assert.strictEqual(
      extraInfoOpenOnPreferenceChange({ isRevealed: false, newPreference: true }),
      false,
    );
  });
});
