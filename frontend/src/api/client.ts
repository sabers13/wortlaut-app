/**
 * Stateless, typed fetch client for /vocab endpoints.
 * Conforms to ADR-0001, ADR-0002 §4.1 / §5, ADR-0004 D47, and AGENTS rules R1, R6, R12.
 *
 * Requirements:
 * - Every non-GET request must send X-Flashcards-Request: 1
 * - JSON requests must send Content-Type: application/json
 * - Uses only the /vocab prefix
 * - Completely stateless and ephemeral: no scheduler, FSRS/rating mapping, due state,
 *   authoritative card cache, IndexedDB, or persistence.
 */

import { parseApiError } from './errors.ts';
import type {
  ActivateDictionaryRequest,
  ActivateDictionaryResponse,
  CaptureCardsRequest,
  CaptureCardsResponse,
  ChangeNoteSenseRequest,
  ChangeNoteSenseResponse,
  ClearOnlineCacheResponse,
  CreateDeckRequest,
  CreateDeckResponse,
  CreateFolderRequest,
  CreateNoteRequest,
  CreateNoteResponse,
  DeckCardsListingResponse,
  DeckFolderAssignment,
  DeckSummary,
  DeleteDeckResponse,
  DeleteFolderResponse,
  DeleteGlossResponse,
  DictionarySettingsInfo,
  Folder,
  HighlightRequest,
  HighlightResponse,
  ImportCsvRequest,
  ImportCsvResponse,
  InstallOfflineRequest,
  InstallOfflineResponse,
  LookupResponse,
  MeaningLanguage,
  MoveNoteBetweenDecksRequest,
  MoveNoteBetweenDecksResponse,
  NextCardResponse,
  RemoveNoteFromDeckResponse,
  RemoveOfflineRequest,
  RemoveOfflineResponse,
  RenameDeckRequest,
  RenameDeckResponse,
  RenameFolderRequest,
  RestoreOrphanedNoteRequest,
  RestoreOrphanedNoteResponse,
  RevertAudioResponse,
  ReviewCardResponse,
  SetDeckFolderRequest,
  SetGlossResponse,
  SetMeaningLanguagesRequest,
  SetMeaningLanguagesResponse,
  UploadAudioResponse,
  UseOfflineResponse,
  UseOnlineResponse,
} from './types.ts';

export interface VocabClientOptions {
  baseUrl?: string;
  fetch?: typeof globalThis.fetch;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'DELETE' | 'PUT' | 'PATCH';
  params?: Record<string, string | number | boolean | null | undefined>;
  body?: unknown;
  headers?: Record<string, string>;
  responseType?: 'json' | 'text' | 'blob';
}

export class VocabClient {
  readonly baseUrl: string;
  private readonly _fetch: typeof globalThis.fetch;

  constructor(options: VocabClientOptions = {}) {
    this.baseUrl = options.baseUrl ? options.baseUrl.replace(/\/+$/, '') : '';
    this._fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const method = options.method ?? 'GET';
    const isGet = method === 'GET';

    // Build URL with query parameters
    let url = `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`;
    if (options.params) {
      const searchParams = new URLSearchParams();
      for (const [key, value] of Object.entries(options.params)) {
        if (value !== undefined && value !== null) {
          searchParams.append(key, String(value));
        }
      }
      const qs = searchParams.toString();
      if (qs) {
        url += (url.includes('?') ? '&' : '?') + qs;
      }
    }

    const headers: Record<string, string> = { ...options.headers };

    // AGENTS R12 / ADR-0002: Custom header required on all non-GET requests
    if (!isGet) {
      headers['X-Flashcards-Request'] = '1';
    }

    let requestBody: BodyInit | undefined;

    if (options.body !== undefined && options.body !== null) {
      if (
        options.body instanceof FormData ||
        options.body instanceof Blob ||
        options.body instanceof ArrayBuffer ||
        ArrayBuffer.isView(options.body)
      ) {
        requestBody = options.body as BodyInit;
        // Do not set Content-Type for FormData; browser/fetch adds multipart boundary
      } else {
        headers['Content-Type'] = 'application/json';
        requestBody = JSON.stringify(options.body);
      }
    }

    const response = await this._fetch(url, {
      method,
      headers,
      body: requestBody,
    });

    if (!response.ok) {
      throw await parseApiError(response);
    }

    if (options.responseType === 'text') {
      return (await response.text()) as unknown as T;
    }
    if (options.responseType === 'blob') {
      return (await response.blob()) as unknown as T;
    }

    if (response.status === 204 || response.headers.get('content-length') === '0') {
      return undefined as unknown as T;
    }

    return (await response.json()) as T;
  }

  // -------------------------------------------------------------------------
  // Dictionary & Lookup Endpoints
  // -------------------------------------------------------------------------

  /**
   * Search lemmas and surface forms in the active dictionary (GET /vocab/lookup).
   */
  async lookup(query: string): Promise<LookupResponse> {
    return this.request<LookupResponse>('/vocab/lookup', {
      method: 'GET',
      params: { q: query },
    });
  }

  /**
   * Search lemmas and surface forms in the active dictionary (POST /vocab/lookup).
   */
  async lookupPost(query: string): Promise<LookupResponse> {
    return this.request<LookupResponse>('/vocab/lookup', {
      method: 'POST',
      body: { query },
    });
  }

  /**
   * Activate a replacement dictionary file (POST /vocab/dictionary/activate).
   */
  async activateDictionary(request: ActivateDictionaryRequest): Promise<ActivateDictionaryResponse> {
    return this.request<ActivateDictionaryResponse>('/vocab/dictionary/activate', {
      method: 'POST',
      body: request,
    });
  }

  // -------------------------------------------------------------------------
  // Capture Endpoints
  // -------------------------------------------------------------------------

  /**
   * Stage 1: Resolve span candidates and rank examples (POST /vocab/highlight).
   * Zero writes to user database.
   */
  async highlight(request: HighlightRequest): Promise<HighlightResponse> {
    return this.request<HighlightResponse>('/vocab/highlight', {
      method: 'POST',
      body: request,
    });
  }

  /**
   * Stage 2: Atomically persist selected candidate cards to deck (POST /vocab/cards).
   */
  async captureCards(request: CaptureCardsRequest): Promise<CaptureCardsResponse> {
    return this.request<CaptureCardsResponse>('/vocab/cards', {
      method: 'POST',
      body: request,
    });
  }

  /**
   * Batch word list import into deck (POST /vocab/import/csv).
   */
  async importCsv(request: ImportCsvRequest): Promise<ImportCsvResponse> {
    return this.request<ImportCsvResponse>('/vocab/import/csv', {
      method: 'POST',
      body: request,
    });
  }

  /**
   * Single note creation endpoint (POST /vocab/notes).
   */
  async createNote(request: CreateNoteRequest): Promise<CreateNoteResponse> {
    return this.request<CreateNoteResponse>('/vocab/notes', {
      method: 'POST',
      body: request,
    });
  }

  // -------------------------------------------------------------------------
  // Review & Study Endpoints
  // -------------------------------------------------------------------------

  /**
   * Fetch next due card for study, optionally filtered by deck (GET /vocab/cards/next).
   */
  async getNextCard(deckId?: number): Promise<NextCardResponse> {
    return this.request<NextCardResponse>('/vocab/cards/next', {
      method: 'GET',
      params: { deck_id: deckId },
    });
  }

  /**
   * Log a review rating for a card with raw confidence 1..5 (POST /vocab/cards/{card_id}/review).
   * Note: Client-supplied rating is forbidden by API; pass raw confidence only.
   */
  async reviewCard(cardId: number, confidence: number): Promise<ReviewCardResponse> {
    return this.request<ReviewCardResponse>(`/vocab/cards/${cardId}/review`, {
      method: 'POST',
      body: { confidence },
    });
  }

  // -------------------------------------------------------------------------
  // Gloss / Meaning Endpoints
  // -------------------------------------------------------------------------

  /**
   * Set user-authored meaning/gloss on a note (POST /vocab/notes/{note_id}/gloss).
   */
  async setGloss(
    noteId: number,
    language: MeaningLanguage,
    meaningText: string,
  ): Promise<SetGlossResponse> {
    return this.request<SetGlossResponse>(`/vocab/notes/${noteId}/gloss`, {
      method: 'POST',
      body: {
        language,
        meaning_text: meaningText,
      },
    });
  }

  /**
   * Delete user-authored meaning/gloss on a note (DELETE /vocab/notes/{note_id}/gloss).
   */
  async deleteGloss(noteId: number, language: MeaningLanguage): Promise<DeleteGlossResponse> {
    return this.request<DeleteGlossResponse>(`/vocab/notes/${noteId}/gloss`, {
      method: 'DELETE',
      params: { language },
    });
  }

  // -------------------------------------------------------------------------
  // Audio Endpoints
  // -------------------------------------------------------------------------

  /**
   * Upload custom pronunciation audio for a note (POST /vocab/notes/{note_id}/audio).
   */
  async uploadAudio(
    noteId: number,
    audioData: Blob | ArrayBuffer | Uint8Array | FormData,
    contentType?: string,
  ): Promise<UploadAudioResponse> {
    const headers: Record<string, string> = {};
    if (contentType && !(audioData instanceof FormData)) {
      headers['Content-Type'] = contentType;
    }
    return this.request<UploadAudioResponse>(`/vocab/notes/${noteId}/audio`, {
      method: 'POST',
      body: audioData,
      headers,
    });
  }

  /**
   * Revert custom pronunciation audio for a note (DELETE /vocab/notes/{note_id}/audio).
   */
  async revertAudio(noteId: number): Promise<RevertAudioResponse> {
    return this.request<RevertAudioResponse>(`/vocab/notes/${noteId}/audio`, {
      method: 'DELETE',
    });
  }

  /**
   * Build URL for audio endpoint (GET /vocab/audio/{audio_id}).
   */
  getAudioUrl(audioId: string | number): string {
    const cleanId = encodeURIComponent(String(audioId));
    return `${this.baseUrl}/vocab/audio/${cleanId}`;
  }

  /**
   * Fetch audio binary data (GET /vocab/audio/{audio_id}).
   */
  async fetchAudio(audioId: string | number): Promise<Blob> {
    const cleanId = encodeURIComponent(String(audioId));
    return this.request<Blob>(`/vocab/audio/${cleanId}`, {
      method: 'GET',
      responseType: 'blob',
    });
  }

  // -------------------------------------------------------------------------
  // Deck Management Endpoints
  // -------------------------------------------------------------------------

  /**
   * List all decks with card count, due count, and mastery % (GET /vocab/decks).
   */
  async getDecks(): Promise<DeckSummary[]> {
    return this.request<DeckSummary[]>('/vocab/decks', {
      method: 'GET',
    });
  }

  /**
   * Create a new deck (POST /vocab/decks).
   */
  async createDeck(name: string): Promise<CreateDeckResponse> {
    const req: CreateDeckRequest = { name };
    return this.request<CreateDeckResponse>('/vocab/decks', {
      method: 'POST',
      body: req,
    });
  }

  /**
   * Delete a deck (DELETE /vocab/decks/{deck_id}).
   * Orphaned notes move to Orphaned deck without cascading review history.
   */
  async deleteDeck(deckId: number): Promise<DeleteDeckResponse> {
    return this.request<DeleteDeckResponse>(`/vocab/decks/${deckId}`, {
      method: 'DELETE',
    });
  }

  /**
   * List all memberships in one deck for the management UI (GET /vocab/decks/{deck_id}/cards).
   */
  async getDeckCards(deckId: number): Promise<DeckCardsListingResponse> {
    return this.request<DeckCardsListingResponse>(`/vocab/decks/${deckId}/cards`, {
      method: 'GET',
    });
  }

  /**
   * Remove one note's membership from one deck (DELETE /vocab/decks/{deck_id}/notes/{note_id}).
   */
  async removeNoteFromDeck(
    deckId: number,
    noteId: number,
  ): Promise<RemoveNoteFromDeckResponse> {
    return this.request<RemoveNoteFromDeckResponse>(
      `/vocab/decks/${deckId}/notes/${noteId}`,
      { method: 'DELETE' },
    );
  }

  /**
   * Atomically swap one note's membership from source deck to destination deck
   * (PATCH /vocab/decks/{source_deck_id}/notes/{note_id}).
   */
  async moveNoteBetweenDecks(
    sourceDeckId: number,
    noteId: number,
    destinationDeckId: number,
  ): Promise<MoveNoteBetweenDecksResponse> {
    const req: MoveNoteBetweenDecksRequest = { deck_id: destinationDeckId };
    return this.request<MoveNoteBetweenDecksResponse>(
      `/vocab/decks/${sourceDeckId}/notes/${noteId}`,
      { method: 'PATCH', body: req },
    );
  }

  /**
   * Rename a deck (PATCH /vocab/decks/{deck_id}).
   */
  async renameDeck(deckId: number, name: string): Promise<RenameDeckResponse> {
    const req: RenameDeckRequest = { name };
    return this.request<RenameDeckResponse>(`/vocab/decks/${deckId}`, {
      method: 'PATCH',
      body: req,
    });
  }

  // -------------------------------------------------------------------------
  // Folder Endpoints (M4A persistent folders)
  // -------------------------------------------------------------------------

  /**
   * List persistent folder identities ordered by id (GET /vocab/folders).
   * Assignment is never read from here: ``deck.folder_id`` is authoritative.
   */
  async listFolders(): Promise<Folder[]> {
    return this.request<Folder[]>('/vocab/folders', {
      method: 'GET',
    });
  }

  /**
   * Create a persistent folder (POST /vocab/folders).
   * Duplicate exact names are rejected by the server with 409.
   */
  async createFolder(name: string): Promise<Folder> {
    const req: CreateFolderRequest = { name };
    return this.request<Folder>('/vocab/folders', {
      method: 'POST',
      body: req,
    });
  }

  /**
   * Rename a folder, preserving its numeric id (PATCH /vocab/folders/{folder_id}).
   */
  async renameFolder(folderId: number, name: string): Promise<Folder> {
    const req: RenameFolderRequest = { name };
    return this.request<Folder>(`/vocab/folders/${folderId}`, {
      method: 'PATCH',
      body: req,
    });
  }

  /**
   * Delete a folder; member decks become unassigned, never deleted
   * (DELETE /vocab/folders/{folder_id}).
   */
  async deleteFolder(folderId: number): Promise<DeleteFolderResponse> {
    return this.request<DeleteFolderResponse>(`/vocab/folders/${folderId}`, {
      method: 'DELETE',
    });
  }

  /**
   * Assign or clear one deck's folder (PUT /vocab/decks/{deck_id}/folder).
   * Pass ``null`` to unassign. Returns the authoritative deck identity.
   */
  async setDeckFolder(
    deckId: number,
    folderId: number | null,
  ): Promise<DeckFolderAssignment> {
    const req: SetDeckFolderRequest = { folder_id: folderId };
    return this.request<DeckFolderAssignment>(`/vocab/decks/${deckId}/folder`, {
      method: 'PUT',
      body: req,
    });
  }

  /**
   * Replace a note's selected meaning-language set
   * (PUT /vocab/notes/{note_id}/meaning-languages).
   */
  async setMeaningLanguages(
    noteId: number,
    languages: MeaningLanguage[],
  ): Promise<SetMeaningLanguagesResponse> {
    const req: SetMeaningLanguagesRequest = { languages };
    return this.request<SetMeaningLanguagesResponse>(
      `/vocab/notes/${noteId}/meaning-languages`,
      { method: 'PUT', body: req },
    );
  }

  /**
   * M5 — recover an orphaned note into a normal deck
   * (POST /vocab/notes/{note_id}/restore).
   * The endpoint never exposes dictionary_key.
   */
  async restoreOrphanedNote(
    noteId: number,
    destinationDeckId: number,
  ): Promise<RestoreOrphanedNoteResponse> {
    const req: RestoreOrphanedNoteRequest = { deck_id: destinationDeckId };
    return this.request<RestoreOrphanedNoteResponse>(
      `/vocab/notes/${noteId}/restore`,
      { method: 'POST', body: req },
    );
  }

  /**
   * M6 — rebind an existing note to a different selected dictionary sense
   * (PUT /vocab/notes/{note_id}/sense). The note's durable lemma ref is
   server authority; the browser only carries the asset_token from the
   active picker and the target sense_semantic_ref. The response never
   exposes ``dictionary_key``.
   */
  async changeNoteSense(
    noteId: number,
    request: ChangeNoteSenseRequest,
  ): Promise<ChangeNoteSenseResponse> {
    return this.request<ChangeNoteSenseResponse>(
      `/vocab/notes/${noteId}/sense`,
      { method: 'PUT', body: request },
    );
  }

  // -------------------------------------------------------------------------
  // Export Endpoints
  // -------------------------------------------------------------------------

  /**
   * Export deck or entire collection to Anki TSV format (GET /vocab/export/anki).
   */
  async exportAnki(deckId?: number): Promise<string> {
    return this.request<string>('/vocab/export/anki', {
      method: 'GET',
      params: { deck_id: deckId },
      responseType: 'text',
    });
  }

  /** Export one deck as a real Anki package (GET /vocab/export/apkg). */
  async exportApkg(deckId: number): Promise<Blob> {
    return this.request<Blob>('/vocab/export/apkg', {
      method: 'GET',
      params: { deck_id: deckId },
      responseType: 'blob',
    });
  }

  // -------------------------------------------------------------------------
  // Dictionary Mode (Slice 12 / ADR-0009)
  // -------------------------------------------------------------------------

  /** Read the chooser / runtime Settings status (GET /vocab/settings/dictionary). */
  async getDictionarySettings(): Promise<DictionarySettingsInfo> {
    return this.request<DictionarySettingsInfo>('/vocab/settings/dictionary', {
      method: 'GET',
    });
  }

  /** Run the hardened full Offline installer (POST /vocab/settings/dictionary/install-offline). */
  async installOffline(
    request: InstallOfflineRequest = {},
  ): Promise<InstallOfflineResponse> {
    return this.request<InstallOfflineResponse>(
      '/vocab/settings/dictionary/install-offline',
      { method: 'POST', body: request },
    );
  }

  /** Remove the managed canonical full Offline asset (POST /vocab/settings/dictionary/remove-offline). */
  async removeOffline(
    request: RemoveOfflineRequest = {},
  ): Promise<RemoveOfflineResponse> {
    return this.request<RemoveOfflineResponse>(
      '/vocab/settings/dictionary/remove-offline',
      { method: 'POST', body: request },
    );
  }

  /** Clear the Online provider's immutable shard cache (POST /vocab/settings/dictionary/clear-online-cache). */
  async clearOnlineCache(): Promise<ClearOnlineCacheResponse> {
    return this.request<ClearOnlineCacheResponse>(
      '/vocab/settings/dictionary/clear-online-cache',
      { method: 'POST', body: {} },
    );
  }

  /** Switch the session to Online for this process (POST /vocab/settings/dictionary/use-online). */
  async useOnline(): Promise<UseOnlineResponse> {
    return this.request<UseOnlineResponse>(
      '/vocab/settings/dictionary/use-online',
      { method: 'POST', body: {} },
    );
  }

  /** Switch the session to Offline for this process (POST /vocab/settings/dictionary/use-offline). */
  async useOffline(): Promise<UseOfflineResponse> {
    return this.request<UseOfflineResponse>(
      '/vocab/settings/dictionary/use-offline',
      { method: 'POST', body: {} },
    );
  }
}

/**
 * Factory function to create a new VocabClient instance.
 */
export function createVocabClient(options?: VocabClientOptions): VocabClient {
  return new VocabClient(options);
}
