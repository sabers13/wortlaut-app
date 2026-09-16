import assert from 'node:assert';
import { describe, it } from 'node:test';
import { ApiError } from './errors.ts';
import { VocabClient, createVocabClient } from './client.ts';

interface CapturedRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

function createMockFetch(
  responder: (req: CapturedRequest) => { status: number; body?: unknown; headers?: Record<string, string>; text?: string; blob?: Blob },
  captured: CapturedRequest[],
): typeof fetch {
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    const method = (init?.method || 'GET').toUpperCase();
    const rawHeaders = init?.headers || {};
    const headers: Record<string, string> = {};

    if (rawHeaders instanceof Headers) {
      rawHeaders.forEach((v, k) => {
        headers[k] = v;
      });
    } else if (Array.isArray(rawHeaders)) {
      for (const [k, v] of rawHeaders) {
        headers[k] = v;
      }
    } else {
      for (const [k, v] of Object.entries(rawHeaders)) {
        headers[k] = v;
      }
    }

    let parsedBody: unknown = init?.body;
    if (typeof init?.body === 'string') {
      try {
        parsedBody = JSON.parse(init.body);
      } catch {
        parsedBody = init.body;
      }
    }

    const recorded: CapturedRequest = {
      url,
      method,
      headers,
      body: parsedBody,
    };
    captured.push(recorded);

    const result = responder(recorded);
    const resHeaders = new Headers(result.headers || {});

    if (result.blob) {
      return new Response(result.blob, {
        status: result.status,
        headers: resHeaders,
      });
    }

    if (result.text !== undefined) {
      return new Response(result.text, {
        status: result.status,
        headers: resHeaders,
      });
    }

    const jsonStr = result.body !== undefined ? JSON.stringify(result.body) : '';
    if (result.body !== undefined && !resHeaders.has('Content-Type')) {
      resHeaders.set('Content-Type', 'application/json');
    }

    return new Response(jsonStr || null, {
      status: result.status,
      headers: resHeaders,
    });
  };
}

describe('VocabClient', () => {
  it('instantiates cleanly via factory and constructor with default or custom options', () => {
    const client1 = new VocabClient();
    assert.strictEqual(client1.baseUrl, '');

    const client2 = createVocabClient({ baseUrl: 'http://127.0.0.1:8000/' });
    assert.strictEqual(client2.baseUrl, 'http://127.0.0.1:8000');
  });

  describe('Security guards & headers (AGENTS R12 / ADR-0002)', () => {
    it('does NOT send X-Flashcards-Request on GET requests', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(() => ({ status: 200, body: [] }), captured);
      const client = new VocabClient({ fetch: mockFetch });

      await client.getDecks();

      assert.strictEqual(captured.length, 1);
      assert.strictEqual(captured[0]?.method, 'GET');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], undefined);
      assert.strictEqual(captured[0]?.headers['Content-Type'], undefined);
    });

    it('sends X-Flashcards-Request: 1 and Content-Type: application/json on non-GET JSON requests', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(() => ({ status: 201, body: { id: 1, name: 'Test' } }), captured);
      const client = new VocabClient({ fetch: mockFetch });

      await client.createDeck('Test');

      assert.strictEqual(captured.length, 1);
      assert.strictEqual(captured[0]?.method, 'POST');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
      assert.deepStrictEqual(captured[0]?.body, { name: 'Test' });
    });

    it('sends X-Flashcards-Request: 1 on DELETE requests', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(() => ({ status: 200, body: { id: 42, deleted: true } }), captured);
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.deleteDeck(42);

      assert.strictEqual(res.deleted, true);
      assert.strictEqual(captured.length, 1);
      assert.strictEqual(captured[0]?.method, 'DELETE');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
    });
  });

  describe('Lookup & Dictionary endpoints', () => {
    it('executes GET /vocab/lookup with query param encoding', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { query: 'das Haus', asset_token: 'tok123', candidates: [] },
        }),
        captured,
      );
      const client = new VocabClient({ baseUrl: 'http://localhost:8000', fetch: mockFetch });

      const res = await client.lookup('das Haus');

      assert.strictEqual(res.query, 'das Haus');
      assert.strictEqual(res.asset_token, 'tok123');
      assert.strictEqual(captured[0]?.url, 'http://localhost:8000/vocab/lookup?q=das+Haus');
    });

    it('executes POST /vocab/lookup with query body', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { query: 'gehen', asset_token: 'tok456', candidates: [] },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.lookupPost('gehen');

      assert.strictEqual(res.asset_token, 'tok456');
      assert.strictEqual(captured[0]?.method, 'POST');
      assert.deepStrictEqual(captured[0]?.body, { query: 'gehen' });
    });

    it('executes POST /vocab/dictionary/activate', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { status: 'activated', version: 'v2', asset_token: 'new_token_789' },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.activateDictionary({ path: '/tmp/dict_v2.sqlite', version: 'v2' });

      assert.strictEqual(res.status, 'activated');
      assert.strictEqual(res.asset_token, 'new_token_789');
      assert.strictEqual(captured[0]?.url, '/vocab/dictionary/activate');
    });
  });

  describe('Capture workflows', () => {
    it('executes POST /vocab/highlight (Stage 1 candidate resolution)', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            asset_token: 'active_tok',
            candidates: [],
            capture_context: {
              sentence_text: 'Das ist ein Haus.',
              selected_span: { start: 12, end: 16 },
              lesson_label: 'L1',
              provenance: { char_start: 12, char_end: 16 },
            },
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.highlight({
        sentence_text: 'Das ist ein Haus.',
        selected_span: { start: 12, end: 16 },
        lesson_label: 'L1',
      });

      assert.strictEqual(res.asset_token, 'active_tok');
      assert.strictEqual(res.capture_context.lesson_label, 'L1');
      assert.strictEqual(captured[0]?.method, 'POST');
      assert.strictEqual(captured[0]?.url, '/vocab/highlight');
    });

    it('executes POST /vocab/cards (Stage 2 atomic card creation)', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 201,
          body: {
            notes: [{ note_id: 10, status: 'resolved', created: true, deck_id: 2 }],
            deck_id: 2,
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.captureCards({
        asset_token: 'active_tok',
        deck: 'Lesson 1',
        selections: [
          {
            lemma_semantic_ref: 'lemma:v1:haus_noun_das',
            sense_semantic_ref: 'sense:v1:haus_noun_das:1',
          },
        ],
      });

      assert.strictEqual(res.deck_id, 2);
      assert.strictEqual(res.notes[0]?.note_id, 10);
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.url, '/vocab/cards');
    });

    it('executes POST /vocab/import/csv for batch imports', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 201,
          body: {
            deck_id: 3,
            notes_created: 5,
            notes_reused: 1,
            total_words: 6,
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.importCsv({
        csv_text: 'Haus\nAuto\nBaum',
        deck_name: 'Wordlist',
      });

      assert.strictEqual(res.notes_created, 5);
      assert.strictEqual(res.total_words, 6);
      assert.strictEqual(captured[0]?.url, '/vocab/import/csv');
    });

    it('executes POST /vocab/notes for single note creation', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 201,
          body: {
            note_id: 15,
            status: 'resolved',
            meaning_languages: ['de', 'en'],
            deck_id: 1,
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.createNote({
        lemma_semantic_ref: 'lemma:v1:haus_noun_das',
        sense_semantic_ref: 'sense:v1:haus_noun_das:1',
        meaning_languages: ['de', 'en'],
      });

      assert.strictEqual(res.note_id, 15);
      assert.strictEqual(captured[0]?.url, '/vocab/notes');
    });
  });

  describe('Review & Study endpoints', () => {
    it('executes GET /vocab/cards/next with optional deck_id', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            card: {
              card_id: 101,
              note_id: 15,
              due_at: '2026-08-27T01:00:00Z',
              state: 0,
              front: {
                headword: 'Haus',
                display_headword: 'das Haus',
                pos: 'NOUN',
                gender: 'das',
                article: 'das',
                ipa: 'haʊ̯s',
                text: 'das Haus',
                audio_trigger: { available: true, lemma: 'Haus' },
              },
              back: {
                display_headword: 'das Haus',
                pos: 'NOUN',
                gender: 'das',
                article: 'das',
                ipa: 'haʊ̯s',
                plural: 'Häuser',
                text: 'das Haus',
                grammar: { pos: 'NOUN', lines: [] },
                meanings: [],
                examples: [],
              },
            },
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.getNextCard(5);

      assert.strictEqual(res.card?.card_id, 101);
      assert.strictEqual(captured[0]?.url, '/vocab/cards/next?deck_id=5');
    });

    it('executes POST /vocab/cards/{card_id}/review with raw confidence', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            card_id: 101,
            confidence: 4,
            rating: 3,
            due_at: '2026-08-28T01:00:00Z',
            interval_days: 1.0,
            state: 1,
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.reviewCard(101, 4);

      assert.strictEqual(res.rating, 3);
      assert.strictEqual(res.confidence, 4);
      assert.strictEqual(captured[0]?.url, '/vocab/cards/101/review');
      assert.deepStrictEqual(captured[0]?.body, { confidence: 4 });
    });
  });

  describe('Gloss & User meaning endpoints', () => {
    it('executes POST /vocab/notes/{note_id}/gloss', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { note_id: 20, language: 'en', meaning_text: 'house' },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.setGloss(20, 'en', 'house');

      assert.strictEqual(res.meaning_text, 'house');
      assert.strictEqual(captured[0]?.url, '/vocab/notes/20/gloss');
      assert.deepStrictEqual(captured[0]?.body, { language: 'en', meaning_text: 'house' });
    });

    it('executes DELETE /vocab/notes/{note_id}/gloss?language=...', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { note_id: 20, language: 'en', deleted: true },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.deleteGloss(20, 'en');

      assert.strictEqual(res.deleted, true);
      assert.strictEqual(captured[0]?.url, '/vocab/notes/20/gloss?language=en');
    });
  });

  describe('Audio endpoints', () => {
    it('generates audio URL via getAudioUrl', () => {
      const client = new VocabClient({ baseUrl: 'http://localhost:8000' });
      assert.strictEqual(client.getAudioUrl('Haus'), 'http://localhost:8000/vocab/audio/Haus');
      assert.strictEqual(client.getAudioUrl('custom:12'), 'http://localhost:8000/vocab/audio/custom%3A12');
    });

    it('fetches audio binary blob via fetchAudio', async () => {
      const mockBlob = new Blob(['audio_bytes'], { type: 'audio/wav' });
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          blob: mockBlob,
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const blob = await client.fetchAudio('Haus');

      assert.strictEqual(blob.type, 'audio/wav');
      assert.strictEqual(captured[0]?.url, '/vocab/audio/Haus');
    });

    it('uploads custom pronunciation audio via uploadAudio', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 201,
          body: {
            note_id: 5,
            media_filename: 'note_5.wav',
            sha256: 'abc123sha',
            byte_size: 1024,
            format: 'wav',
            source_type: 'uploaded',
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const dummyBytes = new Uint8Array([1, 2, 3, 4]);
      const res = await client.uploadAudio(5, dummyBytes, 'audio/wav');

      assert.strictEqual(res.media_filename, 'note_5.wav');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'audio/wav');
    });

    it('reverts custom pronunciation audio via revertAudio', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { note_id: 5, reverted: true },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.revertAudio(5);

      assert.strictEqual(res.reverted, true);
      assert.strictEqual(captured[0]?.method, 'DELETE');
      assert.strictEqual(captured[0]?.url, '/vocab/notes/5/audio');
    });
  });

  describe('Export Anki endpoint', () => {
    it('executes GET /vocab/export/anki with text response', async () => {
      const captured: CapturedRequest[] = [];
      const tsvContent = '#separator:tab\nFront\tBack\n';
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          text: tsvContent,
          headers: { 'Content-Type': 'text/tab-separated-values' },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const text = await client.exportAnki(1);

      assert.strictEqual(text, tsvContent);
      assert.strictEqual(captured[0]?.url, '/vocab/export/anki?deck_id=1');
    });

    it('executes deck-scoped GET /vocab/export/apkg with a blob response', async () => {
      const captured: CapturedRequest[] = [];
      const content = new Blob(['apkg'], { type: 'application/apkg' });
      const mockFetch = createMockFetch(
        () => ({ status: 200, blob: content }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const exported = await client.exportApkg(7);

      assert.strictEqual(await exported.text(), 'apkg');
      assert.strictEqual(captured[0]?.url, '/vocab/export/apkg?deck_id=7');
      assert.strictEqual(captured[0]?.method, 'GET');
    });
  });

  describe('Error handling & typed ApiError', () => {
    it('throws ApiError on 404 with parsed detail', async () => {
      const mockFetch = createMockFetch(() => ({ status: 404, body: { detail: 'Note 99 not found' } }), []);
      const client = new VocabClient({ fetch: mockFetch });

      await assert.rejects(
        async () => client.getNextCard(),
        (err: unknown) => {
          assert.ok(err instanceof ApiError);
          assert.strictEqual(err.status, 404);
          assert.strictEqual(err.isNotFound, true);
          assert.strictEqual(err.detail, 'Note 99 not found');
          return true;
        },
      );
    });

    it('throws ApiError on 409 Conflict with picker_token and active_token', async () => {
      const mockFetch = createMockFetch(
        () => ({
          status: 409,
          body: {
            detail: 'Asset token mismatch; dictionary has changed',
            picker_token: 'old_tok',
            active_token: 'new_tok',
          },
        }),
        [],
      );
      const client = new VocabClient({ fetch: mockFetch });

      await assert.rejects(
        async () =>
          client.captureCards({
            asset_token: 'old_tok',
            deck: 'D1',
            selections: [{ lemma_semantic_ref: 'lemma:v1:test' }],
          }),
        (err: unknown) => {
          assert.ok(err instanceof ApiError);
          assert.strictEqual(err.status, 409);
          assert.strictEqual(err.isConflict, true);
          assert.strictEqual(err.pickerToken, 'old_tok');
          assert.strictEqual(err.activeToken, 'new_tok');
          return true;
        },
      );
    });

    it('throws ApiError on 422 Unprocessable Entity with error list or string detail', async () => {
      const mockFetch = createMockFetch(
        () => ({
          status: 422,
          body: {
            detail: [{ loc: ['body', 'confidence'], msg: 'confidence must be 1..5', type: 'value_error' }],
          },
        }),
        [],
      );
      const client = new VocabClient({ fetch: mockFetch });

      await assert.rejects(
        async () => client.reviewCard(1, 99),
        (err: unknown) => {
          assert.ok(err instanceof ApiError);
          assert.strictEqual(err.status, 422);
          assert.strictEqual(err.isUnprocessable, true);
          assert.strictEqual(err.detail, 'confidence must be 1..5');
          return true;
        },
      );
    });

    it('throws ApiError on non-JSON error response', async () => {
      const mockFetch = createMockFetch(
        () => ({
          status: 500,
          text: 'Internal Server Error',
          headers: { 'Content-Type': 'text/plain' },
        }),
        [],
      );
      const client = new VocabClient({ fetch: mockFetch });

      await assert.rejects(
        async () => client.getDecks(),
        (err: unknown) => {
          assert.ok(err instanceof ApiError);
          assert.strictEqual(err.status, 500);
          assert.strictEqual(err.detail, 'Internal Server Error');
          return true;
        },
      );
    });
  });

  describe('Dictionary Settings endpoints (Slice 12 / ADR-0009)', () => {
    it('executes GET /vocab/settings/dictionary without custom headers', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            mode: 'unconfigured',
            canonical_offline_path: '/data/dictionary/dictionary.sqlite',
            canonical_offline_present: false,
            canonical_offline_valid: false,
            online_active: false,
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.getDictionarySettings();

      assert.strictEqual(res.mode, 'unconfigured');
      assert.strictEqual(captured[0]?.method, 'GET');
      assert.strictEqual(captured[0]?.url, '/vocab/settings/dictionary');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], undefined);
    });

    it('executes POST /vocab/settings/dictionary/install-offline with empty body and no source fields', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 202,
          body: {
            status: 'started',
            canonical_offline_path: '/data/dictionary/dictionary.sqlite',
            trusted_bytes: 945418240,
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.installOffline();

      assert.strictEqual(res.status, 'started');
      assert.strictEqual(captured[0]?.method, 'POST');
      assert.strictEqual(captured[0]?.url, '/vocab/settings/dictionary/install-offline');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
      // The browser never supplies a source: no URL, manifest, SHA, or filename.
      const body = captured[0]?.body as Record<string, unknown>;
      assert.ok(!('download_url' in body));
      assert.ok(!('manifest_bytes' in body));
      assert.ok(!('filename' in body));
      assert.ok(!('sha256' in body));
    });

    it('executes POST /vocab/settings/dictionary/remove-offline with empty body and no filename', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            status: 'removed',
            detail: 'offline_removal_succeeded',
            canonical_offline_path: '/data/dictionary/dictionary.sqlite',
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.removeOffline();

      assert.strictEqual(res.status, 'removed');
      assert.strictEqual(captured[0]?.method, 'POST');
      assert.strictEqual(captured[0]?.url, '/vocab/settings/dictionary/remove-offline');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
      const body = captured[0]?.body as Record<string, unknown>;
      assert.ok(!('filename' in body));
      assert.ok(!('path' in body));
    });

    it('executes POST /vocab/settings/dictionary/clear-online-cache', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({ status: 200, body: { status: 'cleared' } }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.clearOnlineCache();

      assert.strictEqual(res.status, 'cleared');
      assert.strictEqual(captured[0]?.method, 'POST');
      assert.strictEqual(captured[0]?.url, '/vocab/settings/dictionary/clear-online-cache');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
    });

    it('executes POST /vocab/settings/dictionary/use-online', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            status: 'online',
            online_info: {
              dataset_token: 'tok',
              asset_token: 'asset',
              cache_dir: '/data/online-cache',
            },
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.useOnline();

      assert.strictEqual(res.status, 'online');
      assert.strictEqual(captured[0]?.method, 'POST');
      assert.strictEqual(captured[0]?.url, '/vocab/settings/dictionary/use-online');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
      // The browser never configures an Online source: no manifest, URL, or host.
      const body = captured[0]?.body as Record<string, unknown>;
      assert.ok(!('manifest' in body));
      assert.ok(!('download_url' in body));
      assert.ok(!('url' in body));
      assert.ok(!('host' in body));
    });

    it('executes POST /vocab/settings/dictionary/use-offline', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            status: 'offline',
            asset_token: 'tok',
            canonical_offline_path: '/data/dictionary/dictionary.sqlite',
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.useOffline();

      assert.strictEqual(res.status, 'offline');
      assert.strictEqual(captured[0]?.method, 'POST');
      assert.strictEqual(captured[0]?.url, '/vocab/settings/dictionary/use-offline');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
    });
  });

  describe('M1 — Deck / Card management client', () => {
    it('executes GET /vocab/decks/{deck_id}/cards without custom headers', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            deck: { id: 7, name: 'Lesson 1', created_at: '2026-08-24T12:00:00Z' },
            cards: [
              {
                card_id: 12,
                note_id: 34,
                lemma_semantic_ref: 'lemma:v1:frieden_noun_der',
                sense_semantic_ref: 'sense:v1:frieden_noun_der:1',
                status: 'resolved',
                headword: 'Frieden',
                pos: 'NOUN',
                gender: 'der',
                due_at: '2026-08-25T01:00:00Z',
                state: 1,
                review_count: 3,
                last_confidence: 4,
                selected_languages: ['de', 'en'],
                user_meanings: { en: 'peace' },
                has_custom_audio: false,
                other_deck_ids: [9],
              },
            ],
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.getDeckCards(7);

      assert.strictEqual(res.cards.length, 1);
      assert.strictEqual(res.cards[0]?.headword, 'Frieden');
      assert.strictEqual(captured[0]?.method, 'GET');
      assert.strictEqual(captured[0]?.url, '/vocab/decks/7/cards');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], undefined);
      assert.strictEqual(captured[0]?.headers['Content-Type'], undefined);
    });

    it('executes DELETE /vocab/decks/{deck_id}/notes/{note_id} with security header', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { note_id: 34, deck_id: 7, removed: true, orphaned: false },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.removeNoteFromDeck(7, 34);

      assert.strictEqual(res.removed, true);
      assert.strictEqual(res.orphaned, false);
      assert.strictEqual(captured[0]?.method, 'DELETE');
      assert.strictEqual(captured[0]?.url, '/vocab/decks/7/notes/34');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], undefined);
    });

    it('executes PATCH /vocab/decks/{source_deck_id}/notes/{note_id} with destination body', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            note_id: 34,
            card_id: 12,
            from_deck_id: 7,
            to_deck_id: 9,
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.moveNoteBetweenDecks(7, 34, 9);

      assert.strictEqual(res.from_deck_id, 7);
      assert.strictEqual(res.to_deck_id, 9);
      assert.strictEqual(captured[0]?.method, 'PATCH');
      assert.strictEqual(captured[0]?.url, '/vocab/decks/7/notes/34');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
      assert.deepStrictEqual(captured[0]?.body, { deck_id: 9 });
    });

    it('executes PATCH /vocab/decks/{deck_id} for rename with security headers', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { id: 7, name: 'Lesson Renamed', created_at: '2026-08-24T12:00:00Z' },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.renameDeck(7, 'Lesson Renamed');

      assert.strictEqual(res.name, 'Lesson Renamed');
      assert.strictEqual(captured[0]?.method, 'PATCH');
      assert.strictEqual(captured[0]?.url, '/vocab/decks/7');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
      assert.deepStrictEqual(captured[0]?.body, { name: 'Lesson Renamed' });
    });

    it('executes PUT /vocab/notes/{note_id}/meaning-languages with language array', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { note_id: 34, languages: ['en'] },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.setMeaningLanguages(34, ['en']);

      assert.deepStrictEqual(res.languages, ['en']);
      assert.strictEqual(captured[0]?.method, 'PUT');
      assert.strictEqual(captured[0]?.url, '/vocab/notes/34/meaning-languages');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
      assert.deepStrictEqual(captured[0]?.body, { languages: ['en'] });
    });

    it('executes POST /vocab/notes/{note_id}/restore with destination deck body', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            note_id: 34,
            card_id: 12,
            from_deck_id: 7,
            to_deck_id: 9,
            previous_status: 'orphaned',
            restored_status: 'resolved',
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.restoreOrphanedNote(34, 9);

      assert.strictEqual(res.note_id, 34);
      assert.strictEqual(res.card_id, 12);
      assert.strictEqual(res.from_deck_id, 7);
      assert.strictEqual(res.to_deck_id, 9);
      assert.strictEqual(res.previous_status, 'orphaned');
      assert.strictEqual(res.restored_status, 'resolved');
      assert.strictEqual(captured[0]?.method, 'POST');
      assert.strictEqual(captured[0]?.url, '/vocab/notes/34/restore');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
      assert.deepStrictEqual(captured[0]?.body, { deck_id: 9 });
    });
  });

  describe('M6 — Selected-sense editing client', () => {
    it('executes PUT /vocab/notes/{note_id}/sense with asset_token + sense_semantic_ref', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            note_id: 34,
            card_id: 12,
            lemma_semantic_ref: 'lemma:v1:haus',
            sense_semantic_ref: 'sense:v1:haus:1',
            status: 'resolved',
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.changeNoteSense(34, {
        asset_token: 'asset-token-abc',
        sense_semantic_ref: 'sense:v1:haus:1',
      });

      assert.strictEqual(res.note_id, 34);
      assert.strictEqual(res.card_id, 12);
      assert.strictEqual(res.lemma_semantic_ref, 'lemma:v1:haus');
      assert.strictEqual(res.sense_semantic_ref, 'sense:v1:haus:1');
      assert.strictEqual(res.status, 'resolved');
      assert.strictEqual(captured[0]?.method, 'PUT');
      assert.strictEqual(captured[0]?.url, '/vocab/notes/34/sense');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
      assert.deepStrictEqual(captured[0]?.body, {
        asset_token: 'asset-token-abc',
        sense_semantic_ref: 'sense:v1:haus:1',
      });
    });

    it('does not accept lemma_semantic_ref or numeric sense_id from the browser', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: {
            note_id: 34,
            card_id: 12,
            lemma_semantic_ref: 'lemma:v1:haus',
            sense_semantic_ref: 'sense:v1:haus:1',
            status: 'resolved',
          },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      // The browser carries only the picker asset_token and the
      // target sense_semantic_ref. lemma_semantic_ref and numeric
      // ids are never supplied by the client; the note's durable
      // lemma ref is server authority (same-lemma enforcement).
      await client.changeNoteSense(34, {
        asset_token: 'asset-token-abc',
        sense_semantic_ref: 'sense:v1:haus:1',
      });

      const body = captured[0]?.body as Record<string, unknown>;
      assert.strictEqual(body.lemma_semantic_ref, undefined);
      assert.strictEqual(body.sense_id, undefined);
      assert.strictEqual(body.lemma_id, undefined);
      assert.strictEqual(body.dictionary_key, undefined);
    });
  });

  describe('M4B — Persistent folder client', () => {
    it('executes GET /vocab/folders without custom headers or body', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: [
            { id: 1, name: 'A2 Course', created_at: '2026-09-01T00:00:00Z' },
            { id: 2, name: 'Personal', created_at: '2026-09-02T00:00:00Z' },
          ],
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.listFolders();

      assert.strictEqual(res.length, 2);
      assert.strictEqual(res[0]?.id, 1);
      assert.strictEqual(res[1]?.name, 'Personal');
      assert.strictEqual(captured[0]?.method, 'GET');
      assert.strictEqual(captured[0]?.url, '/vocab/folders');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], undefined);
    });

    it('executes POST /vocab/folders with a numeric-id folder response', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 201,
          body: { id: 7, name: 'Trips', created_at: '2026-09-03T00:00:00Z' },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.createFolder('Trips');

      assert.strictEqual(res.id, 7);
      assert.strictEqual(captured[0]?.method, 'POST');
      assert.strictEqual(captured[0]?.url, '/vocab/folders');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
      assert.deepStrictEqual(captured[0]?.body, { name: 'Trips' });
    });

    it('executes PATCH /vocab/folders/{folder_id}', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { id: 7, name: 'Renamed', created_at: '2026-09-03T00:00:00Z' },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.renameFolder(7, 'Renamed');

      assert.strictEqual(res.name, 'Renamed');
      assert.strictEqual(res.id, 7);
      assert.strictEqual(captured[0]?.method, 'PATCH');
      assert.strictEqual(captured[0]?.url, '/vocab/folders/7');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.strictEqual(captured[0]?.headers['Content-Type'], 'application/json');
      assert.deepStrictEqual(captured[0]?.body, { name: 'Renamed' });
    });

    it('executes DELETE /vocab/folders/{folder_id} with the security header', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({ status: 200, body: { id: 7, deleted: true } }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.deleteFolder(7);

      assert.strictEqual(res.deleted, true);
      assert.strictEqual(captured[0]?.method, 'DELETE');
      assert.strictEqual(captured[0]?.url, '/vocab/folders/7');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
    });

    it('executes PUT /vocab/decks/{deck_id}/folder with a numeric folder id', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { id: 12, name: 'Lesson 1', created_at: '2026-09-01T00:00:00Z', folder_id: 3 },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.setDeckFolder(12, 3);

      assert.strictEqual(res.folder_id, 3);
      assert.strictEqual(captured[0]?.method, 'PUT');
      assert.strictEqual(captured[0]?.url, '/vocab/decks/12/folder');
      assert.strictEqual(captured[0]?.headers['X-Flashcards-Request'], '1');
      assert.deepStrictEqual(captured[0]?.body, { folder_id: 3 });
    });

    it('executes PUT /vocab/decks/{deck_id}/folder with folder_id null to unassign', async () => {
      const captured: CapturedRequest[] = [];
      const mockFetch = createMockFetch(
        () => ({
          status: 200,
          body: { id: 12, name: 'Lesson 1', created_at: '2026-09-01T00:00:00Z', folder_id: null },
        }),
        captured,
      );
      const client = new VocabClient({ fetch: mockFetch });

      const res = await client.setDeckFolder(12, null);

      assert.strictEqual(res.folder_id, null);
      assert.strictEqual(captured[0]?.method, 'PUT');
      assert.strictEqual(captured[0]?.url, '/vocab/decks/12/folder');
      assert.deepStrictEqual(captured[0]?.body, { folder_id: null });
    });
  });
});
