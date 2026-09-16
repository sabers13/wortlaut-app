import { expect, test } from '@playwright/test';

/**
 * M4B persistent folder integration, exercised against the real served
 * FastAPI product (no mocked deck/folder payloads). Folder state lives in the
 * shared user database, so every folder and deck name here is prefixed to stay
 * collision-free, and the integration cleans up the decks/folders it created
 * at the end of the file.
 */
test.describe.configure({ mode: 'serial' });

const firstServerTimeout = 60_000;

const FOLDER_ALPHA = 'FUIFolderAlpha';
const FOLDER_BETA = 'FUIFolderBeta';
const FOLDER_EMPTY = 'FUIFolderEmpty';
const FOLDER_RENAME_FROM = 'FUIFolderRename';
const FOLDER_RENAME_TO = 'FUIFolderRenamed';
const FOLDER_CONFLICT = 'FUIFolderConflict';
const FOLDER_DELETE = 'FUIFolderDelete';
const FOLDER_DELETE_KEEP = 'FUIFolderDeleteKeep';
const FOLDER_PERSIST = 'FUIFolderPersist';
const FOLDER_LONG = 'FUIFolderResponsiveLongNameForWrappingChecks';

const DECK_MOVE = 'FUIDeckMove';
const DECK_DELETE = 'FUIDeckDelete';
const DECK_PERSIST = 'FUIDeckPersist';

function folderRegion(page: import('@playwright/test').Page, name: string) {
  return page.getByRole('region', { name: `Folder ${name}`, exact: true });
}

function unassignedRegion(page: import('@playwright/test').Page) {
  return page.getByRole('region', { name: 'Not in a folder', exact: true });
}

async function gotoServedProduct(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Your decks' })).toBeVisible({ timeout: firstServerTimeout });
}

async function createEmptyDeck(page: import('@playwright/test').Page, name: string): Promise<void> {
  await page.getByLabel('New deck name').fill(name);
  await page.getByRole('button', { name: 'Create deck' }).click();
  await expect(page.getByRole('status')).toContainText(`Created and opened “${name}”`);
  await page.getByRole('button', { name: 'All decks' }).click();
  await expect(page.getByRole('heading', { name: 'Your decks' })).toBeVisible({ timeout: firstServerTimeout });
}

async function createFolder(page: import('@playwright/test').Page, name: string): Promise<void> {
  await page.getByRole('button', { name: 'New folder' }).click();
  const dialog = page.locator('[data-create-folder-dialog]');
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('Folder name').fill(name);
  await dialog.getByRole('button', { name: 'Create folder' }).click();
  await expect(folderRegion(page, name)).toBeVisible({ timeout: firstServerTimeout });
}

async function moveDeckToFolder(page: import('@playwright/test').Page, deckName: string, folderLabel: string): Promise<void> {
  await page.getByRole('button', { name: `Move ${deckName} to folder` }).click();
  const dialog = page.locator('[data-move-deck-dialog]');
  await expect(dialog).toBeVisible();
  await dialog.locator('select[data-move-deck-folder-select]').selectOption({ label: folderLabel });
  await dialog.getByRole('button', { name: 'Move deck' }).click();
  await expect(dialog).toBeHidden();
}

test.afterAll(async ({ browser }) => {
  // Remove the decks/folders this file created so later specs see a clean
  // deck list. All created decks are empty, so deleting them cannot orphan a
  // note. The protected Orphaned deck (asserted, never created with notes) is
  // intentionally left alone: it is not deletable by contract.
  const page = await browser.newPage();
  await page.goto('/');
  await page.evaluate(async () => {
    const headers = { 'X-Flashcards-Request': '1' };
    const decks = await (await fetch('/vocab/decks')).json();
    for (const deck of decks as Array<{ id: number; name: string }>) {
      if (deck.name.startsWith('FUI')) {
        await fetch(`/vocab/decks/${deck.id}`, { method: 'DELETE', headers });
      }
    }
    const folders = await (await fetch('/vocab/folders')).json();
    for (const folder of folders as Array<{ id: number; name: string }>) {
      if (folder.name.startsWith('FUI')) {
        await fetch(`/vocab/folders/${folder.id}`, { method: 'DELETE', headers });
      }
    }
  });
  await page.close();
});

test('creates a folder and reports authoritative confirmation', async ({ page }) => {
  await gotoServedProduct(page);
  await createFolder(page, FOLDER_ALPHA);
  await expect(page.locator('.notice.success')).toContainText(`Created folder “${FOLDER_ALPHA}”`);
  // The folder is served from the backend, not fabricated locally.
  const persisted = await page.evaluate(async () => {
    const folders = await (await fetch('/vocab/folders')).json() as Array<{ name: string }>;
    return folders.some((folder) => folder.name === 'FUIFolderAlpha');
  });
  expect(persisted).toBe(true);
});

test('empty folder stays visible after an authoritative refresh', async ({ page }) => {
  await gotoServedProduct(page);
  await createFolder(page, FOLDER_EMPTY);
  await expect(folderRegion(page, FOLDER_EMPTY)).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Your decks' })).toBeVisible({ timeout: firstServerTimeout });
  await expect(folderRegion(page, FOLDER_EMPTY)).toBeVisible();
  await expect(folderRegion(page, FOLDER_EMPTY)).toContainText('No decks in this folder yet.');
});

test('moves a normal deck into a folder, from folder to folder, and back out', async ({ page }) => {
  await gotoServedProduct(page);
  await createEmptyDeck(page, DECK_MOVE);

  // Unassigned -> folder A.
  await moveDeckToFolder(page, DECK_MOVE, FOLDER_ALPHA);
  await expect(folderRegion(page, FOLDER_ALPHA).getByRole('button', { name: `Open ${DECK_MOVE}` })).toBeVisible({ timeout: firstServerTimeout });
  await expect(unassignedRegion(page).getByRole('button', { name: `Open ${DECK_MOVE}` })).toHaveCount(0);

  // Folder A -> folder B.
  await createFolder(page, FOLDER_BETA);
  await moveDeckToFolder(page, DECK_MOVE, FOLDER_BETA);
  await expect(folderRegion(page, FOLDER_BETA).getByRole('button', { name: `Open ${DECK_MOVE}` })).toBeVisible({ timeout: firstServerTimeout });
  await expect(folderRegion(page, FOLDER_ALPHA).getByRole('button', { name: `Open ${DECK_MOVE}` })).toHaveCount(0);

  // Folder B -> Not in a folder.
  await moveDeckToFolder(page, DECK_MOVE, 'Not in a folder');
  await expect(unassignedRegion(page).getByRole('button', { name: `Open ${DECK_MOVE}` })).toBeVisible({ timeout: firstServerTimeout });
  await expect(folderRegion(page, FOLDER_BETA).getByRole('button', { name: `Open ${DECK_MOVE}` })).toHaveCount(0);
});

test('renames a folder while keeping its numeric id', async ({ page }) => {
  await gotoServedProduct(page);
  await createFolder(page, FOLDER_RENAME_FROM);
  const originalId = await page.evaluate(async () => {
    const folders = await (await fetch('/vocab/folders')).json() as Array<{ id: number; name: string }>;
    return folders.find((folder) => folder.name === 'FUIFolderRename')?.id ?? null;
  });
  expect(originalId).not.toBeNull();

  await folderRegion(page, FOLDER_RENAME_FROM).getByRole('button', { name: `Rename folder ${FOLDER_RENAME_FROM}` }).click();
  const dialog = page.locator('[data-rename-folder-dialog]');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel('Folder name')).toHaveValue(FOLDER_RENAME_FROM);
  await dialog.getByLabel('Folder name').fill(FOLDER_RENAME_TO);
  await dialog.getByRole('button', { name: 'Rename folder' }).click();

  await expect(folderRegion(page, FOLDER_RENAME_TO)).toBeVisible({ timeout: firstServerTimeout });
  await expect(folderRegion(page, FOLDER_RENAME_FROM)).toHaveCount(0);
  const renamedId = await page.evaluate(async () => {
    const folders = await (await fetch('/vocab/folders')).json() as Array<{ id: number; name: string }>;
    return folders.find((folder) => folder.name === 'FUIFolderRenamed')?.id ?? null;
  });
  expect(renamedId).toBe(originalId);
});

test('rejects a blank folder name and surfaces a duplicate-name conflict', async ({ page }) => {
  await gotoServedProduct(page);
  await createFolder(page, FOLDER_CONFLICT);

  await page.getByRole('button', { name: 'New folder' }).click();
  const dialog = page.locator('[data-create-folder-dialog]');
  await expect(dialog).toBeVisible();

  // Blank submit is rejected client-side and keeps the dialog open.
  await dialog.getByLabel('Folder name').press('Enter');
  await expect(dialog.getByRole('alert')).toContainText('Enter a folder name.');
  await expect(dialog).toBeVisible();

  // Duplicate name is surfaced from the server conflict, human-readable.
  await dialog.getByLabel('Folder name').fill(FOLDER_CONFLICT);
  await dialog.getByRole('button', { name: 'Create folder' }).click();
  await expect(dialog.getByRole('alert')).toContainText(/already exists/);
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
});

test('deletes a folder after confirmation', async ({ page }) => {
  await gotoServedProduct(page);
  await createFolder(page, FOLDER_DELETE);
  await folderRegion(page, FOLDER_DELETE).getByRole('button', { name: `Delete folder ${FOLDER_DELETE}` }).click();
  const dialog = page.locator('[data-delete-folder-dialog]');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Deleting the folder does not delete its decks');
  await dialog.getByRole('button', { name: 'Delete folder', exact: true }).click();
  await expect(folderRegion(page, FOLDER_DELETE)).toHaveCount(0);
  await expect(page.locator('.notice.success')).toContainText(`Deleted folder “${FOLDER_DELETE}”`);
});

test('deleting a folder preserves its decks and returns them to unassigned', async ({ page }) => {
  await gotoServedProduct(page);
  await createEmptyDeck(page, DECK_DELETE);
  await createFolder(page, FOLDER_DELETE_KEEP);
  await moveDeckToFolder(page, DECK_DELETE, FOLDER_DELETE_KEEP);
  await expect(folderRegion(page, FOLDER_DELETE_KEEP).getByRole('button', { name: `Open ${DECK_DELETE}` })).toBeVisible({ timeout: firstServerTimeout });

  await folderRegion(page, FOLDER_DELETE_KEEP).getByRole('button', { name: `Delete folder ${FOLDER_DELETE_KEEP}` }).click();
  await page.locator('[data-delete-folder-dialog]').getByRole('button', { name: 'Delete folder', exact: true }).click();

  await expect(folderRegion(page, FOLDER_DELETE_KEEP)).toHaveCount(0);
  // The deck survives, now unassigned.
  await expect(unassignedRegion(page).getByRole('button', { name: `Open ${DECK_DELETE}` })).toBeVisible({ timeout: firstServerTimeout });
  const stillOwned = await page.evaluate(async () => {
    const decks = await (await fetch('/vocab/decks')).json() as Array<{ name: string }>;
    return decks.some((deck) => deck.name === 'FUIDeckDelete');
  });
  expect(stillOwned).toBe(true);
});

test('the protected Orphaned deck exposes no folder-assignment control', async ({ page }) => {
  await gotoServedProduct(page);
  await page.evaluate(async () => {
    const decks = await (await fetch('/vocab/decks')).json() as Array<{ name: string }>;
    if (!decks.some((deck) => deck.name === 'Orphaned')) {
      await fetch('/vocab/decks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Flashcards-Request': '1' },
        body: JSON.stringify({ name: 'Orphaned' }),
      });
    }
  });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Your decks' })).toBeVisible({ timeout: firstServerTimeout });
  await expect(page.getByRole('button', { name: 'Open Orphaned' })).toBeVisible({ timeout: firstServerTimeout });
  await expect(page.getByRole('button', { name: 'Move Orphaned to folder' })).toHaveCount(0);
  // The server agrees the Orphaned deck is not assignable.
  const protectedPut = await page.evaluate(async () => {
    const decks = await (await fetch('/vocab/decks')).json() as Array<{ id: number; name: string }>;
    const orphaned = decks.find((deck) => deck.name === 'Orphaned');
    if (!orphaned) return 0;
    const response = await fetch(`/vocab/decks/${orphaned.id}/folder`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-Flashcards-Request': '1' },
      body: JSON.stringify({ folder_id: null }),
    });
    return response.status;
  });
  expect(protectedPut).toBe(409);
});

test('folder grouping persists across a full reload', async ({ page }) => {
  await gotoServedProduct(page);
  await createFolder(page, FOLDER_PERSIST);
  await createEmptyDeck(page, DECK_PERSIST);
  await moveDeckToFolder(page, DECK_PERSIST, FOLDER_PERSIST);
  await expect(folderRegion(page, FOLDER_PERSIST).getByRole('button', { name: `Open ${DECK_PERSIST}` })).toBeVisible({ timeout: firstServerTimeout });

  await page.reload();
  await expect(page.getByRole('heading', { name: 'Your decks' })).toBeVisible({ timeout: firstServerTimeout });
  await expect(folderRegion(page, FOLDER_PERSIST).getByRole('button', { name: `Open ${DECK_PERSIST}` })).toBeVisible({ timeout: firstServerTimeout });
  await expect(unassignedRegion(page).getByRole('button', { name: `Open ${DECK_PERSIST}` })).toHaveCount(0);
});

test('folder view and dialogs have no horizontal overflow at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await gotoServedProduct(page);
  await createFolder(page, FOLDER_LONG);

  await expect(folderRegion(page, FOLDER_LONG)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);

  // Create-folder dialog fits the viewport.
  await page.getByRole('button', { name: 'New folder' }).click();
  const dialog = page.locator('[data-create-folder-dialog]');
  await expect(dialog).toBeVisible();
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox?.width ?? 0).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
});
