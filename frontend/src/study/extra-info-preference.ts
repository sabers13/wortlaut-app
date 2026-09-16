/**
 * "Always show extra info" is a presentation preference, not study-domain
 * data — it is deliberately kept out of the user database and persisted
 * client-side only, under one stable, product-namespaced localStorage key.
 *
 * The read/write/derive helpers here are pure and DOM-framework-free so the
 * safe-fallback and state-transition rules (ADR-less UX spec, see the
 * Wortlaut study-card mission) can be unit tested directly, independent of
 * Lit rendering.
 */

export const ALWAYS_SHOW_EXTRA_INFO_STORAGE_KEY = 'wortlaut.study.alwaysShowExtraInfo';

/** The subset of the Storage interface these helpers depend on. */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/**
 * Read the persisted "Always show extra info" preference.
 *
 * Only the literal stored value ``"true"`` is accepted as true. A missing
 * key, any other stored value, or a storage accessor that throws (private
 * browsing, disabled storage, a hostile shim) all safely fall back to
 * ``false`` — a storage failure must never break studying.
 */
export function readAlwaysShowExtraInfo(storage: StorageLike | null | undefined): boolean {
  if (!storage) return false;
  try {
    return storage.getItem(ALWAYS_SHOW_EXTRA_INFO_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

/**
 * Persist the "Always show extra info" preference. Failures are swallowed:
 * losing the persisted preference must never break the current session.
 */
export function writeAlwaysShowExtraInfo(storage: StorageLike | null | undefined, value: boolean): void {
  if (!storage) return;
  try {
    storage.setItem(ALWAYS_SHOW_EXTRA_INFO_STORAGE_KEY, value ? 'true' : 'false');
  } catch {
    // Storage may be unavailable or full; the in-memory preference still
    // governs the current session.
  }
}

/**
 * Whether Extra info should be open immediately after a card is loaded
 * (before it is revealed). Extra info must never be visible on an
 * unrevealed card, regardless of the persisted preference.
 */
export function extraInfoOpenOnCardLoad(): boolean {
  return false;
}

/**
 * Whether Extra info should be open the moment a card is revealed.
 */
export function extraInfoOpenOnReveal(alwaysShowExtraInfo: boolean): boolean {
  return alwaysShowExtraInfo;
}

/**
 * Whether Extra info should be open immediately after the "Always show
 * extra info" preference itself changes. Before reveal, extra info stays
 * closed no matter what the new preference is; once revealed, the change
 * takes effect immediately on the current card.
 */
export function extraInfoOpenOnPreferenceChange(options: {
  isRevealed: boolean;
  newPreference: boolean;
}): boolean {
  return options.isRevealed ? options.newPreference : false;
}
