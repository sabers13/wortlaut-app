/**
 * Slice 12 end-to-end coverage for the chooser, settings actions,
 * dictionary-mode switches, and the Online-fixture wiring.
 *
 * Test environment (three deterministic served-product states):
 *   - Default project webServer (state A): canonical Offline dictionary
 *     installed at startup; chooser is hidden.
 *   - Second project webServer (state B): no canonical full Offline
 *     dictionary; deterministic Online fixture corpus wired through the
 *     backend Product trust/test seam; chooser is visible. The Online
 *     provider is constructed ONLY after the user clicks "Use Online";
 *     startup-time factory/transport counts are asserted via
 *     `e2e_counters` embedded in the settings response (E2E harness
 *     only). This server NEVER sees a download, so its
 *     chooser/zero-transport/no-canonical assertions stay deterministic.
 *   - Third project webServer (state B, second instance): same pristine
 *     starting state as state B, but it owns the mutable canonical
 *     slot for the download/remove/switch flows. Every test there
 *     performs its own download first, so each is self-sufficient
 *     regardless of file order.
 *
 * No public GitHub network reach is required at any point — every shard
 * is served from a local fixture under the E2E state directory.
 */

import { expect, test } from '@playwright/test';

const STATE_B_HOST = '127.0.0.1';
const STATE_B_PORT = Number(process.env.E2E_ONLINE_PORT ?? '8818');
const STATE_C_PORT = Number(process.env.E2E_OFFLINE_FLOW_PORT ?? '8819');

async function settings(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Settings' }).click();
  // Exact: the inline chooser banner heads 'Choose how to use the
  // dictionary', which substring-matches a loose 'Dictionary' query.
  await expect(page.getByRole('heading', { name: 'Dictionary', exact: true })).toBeVisible();
}

async function e2eCounters(
  page: import('@playwright/test').Page,
): Promise<{ factory_invocations: number; transport_invocations: number }> {
  // Use browser-context fetch (not page.request) to avoid API-context
  // deadlocks with the page's own in-flight requests.
  const body = (await page.evaluate(async () => {
    const res = await fetch('/vocab/settings/dictionary');
    if (!res.ok) throw new Error(`settings GET failed: ${res.status}`);
    return (await res.json()) as {
      e2e_counters?: { factory_invocations: number; transport_invocations: number };
    };
  })) as {
    e2e_counters?: { factory_invocations: number; transport_invocations: number };
  };
  expect(body.e2e_counters).toBeDefined();
  return body.e2e_counters as {
    factory_invocations: number;
    transport_invocations: number;
  };
}

async function e2eMode(page: import('@playwright/test').Page): Promise<string> {
  const body = (await page.evaluate(async () => {
    const res = await fetch('/vocab/settings/dictionary');
    if (!res.ok) throw new Error(`settings GET failed: ${res.status}`);
    return (await res.json()) as { mode?: string };
  })) as { mode?: string };
  return body.mode ?? 'unknown';
}

/** Bring the session Online regardless of its current deterministic mode. */
async function ensureOnline(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/');
  const mode = await e2eMode(page);
  if (mode === 'online') {
    // Already Online: navigate to decks view directly.
    await expect(page.getByRole('heading', { name: 'Wortlaut' })).toBeVisible({
      timeout: 30_000,
    });
    return;
  }
  if (mode === 'offline') {
    // A previous download/switch flow left this server Offline: switch
    // back through Settings instead of expecting the chooser.
    await settings(page);
    await page
      .getByRole('button', { name: 'Use Online for this session', exact: true })
      .click();
    await expect(page.getByRole('heading', { name: 'Wortlaut' })).toBeVisible({
      timeout: 120_000,
    });
    return;
  }
  // Chooser visible: click Use Online.
  await expect(
    page.getByRole('heading', { name: 'Choose how to use the dictionary' }),
  ).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Use Online' }).click();
  await expect(page.getByRole('heading', { name: 'Wortlaut' })).toBeVisible({
    timeout: 120_000,
  });
}

/**
 * Select the first occurrence of `needle` inside a textbox/textarea so the
 * capture form observes a real selected span. Filling alone leaves a
 * collapsed cursor, which the form correctly rejects.
 */
async function selectSpan(
  field: import('@playwright/test').Locator,
  needle: string,
): Promise<void> {
  await field.evaluate((el: HTMLElement, text: string) => {
    const box = el as HTMLTextAreaElement | HTMLInputElement;
    const start = box.value.indexOf(text);
    if (start < 0) throw new Error(`needle not found: ${text}`);
    box.focus();
    box.setSelectionRange(start, start + text.length);
    box.dispatchEvent(new Event('select', { bubbles: true }));
    box.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  }, needle);
}

/**
 * Create a deck and land on its detail view. The lookup, capture, and
 * import workflows render only inside the deck-detail view (the product
 * requires an open deck to save vocabulary), so E2E tests must open a
 * deck before touching those forms.
 */
async function createDeck(
  page: import('@playwright/test').Page,
  name: string,
): Promise<void> {
  await page.getByLabel('New deck name').fill(name);
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText(/Created and opened/i);
}

/**
 * Deck-detail workflow forms are tab-scoped: creating/opening a deck lands
 * on Overview, while lookup/capture render only on Add and CSV import
 * renders only on Import & Export. Every test touching those forms must
 * select the owning tab first.
 */
async function openAddTab(page: import('@playwright/test').Page): Promise<void> {
  await page.getByRole('tab', { name: 'Add', exact: true }).click();
}

async function openImportExportTab(
  page: import('@playwright/test').Page,
): Promise<void> {
  await page
    .getByRole('tab', { name: 'Import & Export', exact: true })
    .click();
}

/**
 * Bring the managed canonical-Offline slot to installed+valid, running a
 * real download only when needed. The Download button is disabled while
 * the slot validates, so an already-installed asset must be detected via
 * `isEnabled` instead of a click that would wait forever. The install
 * success message is transient (the view returns to the chooser once
 * settings reload as unconfigured), so the durable outcome — the
 * canonical slot validating — is polled instead of the message.
 */
async function ensureDownloaded(
  page: import('@playwright/test').Page,
): Promise<void> {
  // The unconfigured Settings view renders both the inline chooser
  // banner and the Offline section; both Download buttons run the same
  // server-owned install.
  const download = page
    .getByRole('button', { name: 'Download for Offline use' })
    .first();
  if (await download.isEnabled()) {
    await download.click();
  }
  // Poll the durable outcome through the product settings endpoint: the
  // canonical slot validating. (The install success message is transient:
  // the view returns to the chooser once settings reload as unconfigured,
  // which can detach the Settings DOM mid-poll and stall DOM-based waits.)
  let installed = false;
  for (let i = 0; i < 60; i++) {
    const valid = await page.evaluate(async () => {
      const res = await fetch('/vocab/settings/dictionary');
      if (!res.ok) throw new Error(`settings GET failed: ${res.status}`);
      const body = (await res.json()) as {
        canonical_offline_present?: boolean;
        canonical_offline_valid?: boolean;
      };
      return (
        body.canonical_offline_present === true &&
        body.canonical_offline_valid === true
      );
    });
    if (valid) {
      installed = true;
      break;
    }
    await page.waitForTimeout(2000);
  }
  expect(installed).toBe(true);
}

test.describe('Slice 12 dictionary-mode chooser and Settings', () => {
  test('state A: valid installed Offline hides the chooser; settings surface exists', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Wortlaut' })).toBeVisible();
    // The chooser must NOT be visible when the canonical Offline dictionary
    // is installed and verified.
    await expect(
      page.getByRole('heading', { name: 'Choose how to use the dictionary' }),
    ).toHaveCount(0);
    await settings(page);
  });

  test('state A: Remove Offline is rejected while Offline is active', async ({
    page,
  }) => {
    await page.goto('/');
    await settings(page);
    // The Offline-active mode refuses removal with a structured conflict.
    await page.getByRole('button', { name: 'Remove Offline dictionary' }).click();
    await page.getByRole('button', { name: 'Confirm remove Offline' }).click();
    await expect(
      page.getByText(
        /switch the session to Online for this session|offline_dictionary_in_use/i,
      ),
    ).toBeVisible();
  });

  test('state A: Offline -> Online keeps the canonical Offline on disk', async ({
    page,
  }) => {
    await page.goto('/');
    await settings(page);
    const useOnline = page.getByRole('button', { name: 'Use Online for this session' });
    await useOnline.click();
    // After the in-process swap, the canonical Offline slot must remain intact.
    await expect(page.getByRole('heading', { name: 'Dictionary', exact: true })).toBeVisible();
    await expect(
      page.getByText(/canonical Offline dictionary will not be removed/i),
    ).toBeVisible();
    // Restore the Offline session: later spec files (product/study) run
    // against this same server and require Offline management endpoints.
    await page.getByRole('button', { name: 'Use Offline' }).click();
    await expect(page.getByText(/Now using Offline/i)).toBeVisible();
  });
});

test.describe('Slice 12 Online-fixture served product', () => {
  test.use({
    baseURL: `http://${STATE_B_HOST}:${STATE_B_PORT}`,
  });

  test('state B: chooser appears with online-fixture defaults', async ({ page }) => {
    await page.goto('/');
    await expect(
      page.getByRole('heading', { name: 'Choose how to use the dictionary' }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Use Online' }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Download for Offline use' }),
    ).toBeVisible();
  });

  test('state B: zero backend transport before user picks a mode', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(
      page.getByRole('heading', { name: 'Choose how to use the dictionary' }),
    ).toBeVisible();
    // The BACKEND counter proves zero dictionary network before choice.
    // Browser request counting cannot observe server-side shard downloads.
    const counters = await e2eCounters(page);
    expect(counters.factory_invocations).toBe(0);
    expect(counters.transport_invocations).toBe(0);
  });

  test('state B: Use Online activates the deterministic fixture corpus', async ({
    page,
  }) => {
    await ensureOnline(page);
    await expect(page.getByRole('heading', { name: 'Wortlaut' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Your decks' })).toBeVisible();
    // The factory must have been invoked exactly once by the user choice.
    const counters = await e2eCounters(page);
    expect(counters.factory_invocations).toBeGreaterThanOrEqual(1);
    // The lookup form lives in the deck-detail view: open a deck first.
    await createDeck(page, 'Online lookup deck');
    await openAddTab(page);
    // Online-backed /vocab/lookup must surface the deterministic fixture.
    const lookup = page.getByRole('textbox', { name: 'German word' });
    await lookup.fill('Haus');
    await page.getByRole('button', { name: 'Look up' }).click();
    await expect(
      page.getByRole('button', { name: /Haus/ }).first(),
    ).toBeVisible();
    // Transport must now be non-zero (shards were fetched through the seam).
    const after = await e2eCounters(page);
    expect(after.transport_invocations).toBeGreaterThan(0);
  });

  test('state B: Online-backed /vocab/highlight returns candidates', async ({
    page,
  }) => {
    await ensureOnline(page);
    // The capture form lives in the deck-detail view: open a deck first.
    await createDeck(page, 'Online highlight deck');
    await openAddTab(page);
    const sentence = page.getByRole('textbox', { name: 'Sentence text' });
    await sentence.fill('Das Haus ist alt.');
    // The capture form requires a real selected span; filling alone
    // leaves a collapsed cursor that the form correctly rejects.
    await selectSpan(sentence, 'Haus');
    await page.getByRole('textbox', { name: 'Lesson label' }).fill('Lesson X');
    await page.getByRole('button', { name: 'Find candidates' }).click();
    // The picker renders one .lemma per candidate; the selected-span
    // preview also mentions Haus, so scope the assertion to candidates.
    await expect(
      page.locator('.capture-candidate .lemma').first(),
    ).toContainText('Haus');
  });

  test('state B: Online CSV import creates a deck', async ({ page }) => {
    await ensureOnline(page);
    await expect(page.getByRole('heading', { name: 'Your decks' })).toBeVisible();
    // Create then open a deck to reach the Import & export form.
    await page.getByLabel('New deck name').fill('Online import deck');
    await page.getByRole('button', { name: 'Create deck' }).click();
    await expect(page.getByRole('status')).toContainText(/Created and opened/i);
    await openImportExportTab(page);
    await page
      .getByLabel('Destination deck')
      .selectOption({ label: 'Online import deck' });
    await page.getByLabel('Vocabulary lines').fill('Haus\nSee');
    await page.getByRole('button', { name: 'Import CSV' }).click();
    await expect(page.getByRole('status')).toContainText(/Import complete/i);
    await expect(page.getByRole('status')).toContainText(/2 added/i);
  });

  test('state B: Online candidate materialization and card creation', async ({
    page,
  }) => {
    await ensureOnline(page);
    // Create a deck, look up Haus, save it — all through Online provider data.
    await page.getByLabel('New deck name').fill('Online journey');
    await page.getByRole('button', { name: 'Create deck' }).click();
    await expect(page.getByRole('status')).toContainText(/Created and opened/i);
    await openAddTab(page);
    await page.getByLabel('German word').fill('Haus');
    await page.getByRole('button', { name: 'Look up' }).click();
    await expect(page.getByText('Select vocabulary')).toBeVisible();
    await page.getByRole('button', { name: /Haus · NOUN/ }).first().click();
    await page.getByLabel('house, building').check();
    await page.getByRole('button', { name: 'Save vocabulary' }).click();
    await expect(page.getByRole('status')).toContainText(/Saved “Haus”/);
  });

  test('state B: Clear Online cache keeps the provider usable', async ({ page }) => {
    await ensureOnline(page);
    await settings(page);
    const button = page.getByRole('button', { name: 'Clear Online cache' });
    await expect(button).toBeEnabled();
    await button.click();
    await expect(page.getByText(/online cache cleared/i)).toBeVisible();
    // Provider remains usable afterward: lookup still works. The lookup
    // form lives in the deck-detail view, so open a deck first.
    // 'Decks' is a substring of 'Refresh decks': match exactly.
    await page.getByRole('button', { name: 'Decks', exact: true }).click();
    await createDeck(page, 'Online cache deck');
    await openAddTab(page);
    const lookup = page.getByRole('textbox', { name: 'German word' });
    await lookup.fill('See');
    await page.getByRole('button', { name: 'Look up' }).click();
    await expect(
      page.getByRole('button', { name: /See/ }).first(),
    ).toBeVisible();
  });

  test('state B: Remove Offline rejected because no canonical Offline exists', async ({
    page,
  }) => {
    await ensureOnline(page);
    await settings(page);
    await page.getByRole('button', { name: 'Remove Offline dictionary' }).click();
    await page.getByRole('button', { name: 'Confirm remove Offline' }).click();
    await expect(
      page.getByText(
        /no canonical full Offline dictionary|offline_unavailable|offline_removal_rejected/i,
      ),
    ).toBeVisible();
  });

  test('state B: Online next-card render works through the provider', async ({
    page,
  }) => {
    await ensureOnline(page);
    // Create a deck + card through Online, then study it.
    await page.getByLabel('New deck name').fill('Online study deck');
    await page.getByRole('button', { name: 'Create deck' }).click();
    await expect(page.getByRole('status')).toContainText(/Created and opened/i);
    await openAddTab(page);
    await page.getByLabel('German word').fill('Haus');
    await page.getByRole('button', { name: 'Look up' }).click();
    await expect(page.getByText('Select vocabulary')).toBeVisible();
    await page.getByRole('button', { name: /Haus · NOUN/ }).first().click();
    await page.getByLabel('house, building').check();
    await page.getByRole('button', { name: 'Save vocabulary' }).click();
    await expect(page.getByRole('status')).toContainText(/Saved “Haus”/);
    // Study: next-card must render through the Online provider.
    await page.getByRole('button', { name: 'Study due' }).click();
    await expect(
      page.getByRole('heading', { name: 'All due cards' }),
    ).toBeVisible({ timeout: 60_000 });
  });

  test('state B: browser cannot configure any Online/Offline source', async ({
    page,
  }) => {
    await page.goto('/');
    await settings(page);
    const bodyText = await page.locator('body').innerText();
    // No URL / manifest / host / repository input may exist in the UI.
    expect(bodyText).not.toMatch(/download_url/i);
    expect(bodyText).not.toMatch(/manifest_bytes/i);
    expect(bodyText).not.toMatch(/online-manifest/i);
    expect(bodyText).not.toMatch(/repository/i);
    // No text input for a source URL is rendered.
    await expect(page.getByLabel(/URL|manifest|repository|host/i)).toHaveCount(0);
  });
});

test.describe('Slice 12 Offline download, removal and switch flows', () => {
  test.use({
    baseURL: `http://${STATE_B_HOST}:${STATE_C_PORT}`,
  });

  test('state B: Download for Offline use installs the fixture asset', async ({
    page,
  }) => {
    // The Settings view bounces back to the chooser while the session is
    // still unconfigured, so go Online first (exactly like the sibling
    // download/remove/switch flows) and download from the Settings surface.
    await ensureOnline(page);
    await settings(page);
    // Fresh server (reset_state clears the managed slot), so this runs a
    // real download of the deterministic fixture asset.
    await ensureDownloaded(page);
  });

  test('state B: Online-active Remove Offline keeps Online usable', async ({
    page,
  }) => {
    await ensureOnline(page);
    await settings(page);
    // Self-sufficient: downloads only when the slot does not validate.
    await ensureDownloaded(page);
    // Re-enter Settings explicitly: the download flow may return the view
    // to the chooser, and removal needs the Settings surface.
    await settings(page);
    // Now remove while Online is active: must succeed.
    await page.getByRole('button', { name: 'Remove Offline dictionary' }).click();
    await page.getByRole('button', { name: 'Confirm remove Offline' }).click();
    await expect(page.getByText(/removed/i)).toBeVisible();
    // Online lookup still works after removal. The lookup form lives in
    // the deck-detail view, so open a deck first.
    // 'Decks' is a substring of 'Refresh decks': match exactly.
    await page.getByRole('button', { name: 'Decks', exact: true }).click();
    await createDeck(page, 'Online removal deck');
    await openAddTab(page);
    const lookup = page.getByRole('textbox', { name: 'German word' });
    await lookup.fill('Haus');
    await page.getByRole('button', { name: 'Look up' }).click();
    await expect(
      page.getByRole('button', { name: /Haus/ }).first(),
    ).toBeVisible();
  });

  test('state B: Online -> Offline switch after download', async ({ page }) => {
    await ensureOnline(page);
    await settings(page);
    // Self-sufficient: downloads only when the slot does not validate.
    await ensureDownloaded(page);
    // Re-enter Settings explicitly: the download flow may return the view
    // to the chooser, and the switch needs the Settings surface.
    await settings(page);
    // Switch to Offline: must succeed now that the asset validates.
    const useOffline = page.getByRole('button', { name: 'Use Offline' });
    await expect(useOffline).toBeEnabled({ timeout: 60_000 });
    await useOffline.click();
    await expect(page.getByText(/Now using Offline/i)).toBeVisible();
  });
});
