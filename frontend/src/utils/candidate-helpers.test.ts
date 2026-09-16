import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeArticle,
  candidateHeadword,
  candidatePreferredEnglish,
  senseDisplayLabel,
  canEditSelectedSense,
} from './candidate-helpers.ts';
import type { Candidate, CandidateSense } from '../api/types.ts';

describe('candidate-helpers', () => {
  describe('normalizeArticle', () => {
    it('normalizes masculine genders for nouns', () => {
      assert.equal(normalizeArticle('m', 'NOUN'), 'der');
      assert.equal(normalizeArticle('der', 'NOUN'), 'der');
      assert.equal(normalizeArticle('masculine', 'NOUN'), 'der');
      assert.equal(normalizeArticle('maskulin', 'noun'), 'der');
    });

    it('normalizes feminine genders for nouns', () => {
      assert.equal(normalizeArticle('f', 'NOUN'), 'die');
      assert.equal(normalizeArticle('die', 'NOUN'), 'die');
      assert.equal(normalizeArticle('feminine', 'NOUN'), 'die');
    });

    it('normalizes neuter genders for nouns', () => {
      assert.equal(normalizeArticle('n', 'NOUN'), 'das');
      assert.equal(normalizeArticle('das', 'NOUN'), 'das');
      assert.equal(normalizeArticle('neuter', 'NOUN'), 'das');
      assert.equal(normalizeArticle('sächlich', 'NOUN'), 'das');
    });

    it('returns null for non-nouns or missing genders', () => {
      assert.equal(normalizeArticle('m', 'VERB'), null);
      assert.equal(normalizeArticle(null, 'NOUN'), null);
      assert.equal(normalizeArticle(undefined, 'NOUN'), null);
      assert.equal(normalizeArticle('unknown', 'NOUN'), null);
    });
  });

  describe('candidateHeadword', () => {
    it('prepends article to noun headword if not already present', () => {
      const candidate: Candidate = {
        ref: 'cand:1',
        lemma: 'Frieden',
        lemma_semantic_ref: 'lr:1',
        pos: 'NOUN',
        gender: 'm',
        grammar: { gender: 'm' },
        status: 'resolved',
      };
      assert.equal(candidateHeadword(candidate), 'der Frieden');
    });

    it('does not duplicate article if headword already contains it', () => {
      const candidate: Candidate = {
        ref: 'cand:2',
        lemma: 'der Frieden',
        lemma_semantic_ref: 'lr:1',
        pos: 'NOUN',
        gender: 'm',
        grammar: { gender: 'm' },
        status: 'resolved',
      };
      assert.equal(candidateHeadword(candidate), 'der Frieden');
    });

    it('leaves verbs and non-gendered headwords unchanged', () => {
      const candidate: Candidate = {
        ref: 'cand:3',
        lemma: 'anrufen',
        lemma_semantic_ref: 'lr:2',
        pos: 'VERB',
        gender: null,
        status: 'resolved',
      };
      assert.equal(candidateHeadword(candidate), 'anrufen');
    });
  });

  describe('candidatePreferredEnglish', () => {
    it('extracts English meaning when present', () => {
      const candidate: Candidate = {
        ref: 'cand:4',
        lemma: 'Haus',
        lemma_semantic_ref: 'lr:1',
        pos: 'NOUN',
        gender: 'n',
        status: 'resolved',
        senses: [
          {
            sense_id: 10,
            sense_semantic_ref: 'sr:10',
            ref: 'sr:10',
            ord: 1,
            gloss: 'Gebäude',
            meanings: [
              {
                language: 'de',
                kind: 'definition',
                ord: 1,
                text: 'Gebäude zum Wohnen',
                source: 'wiktionary',
                license: 'CC-BY-SA',
              },
              {
                language: 'en',
                kind: 'gloss',
                ord: 1,
                text: 'house, building',
                source: 'wiktionary',
                license: 'CC-BY-SA',
              },
            ],
          },
        ],
      };
      assert.equal(candidatePreferredEnglish(candidate), 'house, building');
    });

    it('returns null when no English meaning is available', () => {
      const candidate: Candidate = {
        ref: 'cand:5',
        lemma: 'Haus',
        lemma_semantic_ref: 'lr:1',
        pos: 'NOUN',
        gender: 'n',
        status: 'resolved',
        senses: [
          {
            sense_id: 10,
            sense_semantic_ref: 'sr:10',
            ref: 'sr:10',
            ord: 1,
            gloss: 'Gebäude',
            meanings: [
              {
                language: 'de',
                kind: 'definition',
                ord: 1,
                text: 'Gebäude zum Wohnen',
                source: 'wiktionary',
                license: 'CC-BY-SA',
              },
            ],
          },
        ],
      };
      assert.equal(candidatePreferredEnglish(candidate), null);
    });
  });

  describe('senseDisplayLabel', () => {
    it('prefers English meaning', () => {
      const sense: CandidateSense = {
        sense_id: 1,
        sense_semantic_ref: 'sr:1',
        ref: 'sr:1',
        ord: 1,
        gloss: 'Gloss text',
        meanings: [
          {
            language: 'en',
            kind: 'gloss',
            ord: 1,
            text: 'peace',
            source: 'src',
            license: 'lic',
          },
          {
            language: 'de',
            kind: 'gloss',
            ord: 1,
            text: 'Zustand der Ruhe',
            source: 'src',
            license: 'lic',
          },
        ],
      };
      assert.equal(senseDisplayLabel(sense), 'peace');
    });

    it('falls back to gloss if no English meaning', () => {
      const sense: CandidateSense = {
        sense_id: 1,
        sense_semantic_ref: 'sr:1',
        ref: 'sr:1',
        ord: 1,
        gloss: 'Gloss text',
        meanings: [],
      };
      assert.equal(senseDisplayLabel(sense), 'Gloss text');
    });

    it('falls back to German meaning if no English meaning or gloss', () => {
      const sense: CandidateSense = {
        sense_id: 1,
        sense_semantic_ref: 'sr:1',
        ref: 'sr:1',
        ord: 1,
        gloss: '',
        meanings: [
          {
            language: 'de',
            kind: 'gloss',
            ord: 1,
            text: 'Zustand der Ruhe',
            source: 'src',
            license: 'lic',
          },
        ],
      };
      assert.equal(senseDisplayLabel(sense), 'Zustand der Ruhe');
    });

    it('falls back to Meaning N if all empty', () => {
      const sense: CandidateSense = {
        sense_id: 1,
        sense_semantic_ref: 'sr:1',
        ref: 'sr:1',
        ord: 3,
        gloss: '',
        meanings: [],
      };
      assert.equal(senseDisplayLabel(sense), 'Meaning 3');
    });
  });

  describe('canEditSelectedSense', () => {
    it('allows resolved notes on normal decks', () => {
      assert.equal(canEditSelectedSense('resolved', false), true);
    });

    it('allows needs_gloss notes on normal decks', () => {
      assert.equal(canEditSelectedSense('needs_gloss', false), true);
    });

    it('hides derived_compound rows on normal decks', () => {
      assert.equal(canEditSelectedSense('derived_compound', false), false);
    });

    it('hides orphaned rows', () => {
      assert.equal(canEditSelectedSense('orphaned', false), false);
      assert.equal(canEditSelectedSense('orphaned', true), false);
    });

    it('hides resolved rows on the Orphaned deck', () => {
      assert.equal(canEditSelectedSense('resolved', true), false);
    });

    it('hides needs_gloss rows on the Orphaned deck', () => {
      assert.equal(canEditSelectedSense('needs_gloss', true), false);
    });

    it('hides derived_compound rows on the Orphaned deck', () => {
      assert.equal(canEditSelectedSense('derived_compound', true), false);
    });
  });
});
