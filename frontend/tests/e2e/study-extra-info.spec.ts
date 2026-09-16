import { expect, test } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

const firstServerTimeout = 60_000;

/**
 * These specs share a live FastAPI server and user database with the rest
 * of the Playwright suite (the server is started once for the whole run),
 * so every deck name here must be unique across spec files.
 */
async function createDeckWithCard(
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

async function addCardToOpenDeck(
  page: import('@playwright/test').Page,
  deckName: string,
  word: string,
  candidateLabel: string | RegExp,
  senseLabel: string,
): Promise<void> {
  await page.getByRole('tab', { name: 'Add' }).click();
  await page.getByLabel('German word').fill(word);
  await page.getByRole('button', { name: 'Look up' }).click();
  await expect(page.getByRole('button', { name: candidateLabel })).toBeVisible({ timeout: firstServerTimeout });
  await page.getByRole('button', { name: candidateLabel }).click();
  await page.getByLabel(senseLabel).check();
  await page.getByRole('button', { name: 'Save vocabulary' }).click();
  await expect(page.getByRole('status')).toContainText(`Saved “${word}” to “${deckName}”`);
}

async function openDeckStudy(page: import('@playwright/test').Page): Promise<void> {
  await page.getByRole('button', { name: 'Study this deck' }).first().click();
}

test('unrevealed card shows no extra info, reveal shows a concise answer with extra info collapsed', async ({ page }) => {
  await page.goto('/');
  await createDeckWithCard(page, 'ExtraInfoBasics', 'Haus', /Haus · NOUN/, 'house, building');
  await openDeckStudy(page);
  await expect(page.getByRole('heading', { name: 'Haus' })).toBeVisible({ timeout: firstServerTimeout });

  // A. Unrevealed: no answer, no extra-info controls at all.
  await expect(page.getByRole('button', { name: 'Reveal answer' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Show extra info/ })).toBeHidden();
  await expect(page.getByText('How well did you know it?')).toBeHidden();

  // B. Reveal -> concise default answer only.
  await page.keyboard.press('Space');
  await expect(page.getByText('How well did you know it?')).toBeVisible();
  await expect(page.getByText('house, building')).toBeVisible();
  await expect(page.getByText('Das Haus ist alt.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Play pronunciation' })).toBeVisible();

  // C. Extra info collapsed by default.
  const toggle = page.getByRole('button', { name: 'Show extra info' });
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByRole('heading', { name: 'Custom pronunciation' })).toBeHidden();
  await expect(page.getByRole('heading', { name: 'Your meanings' })).toBeHidden();

  // D. Show extra info -> secondary content appears, plural shown exactly
  // once with no "Plural: Plural:" duplication (Part F regression).
  await toggle.click();
  await expect(page.getByRole('button', { name: 'Hide extra info' })).toHaveAttribute('aria-expanded', 'true');
  const grammar = page.locator('.compact-grammar');
  await expect(grammar).toContainText('Plural: die Häuser');
  await expect(grammar).not.toContainText('Plural: Plural:');
  const pluralOccurrences = await grammar.evaluate((el) => (el.textContent?.match(/Plural:/g) ?? []).length);
  expect(pluralOccurrences).toBe(1);
  await expect(page.getByRole('heading', { name: 'Custom pronunciation' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Your meanings' })).toBeVisible();

  // Collapsing again hides the secondary content.
  await page.getByRole('button', { name: 'Hide extra info' }).click();
  await expect(page.getByRole('heading', { name: 'Custom pronunciation' })).toBeHidden();
  await expect(page.getByRole('heading', { name: 'Your meanings' })).toBeHidden();
});

test('Always show extra info opens the current card immediately, then follows subsequent cards until turned off', async ({ page }) => {
  await page.goto('/');
  await createDeckWithCard(page, 'ExtraInfoAlways', 'Haus', /Haus · NOUN/, 'house, building');
  await addCardToOpenDeck(page, 'ExtraInfoAlways', 'anrufen', /anrufen · VERB/, 'to call, phone');
  await openDeckStudy(page);
  await expect(page.locator('.study-lemma')).toBeVisible({ timeout: firstServerTimeout });
  await page.keyboard.press('Space');
  await expect(page.getByText('How well did you know it?')).toBeVisible();

  const alwaysToggle = page.getByLabel('Always show extra info');
  await expect(alwaysToggle).not.toBeChecked();
  await expect(page.getByRole('button', { name: 'Show extra info' })).toBeVisible();

  // E. Turning the preference ON expands the currently revealed card
  // immediately, without needing a manual "Show extra info" click.
  await alwaysToggle.check();
  await expect(page.getByRole('button', { name: 'Hide extra info' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Custom pronunciation' })).toBeVisible();

  // Rate the card to advance; the persisted preference is ON. Wait for the
  // actual next card to land (its unrevealed "Reveal answer" state) rather
  // than just for *a* lemma heading to be visible, since the outgoing
  // card's heading is already visible and loading the next one is async.
  await page.getByRole('button', { name: /3 With effort/ }).click();
  await expect(page.getByRole('button', { name: 'Reveal answer' })).toBeVisible({ timeout: firstServerTimeout });

  // F. Advance/load another card -> extra info automatically appears after
  // reveal, with no manual expansion.
  await page.keyboard.press('Space');
  await expect(page.getByText('How well did you know it?')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hide extra info' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Custom pronunciation' })).toBeVisible();

  // G. Turning the preference OFF collapses the current card immediately.
  await page.getByLabel('Always show extra info').uncheck();
  await expect(page.getByRole('button', { name: 'Show extra info' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Custom pronunciation' })).toBeHidden();
});

test('the Always show extra info preference persists across a reload and defaults safely for a fresh browser', async ({ page }) => {
  await page.goto('/');
  // 'See' (lake) is still due here: the shared Haus note was rated in the
  // previous test, so a Haus deck would open to 'Study complete'.
  await createDeckWithCard(page, 'ExtraInfoPersist', 'See', /der See · NOUN/, 'lake');
  await openDeckStudy(page);
  await expect(page.getByRole('heading', { name: 'See' })).toBeVisible({ timeout: firstServerTimeout });
  await page.keyboard.press('Space');
  await page.getByLabel('Always show extra info').check();
  await expect(page.getByRole('button', { name: 'Hide extra info' })).toBeVisible();

  // H2. ON persists across a reload (a same-origin reload is the closest
  // Playwright equivalent of an application/browser restart: the page's
  // in-memory state is fully torn down, only localStorage survives).
  await page.reload();
  await page.getByRole('button', { name: 'Open ExtraInfoPersist' }).click();
  await openDeckStudy(page);
  await expect(page.getByRole('heading', { name: 'See' })).toBeVisible({ timeout: firstServerTimeout });
  // Reveal before reading the checkbox: it only renders on the revealed
  // answer, and per Part E the persisted preference governs the *next*
  // reveal — it must show as checked and open extra info immediately.
  await page.keyboard.press('Space');
  await expect(page.getByLabel('Always show extra info')).toBeChecked();
  await expect(page.getByRole('button', { name: 'Hide extra info' })).toBeVisible();
});

test('an invalid persisted value falls back safely to Always show extra info OFF', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('wortlaut.study.alwaysShowExtraInfo', 'not-a-boolean');
  });
  await page.goto('/');
  // anrufen is still due (only Haus was rated in the earlier test).
  await createDeckWithCard(page, 'ExtraInfoInvalidStorage', 'anrufen', /anrufen · VERB/, 'to call, phone');
  await openDeckStudy(page);
  await expect(page.getByRole('heading', { name: 'anrufen' })).toBeVisible({ timeout: firstServerTimeout });
  await page.keyboard.press('Space');
  await expect(page.getByLabel('Always show extra info')).not.toBeChecked();
  await expect(page.getByRole('button', { name: 'Show extra info' })).toBeVisible();
});

test('studying still works when localStorage access throws', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('storage disabled in this simulated browser');
      },
    });
  });
  await page.goto('/');
  // 'See' (sea) is still due here; Haus was rated earlier and anrufen is
  // exercised by the previous test.
  await createDeckWithCard(page, 'ExtraInfoStorageFailure', 'See', /die See · NOUN/, 'sea, ocean');
  await openDeckStudy(page);
  await expect(page.getByRole('heading', { name: 'See' })).toBeVisible({ timeout: firstServerTimeout });
  await page.keyboard.press('Space');
  await expect(page.getByText('How well did you know it?')).toBeVisible();
  await expect(page.getByLabel('Always show extra info')).not.toBeChecked();
  // The toggle still works for the current session even though nothing can
  // be persisted.
  await page.getByLabel('Always show extra info').check();
  await expect(page.getByRole('button', { name: 'Hide extra info' })).toBeVisible();
  await page.getByRole('button', { name: /4 Comfortably/ }).click();
  await expect(page.getByRole('heading', { name: 'Study complete' })).toBeVisible({ timeout: firstServerTimeout });
});
