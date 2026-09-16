import { expect, test } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

test.describe.configure({ mode: 'serial' });

const here = path.dirname(fileURLToPath(import.meta.url));
const replacementDictionary = path.resolve(here, '../../test-results/.e2e-state/replacement.sqlite');
const firstServerTimeout = 60_000;

function tinyWavFixture(): Buffer {
  // 10 ms, mono, 8 kHz, PCM16 silence: a valid browser-local upload fixture.
  const samples = 80;
  const buffer = Buffer.alloc(44 + samples * 2);
  buffer.write('RIFF', 0);
  buffer.writeUInt32LE(buffer.length - 8, 4);
  buffer.write('WAVEfmt ', 8);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(8000, 24);
  buffer.writeUInt32LE(16000, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write('data', 36);
  buffer.writeUInt32LE(samples * 2, 40);
  return buffer;
}

async function createDeckAndManualCard(page: import('@playwright/test').Page): Promise<void> {
  await page.getByLabel('New deck name').fill('Journey');
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText('Created and opened “Journey”');
  await page.getByRole('tab', { name: 'Add' }).click();
  await page.getByLabel('German word').fill('Haus');
  await page.getByRole('button', { name: 'Look up' }).click();
  await expect(page.getByText('Select vocabulary')).toBeVisible({ timeout: firstServerTimeout });
  await expect(page.getByRole('button', { name: /Haus · NOUN/ })).toBeVisible({ timeout: firstServerTimeout });
  await page.getByRole('button', { name: /Haus · NOUN/ }).click();
  await page.getByLabel('house, building').check();
  await page.getByRole('button', { name: 'Save vocabulary' }).click();
  await expect(page.getByRole('status')).toContainText('Saved “Haus” to “Journey”');
}

async function openStudy(page: import('@playwright/test').Page): Promise<void> {
  await page.locator('.primary-nav').getByRole('button', { name: 'Study due' }).click();
  await expect(page.getByRole('heading', { name: 'All due cards' })).toBeVisible({ timeout: firstServerTimeout });
}

test('FastAPI static product has explicit loading, error, and empty deck states', async ({ page }) => {
  // Deterministic loading/error synchronization. We hold the intercepted
  // /vocab/decks request in a pending state behind a Promise barrier so the
  // sequence is provable rather than dependent on timing:
  //   1. request begins (interception matched);
  //   2. request remains pending (barrier unreleased);
  //   3. visible role="status" contains "Loading decks…";
  //   4. barrier released → route aborts → product shows error state;
  //   5. interception removed, reload → product shows empty-deck state.
  let releaseInterceptedRequest: () => void = () => {};
  const interceptedRequestBarrier = new Promise<void>((resolve) => {
    releaseInterceptedRequest = resolve;
  });

  await page.route('**/vocab/decks', async (route) => {
    await interceptedRequestBarrier;
    await route.abort();
  });

  const documentResponse = await page.goto('/');
  expect(documentResponse?.headers()['content-type']).toContain('text/html');
  await expect(page.getByRole('status')).toContainText('Loading decks');
  releaseInterceptedRequest();
  await expect(page.getByText('We could not reach your deck list.')).toBeVisible();
  await page.unroute('**/vocab/decks');
  await page.reload();
  await expect(page.getByText('No decks yet. Create one to begin organizing German vocabulary.')).toBeVisible();
  const openApi = await page.evaluate(async () => (await fetch('/openapi.json')).json());
  expect(openApi.info.title).toBe('Wortlaut Vocabulary API');
});

test('manual creation, local audio, review, unavailable fallback, and both exports work through FastAPI', async ({ page }) => {
  await page.goto('/');
  await createDeckAndManualCard(page);

  await page.getByRole('tab', { name: 'Import & Export' }).click();
  await expect(page.getByText('Recommended — includes audio and richer card data.')).toBeVisible();
  await expect(page.getByText('Text-only backup/interchange — audio is not included.')).toBeVisible();
  const tsvDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: /Export “Journey” as TSV/ }).click();
  await expect(page.getByRole('status')).toContainText('Prepared a TSV export');
  expect((await tsvDownload).suggestedFilename()).toBe('Journey.tsv');
  const apkgDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: /Export “Journey” to Anki/ }).click();
  await expect(page.getByRole('status')).toContainText('Prepared an APKG export');
  expect((await apkgDownload).suggestedFilename()).toBe('Journey.apkg');

  await openStudy(page);
  await expect(page.getByRole('heading', { name: 'Haus' })).toBeVisible({ timeout: firstServerTimeout });
  await page.keyboard.press('Space');
  await expect(page.getByText('How well did you know it?')).toBeVisible();
  // Custom pronunciation management is secondary and lives behind Extra info.
  await expect(page.getByRole('button', { name: 'Add your pronunciation' })).toBeHidden();
  await page.getByRole('button', { name: 'Show extra info' }).click();
  await page.getByRole('button', { name: 'Add your pronunciation' }).click();
  await page.locator('input[type=file]').setInputFiles({
    name: 'tiny.wav',
    mimeType: 'audio/wav',
    buffer: tinyWavFixture(),
  });
  await expect(page.locator('audio.audio-preview')).toBeVisible();
  await page.getByRole('button', { name: 'Save recording' }).click();
  await expect(page.locator('.pronunciation-simple').getByRole('status')).toContainText('Custom pronunciation saved.');
  await page.getByRole('button', { name: 'Revert to automatic' }).click();
  await page.getByRole('button', { name: 'Confirm revert to automatic' }).click();
  await expect(page.locator('.pronunciation-simple').getByRole('status')).toContainText('Automatic pronunciation restored.');
  await page.getByRole('button', { name: 'Play pronunciation' }).click();
  await expect(page.getByRole('alert')).toContainText(/Audio for 'Haus' not found|Pronunciation is unavailable/);
  await page.keyboard.press("Space");
  await expect(page.getByText("How well did you know it?")).toBeVisible();
  await page.getByRole('button', { name: /5 Without doubt/ }).click();
  await expect(page.getByRole('heading', { name: 'Study complete' })).toBeVisible({ timeout: firstServerTimeout });
});

test('two-stage capture handles stale dictionary tokens with zero-write recovery and multi-select confirmation', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Journey' }).click();
  await page.getByRole('tab', { name: 'Add' }).click();
  const sentence = page.getByLabel('Sentence text');
  await sentence.fill('Der See ist tief.');
  await sentence.evaluate((element: HTMLTextAreaElement) => {
    element.focus();
    element.setSelectionRange(4, 7);
    element.dispatchEvent(new Event('select', { bubbles: true }));
  });
  await page.getByLabel('Lesson label').fill('Lesson 8');
  await page.getByRole('button', { name: 'Find candidates' }).click();
  await expect(page.getByText('Choose vocabulary')).toBeVisible({ timeout: firstServerTimeout });
  const choices = page.locator('.capture-candidate input[type=checkbox]');
  await expect(choices).toHaveCount(2, { timeout: firstServerTimeout });
  await choices.nth(0).check();
  await choices.nth(1).check();
  const cardsBefore = await page.evaluate(async () => (await (await fetch('/vocab/decks')).json())[0].card_count);
  const activation = await page.evaluate(async (dictionaryPath) => {
    const response = await fetch('/vocab/dictionary/activate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Flashcards-Request': '1' },
      body: JSON.stringify({ path: dictionaryPath, version: 'e2e-replacement' }),
    });
    return { status: response.status, body: await response.json() };
  }, replacementDictionary);
  expect(activation.status).toBe(200);
  await page.getByRole('button', { name: /Create 2 cards/ }).click();
  await expect(page.getByRole('alert')).toContainText('The dictionary changed while you were choosing cards. Your selections have not been saved.');
  const cardsAfterConflict = await page.evaluate(async () => (await (await fetch('/vocab/decks')).json())[0].card_count);
  expect(cardsAfterConflict).toBe(cardsBefore);
  await page.getByRole('button', { name: 'Find fresh candidates' }).click();
  await expect(choices).toHaveCount(2);
  await choices.nth(0).check();
  await choices.nth(1).check();
  await page.getByRole('button', { name: /Create 2 cards/ }).click();
  await expect(page.getByRole('status')).toContainText('Server confirmed 2 cards created');
});

test('review controls and navigation respond at every required viewport', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Journey' }).click();
  await page.getByRole('button', { name: 'Study this deck' }).first().click();
  await expect(page.getByRole('heading', { name: /See/ })).toBeVisible({ timeout: firstServerTimeout });
  await page.keyboard.press('Space');
  const confidence = page.locator('.confidence-grid > button');
  await expect(confidence).toHaveCount(5);
  for (const viewport of [
    { width: 360, height: 800 },
    { width: 768, height: 1024 },
    { width: 1366, height: 768 },
    { width: 1920, height: 1080 },
  ]) {
    await page.setViewportSize(viewport);
    if (viewport.width < 800) {
      await expect(page.locator('.bottom-nav')).toBeVisible();
      const gridColumns = await page.locator('.confidence-grid').evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length);
      expect(gridColumns).toBe(1);
      const order = await confidence.allTextContents();
      expect(order.map((text) => text.trim().charAt(0))).toEqual(['1', '2', '3', '4', '5']);
      const first = await confidence.first().boundingBox();
      const gridWidth = (await page.locator(".confidence-grid").boundingBox())?.width ?? 0;
      expect(first?.width ?? 0).toBeGreaterThan(gridWidth * 0.8);
    } else {
      await expect(page.locator('.bottom-nav')).toBeHidden();
      const shell = await page.locator('.shell').boundingBox();
      expect(shell?.width ?? 0).toBeLessThan(page.viewportSize()?.width ?? 0);
    }
  }
});

async function createDeckOnly(page: import('@playwright/test').Page, deckName: string): Promise<void> {
  await page.getByLabel('New deck name').fill(deckName);
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText(`Created and opened “${deckName}”`);
}

async function createDeckWithWord(
  page: import('@playwright/test').Page,
  deckName: string,
  word: string,
  candidateLabel: string | RegExp,
  senseLabel: string,
): Promise<void> {
  await createDeckOnly(page, deckName);
  await page.getByRole('tab', { name: 'Add' }).click();
  await page.getByLabel('German word').fill(word);
  await page.getByRole('button', { name: 'Look up' }).click();
  await expect(page.getByText('Select vocabulary')).toBeVisible({ timeout: firstServerTimeout });
  await expect(page.getByRole('button', { name: candidateLabel })).toBeVisible({ timeout: firstServerTimeout });
  await page.getByRole('button', { name: candidateLabel }).click();
  await page.getByLabel(senseLabel).check();
  await page.getByRole('button', { name: 'Save vocabulary' }).click();
  await expect(page.getByRole('status')).toContainText(`Saved “${word}” to “${deckName}”`);
}

test('an active deck study exposes Back to deck and leaving never submits a rating', async ({ page }) => {
  await page.goto('/');
  // anrufen is still due here (Haus was reviewed to mastery in the export
  // test and See cards belong to Journey, so a shared Haus note would show
  // due_count 0 via the duplicate-safe note identity).
  await createDeckWithWord(page, 'StudyBackDeck', 'anrufen', /anrufen · VERB/, 'to call, phone');

  const deckSnapshot = async () => page.evaluate(async () => {
    const decks = await (await fetch('/vocab/decks')).json() as Array<{
      name: string;
      due_count: number;
      mastery_percent: number;
    }>;
    return decks.find((deck) => deck.name === 'StudyBackDeck');
  });
  const before = await deckSnapshot();
  expect(before?.due_count).toBe(1);

  await page.getByRole('button', { name: 'Study this deck' }).first().click();
  await expect(page.getByRole('heading', { name: 'anrufen' })).toBeVisible({ timeout: firstServerTimeout });

  // Back is visible before completion, in the question state.
  const back = page.getByRole('button', { name: 'Back to StudyBackDeck' });
  await expect(back).toBeVisible();
  await page.setViewportSize({ width: 390, height: 800 });
  await expect(back).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
  await page.setViewportSize({ width: 1280, height: 720 });
  await back.click();
  await expect(page.getByRole('heading', { name: 'StudyBackDeck', level: 2 })).toBeVisible();

  // Re-enter and leave from the revealed-answer state.
  await page.getByRole('button', { name: 'Study this deck' }).first().click();
  await expect(page.getByRole('heading', { name: 'anrufen' })).toBeVisible({ timeout: firstServerTimeout });
  await page.keyboard.press('Space');
  await expect(page.getByText('How well did you know it?')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Back to StudyBackDeck' })).toBeVisible();
  await page.getByRole('button', { name: 'Back to StudyBackDeck' }).click();
  await expect(page.getByRole('heading', { name: 'StudyBackDeck', level: 2 })).toBeVisible();

  // No rating reached the server: still due, still the same mastery.
  const after = await deckSnapshot();
  expect(after?.due_count).toBe(before?.due_count);
  expect(after?.mastery_percent).toBe(before?.mastery_percent);

  // Global "Study due" offers its own Back to decks action.
  await page.locator('.primary-nav').getByRole('button', { name: 'Study due' }).click();
  await expect(page.getByRole('heading', { name: 'All due cards' })).toBeVisible({ timeout: firstServerTimeout });
  await expect(page.getByRole('button', { name: 'Back to decks' })).toBeVisible();
  await page.getByRole('button', { name: 'Back to decks' }).click();
  await expect(page.getByRole('heading', { name: 'Your decks' })).toBeVisible();
});

test('capture meaning-language controls are checkboxes that keep at least one selected', async ({ page }) => {
  await page.goto('/');
  await createDeckOnly(page, 'CaptureLangDeck');
  await page.getByRole('tab', { name: 'Add' }).click();
  const captureSection = page.locator('.capture-workflow');
  const sentence = captureSection.getByLabel('Sentence text');
  await sentence.fill('Der See ist tief.');
  await sentence.evaluate((element: HTMLTextAreaElement) => {
    element.focus();
    element.setSelectionRange(4, 7);
    element.dispatchEvent(new Event('select', { bubbles: true }));
  });
  await captureSection.getByLabel('Lesson label').fill('Lesson 1');
  await captureSection.getByRole('button', { name: 'Find candidates' }).click();
  await expect(captureSection.getByText('Choose vocabulary')).toBeVisible({ timeout: firstServerTimeout });

  const german = captureSection.getByRole('checkbox', { name: 'German (DE)' });
  const english = captureSection.getByRole('checkbox', { name: 'English (EN)' });
  await expect(german).toBeChecked();
  await expect(english).toBeChecked();

  // The old chip buttons are gone.
  await expect(captureSection.getByRole('button', { name: 'German · DE' })).toHaveCount(0);
  await expect(captureSection.getByRole('button', { name: 'English · EN' })).toHaveCount(0);

  await german.uncheck();
  await expect(german).not.toBeChecked();
  await expect(english).toBeChecked();

  // Both can be selected simultaneously.
  await german.check();
  await expect(german).toBeChecked();
  await expect(english).toBeChecked();

  // The last remaining language cannot be deselected.
  await german.uncheck();
  await expect(german).not.toBeChecked();
  await expect(english).toBeChecked();
  await english.click();
  await expect(english).toBeChecked();
  await expect(german).not.toBeChecked();
});

test('CSV import uses an existing-deck select, shows an indeterminate busy state, and reports server totals', async ({ page }) => {
  await page.goto('/');
  await createDeckOnly(page, 'CsvDestDeck');
  await page.getByRole('tab', { name: 'Import & Export' }).click();
  const importSection = page.locator('section[aria-labelledby="import-export-title"]');

  // Destination is a select over existing decks, never free text.
  const destination = importSection.getByLabel('Destination deck');
  expect(await destination.evaluate((element) => element.tagName)).toBe('SELECT');
  await expect(importSection.getByLabel('CSV import deck name')).toHaveCount(0);
  const optionNames = await destination.locator('option').allTextContents();
  expect(optionNames).toContain('CsvDestDeck');
  const selectedName = await destination.evaluate((element) => {
    const select = element as HTMLSelectElement;
    return select.options[select.selectedIndex]?.textContent ?? '';
  });
  expect(selectedName).toBe('CsvDestDeck');

  // Hold the request open to observe the indeterminate busy state.
  let releaseImport: () => void = () => {};
  const importBarrier = new Promise<void>((resolve) => { releaseImport = resolve; });
  await page.route('**/vocab/import/csv', async (route) => {
    await importBarrier;
    await route.continue();
  });

  await importSection.getByLabel('Vocabulary lines').fill('Haus\nanrufen');
  await importSection.getByRole('button', { name: 'Import CSV' }).click();
  await expect(importSection.locator('.import-busy')).toBeVisible();
  await expect(importSection.locator('progress.import-progress')).toBeVisible();
  await expect(importSection.getByRole('button', { name: 'Importing vocabulary…' })).toBeDisabled();

  releaseImport();
  await expect(page.locator('.notice.success')).toContainText('Import complete');
  await expect(page.locator('.notice.success')).toContainText(/added, \d+ already existed, \d+ total/);
  await page.unroute('**/vocab/import/csv');

  // Narrow width: the destination select fits and nothing overflows.
  await page.setViewportSize({ width: 390, height: 800 });
  const destinationBox = await destination.boundingBox();
  expect(destinationBox?.width ?? 0).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
});

test('unavailable TSV and APKG import placeholders are removed', async ({ page }) => {
  await page.goto('/');
  await createDeckOnly(page, 'ImportPlaceholderDeck');
  await page.getByRole('tab', { name: 'Import & Export' }).click();
  await expect(page.getByText('TSV import pending')).toHaveCount(0);
  await expect(page.getByText('APKG import pending')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'TSV import' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'APKG import' })).toHaveCount(0);
  const importSection = page.locator('section[aria-labelledby="import-export-title"]');
  await expect(importSection.getByRole('button', { name: /as TSV/ })).toBeVisible();
  await expect(importSection.getByRole('button', { name: /to Anki/ })).toBeVisible();
});
