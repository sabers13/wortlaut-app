import type { Candidate, CandidateSense, NoteStatus } from '../api/types.ts';

export function normalizeArticle(gender: string | null | undefined, pos: string): string | null {
  if (!gender || pos.trim().toUpperCase() !== 'NOUN') return null;
  const g = gender.trim().toLowerCase();
  if (['der', 'm', 'masculine', 'maskulin'].includes(g)) return 'der';
  if (['die', 'f', 'feminine', 'feminin'].includes(g)) return 'die';
  if (['das', 'n', 'neuter', 'neutral', 'sächlich'].includes(g)) return 'das';
  return null;
}

export function candidateHeadword(candidate: Candidate): string {
  const gender = candidate.gender ?? candidate.grammar?.gender ?? null;
  const article = normalizeArticle(gender, candidate.pos);
  if (article && !candidate.lemma.toLowerCase().startsWith(article.toLowerCase() + ' ')) {
    return `${article} ${candidate.lemma}`;
  }
  return candidate.lemma;
}

export function candidatePreferredEnglish(candidate: Candidate): string | null {
  if (candidate.senses) {
    for (const sense of candidate.senses) {
      if (sense.meanings) {
        for (const m of sense.meanings) {
          if (m.language === 'en' && m.text && m.text.trim()) {
            return m.text.trim();
          }
        }
      }
    }
  }
  return null;
}

export function senseDisplayLabel(sense: CandidateSense): string {
  const enMeaning = sense.meanings?.find((m) => m.language === 'en' && m.text?.trim())?.text?.trim();
  const deMeaning = sense.meanings?.find((m) => m.language === 'de' && m.text?.trim())?.text?.trim();
  return enMeaning || sense.gloss || deMeaning || `Meaning ${sense.ord}`;
}

export function canEditSelectedSense(status: NoteStatus, isOrphanedDeck: boolean): boolean {
  if (isOrphanedDeck) return false;
  return status === 'resolved' || status === 'needs_gloss';
}
