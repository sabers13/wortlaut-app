import { expect, test } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

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

async function createDeckWithWord(
  page: import('@playwright/test').Page,
  deckName: string,
  word: string,
  candidateLabel: string | RegExp,
  senseLabel: string,
): Promise<void> {
  await page.getByLabel('New deck name').fill(deckName);
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText(`Created and opened “${deckName}”`);
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

async function addWordToOpenDeck(
  page: import('@playwright/test').Page,
  word: string,
  candidateLabel: string | RegExp,
  senseLabel: string,
): Promise<void> {
  await page.getByLabel('German word').fill(word);
  await page.getByRole('button', { name: 'Look up' }).click();
  await expect(page.getByRole('button', { name: candidateLabel })).toBeVisible({ timeout: firstServerTimeout });
  await page.getByRole('button', { name: candidateLabel }).click();
  await page.getByLabel(senseLabel).check();
  await page.getByRole('button', { name: 'Save vocabulary' }).click();
  await expect(page.getByRole('status')).toContainText(`Saved “${word}” to`);
}

async function openCardsTab(page: import('@playwright/test').Page): Promise<void> {
  await page.getByRole('tab', { name: 'Cards' }).click();
  await expect(page.getByRole('heading', { name: 'Cards' })).toBeVisible();
}

test('Cards tab loads the deck cards with metadata and shows the empty state for an empty deck', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('New deck name').fill('MgmtEmptyDeck');
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText('Created and opened “MgmtEmptyDeck”');

  await openCardsTab(page);
  await expect(page.getByText('This deck has no cards yet.')).toBeVisible();

  // Now add a card and verify the list re-renders with the headword + meta.
  await page.getByRole('tab', { name: 'Add' }).click();
  await addWordToOpenDeck(page, 'Haus', /Haus · NOUN/, 'house, building');
  await openCardsTab(page);
  const row = page.locator('.deck-card-row', { hasText: 'Haus' });
  await expect(row).toBeVisible({ timeout: firstServerTimeout });
  // The raw technical metadata panel is intentionally not rendered.
  await expect(page.locator('.deck-card-row', { hasText: 'lemma_semantic_ref' })).toHaveCount(0);
  await expect(row.getByText(/NOUN/)).toBeVisible();
  await expect(row.getByText(/languages:/)).toBeVisible();
});

test('Edit card preserves prefill, refuses to remove the last language, and saves via the existing API', async ({ page }) => {
  await page.goto('/');
  await createDeckWithWord(page, 'MgmtEditDeck', 'Haus', /Haus · NOUN/, 'house, building');

  // Seed an English user meaning via the existing study flow so the edit
  // dialog demonstrates prefill of a real persisted value.
  await page.locator('.primary-nav').getByRole('button', { name: 'Study due' }).click();
  await expect(page.getByRole('heading', { name: 'Haus' })).toBeVisible({ timeout: firstServerTimeout });
  await page.keyboard.press('Space');
  await page.getByRole('button', { name: 'Show extra info' }).click();
  await page.getByLabel('Your English meaning').fill('building');
  const englishRow = page.locator('.gloss-row', { hasText: 'Your English meaning' });
  await englishRow.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('.inline-status', { hasText: 'English meaning saved.' })).toBeVisible();
  // Leave the study session.
  // "Study due" opens global study, so Back returns to the deck list;
  // reopen the deck before using the Cards tab.
  await page.getByRole('button', { name: /Back to/ }).click();
  await page.getByRole('button', { name: 'Open MgmtEditDeck' }).click();

  await openCardsTab(page);
  await page.locator('.deck-card-row', { hasText: 'Haus' }).getByRole('button', { name: 'Edit', exact: true }).click();
  const dialog = page.locator('[data-edit-dialog]');
  await expect(dialog).toBeVisible();

  // Prefill: German and English both checked.
  const german = dialog.getByRole('checkbox', { name: 'German (DE)' });
  const english = dialog.getByRole('checkbox', { name: 'English (EN)' });
  await expect(german).toBeChecked();
  await expect(english).toBeChecked();

  // Prefilled user meaning text.
  const englishInput = dialog.locator('label', { hasText: 'Your English meaning' }).locator('input');
  await expect(englishInput).toHaveValue('building');

  // No sense-edit control is exposed in the management surface.
  await expect(dialog.getByRole('radio')).toHaveCount(0);
  await expect(dialog.getByText(/Choose another dictionary sense|Dictionary meaning/i)).toHaveCount(0);

  // The last remaining language cannot be deselected.
  await german.uncheck();
  await expect(german).not.toBeChecked();
  await expect(english).toBeChecked();
  await english.click();
  await expect(english).toBeChecked();
  await expect(german).not.toBeChecked();
  // Restore both for the rest of the test.
  await german.check();
  await expect(german).toBeChecked();
  await expect(english).toBeChecked();

  // Uncheck English so the save will issue setMeaningLanguages([de]).
  await english.uncheck();

  // Capture the PUT request.
  const putRequest = page.waitForRequest('**/vocab/notes/**/meaning-languages');
  await dialog.getByRole('button', { name: 'Save changes' }).click();
  const req = await putRequest;
  expect(req.method()).toBe('PUT');
  expect(req.url()).toMatch(/\/vocab\/notes\/\d+\/meaning-languages$/);
  expect(req.headers()['x-flashcards-request']).toBe('1');
  expect(req.postDataJSON()).toEqual({ languages: ['de'] });
  await expect(page.getByRole('status')).toContainText('Changes saved.');

  // The edit dialog stays open to show confirmation; close it before
  // re-opening to verify the persisted server state.
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-edit-dialog]')).toHaveCount(0);

  // Re-open and verify the English checkbox now reflects the server state.
  await page.locator('.deck-card-row', { hasText: 'Haus' }).getByRole('button', { name: 'Edit', exact: true }).click();
  const reopened = page.locator('[data-edit-dialog]');
  await expect(reopened.getByRole('checkbox', { name: 'English (EN)' })).not.toBeChecked();
  await expect(reopened.getByRole('checkbox', { name: 'German (DE)' })).toBeChecked();
  // Existing user English meaning still prefilled.
  await expect(reopened.locator('label', { hasText: 'Your English meaning' }).locator('input')).toHaveValue('building');

  // Clearing the existing English meaning calls DELETE /vocab/notes/{note_id}/gloss?language=en
  const deleteRequest = page.waitForRequest('**/vocab/notes/**/gloss**');
  const cardsReload = page.waitForResponse('**/vocab/decks/*/cards');
  await reopened.locator('label', { hasText: 'Your English meaning' }).locator('input').fill('');
  await reopened.locator('.edit-gloss-row', { hasText: 'Your English meaning' }).getByRole('button', { name: 'Remove meaning' }).click();
  const delReq = await deleteRequest;
  expect(delReq.method()).toBe('DELETE');
  expect(delReq.url()).toMatch(/\/vocab\/notes\/\d+\/gloss\?language=en$/);
  expect(delReq.headers()['x-flashcards-request']).toBe('1');
  // Wait for the post-delete cards refresh before leaving; Escape during
  // saving-gloss is intentionally ignored.
  await cardsReload;
  await expect(reopened.locator('.edit-gloss-row', { hasText: 'Your English meaning' }).getByRole('button', { name: 'Remove meaning' })).toBeDisabled();

  // Close the dialog and confirm Escape works.
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-edit-dialog]')).toHaveCount(0);
});

test('Move dialog only lists existing decks, excludes the source deck, and refreshes after success', async ({ page }) => {
  await page.goto('/');
  await createDeckWithWord(page, 'MgmtMoveSource', 'Haus', /Haus · NOUN/, 'house, building');
  // Return to the deck list; deck creation lives there, not in deck detail.
  await page.getByRole('button', { name: 'All decks' }).click();
  await page.getByLabel('New deck name').fill('MgmtMoveDest');
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText('Created and opened “MgmtMoveDest”');
  // Switch back to the source deck.
  await page.getByRole('button', { name: 'All decks' }).click();
  await page.getByRole('button', { name: 'Open MgmtMoveSource' }).click();

  await openCardsTab(page);
  await page.locator('.deck-card-row', { hasText: 'Haus' }).getByRole('button', { name: 'Move', exact: true }).click();
  const dialog = page.locator('[data-move-dialog]');
  await expect(dialog).toBeVisible();

  // Source deck must not be a destination option.
  const select = dialog.getByLabel('Destination deck');
  const options = await select.locator('option').allTextContents();
  expect(options).not.toContain('MgmtMoveSource');
  expect(options).toContain('MgmtMoveDest');
  // Orphaned is created lazily on first orphan and may not exist on a
  // fresh state; destinations are whatever decks exist besides the source.

  // Capture the PATCH /vocab/decks/{source}/notes/{note_id} call.
  const patchRequest = page.waitForRequest('**/vocab/decks/*/notes/*');
  await select.selectOption({ label: 'MgmtMoveDest' });
  await dialog.getByRole('button', { name: 'Move', exact: true }).click();
  const req = await patchRequest;
  expect(req.method()).toBe('PATCH');
  expect(req.postDataJSON()).toEqual({ deck_id: expect.any(Number) as unknown as number });

  // After success the source deck no longer lists the card and the destination
  // shows it (without recreating the note: same note_id, taken from the
  // PATCH URL).
  const movedNoteId = Number(req.url().match(/\/notes\/(\d+)$/)?.[1]);
  expect(Number.isInteger(movedNoteId)).toBe(true);
  await expect(page.locator('.deck-card-row', { hasText: 'Haus' })).toHaveCount(0);
  await page.getByRole('button', { name: 'All decks' }).click();
  await page.getByRole('button', { name: 'Open MgmtMoveDest' }).click();
  await openCardsTab(page);
  await expect(page.locator('.deck-card-row', { hasText: 'Haus' })).toBeVisible({ timeout: firstServerTimeout });
  const destinationNoteIds = await page.evaluate(async () => {
    const response = await fetch('/vocab/decks');
    const decks = await response.json() as Array<{ name: string; id: number }>;
    const dest = decks.find((deck) => deck.name === 'MgmtMoveDest')!;
    const cards = await (await fetch(`/vocab/decks/${dest.id}/cards`)).json() as { cards: Array<{ note_id: number }> };
    return cards.cards.map((card) => card.note_id);
  });
  expect(destinationNoteIds).toContain(movedNoteId);
});

test('Remove from deck requires confirmation, uses non-destructive wording, and refreshes the deck cards', async ({ page }) => {
  await page.goto('/');
  // anrufen is unused by the other management tests, so removing its only
  // membership orphans the note (Haus is shared across decks via the
  // duplicate-safe note identity and would not orphan).
  await createDeckWithWord(page, 'MgmtRemoveDeck', 'anrufen', /anrufen · VERB/, 'to call, phone');
  await openCardsTab(page);
  await page.locator('.deck-card-row', { hasText: 'anrufen' }).getByRole('button', { name: 'Remove from deck' }).click();

  const dialog = page.locator('[data-remove-dialog]');
  await expect(dialog).toBeVisible();
  // Non-destructive wording: no "permanently delete" or "delete card".
  await expect(dialog).toContainText('Remove “anrufen” from this deck');
  await expect(dialog).toContainText('study history and saved vocabulary data are preserved');
  await expect(dialog).not.toContainText(/permanently delete|delete card/i);

  const deleteRequest = page.waitForRequest('**/vocab/decks/*/notes/*');
  await dialog.getByRole('button', { name: 'Remove from deck' }).click();
  const req = await deleteRequest;
  expect(req.method()).toBe('DELETE');
  expect(req.url()).toMatch(/\/vocab\/decks\/\d+\/notes\/\d+$/);

  // The card is gone from this deck, but the note survives in Orphaned
  // (server-controlled fallback) — the UI never claims destruction.
  await expect(page.locator('.deck-card-row', { hasText: 'anrufen' })).toHaveCount(0);
  await expect(page.getByText('This deck has no cards yet.')).toBeVisible();
  await expect(page.getByText('permanently deleted', { exact: false })).toHaveCount(0);
  await page.getByRole('button', { name: 'All decks' }).click();
  await page.reload();
  await page.getByRole('button', { name: 'Open Orphaned' }).click();
  await openCardsTab(page);
  await expect(page.locator('.deck-card-row', { hasText: 'anrufen' })).toBeVisible({ timeout: firstServerTimeout });
});

test('Rename deck prefills, rejects blank, surfaces duplicate-name conflict, and updates everywhere', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('New deck name').fill('MgmtRenameDeck');
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText('Created and opened “MgmtRenameDeck”');
  // Create the duplicate target in this test so the conflict check is
  // self-contained (no cross-test deck dependency).
  await page.getByRole('button', { name: 'All decks' }).click();
  await page.getByLabel('New deck name').fill('MgmtRenameConflict');
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText('Created and opened “MgmtRenameConflict”');
  await page.getByRole('button', { name: 'All decks' }).click();
  await page.getByRole('button', { name: 'Open MgmtRenameDeck' }).click();

  await page.getByRole('button', { name: 'Rename deck' }).click();
  const dialog = page.locator('[data-rename-dialog]');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel('New deck name')).toHaveValue('MgmtRenameDeck');

  // Blank rejected client-side without an API call.
  let renameAttempted = false;
  await page.route('**/vocab/decks/*', async (route) => {
    if (route.request().method() === 'PATCH') renameAttempted = true;
    await route.continue();
  });
  await dialog.getByLabel('New deck name').fill('   ');
  await dialog.getByRole('button', { name: 'Rename deck' }).click();
  await expect(dialog).toBeVisible();
  expect(renameAttempted).toBe(false);
  await page.unroute('**/vocab/decks/*');

  // Duplicate name conflict surfaces a human-readable message.
  await page.getByLabel('New deck name').fill('MgmtRenameConflict');
  await dialog.getByRole('button', { name: 'Rename deck' }).click();
  await expect(dialog.getByRole('alert')).toContainText(/already exists/);

  // Successful rename updates the heading and persists in the deck list.
  await page.getByLabel('New deck name').fill('MgmtRenamed');
  await dialog.getByRole('button', { name: 'Rename deck' }).click();
  await expect(page.getByRole('heading', { name: 'MgmtRenamed' })).toBeVisible();
  await page.getByRole('button', { name: 'All decks' }).click();
  await expect(page.getByRole('button', { name: 'Open MgmtRenamed' })).toBeVisible();
  await expect(page.getByText('MgmtRenameDeck')).toHaveCount(0);
});

test('Orphaned deck hides the Move action, refuses rename, and explains its protected status', async ({ page }) => {
  await page.goto('/');
  // Force a note into the Orphaned deck by removing the only membership.
  // 'See' (lake) is unused by the other management tests, so its removal
  // orphans (Haus/anrufen are shared/reused via the duplicate-safe note
  // identity and would not orphan).
  await page.getByLabel('New deck name').fill('MgmtOrphanSource');
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText('Created and opened “MgmtOrphanSource”');
  await page.getByRole('tab', { name: 'Add' }).click();
  await page.getByLabel('German word').fill('See');
  await page.getByRole('button', { name: 'Look up' }).click();
  await expect(page.getByRole('button', { name: /der See · NOUN/ })).toBeVisible({ timeout: firstServerTimeout });
  await page.getByRole('button', { name: /der See · NOUN/ }).click();
  await page.getByLabel('lake').check();
  await page.getByRole('button', { name: 'Save vocabulary' }).click();
  await expect(page.getByRole('status')).toContainText('Saved “See” to “MgmtOrphanSource”');

  await openCardsTab(page);
  await page.locator('.deck-card-row', { hasText: 'See' }).getByRole('button', { name: 'Remove from deck' }).click();
  await page.locator('[data-remove-dialog]').getByRole('button', { name: 'Remove from deck' }).click();
  await page.getByRole('button', { name: 'All decks' }).click();
  await page.reload();
  await page.getByRole('button', { name: 'Open Orphaned' }).click();

  // No Rename action available on the protected deck.
  await expect(page.getByRole('button', { name: 'Rename deck' })).toBeDisabled();
  await openCardsTab(page);
  // Move buttons on every row are disabled, so a misleading action never
  // surfaces.
  await expect(page.locator('.deck-card-row', { hasText: 'See' }).getByRole('button', { name: 'Move', exact: true })).toBeDisabled();
  await expect(page.getByText('Protected recovery deck.', { exact: true })).toBeVisible();
});

test('CSV import shows the dedicated whole-batch legacy-duplicate message without exposing dictionary_key', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('New deck name').fill('MgmtLegacyDeck');
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText('Created and opened “MgmtLegacyDeck”');

  // First insert a card that the importer will later collide on via the
  // legacy_duplicate_conflict stable code.
  await page.getByRole('tab', { name: 'Add' }).click();
  await page.getByLabel('German word').fill('Haus');
  await page.getByRole('button', { name: 'Look up' }).click();
  await expect(page.getByRole('button', { name: /Haus · NOUN/ })).toBeVisible({ timeout: firstServerTimeout });
  await page.getByRole('button', { name: /Haus · NOUN/ }).click();
  await page.getByLabel('house, building').check();
  await page.getByRole('button', { name: 'Save vocabulary' }).click();
  await expect(page.getByRole('status')).toContainText('Saved “Haus” to “MgmtLegacyDeck”');

  // Now intercept /vocab/import/csv with the server's stable 409 shape and
  // verify the UI renders the whole-batch message without exposing the
  // internal dictionary_key.
  await page.route('**/vocab/import/csv', async (route) => {
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: 'ambiguous legacy vocabulary: pre-M2 duplicate',
        code: 'legacy_duplicate_conflict',
        dictionary_key: 'lemma:v1:deadbeef',
      }),
    });
  });

  await page.getByRole('tab', { name: 'Import & Export' }).click();
  const importSection = page.locator('section[aria-labelledby="import-export-title"]');
  await importSection.getByLabel('Vocabulary lines').fill('Haus');
  await importSection.getByRole('button', { name: 'Import CSV' }).click();
  await expect(page.getByRole('alert')).toContainText('Import stopped because existing duplicate vocabulary needs attention.');
  await expect(page.getByRole('alert')).toContainText('No words from this import were added.');
  // Internal identifier is not shown to the user.
  await expect(page.getByText('lemma:v1:deadbeef')).toHaveCount(0);
  // The form did not claim partial success.
  await expect(page.getByText(/Import complete|added, .* already existed/)).toHaveCount(0);
});

test('Edit dialog reuses the existing pronunciation upload/revert contract', async ({ page }) => {
  await page.goto('/');
  await createDeckWithWord(page, 'MgmtAudioDeck', 'Haus', /Haus · NOUN/, 'house, building');

  await openCardsTab(page);
  await page.locator('.deck-card-row', { hasText: 'Haus' }).getByRole('button', { name: 'Edit', exact: true }).click();
  const dialog = page.locator('[data-edit-dialog]');
  await dialog.getByRole('button', { name: 'Add your pronunciation' }).click();
  await dialog.locator('input[type=file]').setInputFiles({
    name: 'tiny.wav',
    mimeType: 'audio/wav',
    buffer: tinyWavFixture(),
  });
  await expect(dialog.locator('audio.audio-preview')).toBeVisible();

  const uploadRequest = page.waitForRequest('**/vocab/notes/*/audio');
  await dialog.getByRole('button', { name: 'Save recording' }).click();
  const req = await uploadRequest;
  expect(req.method()).toBe('POST');
  expect(req.url()).toMatch(/\/vocab\/notes\/\d+\/audio$/);
  expect(req.headers()['x-flashcards-request']).toBe('1');

  // Revert via the same contract used by study.
  await dialog.getByRole('button', { name: 'Revert to automatic' }).click();
  const revertRequest = page.waitForRequest('**/vocab/notes/*/audio');
  await dialog.getByRole('button', { name: 'Confirm revert to automatic' }).click();
  const revertReq = await revertRequest;
  expect(revertReq.method()).toBe('DELETE');
  expect(revertReq.url()).toMatch(/\/vocab\/notes\/\d+\/audio$/);
});

test('Card list and edit dialog remain usable on a 390px viewport without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto('/');
  await createDeckWithWord(page, 'MgmtResponsiveDeck', 'Haus', /Haus · NOUN/, 'house, building');

  await openCardsTab(page);
  const row = page.locator('.deck-card-row', { hasText: 'Haus' });
  await expect(row).toBeVisible({ timeout: firstServerTimeout });
  const rowBox = await row.boundingBox();
  expect(rowBox?.width ?? 0).toBeLessThanOrEqual(390);

  // No horizontal overflow on the cards view.
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);

  await row.getByRole('button', { name: 'Edit', exact: true }).click();
  const dialog = page.locator('[data-edit-dialog]');
  await expect(dialog).toBeVisible();
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox?.width ?? 0).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
});

test('Restore to deck recycles an orphaned note into a normal deck and persists across reload', async ({ page }) => {
  // Use the 'die See' (sea) sense so M2 duplicate-safe identity is
  // distinct from any 'der See' (lake) row from earlier management tests.
  await page.goto('/');
  await page.getByLabel('New deck name').fill('M5RestoreSource');
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText('Created and opened “M5RestoreSource”');
  await page.getByRole('tab', { name: 'Add' }).click();
  await page.getByLabel('German word').fill('See');
  // Capture the note_id from the create-note response so we can target
  // the exact row in the management view.
  const createResponse = page.waitForResponse(
    (response) =>
      response.url().includes('/vocab/notes') &&
      response.request().method() === 'POST' &&
      !response.url().includes('/gloss') &&
      !response.url().includes('/audio') &&
      !response.url().includes('/restore'),
  );
  await page.getByRole('button', { name: 'Look up' }).click();
  await expect(page.getByRole('button', { name: /die See · NOUN/ })).toBeVisible({ timeout: firstServerTimeout });
  await page.getByRole('button', { name: /die See · NOUN/ }).click();
  await page.getByLabel('sea, ocean').check();
  await page.getByRole('button', { name: 'Save vocabulary' }).click();
  const created = await createResponse;
  const createdBody = await created.json() as { note_id: number };
  const seaNoteId = createdBody.note_id;
  expect(seaNoteId).toBeGreaterThan(0);
  const seaRow = page.locator(`.deck-card-row[data-note-id="${seaNoteId}"]`);

  // Create the destination deck up front so we can verify it remains
  // empty until the restore completes.
  await page.getByRole('button', { name: 'All decks' }).click();
  await page.getByLabel('New deck name').fill('M5RestoreTarget');
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText('Created and opened “M5RestoreTarget”');
  await page.getByRole('button', { name: 'All decks' }).click();
  await page.getByRole('button', { name: 'Open M5RestoreSource' }).click();

  // Remove the last normal membership so the note becomes Orphaned.
  await openCardsTab(page);
  await seaRow.getByRole('button', { name: 'Remove from deck' }).click();
  await page.locator('[data-remove-dialog]').getByRole('button', { name: 'Remove from deck' }).click();
  await page.getByRole('button', { name: 'All decks' }).click();
  await page.reload();
  await page.getByRole('button', { name: 'Open Orphaned' }).click();

  // On the Orphaned deck, Move remains disabled and a Restore button appears.
  await openCardsTab(page);
  const orphanRow = page.locator(`.deck-card-row[data-note-id="${seaNoteId}"]`);
  await expect(orphanRow).toBeVisible({ timeout: firstServerTimeout });
  await expect(orphanRow.getByRole('button', { name: 'Move', exact: true })).toBeDisabled();
  await expect(orphanRow.getByRole('button', { name: 'Restore to deck' })).toBeVisible();

  // Open the restore dialog.
  await orphanRow.getByRole('button', { name: 'Restore to deck' }).click();
  const dialog = page.locator('[data-restore-dialog]');
  await expect(dialog).toBeVisible();

  // The destination list excludes the Orphaned deck.
  const options = await dialog.getByLabel('Destination deck').locator('option').allTextContents();
  expect(options).not.toContain('Orphaned');
  expect(options).toContain('M5RestoreTarget');
  expect(options).toContain('M5RestoreSource');

  // Capture the POST request to verify the body shape.
  const restoreRequest = page.waitForRequest('**/vocab/notes/*/restore');
  await dialog.getByLabel('Destination deck').selectOption({ label: 'M5RestoreTarget' });
  await dialog.getByRole('button', { name: 'Restore', exact: true }).click();
  const req = await restoreRequest;
  expect(req.method()).toBe('POST');
  expect(req.url()).toMatch(/\/vocab\/notes\/\d+\/restore$/);
  expect(req.headers()['x-flashcards-request']).toBe('1');
  expect(req.postDataJSON()).toEqual({ deck_id: expect.any(Number) as unknown as number });

  const restoredNoteId = Number(req.url().match(/\/notes\/(\d+)\/restore$/)?.[1]);
  expect(restoredNoteId).toBe(seaNoteId);

  // Success: dialog closes, note disappears from Orphaned, destination contains it.
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(`.deck-card-row[data-note-id="${seaNoteId}"]`)).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('Restored “See” to “M5RestoreTarget”');

  // Visit the destination deck and confirm the note is there with its
  // identity preserved (headword + noun POS).
  await page.getByRole('button', { name: 'All decks' }).click();
  await page.getByRole('button', { name: 'Open M5RestoreTarget' }).click();
  await openCardsTab(page);
  const destRow = page.locator(`.deck-card-row[data-note-id="${seaNoteId}"]`);
  await expect(destRow).toBeVisible({ timeout: firstServerTimeout });
  await expect(destRow.getByText(/NOUN/)).toBeVisible();

  // Persists across a hard reload. After reload the SPA loses its
  // selectedDeckId state, so re-open the destination deck before
  // checking the restored row.
  await page.reload();
  await page.getByRole('button', { name: 'Open M5RestoreTarget' }).click();
  await openCardsTab(page);
  await expect(page.locator(`.deck-card-row[data-note-id="${seaNoteId}"]`)).toBeVisible({ timeout: firstServerTimeout });
  const persisted = await page.evaluate(async (noteId: number) => {
    const response = await fetch('/vocab/decks');
    const decks = await response.json() as Array<{ name: string; id: number }>;
    const dest = decks.find((deck) => deck.name === 'M5RestoreTarget');
    if (!dest) return false;
    const cards = await (await fetch(`/vocab/decks/${dest.id}/cards`)).json() as { cards: Array<{ note_id: number }> };
    return cards.cards.some((card) => card.note_id === noteId);
  }, seaNoteId);
  expect(persisted).toBe(true);
});

test('Restore dialog remains usable on a 390px viewport without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto('/');
  // Drive setup entirely through the API so the test state is
  // deterministic across runs and independent of upstream test order.
  await page.evaluate(async () => {
    const headers = {
      'Content-Type': 'application/json',
      'X-Flashcards-Request': '1',
    };
    // sourceResp is intentionally unused: deck is created for setup state only
    await fetch('/vocab/decks', {
      method: 'POST',
      headers,
      body: JSON.stringify({ name: 'M5ResponsiveSource' }),
    });
    const lookupResp = await fetch('/vocab/lookup?q=anrufen', {
      method: 'POST',
      headers,
      body: JSON.stringify({ query: 'anrufen' }),
    });
    const lookup = await lookupResp.json() as {
      asset_token: string;
      candidates: Array<{
        lemma: string;
        lemma_semantic_ref: string;
        senses?: Array<{ sense_semantic_ref: string }>;
      }>;
    };
    const anrufen = lookup.candidates.find((c) => c.lemma === 'anrufen');
    if (!anrufen || !anrufen.senses || anrufen.senses.length === 0) {
      throw new Error('expected anrufen candidate in fixture');
    }
    const noteResp = await fetch('/vocab/notes', {
      method: 'POST',
      headers,
      body: JSON.stringify({
        asset_token: lookup.asset_token,
        lemma_semantic_ref: anrufen.lemma_semantic_ref,
        sense_semantic_ref: anrufen.senses[0]!.sense_semantic_ref,
        status: 'resolved',
        meaning_languages: ['de', 'en'],
        deck_name: 'M5ResponsiveSource',
      }),
    });
    const note = await noteResp.json() as { note_id: number };
    // Find every non-Orphaned membership for this note and remove it
    // so that removing the test's own membership leaves the note in
    // Orphaned even when M2 duplicate-safe identity reuses a shared row.
    const allDecks = await fetch('/vocab/decks').then((r) => r.json()) as Array<{ id: number; name: string }>;
    const orphanRow = allDecks.find((d) => d.name === 'Orphaned');
    if (!orphanRow) throw new Error('Orphaned deck missing');
    for (const deck of allDecks) {
      if (deck.name === 'Orphaned') continue;
      const listingResp = await fetch(`/vocab/decks/${deck.id}/cards`);
      const listing = await listingResp.json() as { cards: Array<{ note_id: number }> };
      for (const card of listing.cards) {
        if (card.note_id !== note.note_id) continue;
        await fetch(`/vocab/decks/${deck.id}/notes/${card.note_id}`, {
          method: 'DELETE',
          headers: { 'X-Flashcards-Request': '1' },
        });
      }
    }
    await fetch('/vocab/decks', {
      method: 'POST',
      headers,
      body: JSON.stringify({ name: 'M5ResponsiveTarget' }),
    });
  });

  await page.reload();
  await page.getByRole('button', { name: 'Open Orphaned' }).click();
  await openCardsTab(page);

  const orphanRow = page.locator('.deck-card-row[data-note-id]').first();
  await expect(orphanRow).toBeVisible({ timeout: firstServerTimeout });

  await orphanRow.getByRole('button', { name: 'Restore to deck' }).click();
  const dialog = page.locator('[data-restore-dialog]');
  await expect(dialog).toBeVisible();
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox?.width ?? 0).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
});

test('Restore dialog shows the no-destination hint and disables submit when no normal deck exists', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(async () => {
    const headers = {
      'Content-Type': 'application/json',
      'X-Flashcards-Request': '1',
    };
    // sourceResp is intentionally unused: deck is created for setup state only
    await fetch('/vocab/decks', {
      method: 'POST',
      headers,
      body: JSON.stringify({ name: 'M5NoDestinationSource' }),
    });
    const lookupResp = await fetch('/vocab/lookup?q=anrufen', {
      method: 'POST',
      headers,
      body: JSON.stringify({ query: 'anrufen' }),
    });
    const lookup = await lookupResp.json() as {
      asset_token: string;
      candidates: Array<{
        lemma: string;
        lemma_semantic_ref: string;
        senses?: Array<{ sense_semantic_ref: string }>;
      }>;
    };
    const anrufen = lookup.candidates.find((c) => c.lemma === 'anrufen');
    if (!anrufen || !anrufen.senses || anrufen.senses.length === 0) {
      throw new Error('expected anrufen candidate in fixture');
    }
    const noteResp = await fetch('/vocab/notes', {
      method: 'POST',
      headers,
      body: JSON.stringify({
        asset_token: lookup.asset_token,
        lemma_semantic_ref: anrufen.lemma_semantic_ref,
        sense_semantic_ref: anrufen.senses[0]!.sense_semantic_ref,
        status: 'resolved',
        meaning_languages: ['de', 'en'],
        deck_name: 'M5NoDestinationSource',
      }),
    });
    const note = await noteResp.json() as { note_id: number };
    const allDecks = await fetch('/vocab/decks').then((r) => r.json()) as Array<{ id: number; name: string }>;
    const orphanRow = allDecks.find((d) => d.name === 'Orphaned');
    if (!orphanRow) throw new Error('Orphaned deck missing');
    for (const deck of allDecks) {
      if (deck.name === 'Orphaned') continue;
      const listingResp = await fetch(`/vocab/decks/${deck.id}/cards`);
      const listing = await listingResp.json() as { cards: Array<{ note_id: number }> };
      for (const card of listing.cards) {
        if (card.note_id !== note.note_id) continue;
        await fetch(`/vocab/decks/${deck.id}/notes/${card.note_id}`, {
          method: 'DELETE',
          headers: { 'X-Flashcards-Request': '1' },
        });
      }
    }
  });

  // Present a valid zero-normal-destination server result to the client
  // without destroying real decks belonging to other tests: intercept the
  // normal GET /vocab/decks deck-list request and retain only the
  // protected Orphaned deck in the fulfilled response.
  await page.route('**/vocab/decks', async (route) => {
    const request = route.request();
    let pathname = '';
    try {
      pathname = new URL(request.url()).pathname;
    } catch {
      await route.continue();
      return;
    }
    if (request.method() !== 'GET' || pathname !== '/vocab/decks') {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    const decks = await response.json() as Array<{ id: number; name: string }>;
    const filtered = decks.filter((deck) => deck.name === 'Orphaned');
    await route.fulfill({
      status: response.status(),
      contentType: 'application/json',
      body: JSON.stringify(filtered),
    });
  });

  await page.reload();
  await page.getByRole('button', { name: 'Open Orphaned' }).click();
  await openCardsTab(page);

  const orphanRow = page.locator('.deck-card-row[data-note-id]').first();
  await expect(orphanRow).toBeVisible({ timeout: firstServerTimeout });

  await orphanRow.getByRole('button', { name: 'Restore to deck' }).click();
  const dialog = page.locator('[data-restore-dialog]');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Create a normal deck first.');
  await expect(dialog.getByRole('button', { name: 'Restore', exact: true })).toBeDisabled();
});

test('Restore dialog surfaces server errors cleanly when the destination disappears', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(async () => {
    const headers = {
      'Content-Type': 'application/json',
      'X-Flashcards-Request': '1',
    };
    // sourceResp is intentionally unused: deck is created for setup state only
    await fetch('/vocab/decks', {
      method: 'POST',
      headers,
      body: JSON.stringify({ name: 'M5DisappearSource' }),
    });
    const lookupResp = await fetch('/vocab/lookup?q=anrufen', {
      method: 'POST',
      headers,
      body: JSON.stringify({ query: 'anrufen' }),
    });
    const lookup = await lookupResp.json() as {
      asset_token: string;
      candidates: Array<{
        lemma: string;
        lemma_semantic_ref: string;
        senses?: Array<{ sense_semantic_ref: string }>;
      }>;
    };
    const anrufen = lookup.candidates.find((c) => c.lemma === 'anrufen');
    if (!anrufen || !anrufen.senses || anrufen.senses.length === 0) {
      throw new Error('expected anrufen candidate in fixture');
    }
    const noteResp = await fetch('/vocab/notes', {
      method: 'POST',
      headers,
      body: JSON.stringify({
        asset_token: lookup.asset_token,
        lemma_semantic_ref: anrufen.lemma_semantic_ref,
        sense_semantic_ref: anrufen.senses[0]!.sense_semantic_ref,
        status: 'resolved',
        meaning_languages: ['de', 'en'],
        deck_name: 'M5DisappearSource',
      }),
    });
    const note = await noteResp.json() as { note_id: number };
    const allDecks = await fetch('/vocab/decks').then((r) => r.json()) as Array<{ id: number; name: string }>;
    const orphanRow = allDecks.find((d) => d.name === 'Orphaned');
    if (!orphanRow) throw new Error('Orphaned deck missing');
    for (const deck of allDecks) {
      if (deck.name === 'Orphaned') continue;
      const listingResp = await fetch(`/vocab/decks/${deck.id}/cards`);
      const listing = await listingResp.json() as { cards: Array<{ note_id: number }> };
      for (const card of listing.cards) {
        if (card.note_id !== note.note_id) continue;
        await fetch(`/vocab/decks/${deck.id}/notes/${card.note_id}`, {
          method: 'DELETE',
          headers: { 'X-Flashcards-Request': '1' },
        });
      }
    }
    await fetch('/vocab/decks', {
      method: 'POST',
      headers,
      body: JSON.stringify({ name: 'M5DisappearTarget' }),
    });
  });

  await page.reload();
  await page.getByRole('button', { name: 'Open Orphaned' }).click();
  await openCardsTab(page);

  const orphanRow = page.locator('.deck-card-row[data-note-id]').first();
  await expect(orphanRow).toBeVisible({ timeout: firstServerTimeout });

  await orphanRow.getByRole('button', { name: 'Restore to deck' }).click();
  const dialog = page.locator('[data-restore-dialog]');
  await expect(dialog).toBeVisible();

  // Force the server to return a structured 404 between the dialog open
  // and the restore submission.
  await page.route('**/vocab/notes/*/restore', async (route) => {
    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'deck 99999 not found', code: 'deck_not_found' }),
    });
  });

  await dialog.getByLabel('Destination deck').selectOption({ label: 'M5DisappearTarget' });
  await dialog.getByRole('button', { name: 'Restore', exact: true }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('alert')).toContainText(/could not be found|Reload/);
  // Dialog remains open: state is recoverable after the error.
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeVisible();
});

test('Edit sense dialog rebinds to another same-lemma sense and preserves history', async ({ page }) => {
  // ``Haus`` is a multi-sense lemma in the E2E fixture: ``house, building``
  // (ord=0) and ``home, figuratively`` (ord=1) share the same durable
  // lemma_semantic_ref. The contract requires this positive case to use
  // a fixture with multiple senses under ONE lemma semantic ref — i.e.
  // not the cross-lemma ``See`` ``der See`` vs ``die See`` case.
  await page.goto('/');
  await createDeckWithWord(
    page,
    'M6RebindSource',
    'Haus',
    /Haus · NOUN/,
    'house, building',
  );

  // Seed an English user meaning so we can verify it survives.
  await page.locator('.primary-nav').getByRole('button', { name: 'Study due' }).click();
  await expect(page.getByRole('heading', { name: 'Haus' })).toBeVisible({ timeout: firstServerTimeout });
  await page.keyboard.press('Space');
  await page.getByRole('button', { name: 'Show extra info' }).click();
  await page.getByLabel('Your English meaning').fill('building');
  const seedEnglishRow = page.locator('.gloss-row', { hasText: 'Your English meaning' });
  await seedEnglishRow.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('.inline-status', { hasText: 'English meaning saved.' })).toBeVisible();
  await page.getByRole('button', { name: /Back to/ }).click();
  await page.getByRole('button', { name: 'Open M6RebindSource' }).click();

  await openCardsTab(page);
  const row = page.locator('.deck-card-row', { hasText: 'Haus' });
  await expect(row).toBeVisible({ timeout: firstServerTimeout });

  // Capture the note_id from the API so we can verify the response shape
  // later (note/card remain the same learning item).
  const cardsJson = await page.evaluate(async () => {
    const response = await fetch('/vocab/decks');
    const decks = await response.json() as Array<{ id: number; name: string }>;
    const target = decks.find((deck) => deck.name === 'M6RebindSource');
    if (!target) throw new Error('expected M6RebindSource deck');
    const cards = await (await fetch(`/vocab/decks/${target.id}/cards`)).json() as {
      cards: Array<{ note_id: number; card_id: number }>;
    };
    return cards.cards[0];
  });
  if (!cardsJson) throw new Error('expected one card in M6RebindSource deck');
  const originalNoteId = cardsJson.note_id;
  const originalCardId = cardsJson.card_id;

  // Open the Edit sense dialog.
  await row.getByTestId(`edit-sense-${originalNoteId}`).click();
  const dialog = page.locator('[data-sense-dialog]');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Change selected dictionary sense');
  // The current sense is preselected.
  await expect(dialog.getByRole('radio', { name: /house, building/ })).toBeChecked();
  // The other same-lemma sense is offered; cross-lemma targets are
  // filtered out by the picker.
  await expect(dialog.getByRole('radio', { name: /home, figuratively/ })).toBeVisible();

  // Capture the PUT request so we can verify URL, method, headers, and
  // body shape.
  const putRequest = page.waitForRequest('**/vocab/notes/*/sense');
  await dialog.getByRole('radio', { name: /home, figuratively/ }).check();
  await dialog.getByTestId(`confirm-sense-${originalNoteId}`).click();
  const req = await putRequest;
  expect(req.method()).toBe('PUT');
  expect(req.url()).toMatch(new RegExp(`/vocab/notes/${originalNoteId}/sense$`));
  expect(req.headers()['x-flashcards-request']).toBe('1');
  expect(req.headers()['content-type']).toBe('application/json');
  const body = req.postDataJSON() as { asset_token: string; sense_semantic_ref: string };
  expect(typeof body.asset_token).toBe('string');
  expect(body.sense_semantic_ref.length).toBeGreaterThan(0);
  // The browser never carries numeric lemma_id, sense_id, lemma_semantic_ref,
  // or dictionary_key.
  expect((body as unknown as Record<string, unknown>).lemma_semantic_ref).toBeUndefined();
  expect((body as unknown as Record<string, unknown>).lemma_id).toBeUndefined();
  expect((body as unknown as Record<string, unknown>).sense_id).toBeUndefined();
  expect((body as unknown as Record<string, unknown>).dictionary_key).toBeUndefined();

  // The dialog closes and a status message confirms the rebind.
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('Updated dictionary sense for');

  // The note/card remain the same learning item.
  const after = await page.evaluate(async () => {
    const response = await fetch('/vocab/decks');
    const decks = await response.json() as Array<{ id: number; name: string }>;
    const target = decks.find((deck) => deck.name === 'M6RebindSource');
    if (!target) throw new Error('expected M6RebindSource deck');
    const cards = await (await fetch(`/vocab/decks/${target.id}/cards`)).json() as {
      cards: Array<{ note_id: number; card_id: number }>;
    };
    return cards.cards[0];
  });
  if (!after) throw new Error('expected one card in M6RebindSource deck after rebind');
  expect(after.note_id).toBe(originalNoteId);
  expect(after.card_id).toBe(originalCardId);

  // The user-authored meaning still wins (preserved across the rebind);
// we verify this on the study view and confirm the dictionary's
// underlying sense identity changed by inspecting the rendered card
// back after the user-authored override is cleared.
  await page.locator('.primary-nav').getByRole('button', { name: 'Study due' }).click();
  await expect(page.getByRole('heading', { name: 'Haus' })).toBeVisible({ timeout: firstServerTimeout });
  await page.keyboard.press('Space');
  await page.getByRole('button', { name: 'Show extra info' }).click();
  await expect(page.getByLabel('Your English meaning')).toHaveValue('building');
  // Remove the override so the dictionary sense shows through.
  const englishRow = page.locator('.gloss-row', { hasText: 'Your English meaning' });
  await englishRow.getByRole('button', { name: 'Remove' }).click();
  await expect(page.locator('.inline-status', { hasText: 'English meaning removed.' })).toBeVisible();
  await expect(page.getByText(/home, figuratively/)).toBeVisible();
  await page.getByRole('button', { name: /Back to/ }).click();
  await page.getByRole('button', { name: 'Open M6RebindSource' }).click();

  // Reload and verify persistence across a hard reload.
  await page.reload();
  await page.getByRole('button', { name: 'Open M6RebindSource' }).click();
  await openCardsTab(page);
  await expect(page.locator('.deck-card-row', { hasText: 'Haus' }))
    .toBeVisible({ timeout: firstServerTimeout });
  // The study view still surfaces the new meaning after the reload.
  await page.locator('.primary-nav').getByRole('button', { name: 'Study due' }).click();
  await expect(page.getByRole('heading', { name: 'Haus' })).toBeVisible({ timeout: firstServerTimeout });
  await page.keyboard.press('Space');
  await page.getByRole('button', { name: 'Show extra info' }).click();
  await expect(page.getByText(/home, figuratively/)).toBeVisible();
});

test('Edit sense dialog reports a stale picker token without writing', async ({ page }) => {
  await page.goto('/');
  await createDeckWithWord(
    page,
    'M6StaleSource',
    'Haus',
    /Haus · NOUN/,
    'house, building',
  );

  await openCardsTab(page);
  const row = page.locator('.deck-card-row', { hasText: 'Haus' }).first();
  const noteId = Number(await row.getAttribute('data-note-id'));
  await row.getByTestId(`edit-sense-${noteId}`).click();
  const dialog = page.locator('[data-sense-dialog]');
  await expect(dialog).toBeVisible();

  // Force a stale-picker response: the active session will return a
  // dictionary_changed 409 the moment the dialog submits.
  await page.route('**/vocab/notes/*/sense', async (route) => {
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: 'Asset token mismatch; dictionary has changed',
        code: 'dictionary_changed',
      }),
    });
  });

  const before = await page.evaluate(async () => {
    const response = await fetch('/vocab/decks');
    const decks = await response.json() as Array<{ id: number; name: string }>;
    const target = decks.find((deck) => deck.name === 'M6StaleSource');
    if (!target) throw new Error('expected M6StaleSource deck');
    const cards = await (await fetch(`/vocab/decks/${target.id}/cards`)).json() as {
      cards: Array<{ sense_semantic_ref: string | null }>;
    };
    return cards.cards[0]?.sense_semantic_ref ?? null;
  });

  await dialog.getByRole('radio', { name: /home, figuratively/ }).check();
  await dialog.getByTestId(`confirm-sense-${noteId}`).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('alert')).toContainText(/dictionary changed|try again/i);
  // The dialog stays open: the user can close and retry, no mutation
  // has happened.
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeVisible();

  const after = await page.evaluate(async () => {
    const response = await fetch('/vocab/decks');
    const decks = await response.json() as Array<{ id: number; name: string }>;
    const target = decks.find((deck) => deck.name === 'M6StaleSource');
    if (!target) throw new Error('expected M6StaleSource deck');
    const cards = await (await fetch(`/vocab/decks/${target.id}/cards`)).json() as {
      cards: Array<{ sense_semantic_ref: string | null }>;
    };
    return cards.cards[0]?.sense_semantic_ref ?? null;
  });
  expect(after).toBe(before);

  await page.unroute('**/vocab/notes/*/sense');
});

test('Edit sense dialog reports a selected-sense collision without writing', async ({ page }) => {
  // Two ``Haus`` notes are impossible under M2's identity contract: only
  // one resolved note can own a given dictionary_key. We instead simulate
  // the collision by forcing the server to return a 409 selected_sense_conflict
  // response when the second rebind attempt targets a sense owned by
  // another (pre-existing) note, and verify the UI surfaces the error.
  await page.goto('/');
  await createDeckWithWord(
    page,
    'M6CollisionSource',
    'Haus',
    /Haus · NOUN/,
    'house, building',
  );
  await openCardsTab(page);
  const row = page.locator('.deck-card-row', { hasText: 'Haus' }).first();
  const noteId = Number(await row.getAttribute('data-note-id'));
  await row.getByTestId(`edit-sense-${noteId}`).click();
  const dialog = page.locator('[data-sense-dialog]');
  await expect(dialog).toBeVisible();

  await page.route('**/vocab/notes/*/sense', async (route) => {
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: 'selected sense key is already owned',
        code: 'selected_sense_conflict',
        dictionary_key: 'resolved:v1:sense-already-owned',
        owner_note_id: 99999,
      }),
    });
  });

  await dialog.getByRole('radio', { name: /home, figuratively/ }).check();
  await dialog.getByTestId(`confirm-sense-${noteId}`).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('alert')).toContainText(/another card already uses|conflict/i);
  // dictionary_key is not surfaced in the user-visible alert.
  await expect(dialog.getByText('resolved:v1:sense-already-owned')).toHaveCount(0);

  await page.unroute('**/vocab/notes/*/sense');
});

test('Edit sense dialog is usable at 390 px without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto('/');
  await createDeckWithWord(
    page,
    'M6ResponsiveSource',
    'Haus',
    /Haus · NOUN/,
    'house, building',
  );
  await openCardsTab(page);
  const row = page.locator('.deck-card-row', { hasText: 'Haus' }).first();
  const noteId = Number(await row.getAttribute('data-note-id'));
  await row.getByTestId(`edit-sense-${noteId}`).click();
  const dialog = page.locator('[data-sense-dialog]');
  await expect(dialog).toBeVisible();
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox?.width ?? 0).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth))
    .toBeLessThanOrEqual(1);
});

test('Edit sense same-sense submit is a no-op success', async ({ page }) => {
  await page.goto('/');
  await createDeckWithWord(
    page,
    'M6NoOpSource',
    'Haus',
    /Haus · NOUN/,
    'house, building',
  );
  await openCardsTab(page);
  const row = page.locator('.deck-card-row', { hasText: 'Haus' }).first();
  const noteId = Number(await row.getAttribute('data-note-id'));
  await row.getByTestId(`edit-sense-${noteId}`).click();
  const dialog = page.locator('[data-sense-dialog]');
  await expect(dialog).toBeVisible();
  // The current sense is preselected.
  await expect(dialog.getByRole('radio', { name: /house, building/ })).toBeChecked();

  // Capture the PUT and verify the same-sense submit succeeds.
  const putRequest = page.waitForRequest('**/vocab/notes/*/sense');
  await dialog.getByTestId(`confirm-sense-${noteId}`).click();
  const req = await putRequest;
  expect(req.method()).toBe('PUT');
  const body = req.postDataJSON() as { sense_semantic_ref: string };
  // The browser submits the same sense; the server is expected to
  // return 200 with no observable change.
  expect(body.sense_semantic_ref.length).toBeGreaterThan(0);
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('Updated dictionary sense for');
});
