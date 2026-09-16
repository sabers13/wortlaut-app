import { LitElement, css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { createVocabClient } from './api/client.ts';
import { ApiError } from './api/errors.ts';
import type {
  Candidate,
  CandidateSense,
  CaptureContext,
  DeckCardRow,
  DeckSummary,
  DictionarySettingsInfo,
  Folder,
  MeaningLanguage,
  NextCardData,
  RenderedMeaning,
} from './api/types.ts';
import {
  extraInfoOpenOnCardLoad,
  extraInfoOpenOnPreferenceChange,
  extraInfoOpenOnReveal,
  readAlwaysShowExtraInfo,
  writeAlwaysShowExtraInfo,
} from './study/extra-info-preference.ts';

type DeckListStatus = 'loading' | 'ready' | 'error';
type FolderListStatus = 'loading' | 'ready' | 'error';
type FolderDialogStatus = 'idle' | 'saving';
type LookupStatus = 'idle' | 'loading' | 'ready' | 'error';
type CaptureStatus = 'idle' | 'loading' | 'ready' | 'error';
type AppView = 'decks' | 'deck' | 'study' | 'chooser' | 'settings';
type StudyStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'error';
type AudioStatus = 'idle' | 'loading' | 'playing' | 'unavailable';
type RecordingStatus = 'idle' | 'recording' | 'ready' | 'saving' | 'save-error';
type SessionMode = 'offline' | 'online' | 'unconfigured';
type DictionarySettingsStatus = 'loading' | 'ready' | 'error';
type DictionarySettingsAction = 'idle' | 'installing' | 'removing' | 'clearing' | 'switching-online' | 'switching-offline';
type DeckTab = 'overview' | 'cards' | 'add' | 'import';
type DeckCardsStatus = 'idle' | 'loading' | 'ready' | 'error';
type EditDialogStatus = 'idle' | 'saving-languages' | 'saving-gloss' | 'saved' | 'error';
type SenseDialogStatus = 'idle' | 'saving' | 'saved' | 'error';
type EditGlossBusy = Record<MeaningLanguage, boolean>;
type MoveDialogStatus = 'idle' | 'saving';
type RemoveDialogStatus = 'idle' | 'saving';
type RenameDialogStatus = 'idle' | 'saving';
type RestoreDialogStatus = 'idle' | 'saving';
const ORPHANED_DECK_NAME = 'Orphaned';

const confidenceLabels = [
  ['1', 'Not at all'],
  ['2', 'Barely'],
  ['3', 'With effort'],
  ['4', 'Comfortably'],
  ['5', 'Without doubt'],
] as const;

interface CaptureCandidateSelection {
  candidate: Candidate;
  senseRef: string | null;
}

const vocabClient = createVocabClient();

/**
 * ``window.localStorage`` itself can throw on access (private browsing in
 * some browsers, storage disabled by policy), not just its methods, so the
 * accessor is wrapped too. Returns ``null`` when storage is unavailable;
 * every caller already treats that the same as "nothing persisted".
 */
function localStorageOrNull(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

import {
  normalizeArticle,
  candidateHeadword,
  candidatePreferredEnglish,
  senseDisplayLabel,
  canEditSelectedSense,
} from './utils/candidate-helpers.ts';
export {
  normalizeArticle,
  candidateHeadword,
  candidatePreferredEnglish,
  senseDisplayLabel,
  canEditSelectedSense,
};

/**
 * Standalone navigation shell for server-authoritative decks.
 *
 * This element deliberately retains only transient display and form state. Deck
 * mutations must be followed by a successful fresh GET /vocab/decks before the
 * UI makes any success or selection claim about the server's deck data.
 */
@customElement('flashcard-app')
export class FlashcardApp extends LitElement {
  static styles = css`
    :host { display: block; min-height: 100vh; color: var(--fg); background: var(--bg); font-family: var(--font-sans); }
    .shell { max-width: 1280px; margin: 0 auto; padding: var(--space-48) var(--space-16); }
    header { display: flex; align-items: end; justify-content: space-between; gap: var(--space-16); margin-bottom: var(--space-32); }
    h1, h2, h3, p { margin-top: 0; }
    h1, h2, h3 { font-family: var(--font-display); font-weight: 600; letter-spacing: -.02em; }
    h1 { margin-bottom: var(--space-4); font-size: clamp(2rem, 5vw, 3.25rem); }
    h2 { margin-bottom: var(--space-8); font-size: 1.75rem; }
    .subtitle, .muted, .result, .caption { color: var(--muted); }
    .caption { font-family: var(--font-mono); font-size: .75rem; letter-spacing: .04em; text-transform: uppercase; }
    .panel { padding: var(--space-32); border: 1px solid var(--border); border-radius: var(--radius-panel); background: var(--surface); box-shadow: var(--shadow-sm); }
    .toolbar, .deck-heading, .form-row, .actions { display: flex; gap: var(--space-12); align-items: center; }
    .toolbar, .deck-heading { justify-content: space-between; }
    .form-row { margin: var(--space-24) 0; align-items: end; }
    label { display: grid; gap: var(--space-4); flex: 1; font-size: .875rem; font-weight: 600; }
    input, select, textarea { width: 100%; padding: 10px var(--space-12); color: var(--fg); background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-control); font: inherit; }
    textarea { min-height: 8rem; resize: vertical; }
    button { min-height: 2.6rem; padding: var(--space-8) var(--space-16); color: var(--fg); border: 1px solid var(--border); border-radius: var(--radius-control); background: var(--surface); cursor: pointer; font: inherit; font-weight: 600; }
    button:hover:not(:disabled) { border-color: var(--accent); }
    button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 3px solid color-mix(in oklch, var(--accent), white 65%); outline-offset: 2px; }
    button.primary { color: white; border-color: var(--accent); background: var(--accent); }
    button.primary:hover:not(:disabled) { filter: brightness(.94); }
    button.danger { color: var(--danger); }
    button:disabled { cursor: not-allowed; opacity: .55; }
    .notice, .capture-state { margin-bottom: var(--space-16); padding: var(--space-12); border: 1px solid var(--border); border-radius: var(--radius-control); overflow-wrap: anywhere; }
    .notice.error, .capture-state.error { color: var(--danger); background: color-mix(in oklch, var(--danger), white 94%); }
    .notice.success { color: var(--success); background: color-mix(in oklch, var(--success), white 94%); }
    .capture-state.warning { border-color: var(--warning); background: color-mix(in oklch, var(--warning), white 91%); }
    .capture-state p { margin-bottom: var(--space-8); }
    .capture-state p:last-child { margin-bottom: 0; }
    .deck-list { display: grid; gap: var(--space-12); padding: 0; margin: var(--space-24) 0 0; list-style: none; }
    .deck { display: grid; grid-template-columns: 1fr auto; gap: var(--space-16); align-items: center; padding: var(--space-16); border: 1px solid var(--border); border-radius: var(--radius-panel); }
    .deck-row-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); align-items: center; justify-content: flex-end; }
    .folder-group { margin-top: var(--space-24); }
    .folder-heading-row { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-8); margin-bottom: var(--space-8); padding-bottom: var(--space-8); border-bottom: 1px solid var(--border); }
    .folder-heading { margin: 0; font-size: 1.05rem; overflow-wrap: anywhere; }
    .folder-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); }
    .folder-empty { margin: var(--space-8) 0 0; }
    .deck-open { min-height: 0; padding: 0; border: 0; background: transparent; text-align: left; }
    .deck-open:hover:not(:disabled) { background: transparent; text-decoration: underline; }
    .deck-name { display: block; font-family: var(--font-display); font-size: 1.2rem; font-weight: 600; }
    .deck-stats { display: block; margin-top: var(--space-4); color: var(--muted); font-family: var(--font-mono); font-size: .75rem; }
    .empty, .loading { padding: var(--space-48) 0; text-align: center; color: var(--muted); }
    .confirm { border-color: var(--warning); background: color-mix(in oklch, var(--warning), white 92%); }
    .workflow-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr)); gap: var(--space-32); margin-top: var(--space-32); }
    .workflow { padding-top: var(--space-24); border-top: 1px solid var(--border); }
    .capture-workflow { grid-column: 1 / -1; }
    .workflow h3 { margin: 0 0 var(--space-8); font-size: 1.4rem; }
    .workflow form { display: grid; gap: var(--space-12); }
    .choice-list, .candidate-list { display: grid; gap: var(--space-8); margin: 0; padding: 0; list-style: none; }
    .choice, .candidate-choice { display: flex; align-items: center; gap: var(--space-8); font-weight: 500; }
    .choice input, .candidate-choice input { width: auto; }
    .candidate { width: 100%; min-height: 0; text-align: left; }
    .candidate.selected { border-color: var(--accent); background: color-mix(in oklch, var(--accent), white 94%); }
    .candidate small { display: block; margin-top: var(--space-4); color: var(--muted); }
    .candidate-header { display: flex; align-items: baseline; gap: var(--space-8); flex-wrap: wrap; }
    .candidate-headword { font-family: var(--font-display); font-size: 1.15rem; font-weight: 600; }
    .candidate-gloss { margin-top: var(--space-4); font-size: 0.95rem; color: var(--fg); font-weight: 500; }
    .save-success-banner { margin-bottom: var(--space-16); padding: var(--space-16); border: 1px solid var(--success); border-radius: var(--radius-panel); background: color-mix(in oklch, var(--success), white 94%); display: grid; gap: var(--space-12); }
    .save-success-banner p { margin: 0; font-weight: 600; color: var(--success); }
    .save-success-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); }
    .primary-meaning { font-size: 1.4rem; font-weight: 600; }
    .compact-grammar { margin: 0; font-size: 0.95rem; color: var(--muted); }
    .study-complete-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); justify-content: center; margin-top: var(--space-16); }
    .front-audio { margin-bottom: var(--space-8); }
    .selection { margin: 0; padding: var(--space-16); border: 1px solid var(--border); border-radius: var(--radius-panel); }
    .selection legend { padding: 0 var(--space-4); font-family: var(--font-display); font-weight: 600; }
    .selection-preview { margin: 0; padding: var(--space-8) var(--space-12); border-left: 3px solid var(--accent); color: var(--muted); }
    .capture-picker { margin-top: var(--space-24); }
    .capture-candidate { padding: var(--space-12); border: 1px solid var(--border); border-radius: var(--radius-control); }
    .capture-candidate.chosen { border-color: var(--accent); }
    .lemma { font-family: var(--font-display); font-size: 1.25rem; }
    .sense-choices { display: grid; gap: var(--space-8); margin: var(--space-12) 0 0 var(--space-24); border: 0; padding: 0; }
    .sense-choices legend { margin-bottom: var(--space-4); color: var(--muted); font-size: .8rem; }
    .optional-meanings { border: 1px solid var(--border); border-radius: var(--radius-control); padding: var(--space-8) var(--space-12); }
    .optional-meanings > summary { cursor: pointer; font-size: .875rem; font-weight: 600; color: var(--muted); }
    .optional-meanings[open] > summary { margin-bottom: var(--space-8); }
    .optional-meanings label { margin-top: var(--space-8); }
    .import-busy { display: grid; gap: var(--space-8); align-items: center; }
    .import-busy p { margin: 0; color: var(--muted); }
    .import-progress { width: 100%; height: .6rem; }
    .create-actions { flex-wrap: wrap; }
    .disabled-explanation { margin: 0; color: var(--muted); font-size: .875rem; }
    .primary-nav, .bottom-nav { display: flex; gap: var(--space-8); }
    .primary-nav button[aria-current="page"], .bottom-nav button[aria-current="page"] { color: white; border-color: var(--accent); background: var(--accent); }
    .study { max-width: 760px; margin: 0 auto; }
    .study-heading { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--space-12) var(--space-16); margin-bottom: var(--space-16); }
    .study-heading h2 { margin: 0; }
    .study-back { min-height: 0; margin-bottom: var(--space-4); padding: 0; border: 0; background: transparent; color: var(--muted); font-size: .875rem; overflow-wrap: anywhere; }
    .study-back:hover:not(:disabled) { background: transparent; text-decoration: underline; }
    .card-stage { min-height: 25rem; display: grid; align-content: center; gap: var(--space-24); padding: clamp(var(--space-24), 7vw, var(--space-72)); border: 1px solid var(--border); border-radius: var(--radius-dialog); background: var(--surface); box-shadow: var(--shadow-sm); }
    .card-stage:focus { outline: none; }
    .card-stage:focus-visible { outline: 3px solid color-mix(in oklch, var(--accent), white 65%); outline-offset: 3px; }
    .card-side { display: grid; gap: var(--space-16); }
    .front-label, .meaning-label { color: var(--muted); font-family: var(--font-mono); font-size: .75rem; letter-spacing: .08em; text-transform: uppercase; }
    .study-lemma { margin: 0; font-family: var(--font-display); font-size: clamp(3rem, 10vw, 6rem); font-weight: 600; line-height: .98; letter-spacing: -.045em; overflow-wrap: anywhere; }
    .study-meta { margin: 0; color: var(--muted); font-family: var(--font-mono); font-size: .82rem; }
    .reveal-action { justify-self: start; }
    .answer-rule { border: 0; border-top: 1px solid var(--border); width: 100%; margin: var(--space-8) 0; }
    .meaning { margin: 0; font-size: 1.25rem; }
    .example { margin: 0; padding-left: var(--space-16); border-left: 3px solid var(--accent); font-size: 1.05rem; }
    .example-translation { display: block; margin-top: var(--space-4); color: var(--muted); font-size: .9rem; }
    .pronunciation-simple { display: grid; gap: var(--space-8); }
    .extra-info-row { display: flex; flex-wrap: wrap; gap: var(--space-12); align-items: center; }
    .always-extra-toggle { display: flex; flex-direction: row; align-items: center; gap: var(--space-8); font-size: .875rem; font-weight: 500; }
    .always-extra-toggle input { width: auto; }
    .extra-info { display: grid; gap: var(--space-16); border-top: 1px solid var(--border); padding-top: var(--space-16); }
    .detail-block { margin-top: 0; }
    .detail-block p, .detail-block ul { margin-bottom: 0; }
    .detail-block ul { padding-left: var(--space-24); }
    .confidence-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: var(--space-8); }
    .confidence { min-height: 5rem; display: grid; align-content: center; justify-items: start; gap: var(--space-4); border-top: 4px solid var(--border); text-align: left; }
    .confidence:nth-child(1) { border-top-color: var(--danger); }
    .confidence:nth-child(2) { border-top-color: var(--warning); }
    .confidence:nth-child(3) { border-top-color: oklch(65% .1 95); }
    .confidence:nth-child(4) { border-top-color: oklch(62% .12 155); }
    .confidence:nth-child(5) { border-top-color: var(--accent); }
    .confidence-number { font-family: var(--font-mono); font-size: 1.1rem; }
    .confidence-text { font-size: .75rem; line-height: 1.15; }
    .study-state { min-height: 25rem; display: grid; place-content: center; text-align: center; }
    .study-state h2 { margin-bottom: var(--space-8); }
    .inline-status { margin: 0; color: var(--muted); }
    .inline-status.error { color: var(--danger); }
    .edit-meanings, .pronunciation { padding: var(--space-16); border: 1px solid var(--border); border-radius: var(--radius-panel); background: color-mix(in oklch, var(--bg), white 45%); }
    .edit-meanings h3, .pronunciation h3 { margin-bottom: var(--space-8); font-size: 1.25rem; }
    .gloss-row { display: grid; grid-template-columns: 1fr auto auto; gap: var(--space-8); align-items: end; margin-top: var(--space-12); }
    .audio-actions, .recording-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); }
    .audio-preview { width: 100%; margin-top: var(--space-12); }
    .local-take { margin-top: var(--space-12); padding: var(--space-12); border: 1px dashed var(--accent); border-radius: var(--radius-control); }
    .deck-tabs { display: flex; flex-wrap: wrap; gap: var(--space-8); margin: var(--space-24) 0 var(--space-16); padding: 0; border-bottom: 1px solid var(--border); }
    .deck-tab { min-height: 0; padding: var(--space-8) var(--space-12); border: 0; border-bottom: 2px solid transparent; border-radius: 0; background: transparent; color: var(--muted); font-weight: 600; cursor: pointer; }
    .deck-tab[aria-selected="true"] { color: var(--fg); border-bottom-color: var(--accent); background: transparent; }
    .deck-tab:hover:not(:disabled) { color: var(--fg); }
    .deck-card-list { display: grid; gap: var(--space-12); margin: var(--space-16) 0 0; padding: 0; list-style: none; }
    .deck-card-row { display: grid; grid-template-columns: 1fr auto; gap: var(--space-12) var(--space-16); align-items: center; padding: var(--space-16); border: 1px solid var(--border); border-radius: var(--radius-panel); background: var(--surface); }
    .deck-card-headword { display: block; font-family: var(--font-display); font-size: 1.15rem; font-weight: 600; }
    .deck-card-meta { display: block; margin-top: var(--space-4); color: var(--muted); font-family: var(--font-mono); font-size: .75rem; letter-spacing: .03em; }
    .deck-card-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); align-items: center; justify-content: flex-end; }
    .orphan-banner { padding: var(--space-16); border: 1px solid var(--warning); border-radius: var(--radius-panel); background: color-mix(in oklch, var(--warning), white 92%); color: var(--fg); }
    .dialog-backdrop { position: fixed; inset: 0; z-index: 100; display: grid; place-items: center; padding: var(--space-16); background: color-mix(in oklch, var(--fg), transparent 75%); overflow-y: auto; }
    .dialog { box-sizing: border-box; width: min(40rem, 100%); padding: clamp(var(--space-16), 4vw, var(--space-32)); border: 1px solid var(--border); border-radius: var(--radius-dialog); background: var(--surface); box-shadow: var(--shadow-sm); display: grid; gap: var(--space-16); max-height: calc(100vh - var(--space-32)); overflow-y: auto; }
    .dialog h2 { margin: 0; }
    .dialog .actions { justify-content: flex-end; flex-wrap: wrap; }
    .edit-gloss-row { display: grid; grid-template-columns: 1fr auto; gap: var(--space-8); align-items: end; margin-top: var(--space-8); }
    .edit-gloss-row label { grid-column: 1 / -1; }
    .edit-gloss-row button { min-height: 2.6rem; }
    .bottom-nav { display: none; }
    @media (max-width: 800px) {
      .shell { padding: var(--space-24) var(--space-16) calc(var(--space-72) + var(--space-24)); }
      header, .form-row, .deck-heading { align-items: stretch; flex-direction: column; }
      header { align-items: flex-start; }
      header > .primary-nav { display: none; }
      .toolbar { flex-wrap: wrap; }
      .deck { grid-template-columns: 1fr; }
      .deck-row-actions { justify-content: flex-start; }
      .folder-heading-row { align-items: flex-start; flex-direction: column; }
      .workflow-grid { grid-template-columns: 1fr; }
      .card-stage { min-height: 20rem; padding: var(--space-24); }
      .confidence-grid { grid-template-columns: 1fr; }
      .confidence { min-height: 3.6rem; grid-template-columns: 2rem 1fr; align-items: center; justify-items: start; }
      .confidence-text { font-size: .9rem; }
      .gloss-row { grid-template-columns: 1fr auto; }
      .gloss-row label { grid-column: 1 / -1; }
      .bottom-nav { position: fixed; z-index: 10; right: 0; bottom: 0; left: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 0; padding: var(--space-8) var(--space-16) calc(var(--space-8) + env(safe-area-inset-bottom)); border-top: 1px solid var(--border); background: color-mix(in oklch, var(--surface), white 12%); box-shadow: 0 -8px 24px oklch(20% .02 240 / 6%); }
      .bottom-nav button { min-height: 3rem; border: 0; background: transparent; }
      .deck-card-row { grid-template-columns: 1fr; }
      .deck-card-actions { justify-content: flex-start; }
      .edit-gloss-row { grid-template-columns: 1fr; }
      .edit-gloss-row button { width: 100%; }
      .dialog { padding: var(--space-16); }
    }
  `;

  @state() private decks: DeckSummary[] = [];
  @state() private deckStatus: DeckListStatus = 'loading';
  @state() private errorMessage = '';
  @state() private successMessage = '';
  @state() private newDeckName = '';
  @state() private selectedDeckId: number | null = null;
  @state() private pendingDeleteDeckId: number | null = null;
  @state() private isCreating = false;
  @state() private isDeleting = false;
  @state() private lookupQuery = '';
  @state() private lookupStatus: LookupStatus = 'idle';
  @state() private lookupCandidates: Candidate[] = [];
  @state() private lookupAssetToken = '';
  @state() private selectedCandidate: Candidate | null = null;
  @state() private selectedSenseRef: string | null = null;
  @state() private selectedMeaningLanguages: MeaningLanguage[] = ['de', 'en'];
  @state() private userMeaningDe = '';
  @state() private userMeaningEn = '';
  @state() private manualDeckId: number | null = null;
  @state() private lastSavedNote: { lemma: string; deckId: number; deckName: string } | null = null;
  @state() private isSavingNote = false;
  @state() private importDeckId: number | null = null;
  @state() private importText = '';
  @state() private importFileName = '';
  @state() private isReadingImportFile = false;
  @state() private isImporting = false;
  @state() private exportingFormat: 'apkg' | 'tsv' | null = null;
  @state() private captureSentence = '';
  @state() private captureLessonLabel = '';
  @state() private captureSpanStart = 0;
  @state() private captureSpanEnd = 0;
  @state() private captureStatus: CaptureStatus = 'idle';
  @state() private captureCandidates: Candidate[] = [];
  @state() private captureAssetToken = '';
  @state() private captureContext: CaptureContext | null = null;
  @state() private captureSelections: Record<string, CaptureCandidateSelection> = {};
  @state() private captureMeaningLanguages: MeaningLanguage[] = ['de', 'en'];
  @state() private captureUserMeaningDe = '';
  @state() private captureUserMeaningEn = '';
  @state() private captureDeckId: number | null = null;
  @state() private captureError = '';
  @state() private captureDictionaryChanged = false;
  @state() private isCapturing = false;
  @state() private view: AppView = 'decks';
  @state() private studyDeckId: number | null = null;
  @state() private studyStatus: StudyStatus = 'idle';
  @state() private studyCard: NextCardData | null = null;
  @state() private isRevealed = false;
  @state() private isReviewing = false;
  @state() private studyError = '';
  @state() private extraInfoOpen = false;
  @state() private alwaysShowExtraInfo: boolean = readAlwaysShowExtraInfo(localStorageOrNull());
  @state() private glossDrafts: Record<MeaningLanguage, string> = { de: '', en: '' };
  @state() private glossState = '';
  @state() private glossError = '';
  @state() private glossSavingLanguage: MeaningLanguage | null = null;
  @state() private audioStatus: AudioStatus = 'idle';
  @state() private audioMessage = '';
  @state() private recordingStatus: RecordingStatus = 'idle';
  @state() private recordingBlob: Blob | null = null;
  @state() private recordingNoteId: number | null = null;
  @state() private recordingPreviewUrl = '';
  @state() private recordingError = '';
  @state() private showRecordingControls = false;
  @state() private revertConfirmation = false;
  @state() private hasCustomAudio = false;
  @state() private dictionaryMode: SessionMode = 'unconfigured';
  @state() private dictionarySettings: DictionarySettingsInfo | null = null;
  @state() private dictionarySettingsStatus: DictionarySettingsStatus = 'loading';
  @state() private dictionaryAction: DictionarySettingsAction = 'idle';
  @state() private dictionaryActionMessage = '';
  @state() private dictionaryActionError = '';
  @state() private confirmRemoveOffline = false;
  @state() private deckTab: DeckTab = 'overview';
  @state() private deckCards: DeckCardRow[] = [];
  @state() private deckCardsStatus: DeckCardsStatus = 'idle';
  @state() private deckCardsError = '';
  @state() private editingCard: DeckCardRow | null = null;
  @state() private editLanguages: MeaningLanguage[] = [];
  @state() private editGlossDrafts: Record<MeaningLanguage, string> = { de: '', en: '' };
  @state() private editGlossBusy: EditGlossBusy = { de: false, en: false };
  @state() private editState: EditDialogStatus = 'idle';
  @state() private editError = '';
  @state() private moveTarget: DeckCardRow | null = null;
  @state() private moveDestinationDeckId: number | null = null;
  @state() private moveState: MoveDialogStatus = 'idle';
  @state() private moveError = '';
  @state() private removeTarget: DeckCardRow | null = null;
  @state() private removeState: RemoveDialogStatus = 'idle';
  @state() private restoreTarget: DeckCardRow | null = null;
  @state() private restoreDestinationDeckId: number | null = null;
  @state() private restoreState: RestoreDialogStatus = 'idle';
  @state() private restoreError = '';
  // M6 — selected-sense editing
  @state() private senseTarget: DeckCardRow | null = null;
  @state() private senseCandidates: Candidate[] = [];
  @state() private senseLookupAssetToken = '';
  @state() private senseLookupStatus: LookupStatus = 'idle';
  @state() private senseSelectedRef: string | null = null;
  @state() private senseState: SenseDialogStatus = 'idle';
  @state() private senseError = '';
  @state() private renameOpen = false;
  @state() private renameDraft = '';
  @state() private renameState: RenameDialogStatus = 'idle';
  @state() private renameError = '';
  @state() private folders: Folder[] = [];
  @state() private folderStatus: FolderListStatus = 'loading';
  @state() private folderError = '';
  @state() private createFolderOpen = false;
  @state() private newFolderName = '';
  @state() private createFolderState: FolderDialogStatus = 'idle';
  @state() private createFolderError = '';
  @state() private renameFolderTarget: Folder | null = null;
  @state() private renameFolderDraft = '';
  @state() private renameFolderState: FolderDialogStatus = 'idle';
  @state() private renameFolderError = '';
  @state() private deleteFolderTarget: Folder | null = null;
  @state() private deleteFolderState: FolderDialogStatus = 'idle';
  @state() private deleteFolderError = '';
  @state() private moveDeckTarget: DeckSummary | null = null;
  @state() private moveDeckFolderId: number | null = null;
  @state() private moveDeckState: FolderDialogStatus = 'idle';
  @state() private moveDeckError = '';
  @state() private mgmtAudioStatus: AudioStatus = 'idle';
  @state() private mgmtAudioMessage = '';
  @state() private mgmtRecordingStatus: RecordingStatus = 'idle';
  @state() private mgmtRecordingBlob: Blob | null = null;
  @state() private mgmtRecordingNoteId: number | null = null;
  @state() private mgmtRecordingPreviewUrl = '';
  @state() private mgmtRevertConfirmation = false;
  @state() private mgmtRecordingError = '';
  @state() private mgmtShowRecordingControls = false;
  private focusTarget: 'answer' | 'empty' | 'edit-dialog' | 'move-dialog' | 'remove-dialog' | 'rename-dialog' | 'restore-dialog' | 'sense-dialog' | 'create-folder-dialog' | 'rename-folder-dialog' | 'delete-folder-dialog' | 'move-deck-dialog' | null = null;
  private audioPlayer: HTMLAudioElement | null = null;
  private mgmtAudioPlayer: HTMLAudioElement | null = null;
  private mediaRecorder: MediaRecorder | null = null;
  private recordingChunks: Blob[] = [];
  private lastDeckTabByDeck = new Map<number, DeckTab>();

  connectedCallback(): void {
    super.connectedCallback();
    void this.loadDecks();
    void this.loadFolders();
    void this.loadDictionarySettings();
    window.addEventListener('keydown', this.handleStudyKeydown);
    window.addEventListener('keydown', this.handleGlobalKeydown);
  }

  disconnectedCallback(): void {
    window.removeEventListener('keydown', this.handleStudyKeydown);
    window.removeEventListener('keydown', this.handleGlobalKeydown);
    this.stopAudio();
    this.stopManagementAudio();
    this.releaseRecordingPreview();
    this.releaseManagementRecordingPreview();
    super.disconnectedCallback();
  }

  updated(): void {
    if (!this.focusTarget) return;
    const focusTarget = this.focusTarget;
    let selector = '';
    switch (focusTarget) {
      case 'answer':
        selector = '[data-study-answer]';
        break;
      case 'empty':
        selector = '[data-study-empty]';
        break;
      case 'edit-dialog':
        selector = '[data-edit-dialog]';
        break;
      case 'move-dialog':
        selector = '[data-move-dialog]';
        break;
      case 'remove-dialog':
        selector = '[data-remove-dialog]';
        break;
      case 'rename-dialog':
        selector = '[data-rename-dialog]';
        break;
      case 'restore-dialog':
        selector = '[data-restore-dialog]';
        break;
      case 'create-folder-dialog':
        selector = '[data-create-folder-dialog]';
        break;
      case 'rename-folder-dialog':
        selector = '[data-rename-folder-dialog]';
        break;
      case 'delete-folder-dialog':
        selector = '[data-delete-folder-dialog]';
        break;
      case 'move-deck-dialog':
        selector = '[data-move-deck-dialog]';
        break;
    }
    const target = selector ? this.renderRoot.querySelector<HTMLElement>(selector) : null;
    if (target) {
      target.focus();
    }
    if (target || focusTarget === 'answer' || focusTarget === 'empty') {
      this.focusTarget = null;
    }
  }

  /**
   * Reconcile displayed decks with the server and return that authoritative list.
   * A refresh failure is intentionally observable to callers, so mutation flows
   * cannot turn it into a false success message.
   */
  private async loadDecks(): Promise<DeckSummary[] | null> {
    this.deckStatus = 'loading';
    this.errorMessage = '';
    this.successMessage = '';
    try {
      const decks = await vocabClient.getDecks();
      this.decks = decks;
      if (this.selectedDeckId !== null && !decks.some((deck) => deck.id === this.selectedDeckId)) {
        this.selectedDeckId = null;
      }
      if (this.manualDeckId !== null && !decks.some((deck) => deck.id === this.manualDeckId)) {
        this.manualDeckId = null;
      }
      if (this.captureDeckId !== null && !decks.some((deck) => deck.id === this.captureDeckId)) {
        this.captureDeckId = null;
      }
      if (this.importDeckId !== null && !decks.some((deck) => deck.id === this.importDeckId)) {
        this.importDeckId = null;
      }
      this.deckStatus = 'ready';
      return decks;
    } catch (error) {
      this.deckStatus = 'error';
      this.errorMessage = this.messageFor(error, 'Decks could not be loaded.');
      return null;
    }
  }

  private async createDeck(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const name = this.newDeckName.trim();
    if (!name) {
      this.successMessage = '';
      this.errorMessage = 'Enter a deck name before creating it.';
      return;
    }

    this.isCreating = true;
    this.errorMessage = '';
    this.successMessage = '';
    try {
      const createdDeck = await vocabClient.createDeck(name);
      this.newDeckName = '';
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.errorMessage = `“${createdDeck.name}” may have been created, but the deck list could not be refreshed.`;
        return;
      }
      const refreshedDeck = refreshedDecks.find((deck) => deck.id === createdDeck.id);
      if (!refreshedDeck) {
        this.errorMessage = `The server did not return “${createdDeck.name}” after creation. It was not opened.`;
        return;
      }
      this.selectedDeckId = refreshedDeck.id;
      this.manualDeckId = refreshedDeck.id;
      this.captureDeckId = refreshedDeck.id;
      this.importDeckId = refreshedDeck.id;
      this.successMessage = `Created and opened “${refreshedDeck.name}”.`;
    } catch (error) {
      this.successMessage = '';
      this.errorMessage = this.messageFor(error, 'Deck could not be created.');
    } finally {
      this.isCreating = false;
    }
  }

  private async deleteDeck(deck: DeckSummary): Promise<void> {
    this.isDeleting = true;
    this.errorMessage = '';
    this.successMessage = '';
    try {
      const result = await vocabClient.deleteDeck(deck.id);
      if (!result.deleted) {
        throw new Error('The server did not confirm deletion.');
      }
      this.pendingDeleteDeckId = null;
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.errorMessage = `“${deck.name}” may have been deleted, but the deck list could not be refreshed.`;
        return;
      }
      if (refreshedDecks.some((refreshedDeck) => refreshedDeck.id === deck.id)) {
        this.errorMessage = `The server still returned “${deck.name}” after deletion. The deletion was not confirmed.`;
        return;
      }
      if (this.selectedDeckId === deck.id) this.selectedDeckId = null;
      this.successMessage = `Deleted “${deck.name}”. Notes with review history were preserved by the server.`;
    } catch (error) {
      this.successMessage = '';
      this.errorMessage = this.messageFor(error, 'Deck could not be deleted.');
    } finally {
      this.isDeleting = false;
    }
  }

  private messageFor(error: unknown, fallback: string): string {
    if (error instanceof ApiError && error.detail) return error.detail;
    if (error instanceof Error && error.message) return error.message;
    return fallback;
  }

  private isOrphanedDeck(deck: DeckSummary | { id: number; name: string }): boolean {
    return deck.name === ORPHANED_DECK_NAME;
  }

  // ---------------------------------------------------------------------------
  // Persistent folders (M4A backend, M4B UI)
  //
  // The server is the authority for both the folder identities and every
  // deck-to-folder assignment (``DeckSummary.folder_id``). The UI never
  // persists folder state locally and never infers assignment from the
  // folder payload.
  // ---------------------------------------------------------------------------

  /**
   * Reconcile the folder identities with the server. A failure is observable
   * to callers and leaves the existing folder view untouched, never faked.
   */
  private async loadFolders(): Promise<Folder[] | null> {
    this.folderStatus = 'loading';
    this.folderError = '';
    try {
      const folders = await vocabClient.listFolders();
      this.folders = folders;
      this.folderStatus = 'ready';
      return folders;
    } catch (error) {
      this.folderStatus = 'error';
      this.folderError = this.messageFor(error, 'Folders could not be loaded.');
      return null;
    }
  }

  /**
   * Group decks by their server-authoritative ``folder_id``. A stale id that
   * no longer matches a known folder fails closed into "Not in a folder"
   * rather than disappearing or inventing a group.
   */
  private groupedDeckSections(): Array<{ folder: Folder | null; decks: DeckSummary[] }> {
    const groups = this.folders.map((folder) => ({ folder, decks: [] as DeckSummary[] }));
    const unassigned: DeckSummary[] = [];
    for (const deck of this.decks) {
      const group = deck.folder_id === null
        ? undefined
        : groups.find((entry) => entry.folder.id === deck.folder_id);
      if (group) {
        group.decks.push(deck);
      } else {
        unassigned.push(deck);
      }
    }
    return [...groups, { folder: null, decks: unassigned }];
  }

  private folderById(folderId: number | null): Folder | undefined {
    if (folderId === null) return undefined;
    return this.folders.find((folder) => folder.id === folderId);
  }

  private openCreateFolderDialog(): void {
    this.createFolderOpen = true;
    this.newFolderName = '';
    this.createFolderError = '';
    this.createFolderState = 'idle';
    this.focusTarget = 'create-folder-dialog';
  }

  private closeCreateFolderDialog(): void {
    if (this.createFolderState === 'saving') return;
    this.createFolderOpen = false;
    this.newFolderName = '';
    this.createFolderError = '';
    this.createFolderState = 'idle';
  }

  private async performCreateFolder(): Promise<void> {
    const name = this.newFolderName.trim();
    if (!name) {
      this.createFolderError = 'Enter a folder name.';
      return;
    }
    this.createFolderState = 'saving';
    this.createFolderError = '';
    try {
      const created = await vocabClient.createFolder(name);
      const refreshed = await this.loadFolders();
      if (refreshed === null) {
        this.createFolderError = `“${created.name}” may have been created, but the folder list could not be refreshed.`;
        return;
      }
      if (!refreshed.some((folder) => folder.id === created.id)) {
        this.createFolderError = 'The server did not return the created folder. It was not confirmed.';
        return;
      }
      this.createFolderOpen = false;
      this.newFolderName = '';
      this.successMessage = `Created folder “${created.name}”.`;
    } catch (error) {
      if (error instanceof ApiError && error.code === 'folder_name_conflict') {
        this.createFolderError = `A folder named “${name}” already exists. Choose another name.`;
      } else {
        this.createFolderError = this.messageFor(error, 'Folder could not be created.');
      }
    } finally {
      this.createFolderState = 'idle';
    }
  }

  private openRenameFolderDialog(folder: Folder): void {
    this.renameFolderTarget = folder;
    this.renameFolderDraft = folder.name;
    this.renameFolderState = 'idle';
    this.renameFolderError = '';
    this.focusTarget = 'rename-folder-dialog';
  }

  private closeRenameFolderDialog(): void {
    if (this.renameFolderState === 'saving') return;
    this.renameFolderTarget = null;
    this.renameFolderDraft = '';
    this.renameFolderError = '';
    this.renameFolderState = 'idle';
  }

  private async performRenameFolder(): Promise<void> {
    const folder = this.renameFolderTarget;
    if (!folder) return;
    const trimmed = this.renameFolderDraft.trim();
    if (!trimmed) {
      this.renameFolderError = 'Folder name must not be blank.';
      return;
    }
    if (trimmed === folder.name) {
      this.renameFolderTarget = null;
      this.renameFolderDraft = '';
      return;
    }
    this.renameFolderState = 'saving';
    this.renameFolderError = '';
    try {
      const updated = await vocabClient.renameFolder(folder.id, trimmed);
      const refreshed = await this.loadFolders();
      if (refreshed === null) {
        this.renameFolderError = 'Folder may have been renamed, but the folder list could not be refreshed.';
        return;
      }
      const confirmed = refreshed.find((item) => item.id === updated.id && item.name === updated.name);
      if (!confirmed) {
        this.renameFolderError = 'The server did not confirm the renamed folder. The change was not confirmed.';
        return;
      }
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.renameFolderError = 'Folder was renamed, but the deck list could not be refreshed.';
        return;
      }
      this.renameFolderTarget = null;
      this.renameFolderDraft = '';
      this.successMessage = `Renamed folder to “${confirmed.name}”.`;
    } catch (error) {
      if (error instanceof ApiError && error.code === 'folder_name_conflict') {
        this.renameFolderError = `A folder named “${trimmed}” already exists. Choose another name.`;
      } else {
        this.renameFolderError = this.messageFor(error, 'Folder could not be renamed.');
      }
    } finally {
      this.renameFolderState = 'idle';
    }
  }

  private openDeleteFolderDialog(folder: Folder): void {
    this.deleteFolderTarget = folder;
    this.deleteFolderState = 'idle';
    this.deleteFolderError = '';
    this.focusTarget = 'delete-folder-dialog';
  }

  private closeDeleteFolderDialog(): void {
    if (this.deleteFolderState === 'saving') return;
    this.deleteFolderTarget = null;
    this.deleteFolderError = '';
    this.deleteFolderState = 'idle';
  }

  private async performDeleteFolder(): Promise<void> {
    const folder = this.deleteFolderTarget;
    if (!folder) return;
    this.deleteFolderState = 'saving';
    this.deleteFolderError = '';
    try {
      const result = await vocabClient.deleteFolder(folder.id);
      if (!result.deleted) {
        throw new Error('The server did not confirm deletion.');
      }
      const refreshedFolders = await this.loadFolders();
      if (refreshedFolders === null) {
        this.deleteFolderError = 'Folder may have been deleted, but the folder list could not be refreshed.';
        return;
      }
      if (refreshedFolders.some((item) => item.id === folder.id)) {
        this.deleteFolderError = 'The server still returned the folder after deletion. The deletion was not confirmed.';
        return;
      }
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.deleteFolderError = 'Folder was deleted, but the deck list could not be refreshed.';
        return;
      }
      this.deleteFolderTarget = null;
      this.successMessage = `Deleted folder “${folder.name}”. Its decks are now unassigned.`;
    } catch (error) {
      this.deleteFolderError = this.messageFor(error, 'Folder could not be deleted.');
    } finally {
      this.deleteFolderState = 'idle';
    }
  }

  private openMoveDeckDialog(deck: DeckSummary): void {
    if (this.isOrphanedDeck(deck)) return;
    this.moveDeckTarget = deck;
    this.moveDeckFolderId = deck.folder_id;
    this.moveDeckState = 'idle';
    this.moveDeckError = '';
    this.focusTarget = 'move-deck-dialog';
  }

  private closeMoveDeckDialog(): void {
    if (this.moveDeckState === 'saving') return;
    this.moveDeckTarget = null;
    this.moveDeckFolderId = null;
    this.moveDeckError = '';
    this.moveDeckState = 'idle';
  }

  private async performMoveDeck(): Promise<void> {
    const deck = this.moveDeckTarget;
    if (!deck) return;
    const targetFolderId = this.moveDeckFolderId;
    if (targetFolderId === deck.folder_id) {
      this.moveDeckTarget = null;
      return;
    }
    this.moveDeckState = 'saving';
    this.moveDeckError = '';
    try {
      const updated = await vocabClient.setDeckFolder(deck.id, targetFolderId);
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.moveDeckError = 'Move may have succeeded, but the deck list could not be refreshed.';
        return;
      }
      const confirmed = refreshedDecks.find((item) => item.id === updated.id && item.folder_id === updated.folder_id);
      if (!confirmed) {
        this.moveDeckError = 'The server did not confirm the deck’s folder. The move was not confirmed.';
        return;
      }
      const destination = this.folderById(targetFolderId);
      this.moveDeckTarget = null;
      this.moveDeckFolderId = null;
      this.successMessage = destination
        ? `Moved “${deck.name}” to “${destination.name}”.`
        : `Moved “${deck.name}” to Not in a folder.`;
    } catch (error) {
      this.moveDeckError = this.messageFor(error, 'Deck could not be moved.');
    } finally {
      this.moveDeckState = 'idle';
    }
  }

  // ---------------------------------------------------------------------------
  // Deck card management (M3)
  // ---------------------------------------------------------------------------

  private setDeckTab(tab: DeckTab): void {
    this.deckTab = tab;
    if (this.selectedDeckId !== null) {
      this.lastDeckTabByDeck.set(this.selectedDeckId, tab);
    }
    if (tab === 'cards' && this.selectedDeckId !== null) {
      void this.loadDeckCards(this.selectedDeckId);
    }
  }

  /**
   * Reconcile the management cards list with the server. A refresh failure is
   * surfaced to callers, so member-edit and remove flows cannot turn it into
   * a false success message.
   */
  private async loadDeckCards(deckId: number): Promise<DeckCardRow[] | null> {
    this.deckCardsStatus = 'loading';
    this.deckCardsError = '';
    try {
      const response = await vocabClient.getDeckCards(deckId);
      this.deckCards = response.cards;
      this.deckCardsStatus = 'ready';
      return response.cards;
    } catch (error) {
      this.deckCardsStatus = 'error';
      this.deckCardsError = this.messageFor(error, 'The cards in this deck could not be loaded.');
      return null;
    }
  }

  private openEditDialog(card: DeckCardRow): void {
    this.stopManagementAudio();
    this.releaseManagementRecordingPreview();
    this.editingCard = card;
    this.editLanguages = [...card.selected_languages];
    this.editGlossDrafts = {
      de: card.user_meanings.de?.trim() ?? '',
      en: card.user_meanings.en?.trim() ?? '',
    };
    this.editGlossBusy = { de: false, en: false };
    this.editState = 'idle';
    this.editError = '';
    this.mgmtAudioStatus = 'idle';
    this.mgmtAudioMessage = '';
    this.mgmtRecordingStatus = 'idle';
    this.mgmtRecordingBlob = null;
    this.mgmtRecordingNoteId = card.note_id;
    this.mgmtRecordingError = '';
    this.mgmtShowRecordingControls = false;
    this.mgmtRevertConfirmation = false;
    this.focusTarget = 'edit-dialog';
  }

  private closeEditDialog(): void {
    if (this.editState === 'saving-languages' || this.editState === 'saving-gloss') return;
    if (this.mgmtRecordingStatus === 'recording') {
      this.stopManagementRecording();
    }
    this.stopManagementAudio();
    this.releaseManagementRecordingPreview();
    this.editingCard = null;
    this.editState = 'idle';
    this.editError = '';
    this.mgmtAudioStatus = 'idle';
    this.mgmtAudioMessage = '';
    this.mgmtRecordingStatus = 'idle';
    this.mgmtRecordingBlob = null;
    this.mgmtRecordingNoteId = null;
    this.mgmtRecordingError = '';
    this.mgmtShowRecordingControls = false;
    this.mgmtRevertConfirmation = false;
  }

  private toggleEditMeaningLanguage(language: MeaningLanguage, input: HTMLInputElement): void {
    if (input.checked) {
      this.editLanguages = [...new Set([...this.editLanguages, language])];
      return;
    }
    if (this.editLanguages.length === 1) {
      input.checked = true;
      return;
    }
    this.editLanguages = this.editLanguages.filter((item) => item !== language);
  }

  private async saveEditGloss(language: MeaningLanguage): Promise<void> {
    const card = this.editingCard;
    if (!card) return;
    const meaningText = this.editGlossDrafts[language].trim();
    if (!meaningText) return;
    const previous = card.user_meanings[language]?.trim() ?? '';
    if (previous === meaningText) return;
    this.editGlossBusy = { ...this.editGlossBusy, [language]: true };
    this.editState = 'saving-gloss';
    this.editError = '';
    try {
      await vocabClient.setGloss(card.note_id, language, meaningText);
      const refreshed = await this.loadDeckCards(this.selectedDeckId ?? -1);
      if (refreshed === null) {
        this.editState = 'error';
        this.editError = 'Custom meaning may have been saved, but the deck cards could not be refreshed.';
        return;
      }
      const updated = refreshed.find((row) => row.note_id === card.note_id);
      if (!updated) {
        this.editState = 'error';
        this.editError = 'The server no longer reports this card in the current deck.';
        return;
      }
      this.editingCard = updated;
      this.editGlossDrafts = { ...this.editGlossDrafts, [language]: updated.user_meanings[language]?.trim() ?? '' };
      this.editState = 'saved';
    } catch (error) {
      this.editState = 'error';
      this.editError = this.messageFor(error, 'Custom meaning could not be saved.');
    } finally {
      this.editGlossBusy = { ...this.editGlossBusy, [language]: false };
    }
  }

  private async deleteEditGloss(language: MeaningLanguage): Promise<void> {
    const card = this.editingCard;
    if (!card) return;
    const previous = card.user_meanings[language]?.trim() ?? '';
    if (!previous) return;
    this.editGlossBusy = { ...this.editGlossBusy, [language]: true };
    this.editState = 'saving-gloss';
    this.editError = '';
    try {
      const result = await vocabClient.deleteGloss(card.note_id, language);
      if (!result.deleted) {
        throw new Error('The server did not confirm removal.');
      }
      const refreshed = await this.loadDeckCards(this.selectedDeckId ?? -1);
      if (refreshed === null) {
        this.editState = 'error';
        this.editError = 'Custom meaning may have been removed, but the deck cards could not be refreshed.';
        return;
      }
      const updated = refreshed.find((row) => row.note_id === card.note_id);
      if (!updated) {
        this.editState = 'error';
        this.editError = 'The server no longer reports this card in the current deck.';
        return;
      }
      this.editingCard = updated;
      this.editGlossDrafts = { ...this.editGlossDrafts, [language]: '' };
      this.editState = 'saved';
    } catch (error) {
      this.editState = 'error';
      this.editError = this.messageFor(error, 'Custom meaning could not be removed.');
    } finally {
      this.editGlossBusy = { ...this.editGlossBusy, [language]: false };
    }
  }

  private async commitEditDialog(): Promise<void> {
    const card = this.editingCard;
    if (!card) return;
    this.editError = '';
    // Languages first, then any per-language gloss mutation. Each helper
    // updates editingCard from a fresh server listing so subsequent calls
    // compare against the up-to-date baseline.
    if (!this.editLanguages.length) {
      this.editState = 'error';
      this.editError = 'Select German, English, or both meaning languages.';
      return;
    }
    const beforeLangs = [...card.selected_languages].sort();
    const afterLangs = [...this.editLanguages].sort();
    const languagesChanged = beforeLangs.length !== afterLangs.length
      || beforeLangs.some((value, index) => value !== afterLangs[index]);
    if (languagesChanged) {
      this.editState = 'saving-languages';
      try {
        await vocabClient.setMeaningLanguages(card.note_id, this.editLanguages);
        const refreshed = await this.loadDeckCards(this.selectedDeckId ?? -1);
        if (refreshed === null) {
          this.editState = 'error';
          this.editError = 'Languages may have been saved, but the deck cards could not be refreshed.';
          return;
        }
        const updated = refreshed.find((row) => row.note_id === card.note_id);
        if (!updated) {
          this.editState = 'error';
          this.editError = 'The server no longer reports this card in the current deck.';
          return;
        }
        this.editingCard = updated;
      } catch (error) {
        this.editState = 'error';
        this.editError = this.messageFor(error, 'Meaning languages could not be saved.');
        return;
      } finally {
      }
    }

    const latest = this.editingCard;
    if (!latest) return;
    for (const language of ['de', 'en'] as MeaningLanguage[]) {
      const draft = this.editGlossDrafts[language].trim();
      const previous = latest.user_meanings[language]?.trim() ?? '';
      if (!draft && !previous) continue;
      if (draft && draft === previous) continue;
      this.editState = 'saving-gloss';
      this.editGlossBusy = { ...this.editGlossBusy, [language]: true };
      try {
        if (!draft) {
          const result = await vocabClient.deleteGloss(latest.note_id, language);
          if (!result.deleted) throw new Error('The server did not confirm removal.');
        } else {
          await vocabClient.setGloss(latest.note_id, language, draft);
        }
        const refreshed = await this.loadDeckCards(this.selectedDeckId ?? -1);
        if (refreshed === null) {
          this.editState = 'error';
          this.editError = 'Custom meaning may have been saved, but the deck cards could not be refreshed.';
          return;
        }
        const updated = refreshed.find((row) => row.note_id === latest.note_id);
        if (!updated) {
          this.editState = 'error';
          this.editError = 'The server no longer reports this card in the current deck.';
          return;
        }
        this.editingCard = updated;
        this.editGlossDrafts = { ...this.editGlossDrafts, [language]: updated.user_meanings[language]?.trim() ?? '' };
      } catch (error) {
        this.editState = 'error';
        this.editError = this.messageFor(error, draft ? 'Custom meaning could not be saved.' : 'Custom meaning could not be removed.');
        return;
      } finally {
        this.editGlossBusy = { ...this.editGlossBusy, [language]: false };
      }
    }
    this.editState = 'saved';
    this.editError = '';
  }

  private openMoveDialog(card: DeckCardRow): void {
    if (this.isOrphanedDeckByName(this.currentDeckName())) {
      return;
    }
    this.moveTarget = card;
    this.moveDestinationDeckId = null;
    this.moveState = 'idle';
    this.moveError = '';
    this.focusTarget = 'move-dialog';
  }

  private closeMoveDialog(): void {
    if (this.moveState === 'saving') return;
    this.moveTarget = null;
    this.moveDestinationDeckId = null;
    this.moveError = '';
  }

  private currentDeckName(): string {
    return this.selectedDeck()?.name ?? '';
  }

  private isOrphanedDeckByName(name: string): boolean {
    return name === ORPHANED_DECK_NAME;
  }

  private moveDestinations(): DeckSummary[] {
    const sourceName = this.currentDeckName();
    return this.decks.filter((deck) => deck.name !== sourceName);
  }

  private async performMove(): Promise<void> {
    const card = this.moveTarget;
    const destinationDeckId = this.moveDestinationDeckId;
    const sourceDeckId = this.selectedDeckId;
    if (!card || destinationDeckId === null || sourceDeckId === null) return;
    this.moveState = 'saving';
    this.moveError = '';
    try {
      await vocabClient.moveNoteBetweenDecks(sourceDeckId, card.note_id, destinationDeckId);
      const refreshedCards = await this.loadDeckCards(sourceDeckId);
      if (refreshedCards === null) {
        this.moveError = 'Move may have succeeded, but the deck cards could not be refreshed.';
        return;
      }
      const stillHere = refreshedCards.some((row) => row.note_id === card.note_id);
      if (stillHere) {
        this.moveError = 'The server still reports the card in the source deck. The move was not confirmed.';
        return;
      }
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.moveError = 'Move succeeded, but the deck list could not be refreshed.';
        return;
      }
      const destination = refreshedDecks.find((deck) => deck.id === destinationDeckId);
      this.successMessage = destination
        ? `Moved “${card.headword}” to “${destination.name}”.`
        : `Moved “${card.headword}”.`;
      this.moveTarget = null;
      this.moveDestinationDeckId = null;
    } catch (error) {
      this.moveError = this.messageFor(error, 'Move could not be completed.');
    } finally {
      this.moveState = 'idle';
    }
  }

  private openRemoveConfirm(card: DeckCardRow): void {
    this.removeTarget = card;
    this.removeState = 'idle';
    this.focusTarget = 'remove-dialog';
  }

  private closeRemoveConfirm(): void {
    if (this.removeState === 'saving') return;
    this.removeTarget = null;
  }

  private async performRemove(): Promise<void> {
    const card = this.removeTarget;
    const sourceDeckId = this.selectedDeckId;
    if (!card || sourceDeckId === null) return;
    this.removeState = 'saving';
    try {
      const result = await vocabClient.removeNoteFromDeck(sourceDeckId, card.note_id);
      if (!result.removed) {
        throw new Error('The server did not confirm removal.');
      }
      const refreshedCards = await this.loadDeckCards(sourceDeckId);
      if (refreshedCards === null) {
        this.errorMessage = 'Remove may have succeeded, but the deck cards could not be refreshed.';
        return;
      }
      const stillHere = refreshedCards.some((row) => row.note_id === card.note_id);
      if (stillHere) {
        this.errorMessage = 'The server still reports the card in this deck. Removal was not confirmed.';
        return;
      }
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.errorMessage = 'Remove succeeded, but the deck list could not be refreshed.';
        return;
      }
      this.removeTarget = null;
      if (result.orphaned) {
        const orphaned = refreshedDecks.find((deck) => deck.name === ORPHANED_DECK_NAME);
        this.successMessage = orphaned
          ? `Removed “${card.headword}” from this deck. Its study history is preserved in “${orphaned.name}”.`
          : `Removed “${card.headword}” from this deck. Its study history is preserved.`;
      } else {
        this.successMessage = `Removed “${card.headword}” from this deck. Its study history is preserved.`;
      }
    } catch (error) {
      this.errorMessage = this.messageFor(error, 'Remove could not be completed.');
    } finally {
      this.removeState = 'idle';
    }
  }

  private openRenameDialog(): void {
    const deck = this.selectedDeck();
    if (!deck || this.isOrphanedDeck(deck)) return;
    this.renameOpen = true;
    this.renameDraft = deck.name;
    this.renameState = 'idle';
    this.renameError = '';
    this.focusTarget = 'rename-dialog';
  }

  private restoreDestinations(): DeckSummary[] {
    return this.decks.filter((deck) => deck.name !== ORPHANED_DECK_NAME);
  }

  private openRestoreDialog(card: DeckCardRow): void {
    if (!this.isOrphanedDeckByName(this.currentDeckName())) return;
    this.restoreTarget = card;
    this.restoreDestinationDeckId = null;
    this.restoreState = 'idle';
    this.restoreError = '';
    this.focusTarget = 'restore-dialog';
  }

  private closeRestoreDialog(): void {
    if (this.restoreState === 'saving') return;
    this.restoreTarget = null;
    this.restoreDestinationDeckId = null;
    this.restoreError = '';
  }

  private async performRestore(): Promise<void> {
    const card = this.restoreTarget;
    const destinationDeckId = this.restoreDestinationDeckId;
    const sourceDeckId = this.selectedDeckId;
    if (!card || destinationDeckId === null || sourceDeckId === null) return;
    this.restoreState = 'saving';
    this.restoreError = '';
    try {
      await vocabClient.restoreOrphanedNote(card.note_id, destinationDeckId);
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.restoreError = 'Restore may have succeeded, but the deck list could not be refreshed.';
        return;
      }
      const destination = refreshedDecks.find((deck) => deck.id === destinationDeckId);
      const destinationName = destination ? destination.name : '';
      const refreshedCards = await this.loadDeckCards(sourceDeckId);
      if (refreshedCards === null) {
        this.restoreError = 'Restore may have succeeded, but the Orphaned deck cards could not be refreshed.';
        return;
      }
      const stillOrphaned = refreshedCards.some((row) => row.note_id === card.note_id);
      if (stillOrphaned) {
        this.restoreError = 'The server still reports the card in the Orphaned deck. The restore was not confirmed.';
        return;
      }
      if (destinationName) {
        this.successMessage = `Restored “${card.headword}” to “${destinationName}”.`;
      } else {
        this.successMessage = `Restored “${card.headword}”.`;
      }
      this.restoreTarget = null;
      this.restoreDestinationDeckId = null;
    } catch (error) {
      this.restoreError = this.messageForRestore(error);
    } finally {
      this.restoreState = 'idle';
    }
  }

  private messageForRestore(error: unknown): string {
    if (error instanceof ApiError) {
      if (error.code === 'note_not_orphaned') {
        return 'This note is no longer orphaned. Reload to see the current state.';
      }
      if (error.code === 'deck_not_found') {
        return 'The destination deck could not be found. Reload and try again.';
      }
      if (error.code === 'orphaned_deck_protected') {
        return 'The Orphaned deck cannot be used as a destination.';
      }
      if (error.code === 'inconsistent_orphan_state') {
        return 'This note cannot be safely restored from Orphaned. Open it from its current deck instead.';
      }
    }
    return this.messageFor(error, 'Restore could not be completed.');
  }

  private closeSenseDialog(): void {
    if (this.senseState === 'saving') return;
    this.senseTarget = null;
    this.senseCandidates = [];
    this.senseLookupAssetToken = '';
    this.senseLookupStatus = 'idle';
    this.senseSelectedRef = null;
    this.senseError = '';
  }

  private async openSenseDialog(card: DeckCardRow): Promise<void> {
    this.senseTarget = card;
    this.senseCandidates = [];
    this.senseLookupAssetToken = '';
    this.senseLookupStatus = 'loading';
    this.senseSelectedRef = null;
    this.senseError = '';
    this.focusTarget = 'sense-dialog';
    const query = card.headword.trim();
    if (!query) {
      this.senseLookupStatus = 'error';
      this.senseError = 'Could not read the dictionary word from this card.';
      return;
    }
    try {
      const result = await vocabClient.lookup(query);
      const candidates = result.candidates.map((candidate) => ({
        ...candidate,
        status: candidate.status ?? (candidate.senses?.length ? 'resolved' : 'needs_gloss'),
        senses: candidate.senses?.map((sense) => ({
          ...sense,
          gloss: sense.gloss ?? sense.meanings?.[0]?.text ?? '',
        })),
      }));
      this.senseCandidates = candidates;
      this.senseLookupAssetToken = result.asset_token;
      this.senseLookupStatus = 'ready';
      // Filter to the note's existing durable lemma_ref so homographs
      // (e.g. ``die See`` vs ``der See``) cannot be selected across
      // lemmas, and preselect the current sense when possible.
      const sameLemma = candidates.find(
        (candidate) => candidate.lemma_semantic_ref === card.lemma_semantic_ref,
      );
      if (!sameLemma) {
        this.senseLookupStatus = 'error';
        this.senseError = 'This card\u2019s lemma is no longer in the active dictionary. Reload and try again.';
        return;
      }
      this.senseCandidates = [sameLemma];
      const directSenses = sameLemma.senses?.length ? sameLemma.senses : [];
      const matching = card.sense_semantic_ref
        ? directSenses.find((sense) => sense.sense_semantic_ref === card.sense_semantic_ref)
        : undefined;
      if (matching) {
        this.senseSelectedRef = matching.sense_semantic_ref;
      } else if (directSenses.length === 1 && directSenses[0]) {
        this.senseSelectedRef = directSenses[0].sense_semantic_ref;
      } else {
        this.senseSelectedRef = null;
      }
    } catch (error) {
      this.senseLookupStatus = 'error';
      this.senseError = this.messageFor(error, 'The dictionary could not be looked up.');
    }
  }

  private async performSenseChange(): Promise<void> {
    const card = this.senseTarget;
    if (!card) return;
    const targetSense = this.senseSelectedRef;
    if (!targetSense) {
      this.senseError = 'Choose a dictionary sense to use for this card.';
      return;
    }
    if (!this.senseLookupAssetToken) {
      this.senseError = 'The dictionary token is missing. Close this dialog and retry.';
      return;
    }
    this.senseState = 'saving';
    this.senseError = '';
    try {
      await vocabClient.changeNoteSense(card.note_id, {
        asset_token: this.senseLookupAssetToken,
        sense_semantic_ref: targetSense,
      });
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.senseState = 'error';
        this.senseError = 'Sense change succeeded, but the deck list could not be refreshed.';
        return;
      }
      const deckId = this.selectedDeckId;
      const refreshedCards = deckId !== null ? await this.loadDeckCards(deckId) : null;
      if (deckId !== null && refreshedCards === null) {
        this.senseState = 'error';
        this.senseError = 'Sense change succeeded, but the deck cards could not be refreshed.';
        return;
      }
      this.successMessage = `Updated dictionary sense for “${card.headword}”.`;
      this.senseTarget = null;
      this.senseCandidates = [];
      this.senseLookupAssetToken = '';
      this.senseSelectedRef = null;
      this.senseState = 'saved';
    } catch (error) {
      this.senseState = 'error';
      this.senseError = this.messageForSense(error);
    } finally {
      if (this.senseState !== 'error' && this.senseState !== 'saved') {
        this.senseState = 'idle';
      }
    }
  }

  private messageForSense(error: unknown): string {
    if (error instanceof ApiError) {
      if (error.code === 'dictionary_changed') {
        return 'The dictionary changed since this dialog opened. Close it and try again.';
      }
      if (error.code === 'invalid_sense_ref') {
        return 'The chosen sense is no longer in the active dictionary. Close this dialog and try again.';
      }
      if (error.code === 'same_lemma_required') {
        return 'The chosen sense belongs to a different word. Pick a sense for this card\u2019s own word.';
      }
      if (error.code === 'selected_sense_conflict') {
        return 'Another card already uses that dictionary sense. Pick a different sense or open that card and change it first.';
      }
      if (error.code === 'legacy_duplicate_conflict') {
        return 'Existing duplicate vocabulary needs attention before this card can change its dictionary sense.';
      }
      if (error.code === 'inconsistent_note_identity') {
        return 'This card\u2019s persisted identity is inconsistent. Reload and try again.';
      }
      if (error.code === 'unsupported_selected_sense_edit') {
        return 'This card cannot change its selected sense here.';
      }
      if (error.code === 'promotion_gates_failed') {
        return 'This card has review history and cannot be promoted to a resolved dictionary sense.';
      }
      if (error.code === 'note_not_found') {
        return 'This card no longer exists. Reload to see the current state.';
      }
    }
    return this.messageFor(error, 'The dictionary sense could not be changed.');
  }

  private closeRenameDialog(): void {
    if (this.renameState === 'saving') return;
    this.renameOpen = false;
    this.renameDraft = '';
    this.renameError = '';
  }

  private async performRename(): Promise<void> {
    const deck = this.selectedDeck();
    if (!deck || this.isOrphanedDeck(deck)) return;
    const trimmed = this.renameDraft.trim();
    if (!trimmed) {
      this.renameError = 'Deck name must not be blank.';
      return;
    }
    if (trimmed === deck.name) {
      this.renameOpen = false;
      this.renameDraft = '';
      return;
    }
    this.renameState = 'saving';
    this.renameError = '';
    try {
      const updated = await vocabClient.renameDeck(deck.id, trimmed);
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.renameError = 'Deck may have been renamed, but the deck list could not be refreshed.';
        return;
      }
      const confirmed = refreshedDecks.find((item) => item.id === updated.id && item.name === updated.name);
      if (!confirmed) {
        this.renameError = 'The server did not return the renamed deck. The change was not confirmed.';
        return;
      }
      this.selectedDeckId = confirmed.id;
      this.manualDeckId = confirmed.id;
      this.captureDeckId = confirmed.id;
      this.importDeckId = confirmed.id;
      this.successMessage = `Renamed deck to “${confirmed.name}”.`;
      this.renameOpen = false;
      this.renameDraft = '';
    } catch (error) {
      if (error instanceof ApiError && error.code === 'deck_name_conflict') {
        this.renameError = `A deck named “${trimmed}” already exists. Choose another name.`;
      } else {
        this.renameError = this.messageFor(error, 'Deck could not be renamed.');
      }
    } finally {
      this.renameState = 'idle';
    }
  }

  // ---------------------------------------------------------------------------
  // Management-side pronunciation (re-uses the same upload/revert contract as
  // the study flow, scoped to the note currently being edited).
  // ---------------------------------------------------------------------------

  private stopManagementAudio(): void {
    if (this.mgmtAudioPlayer) {
      this.mgmtAudioPlayer.pause();
      this.mgmtAudioPlayer.src = '';
      this.mgmtAudioPlayer = null;
    }
    if (this.mgmtAudioStatus === 'playing') this.mgmtAudioStatus = 'idle';
  }

  private async playManagementPronunciation(): Promise<void> {
    const card = this.editingCard;
    if (!card || this.mgmtAudioStatus === 'loading') return;
    this.stopManagementAudio();
    this.mgmtAudioStatus = 'loading';
    this.mgmtAudioMessage = 'Loading pronunciation…';
    try {
      const requestId = card.has_custom_audio ? card.note_id : card.headword;
      const blob = await vocabClient.fetchAudio(requestId);
      const url = URL.createObjectURL(blob);
      const player = new Audio(url);
      this.mgmtAudioPlayer = player;
      player.onended = () => {
        URL.revokeObjectURL(url);
        this.mgmtAudioPlayer = null;
        this.mgmtAudioStatus = 'idle';
        this.mgmtAudioMessage = '';
      };
      await player.play();
      this.mgmtAudioStatus = 'playing';
      this.mgmtAudioMessage = 'Playing pronunciation…';
    } catch (error) {
      this.mgmtAudioStatus = 'unavailable';
      this.mgmtAudioMessage = this.messageFor(error, 'Pronunciation is unavailable right now.');
    }
  }

  private releaseManagementRecordingPreview(): void {
    if (this.mgmtRecordingPreviewUrl) URL.revokeObjectURL(this.mgmtRecordingPreviewUrl);
    this.mgmtRecordingPreviewUrl = '';
  }

  private setManagementLocalRecording(blob: Blob): void {
    this.releaseManagementRecordingPreview();
    this.mgmtRecordingBlob = blob;
    this.mgmtRecordingNoteId = this.editingCard?.note_id ?? null;
    this.mgmtRecordingPreviewUrl = URL.createObjectURL(blob);
    this.mgmtRecordingStatus = 'ready';
    this.mgmtRecordingError = '';
  }

  private async startManagementRecording(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      this.mgmtRecordingError = 'Recording is not available in this browser. You can choose an audio file instead.';
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      this.recordingChunks = [];
      recorder.ondataavailable = (event) => { if (event.data.size) this.recordingChunks.push(event.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        this.setManagementLocalRecording(new Blob(this.recordingChunks, { type: recorder.mimeType || 'audio/webm' }));
      };
      recorder.start();
      this.mediaRecorder = recorder;
      this.mgmtRecordingStatus = 'recording';
      this.mgmtRecordingError = '';
    } catch (error) {
      this.mgmtRecordingError = this.messageFor(error, 'Microphone access was not granted. You can choose an audio file instead.');
    }
  }

  private stopManagementRecording(): void {
    if (this.mediaRecorder?.state === 'recording') this.mediaRecorder.stop();
    this.mediaRecorder = null;
  }

  private selectManagementAudioFile(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (file) this.setManagementLocalRecording(file);
  }

  private discardManagementRecording(): void {
    this.releaseManagementRecordingPreview();
    this.mgmtRecordingBlob = null;
    this.mgmtRecordingNoteId = null;
    this.mgmtRecordingStatus = 'idle';
    this.mgmtRecordingError = '';
  }

  private async saveManagementRecording(): Promise<void> {
    const card = this.editingCard;
    const recording = this.mgmtRecordingBlob;
    if (!card || !recording || this.mgmtRecordingNoteId !== card.note_id) return;
    this.mgmtRecordingStatus = 'saving';
    this.mgmtRecordingError = '';
    try {
      await vocabClient.uploadAudio(card.note_id, recording, recording.type || 'audio/webm');
      this.discardManagementRecording();
      this.mgmtShowRecordingControls = false;
      const refreshed = await this.loadDeckCards(this.selectedDeckId ?? -1);
      if (refreshed) {
        const updated = refreshed.find((row) => row.note_id === card.note_id);
        if (updated) this.editingCard = updated;
      }
      this.mgmtAudioMessage = 'Custom pronunciation saved.';
    } catch (error) {
      this.mgmtRecordingStatus = 'save-error';
      this.mgmtRecordingError = this.messageFor(error, 'The recording was not saved. Your local take is still available.');
    }
  }

  private async revertManagementCustomAudio(): Promise<void> {
    const card = this.editingCard;
    if (!card) return;
    this.mgmtAudioMessage = '';
    try {
      const result = await vocabClient.revertAudio(card.note_id);
      if (!result.reverted) throw new Error('The server did not confirm the change.');
      this.mgmtRevertConfirmation = false;
      const refreshed = await this.loadDeckCards(this.selectedDeckId ?? -1);
      if (refreshed) {
        const updated = refreshed.find((row) => row.note_id === card.note_id);
        if (updated) this.editingCard = updated;
      }
      this.mgmtAudioMessage = 'Automatic pronunciation restored.';
    } catch (error) {
      this.mgmtAudioMessage = this.messageFor(error, 'Automatic pronunciation could not be restored.');
    }
  }

  private openDeck(deck: DeckSummary): void {
    this.selectedDeckId = deck.id;
    this.manualDeckId = deck.id;
    this.captureDeckId = deck.id;
    this.importDeckId = deck.id;
    this.view = 'deck';
    this.deckTab = this.lastDeckTabByDeck.get(deck.id) ?? 'overview';
    this.successMessage = '';
  }

  private async openStudy(deckId?: number): Promise<void> {
    if (this.recordingBlob || this.recordingStatus === 'recording') {
      this.view = 'study';
      this.errorMessage = 'Save or discard the local recording before changing study sessions.';
      return;
    }
    this.view = 'study';
    this.studyDeckId = deckId ?? null;
    this.studyCard = null;
    this.isRevealed = false;
    this.extraInfoOpen = extraInfoOpenOnCardLoad();
    this.studyError = '';
    this.clearPronunciationState();
    await this.loadStudyCard();
  }

  /**
   * Leave an active study session without submitting a rating or touching
   * FSRS. A pending local recording is never silently discarded: leaving is
   * blocked until the user saves or discards it, matching the existing
   * card-to-card safety rule.
   */
  private backFromStudy(): void {
    if (this.recordingBlob || this.recordingStatus === 'recording') {
      this.studyError = 'Save or discard the local recording before leaving this study session.';
      return;
    }
    this.studyError = '';
    if (this.studyDeckId !== null) {
      this.selectedDeckId = this.studyDeckId;
      this.importDeckId = this.studyDeckId;
      this.view = 'deck';
      return;
    }
    this.selectedDeckId = null;
    this.view = 'decks';
  }

  private clearPronunciationState(): void {
    this.stopAudio();
    this.audioMessage = '';
    this.audioStatus = 'idle';
    this.showRecordingControls = false;
    this.revertConfirmation = false;
  }

  private async loadStudyCard(): Promise<void> {
    this.studyStatus = 'loading';
    this.studyError = '';
    try {
      const response = await vocabClient.getNextCard(this.studyDeckId ?? undefined);
      this.studyCard = response.card;
      this.isRevealed = false;
      this.extraInfoOpen = extraInfoOpenOnCardLoad();
      this.hasCustomAudio = Boolean(response.card?.front.audio_trigger.token?.startsWith('custom:'));
      this.glossDrafts = { de: this.userGlossValue(response.card, 'de'), en: this.userGlossValue(response.card, 'en') };
      this.glossState = '';
      this.glossError = '';
      this.studyStatus = response.card ? 'ready' : 'empty';
      if (!response.card) this.focusTarget = 'empty';
    } catch (error) {
      this.studyCard = null;
      this.studyStatus = 'error';
      this.studyError = this.messageFor(error, 'The next card could not be loaded.');
    }
  }

  private revealCard(): void {
    if (!this.studyCard || this.isRevealed || this.isReviewing) return;
    this.isRevealed = true;
    this.extraInfoOpen = extraInfoOpenOnReveal(this.alwaysShowExtraInfo);
    this.focusTarget = 'answer';
  }

  private toggleExtraInfo(): void {
    this.extraInfoOpen = !this.extraInfoOpen;
  }

  private setAlwaysShowExtraInfo(checked: boolean): void {
    this.alwaysShowExtraInfo = checked;
    writeAlwaysShowExtraInfo(localStorageOrNull(), checked);
    this.extraInfoOpen = extraInfoOpenOnPreferenceChange({
      isRevealed: this.isRevealed,
      newPreference: checked,
    });
  }

  private async submitConfidence(confidence: number): Promise<void> {
    const card = this.studyCard;
    if (!card || !this.isRevealed || this.isReviewing) return;
    if (this.recordingBlob) {
      this.studyError = 'Save or discard the local recording before continuing to the next card.';
      return;
    }
    this.isReviewing = true;
    this.studyError = '';
    try {
      await vocabClient.reviewCard(card.card_id, confidence);
      await this.loadStudyCard();
    } catch (error) {
      this.studyError = this.messageFor(error, 'Your confidence could not be saved. Try the same rating again.');
    } finally {
      this.isReviewing = false;
    }
  }

  private readonly handleStudyKeydown = (event: KeyboardEvent): void => {
    if (this.view !== 'study') return;
    const target = event.target as HTMLElement | null;
    if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
    if (event.code === 'Space' && !this.isRevealed) {
      event.preventDefault();
      this.revealCard();
      return;
    }
    if (event.key >= '1' && event.key <= '5' && this.isRevealed) {
      event.preventDefault();
      void this.submitConfidence(Number(event.key));
      return;
    }
    if (event.key.toLowerCase() === 'r') {
      event.preventDefault();
      void this.playPronunciation();
    }
  };

  private readonly handleGlobalKeydown = (event: KeyboardEvent): void => {
    if (event.key !== 'Escape') return;
    if (this.editingCard) {
      if (this.editState === 'saving-languages' || this.editState === 'saving-gloss') return;
      event.preventDefault();
      this.closeEditDialog();
      return;
    }
    if (this.moveTarget) {
      if (this.moveState === 'saving') return;
      event.preventDefault();
      this.closeMoveDialog();
      return;
    }
    if (this.removeTarget) {
      if (this.removeState === 'saving') return;
      event.preventDefault();
      this.closeRemoveConfirm();
      return;
    }
    if (this.renameOpen) {
      if (this.renameState === 'saving') return;
      event.preventDefault();
      this.closeRenameDialog();
      return;
    }
    if (this.restoreTarget) {
      if (this.restoreState === 'saving') return;
      event.preventDefault();
      this.closeRestoreDialog();
      return;
    }
    if (this.createFolderOpen) {
      if (this.createFolderState === 'saving') return;
      event.preventDefault();
      this.closeCreateFolderDialog();
      return;
    }
    if (this.renameFolderTarget) {
      if (this.renameFolderState === 'saving') return;
      event.preventDefault();
      this.closeRenameFolderDialog();
      return;
    }
    if (this.deleteFolderTarget) {
      if (this.deleteFolderState === 'saving') return;
      event.preventDefault();
      this.closeDeleteFolderDialog();
      return;
    }
    if (this.moveDeckTarget) {
      if (this.moveDeckState === 'saving') return;
      event.preventDefault();
      this.closeMoveDeckDialog();
    }
  };

  private meaningFor(card: NextCardData | null, language: MeaningLanguage): RenderedMeaning | undefined {
    return card?.back.meanings.find((meaning) => meaning.language === language);
  }

  private userGlossValue(card: NextCardData | null, language: MeaningLanguage): string {
    const meaning = this.meaningFor(card, language);
    return meaning?.is_user_authored ? meaning.lines.join(' ') : '';
  }

  private async saveGloss(language: MeaningLanguage): Promise<void> {
    const card = this.studyCard;
    const meaningText = this.glossDrafts[language].trim();
    if (!card || !meaningText) return;
    this.glossSavingLanguage = language;
    this.glossError = '';
    this.glossState = '';
    try {
      const result = await vocabClient.setGloss(card.note_id, language, meaningText);
      this.glossDrafts = { ...this.glossDrafts, [language]: result.meaning_text };
      this.glossState = `${language === 'de' ? 'German' : 'English'} meaning saved.`;
      await this.refreshStudyFace(card.card_id);
    } catch (error) {
      this.glossError = this.messageFor(error, 'That meaning could not be saved.');
    } finally {
      this.glossSavingLanguage = null;
    }
  }

  private async deleteGloss(language: MeaningLanguage): Promise<void> {
    const card = this.studyCard;
    if (!card) return;
    this.glossSavingLanguage = language;
    this.glossError = '';
    this.glossState = '';
    try {
      const result = await vocabClient.deleteGloss(card.note_id, language);
      if (!result.deleted) throw new Error('The server did not confirm removal.');
      this.glossDrafts = { ...this.glossDrafts, [language]: '' };
      this.glossState = `${language === 'de' ? 'German' : 'English'} meaning removed.`;
      await this.refreshStudyFace(card.card_id);
    } catch (error) {
      this.glossError = this.messageFor(error, 'That meaning could not be removed.');
    } finally {
      this.glossSavingLanguage = null;
    }
  }

  private async refreshStudyFace(cardId: number): Promise<void> {
    try {
      const response = await vocabClient.getNextCard(this.studyDeckId ?? undefined);
      if (response.card?.card_id === cardId) {
        this.studyCard = response.card;
        this.hasCustomAudio = Boolean(response.card.front.audio_trigger.token?.startsWith('custom:'));
      }
    } catch {
      // The mutation result is already server-confirmed; leave the visible face usable.
    }
  }

  private audioRequestId(card: NextCardData): string | number {
    return this.hasCustomAudio ? card.note_id : card.front.audio_trigger.lemma;
  }

  private stopAudio(): void {
    if (this.audioPlayer) {
      this.audioPlayer.pause();
      this.audioPlayer.src = '';
      this.audioPlayer = null;
    }
    if (this.audioStatus === 'playing') this.audioStatus = 'idle';
  }

  private async playPronunciation(): Promise<void> {
    const card = this.studyCard;
    if (!card || !card.front.audio_trigger.available || this.audioStatus === 'loading') return;
    this.stopAudio();
    this.audioStatus = 'loading';
    this.audioMessage = 'Loading pronunciation…';
    try {
      const blob = await vocabClient.fetchAudio(this.audioRequestId(card));
      const url = URL.createObjectURL(blob);
      const player = new Audio(url);
      this.audioPlayer = player;
      player.onended = () => { URL.revokeObjectURL(url); this.audioPlayer = null; this.audioStatus = 'idle'; this.audioMessage = ''; };
      await player.play();
      this.audioStatus = 'playing';
      this.audioMessage = 'Playing pronunciation…';
    } catch (error) {
      this.audioStatus = 'unavailable';
      this.audioMessage = this.messageFor(error, 'Pronunciation is unavailable right now.');
    }
  }

  private releaseRecordingPreview(): void {
    if (this.recordingPreviewUrl) URL.revokeObjectURL(this.recordingPreviewUrl);
    this.recordingPreviewUrl = '';
  }

  private setLocalRecording(blob: Blob): void {
    this.releaseRecordingPreview();
    this.recordingBlob = blob;
    this.recordingNoteId = this.studyCard?.note_id ?? null;
    this.recordingPreviewUrl = URL.createObjectURL(blob);
    this.recordingStatus = 'ready';
    this.recordingError = '';
  }

  private async startRecording(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      this.recordingError = 'Recording is not available in this browser. You can choose an audio file instead.';
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      this.recordingChunks = [];
      recorder.ondataavailable = (event) => { if (event.data.size) this.recordingChunks.push(event.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        this.setLocalRecording(new Blob(this.recordingChunks, { type: recorder.mimeType || 'audio/webm' }));
      };
      recorder.start();
      this.mediaRecorder = recorder;
      this.recordingStatus = 'recording';
      this.recordingError = '';
    } catch (error) {
      this.recordingError = this.messageFor(error, 'Microphone access was not granted. You can choose an audio file instead.');
    }
  }

  private stopRecording(): void {
    if (this.mediaRecorder?.state === 'recording') this.mediaRecorder.stop();
    this.mediaRecorder = null;
  }

  private selectAudioFile(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (file) this.setLocalRecording(file);
  }

  private discardRecording(): void {
    this.releaseRecordingPreview();
    this.recordingBlob = null;
    this.recordingNoteId = null;
    this.recordingStatus = 'idle';
    this.recordingError = '';
  }

  private async saveRecording(): Promise<void> {
    const card = this.studyCard;
    const recording = this.recordingBlob;
    if (!card || !recording || this.recordingNoteId !== card.note_id) return;
    this.recordingStatus = 'saving';
    this.recordingError = '';
    try {
      await vocabClient.uploadAudio(card.note_id, recording, recording.type || 'audio/webm');
      this.discardRecording();
      this.showRecordingControls = false;
      this.hasCustomAudio = true;
      this.audioMessage = 'Custom pronunciation saved.';
      await this.refreshStudyFace(card.card_id);
    } catch (error) {
      this.recordingStatus = 'save-error';
      this.recordingError = this.messageFor(error, 'The recording was not saved. Your local take is still available.');
    }
  }

  private async revertCustomAudio(): Promise<void> {
    const card = this.studyCard;
    if (!card) return;
    this.audioMessage = '';
    try {
      const result = await vocabClient.revertAudio(card.note_id);
      if (!result.reverted) throw new Error('The server did not confirm the change.');
      this.hasCustomAudio = false;
      this.revertConfirmation = false;
      this.audioMessage = 'Automatic pronunciation restored.';
      await this.refreshStudyFace(card.card_id);
    } catch (error) {
      this.audioMessage = this.messageFor(error, 'Automatic pronunciation could not be restored.');
    }
  }

  private selectedDeck(): DeckSummary | undefined {
    return this.decks.find((deck) => deck.id === this.selectedDeckId);
  }

  private manualDeck(): DeckSummary | undefined {
    const targetId = this.manualDeckId ?? this.selectedDeckId;
    return this.decks.find((deck) => deck.id === targetId);
  }

  private resetManualSelection(): void {
    this.selectedCandidate = null;
    this.selectedSenseRef = null;
    this.selectedMeaningLanguages = ['de', 'en'];
    this.userMeaningDe = '';
    this.userMeaningEn = '';
  }

  private selectCandidate(candidate: Candidate): void {
    this.selectedCandidate = candidate;
    if (candidate.status !== 'resolved' || !candidate.senses?.length) {
      this.selectedSenseRef = null;
      return;
    }
    const englishSenses = candidate.senses.filter((s) =>
      s.meanings?.some((m) => m.language === 'en' && m.text?.trim())
    );
    if (englishSenses.length === 1 && englishSenses[0]) {
      this.selectedSenseRef = englishSenses[0].sense_semantic_ref;
    } else if (englishSenses.length > 1) {
      this.selectedSenseRef = null;
    } else if (candidate.senses.length === 1 && candidate.senses[0]) {
      this.selectedSenseRef = candidate.senses[0].sense_semantic_ref;
    } else {
      this.selectedSenseRef = null;
    }
  }

  private toggleMeaningLanguage(language: MeaningLanguage, checked: boolean): void {
    this.selectedMeaningLanguages = checked
      ? [...new Set([...this.selectedMeaningLanguages, language])]
      : this.selectedMeaningLanguages.filter((selected) => selected !== language);
  }

  private async lookup(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const query = this.lookupQuery.trim();
    if (!query) {
      this.errorMessage = 'Enter a German word before looking it up.';
      this.successMessage = '';
      return;
    }

    this.lookupStatus = 'loading';
    this.lookupCandidates = [];
    this.lookupAssetToken = '';
    this.lastSavedNote = null;
    this.resetManualSelection();
    this.errorMessage = '';
    this.successMessage = '';
    try {
      const result = await vocabClient.lookup(query);
      const candidates = result.candidates.map((candidate) => ({
        ...candidate,
        status: candidate.status ?? (candidate.senses?.length ? 'resolved' : 'needs_gloss'),
        senses: candidate.senses?.map((sense) => ({
          ...sense,
          gloss: sense.gloss ?? sense.meanings?.[0]?.text ?? '',
        })),
      }));
      this.lookupCandidates = candidates;
      this.lookupAssetToken = result.asset_token;
      this.lookupStatus = 'ready';
      const soleCandidate = candidates.length === 1 ? candidates[0] : undefined;
      if (soleCandidate) this.selectCandidate(soleCandidate);
    } catch (error) {
      this.lookupStatus = 'error';
      this.errorMessage = this.messageFor(error, 'German vocabulary could not be looked up.');
    }
  }

  private userMeanings(): Record<string, string> | undefined {
    const meanings: Record<string, string> = {};
    if (this.userMeaningDe.trim()) meanings.de = this.userMeaningDe.trim();
    if (this.userMeaningEn.trim()) meanings.en = this.userMeaningEn.trim();
    return Object.keys(meanings).length ? meanings : undefined;
  }

  private async saveManualNote(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const candidate = this.selectedCandidate;
    const deck = this.manualDeck();
    if (!candidate || !this.lookupAssetToken) {
      this.errorMessage = 'Look up and select a German vocabulary candidate before saving.';
      this.successMessage = '';
      return;
    }
    if (!deck) {
      this.errorMessage = 'Select a deck before saving this vocabulary.';
      this.successMessage = '';
      return;
    }
    if (!this.selectedMeaningLanguages.length) {
      this.errorMessage = 'Select German, English, or both meaning languages.';
      this.successMessage = '';
      return;
    }
    if (candidate.status === 'resolved' && !this.selectedSenseRef) {
      this.errorMessage = 'Select a meaning for this resolved dictionary entry.';
      this.successMessage = '';
      return;
    }
    if (candidate.status === 'derived_compound' && !candidate.component_refs?.length) {
      this.errorMessage = 'This derived compound has no supported component bindings to save.';
      this.successMessage = '';
      return;
    }

    this.isSavingNote = true;
    this.errorMessage = '';
    this.successMessage = '';
    try {
      const result = await vocabClient.createNote({
        asset_token: this.lookupAssetToken,
        lemma_semantic_ref: candidate.lemma_semantic_ref,
        sense_semantic_ref: this.selectedSenseRef,
        status: candidate.status,
        component_refs: candidate.component_refs,
        meaning_languages: this.selectedMeaningLanguages,
        deck_name: deck.name,
        user_meanings: this.userMeanings(),
      });
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.errorMessage = `“${candidate.lemma}” may have been saved, but the deck list could not be refreshed.`;
        return;
      }
      const refreshedDeck = refreshedDecks.find((item) => item.id === result.deck_id);
      if (result.deck_id !== deck.id || !refreshedDeck) {
        this.errorMessage = `The server did not confirm “${candidate.lemma}” in the selected deck. It was not reported as saved.`;
        return;
      }
      this.selectedDeckId = refreshedDeck.id;
      this.manualDeckId = refreshedDeck.id;
      this.lastSavedNote = {
        lemma: candidate.lemma,
        deckId: refreshedDeck.id,
        deckName: refreshedDeck.name,
      };
      this.successMessage = `Saved “${candidate.lemma}” to “${refreshedDeck.name}”.`;
      this.lookupQuery = '';
      this.lookupCandidates = [];
      this.lookupAssetToken = '';
      this.lookupStatus = 'idle';
      this.resetManualSelection();
    } catch (error) {
      this.successMessage = '';
      this.errorMessage = this.messageFor(error, 'Vocabulary could not be saved.');
    } finally {
      this.isSavingNote = false;
    }
  }

  private captureKey(candidate: Candidate): string {
    return `${candidate.lemma_semantic_ref}:${candidate.status}`;
  }

  private updateCaptureSpan(event: Event): void {
    const input = event.target as HTMLTextAreaElement;
    this.captureSpanStart = input.selectionStart ?? 0;
    this.captureSpanEnd = input.selectionEnd ?? 0;
  }

  private resetCapturePicker(): void {
    this.captureCandidates = [];
    this.captureAssetToken = '';
    this.captureContext = null;
    this.captureSelections = {};
    this.captureDictionaryChanged = false;
  }

  private async highlightCapture(event?: Event): Promise<void> {
    event?.preventDefault();
    const sentenceText = this.captureSentence;
    const lessonLabel = this.captureLessonLabel.trim();
    const selectedSpan = { start: this.captureSpanStart, end: this.captureSpanEnd };
    if (!sentenceText.trim()) {
      this.captureStatus = 'error';
      this.captureError = 'Enter the sentence you want this card to remember.';
      return;
    }
    if (selectedSpan.start === selectedSpan.end) {
      this.captureStatus = 'error';
      this.captureError = 'Select the German word or phrase in the sentence before finding candidates.';
      return;
    }
    if (!lessonLabel) {
      this.captureStatus = 'error';
      this.captureError = 'Add a lesson label so this capture keeps its provenance.';
      return;
    }

    this.captureStatus = 'loading';
    this.captureError = '';
    this.resetCapturePicker();
    try {
      const result = await vocabClient.highlight({
        sentence_text: sentenceText,
        selected_span: selectedSpan,
        lesson_label: lessonLabel,
      });
      this.captureCandidates = result.candidates;
      this.captureAssetToken = result.asset_token;
      this.captureContext = result.capture_context;
      this.captureStatus = 'ready';
      const soleCandidate = result.candidates.length === 1 ? result.candidates[0] : undefined;
      if (soleCandidate) this.toggleCaptureCandidate(soleCandidate, true);
    } catch (error) {
      this.captureStatus = 'error';
      this.captureError = this.messageFor(error, 'Candidates could not be found.');
    }
  }

  private toggleCaptureCandidate(candidate: Candidate, checked: boolean): void {
    const key = this.captureKey(candidate);
    const selections = { ...this.captureSelections };
    if (checked) {
      selections[key] = {
        candidate,
        senseRef: candidate.status === 'resolved' ? candidate.senses?.[0]?.sense_semantic_ref ?? null : null,
      };
    } else {
      delete selections[key];
    }
    this.captureSelections = selections;
  }

  private setCaptureSense(candidate: Candidate, senseRef: string): void {
    const key = this.captureKey(candidate);
    const current = this.captureSelections[key];
    if (!current) return;
    this.captureSelections = { ...this.captureSelections, [key]: { ...current, senseRef } };
  }

  private toggleCaptureMeaningLanguage(language: MeaningLanguage, input: HTMLInputElement): void {
    if (input.checked) {
      this.captureMeaningLanguages = [...new Set([...this.captureMeaningLanguages, language])];
      return;
    }
    if (this.captureMeaningLanguages.length === 1) {
      // At least one meaning language must stay selected. Restore the
      // checkbox directly: the bound value is unchanged, so Lit would not
      // rewrite the property after the browser toggled it.
      input.checked = true;
      return;
    }
    this.captureMeaningLanguages = this.captureMeaningLanguages.filter((item) => item !== language);
  }

  private captureUserMeanings(): Record<string, string> | undefined {
    const meanings: Record<string, string> = {};
    if (this.captureUserMeaningDe.trim()) meanings.de = this.captureUserMeaningDe.trim();
    if (this.captureUserMeaningEn.trim()) meanings.en = this.captureUserMeaningEn.trim();
    return Object.keys(meanings).length ? meanings : undefined;
  }

  private async saveCapture(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const deck = this.decks.find((item) => item.id === this.captureDeckId);
    const selections = Object.values(this.captureSelections);
    if (!selections.length) return;
    if (!deck) {
      this.captureError = 'Choose a destination deck before creating cards.';
      return;
    }
    if (!this.captureContext || !this.captureAssetToken) {
      this.captureError = 'Find candidates again before creating cards.';
      return;
    }
    const incomplete = selections.some(({ candidate, senseRef }) => candidate.status === 'resolved' && !senseRef);
    if (incomplete) {
      this.captureError = 'Choose a dictionary meaning for every selected candidate.';
      return;
    }

    this.isCapturing = true;
    this.captureError = '';
    this.captureDictionaryChanged = false;
    this.successMessage = '';
    try {
      const result = await vocabClient.captureCards({
        asset_token: this.captureAssetToken,
        deck: { name: deck.name, lesson_label: this.captureContext.lesson_label },
        capture_context: this.captureContext,
        selections: selections.map(({ candidate, senseRef }) => ({
          lemma_semantic_ref: candidate.lemma_semantic_ref,
          sense_semantic_ref: senseRef,
          status: candidate.status,
          component_refs: candidate.component_refs,
          overrides: {
            meaning_langs: this.captureMeaningLanguages,
            user_meanings: this.captureUserMeanings(),
          },
        })),
      });
      const refreshedDecks = await this.loadDecks();
      const selectedDeck = refreshedDecks?.find((item) => item.id === result.deck_id);
      if (!selectedDeck || selectedDeck.id !== deck.id) {
        this.captureError = 'The server did not confirm the selected destination deck. Cards were not reported as created.';
        return;
      }
      const created = result.notes.filter((note) => note.created).length;
      const reused = result.notes.length - created;
      this.selectedDeckId = selectedDeck.id;
      this.manualDeckId = selectedDeck.id;
      this.captureDeckId = selectedDeck.id;
      this.successMessage = `Server confirmed ${created} ${created === 1 ? 'card' : 'cards'} created and ${reused} ${reused === 1 ? 'card' : 'cards'} reused in “${selectedDeck.name}”.`;
      this.captureStatus = 'idle';
      this.captureSentence = '';
      this.captureLessonLabel = '';
      this.captureSpanStart = 0;
      this.captureSpanEnd = 0;
      this.captureUserMeaningDe = '';
      this.captureUserMeaningEn = '';
      this.resetCapturePicker();
    } catch (error) {
      if (error instanceof ApiError && error.code === 'legacy_duplicate_conflict') {
        this.captureError = 'Capture stopped because existing duplicate vocabulary needs attention. No words from this capture were added.';
      } else if (error instanceof ApiError && error.isConflict) {
        this.captureDictionaryChanged = true;
        this.captureError = '';
      } else {
        this.captureError = this.messageFor(error, 'Cards could not be created.');
      }
    } finally {
      this.isCapturing = false;
    }
  }

  private async readImportFile(event: Event): Promise<void> {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    this.isReadingImportFile = true;
    this.errorMessage = '';
    this.successMessage = '';
    try {
      this.importText = await file.text();
      this.importFileName = file.name;
    } catch (error) {
      this.importFileName = '';
      this.errorMessage = this.messageFor(error, 'The selected file could not be read.');
    } finally {
      this.isReadingImportFile = false;
    }
  }

  private async importCsv(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const importDeck = this.decks.find((item) => item.id === this.importDeckId);
    const csvText = this.importText.trim();
    if (!importDeck) {
      this.errorMessage = 'Select a destination deck before importing.';
      this.successMessage = '';
      return;
    }
    if (!csvText) {
      this.errorMessage = 'Paste vocabulary lines or choose a CSV/text file before importing.';
      this.successMessage = '';
      return;
    }

    this.isImporting = true;
    this.errorMessage = '';
    this.successMessage = '';
    try {
      const result = await vocabClient.importCsv({ csv_text: csvText, deck_name: importDeck.name });
      const refreshedDecks = await this.loadDecks();
      if (refreshedDecks === null) {
        this.errorMessage = `The import may have completed, but the deck list could not be refreshed.`;
        return;
      }
      const importedDeck = refreshedDecks.find((deck) => deck.id === result.deck_id);
      if (!importedDeck) {
        this.errorMessage = 'The server did not return the import deck after completion. The import was not reported as successful.';
        return;
      }
      this.selectedDeckId = importedDeck.id;
      this.manualDeckId = importedDeck.id;
      this.importDeckId = importedDeck.id;
      this.successMessage = `Import complete: ${result.notes_created} added, ${result.notes_reused} already existed, ${result.total_words} total in “${importedDeck.name}”.`;
      this.importText = '';
      this.importFileName = '';
    } catch (error) {
      this.successMessage = '';
      if (error instanceof ApiError && error.code === 'legacy_duplicate_conflict') {
        this.errorMessage = 'Import stopped because existing duplicate vocabulary needs attention. No words from this import were added.';
      } else {
        this.errorMessage = this.messageFor(error, 'CSV import could not be completed.');
      }
    } finally {
      this.isImporting = false;
    }
  }

  private async exportTsv(deck: DeckSummary): Promise<void> {
    this.exportingFormat = 'tsv';
    this.errorMessage = '';
    this.successMessage = '';
    try {
      const tsv = await vocabClient.exportAnki(deck.id);
      const url = URL.createObjectURL(new Blob([tsv], { type: 'text/tab-separated-values;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `${deck.name.replace(/[^a-z0-9._-]+/gi, '-') || 'flashcards'}.tsv`;
      link.click();
      URL.revokeObjectURL(url);
      this.successMessage = `Prepared a TSV export for “${deck.name}”.`;
    } catch (error) {
      this.errorMessage = this.messageFor(error, 'TSV export could not be prepared.');
    } finally {
      this.exportingFormat = null;
    }
  }

  private async exportApkg(deck: DeckSummary): Promise<void> {
    this.exportingFormat = 'apkg';
    this.errorMessage = '';
    this.successMessage = '';
    try {
      const apkg = await vocabClient.exportApkg(deck.id);
      const url = URL.createObjectURL(apkg);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${deck.name.replace(/[^a-z0-9._-]+/gi, '-') || 'flashcards'}.apkg`;
      link.click();
      URL.revokeObjectURL(url);
      this.successMessage = `Prepared an APKG export for “${deck.name}”.`;
    } catch (error) {
      this.errorMessage = this.messageFor(error, 'APKG export could not be prepared.');
    } finally {
      this.exportingFormat = null;
    }
  }

  private renderNotices() {
    return html`
      ${this.errorMessage ? html`<div class="notice error" role="alert">${this.errorMessage}</div>` : nothing}
      ${this.successMessage ? html`<div class="notice success" role="status">${this.successMessage}</div>` : nothing}
    `;
  }

  private renderNoDecksNote() {
    return html`<div class="empty"><p>No decks yet. Create one to begin organizing German vocabulary.</p></div>`;
  }

  private renderDeckRow(deck: DeckSummary, canMove: boolean) {
    const isOrphaned = this.isOrphanedDeck(deck);
    const stats = `${deck.card_count} ${deck.card_count === 1 ? 'card' : 'cards'} · ${deck.due_count} due · ${deck.mastery_percent}% mastered`;
    return html`
      <li class="deck">
        <button class="deck-open" @click=${() => this.openDeck(deck)} aria-label=${`Open ${deck.name}`}>
          <span class="deck-name">${deck.name}</span>
          <span class="deck-stats">${stats}</span>
        </button>
        <div class="deck-row-actions">
          ${canMove && !isOrphaned ? html`
            <button type="button" @click=${() => this.openMoveDeckDialog(deck)} aria-label=${`Move ${deck.name} to folder`}>Move to folder</button>
          ` : nothing}
          ${this.pendingDeleteDeckId === deck.id ? html`
            <div class="actions confirm" aria-label=${`Confirm deletion of ${deck.name}`}>
              <button class="danger" ?disabled=${this.isDeleting} @click=${() => void this.deleteDeck(deck)}>${this.isDeleting ? 'Deleting…' : 'Confirm delete'}</button>
              <button ?disabled=${this.isDeleting} @click=${() => { this.pendingDeleteDeckId = null; }}>Cancel</button>
            </div>
          ` : html`
            <button class="danger" @click=${() => { this.pendingDeleteDeckId = deck.id; this.successMessage = ''; }}>Delete</button>
          `}
        </div>
      </li>
    `;
  }

  private renderFlatDeckList() {
    return html`
      <ul class="deck-list" aria-label="Your decks">
        ${this.decks.map((deck) => this.renderDeckRow(deck, false))}
      </ul>
    `;
  }

  private renderGroupedDeckList() {
    const canMove = this.folders.length > 0;
    return html`
      ${this.groupedDeckSections().map((group) => html`
        <section class="folder-group" aria-label=${group.folder ? `Folder ${group.folder.name}` : 'Not in a folder'}>
          <div class="folder-heading-row">
            <h3 class="folder-heading">${group.folder ? group.folder.name : 'Not in a folder'}</h3>
            ${group.folder ? html`
              <div class="folder-actions">
                <button type="button" aria-label=${`Rename folder ${group.folder.name}`} @click=${() => this.openRenameFolderDialog(group.folder!)}>Rename</button>
                <button class="danger" type="button" aria-label=${`Delete folder ${group.folder.name}`} @click=${() => this.openDeleteFolderDialog(group.folder!)}>Delete folder</button>
              </div>
            ` : nothing}
          </div>
          ${group.decks.length ? html`
            <ul class="deck-list" aria-label=${group.folder ? `Decks in ${group.folder.name}` : 'Decks not in a folder'}>
              ${group.decks.map((deck) => this.renderDeckRow(deck, canMove))}
            </ul>
          ` : html`
            <p class="muted folder-empty">${group.folder ? 'No decks in this folder yet.' : 'No decks outside folders yet.'}</p>
          `}
        </section>
      `)}
    `;
  }

  private renderDeckList() {
    if (this.deckStatus === 'loading') {
      return html`<p class="loading" role="status">Loading decks…</p>`;
    }
    if (this.deckStatus === 'error') {
      return html`<div class="empty"><p>We could not reach your deck list.</p><button @click=${this.loadDecks}>Try again</button></div>`;
    }
    if (this.folderStatus === 'loading') {
      return html`
        <p class="loading" aria-live="polite">Loading folders…</p>
        ${this.decks.length ? this.renderFlatDeckList() : this.renderNoDecksNote()}
      `;
    }
    if (this.folderStatus === 'error') {
      return html`
        <div class="notice error" role="alert">
          ${this.folderError || 'Folders could not be loaded.'}
          ${' '}<button @click=${() => void this.loadFolders()}>Try again</button>
        </div>
        ${this.decks.length ? this.renderFlatDeckList() : this.renderNoDecksNote()}
      `;
    }
    if (this.decks.length === 0 && this.folders.length === 0) {
      return this.renderNoDecksNote();
    }
    const hasUserDecks = this.decks.some((deck) => !this.isOrphanedDeck(deck));
    return html`
      ${!hasUserDecks ? this.renderNoDecksNote() : nothing}
      ${this.renderGroupedDeckList()}
    `;
  }

  private renderCreateFolderDialog() {
    if (!this.createFolderOpen) return nothing;
    const busy = this.createFolderState === 'saving';
    return html`
      <div class="dialog-backdrop" @click=${(event: MouseEvent) => { if (event.target === event.currentTarget) this.closeCreateFolderDialog(); }}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="create-folder-title" data-create-folder-dialog tabindex="-1">
          <h2 id="create-folder-title">New folder</h2>
          <p class="muted">Folders group your decks. A deck belongs to at most one folder.</p>
          <form @submit=${(event: SubmitEvent) => { event.preventDefault(); void this.performCreateFolder(); }}>
            <label>Folder name
              <input
                .value=${this.newFolderName}
                @input=${(event: InputEvent) => { this.newFolderName = (event.target as HTMLInputElement).value; this.createFolderError = ''; }}
                ?disabled=${busy}
                maxlength="200"
                autocomplete="off"
              />
            </label>
            ${this.createFolderError ? html`<p class="inline-status error" role="alert">${this.createFolderError}</p>` : nothing}
            <div class="actions">
              <button type="button" @click=${this.closeCreateFolderDialog} ?disabled=${busy}>Cancel</button>
              <button class="primary" type="submit" ?disabled=${busy}>${busy ? 'Creating…' : 'Create folder'}</button>
            </div>
          </form>
        </div>
      </div>
    `;
  }

  private renderRenameFolderDialog() {
    const folder = this.renameFolderTarget;
    if (!folder) return nothing;
    const busy = this.renameFolderState === 'saving';
    return html`
      <div class="dialog-backdrop" @click=${(event: MouseEvent) => { if (event.target === event.currentTarget) this.closeRenameFolderDialog(); }}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="rename-folder-title" data-rename-folder-dialog tabindex="-1">
          <h2 id="rename-folder-title">Rename folder</h2>
          <label>Folder name
            <input
              .value=${this.renameFolderDraft}
              @input=${(event: InputEvent) => { this.renameFolderDraft = (event.target as HTMLInputElement).value; this.renameFolderError = ''; }}
              ?disabled=${busy}
              maxlength="200"
              autocomplete="off"
            />
          </label>
          ${this.renameFolderError ? html`<p class="inline-status error" role="alert">${this.renameFolderError}</p>` : nothing}
          <div class="actions">
            <button type="button" @click=${this.closeRenameFolderDialog} ?disabled=${busy}>Cancel</button>
            <button class="primary" type="button" @click=${() => void this.performRenameFolder()} ?disabled=${busy}>${busy ? 'Renaming…' : 'Rename folder'}</button>
          </div>
        </div>
      </div>
    `;
  }

  private renderDeleteFolderDialog() {
    const folder = this.deleteFolderTarget;
    if (!folder) return nothing;
    const busy = this.deleteFolderState === 'saving';
    return html`
      <div class="dialog-backdrop" @click=${(event: MouseEvent) => { if (event.target === event.currentTarget) this.closeDeleteFolderDialog(); }}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="delete-folder-title" data-delete-folder-dialog tabindex="-1">
          <h2 id="delete-folder-title">Delete folder</h2>
          <p>Delete the folder “<strong>${folder.name}</strong>”? Deleting the folder does not delete its decks; those decks become unassigned.</p>
          ${this.deleteFolderError ? html`<p class="inline-status error" role="alert">${this.deleteFolderError}</p>` : nothing}
          <div class="actions">
            <button type="button" @click=${this.closeDeleteFolderDialog} ?disabled=${busy}>Cancel</button>
            <button class="danger" type="button" @click=${() => void this.performDeleteFolder()} ?disabled=${busy}>${busy ? 'Deleting…' : 'Delete folder'}</button>
          </div>
        </div>
      </div>
    `;
  }

  private renderMoveDeckDialog() {
    const deck = this.moveDeckTarget;
    if (!deck) return nothing;
    const busy = this.moveDeckState === 'saving';
    return html`
      <div class="dialog-backdrop" @click=${(event: MouseEvent) => { if (event.target === event.currentTarget) this.closeMoveDeckDialog(); }}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="move-deck-title" data-move-deck-dialog tabindex="-1">
          <h2 id="move-deck-title">Move to folder</h2>
          <p>Move the deck “<strong>${deck.name}</strong>” to a folder. Choosing “Not in a folder” removes it from its current folder.</p>
          <label>Folder
            <select
              .value=${this.moveDeckFolderId === null ? '' : String(this.moveDeckFolderId)}
              @change=${(event: Event) => { const value = (event.target as HTMLSelectElement).value; this.moveDeckFolderId = value ? Number(value) : null; }}
              ?disabled=${busy}
              data-move-deck-folder-select
            >
              <option value="">Not in a folder</option>
              ${this.folders.map((folder) => html`<option value=${folder.id}>${folder.name}</option>`)}
            </select>
          </label>
          ${this.moveDeckError ? html`<p class="inline-status error" role="alert">${this.moveDeckError}</p>` : nothing}
          <div class="actions">
            <button type="button" @click=${this.closeMoveDeckDialog} ?disabled=${busy}>Cancel</button>
            <button class="primary" type="button" @click=${() => void this.performMoveDeck()} ?disabled=${busy}>${busy ? 'Moving…' : 'Move deck'}</button>
          </div>
        </div>
      </div>
    `;
  }

  private renderSenseChoices(senses: CandidateSense[]) {
    const englishSenses = senses.filter((s) => s.meanings?.some((m) => m.language === 'en' && m.text?.trim()));
    const visibleSenses = englishSenses.length ? englishSenses : senses;
    return html`
      <fieldset class="selection">
        <legend>Dictionary meaning</legend>
        <ul class="choice-list">
          ${visibleSenses.map((sense) => html`
            <li>
              <label class="choice">
                <input
                  type="radio"
                  name="sense"
                  .value=${sense.sense_semantic_ref}
                  .checked=${this.selectedSenseRef === sense.sense_semantic_ref}
                  @change=${() => { this.selectedSenseRef = sense.sense_semantic_ref; }}
                />
                <span>${senseDisplayLabel(sense)}</span>
              </label>
            </li>
          `)}
        </ul>
      </fieldset>
    `;
  }

  private renderManualCreation() {
    const candidate = this.selectedCandidate;
    const manualDeck = this.manualDeck();
    return html`
      <section class="workflow" aria-labelledby="manual-title">
        <h3 id="manual-title">Add German vocabulary</h3>
        <p class="muted">Look up a German word, choose its dictionary meaning, then let the server create the note.</p>
        ${this.lastSavedNote ? html`
          <div class="save-success-banner" role="region" aria-label="Vocabulary saved">
            <p>Saved “${this.lastSavedNote.lemma}” to “${this.lastSavedNote.deckName}”.</p>
            <div class="save-success-actions">
              <button class="primary" type="button" @click=${() => void this.openStudy(this.lastSavedNote!.deckId)}>Study this deck</button>
              <button class="secondary" type="button" @click=${() => { this.selectedDeckId = this.lastSavedNote!.deckId; this.importDeckId = this.lastSavedNote!.deckId; this.view = 'deck'; }}>Open deck</button>
              <button class="secondary" type="button" @click=${() => { this.lastSavedNote = null; this.lookupQuery = ''; this.resetManualSelection(); }}>Add another word</button>
            </div>
          </div>
        ` : nothing}
        <form @submit=${this.lookup}>
          <label>German word
            <input
              .value=${this.lookupQuery}
              @input=${(event: InputEvent) => { this.lookupQuery = (event.target as HTMLInputElement).value; this.lastSavedNote = null; }}
              ?disabled=${this.lookupStatus === 'loading' || this.isSavingNote}
              autocomplete="off"
              placeholder="e.g. anrufen"
            />
          </label>
          <div class="actions"><button class="primary" type="submit" ?disabled=${this.lookupStatus === 'loading' || this.isSavingNote}>${this.lookupStatus === 'loading' ? 'Looking up…' : 'Look up'}</button></div>
        </form>
        ${this.lookupStatus === 'loading' ? html`<p class="result" role="status">Looking up the active dictionary…</p>` : nothing}
        ${this.lookupStatus === 'ready' && !this.lookupCandidates.length ? html`<p class="result">No dictionary candidate was returned. Try a different German form.</p>` : nothing}
        ${this.lookupCandidates.length ? html`
          <fieldset class="selection">
            <legend>Select vocabulary</legend>
            <ul class="candidate-list">
              ${this.lookupCandidates.map((item) => html`
                <li>
                  <button
                    class="candidate ${candidate === item ? 'selected' : ''}"
                    type="button"
                    @click=${() => this.selectCandidate(item)}
                    aria-pressed=${candidate === item ? 'true' : 'false'}
                  >
                    <div class="candidate-header">
                      <span class="candidate-headword">${candidateHeadword(item)}</span>
                      <span class="muted">· ${item.pos}</span>
                    </div>
                    ${candidatePreferredEnglish(item) ? html`<div class="candidate-gloss">${candidatePreferredEnglish(item)}</div>` : nothing}
                    <small>${item.status === 'resolved' ? 'Dictionary entry' : item.status.replace('_', ' ')}</small>
                  </button>
                </li>
              `)}
            </ul>
          </fieldset>
        ` : nothing}
        ${candidate ? html`
          <form @submit=${this.saveManualNote}>
            ${candidate.status === 'resolved' ? (candidate.senses?.length
              ? this.renderSenseChoices(candidate.senses)
              : html`<p class="result">This result has no selectable sense and cannot be saved as a resolved note.</p>`) : nothing}
            ${candidate.status === 'derived_compound' ? html`<p class="result">The server will retain this compound’s supported component bindings.</p>` : nothing}
            <label>Deck
              <select
                .value=${manualDeck ? String(manualDeck.id) : ''}
                @change=${(event: Event) => { const value = (event.target as HTMLSelectElement).value; this.manualDeckId = value ? Number(value) : null; }}
                ?disabled=${this.isSavingNote}
              >
                <option value="">Select a deck</option>
                ${this.decks.map((item) => html`<option value=${item.id}>${item.name}</option>`)}
              </select>
            </label>
            <fieldset class="selection">
              <legend>Meaning languages</legend>
              <label class="choice"><input type="checkbox" .checked=${this.selectedMeaningLanguages.includes('de')} @change=${(event: Event) => this.toggleMeaningLanguage('de', (event.target as HTMLInputElement).checked)} /> German (DE)</label>
              <label class="choice"><input type="checkbox" .checked=${this.selectedMeaningLanguages.includes('en')} @change=${(event: Event) => this.toggleMeaningLanguage('en', (event.target as HTMLInputElement).checked)} /> English (EN)</label>
            </fieldset>
            <details class="optional-meanings">
              <summary>Optional custom meanings</summary>
              <label>Your German meaning <span class="muted">(optional)</span>
                <input .value=${this.userMeaningDe} @input=${(event: InputEvent) => { this.userMeaningDe = (event.target as HTMLInputElement).value; }} ?disabled=${this.isSavingNote} autocomplete="off" />
              </label>
              <label>Your English meaning <span class="muted">(optional)</span>
                <input .value=${this.userMeaningEn} @input=${(event: InputEvent) => { this.userMeaningEn = (event.target as HTMLInputElement).value; }} ?disabled=${this.isSavingNote} autocomplete="off" />
              </label>
            </details>
            <div class="actions"><button class="primary" type="submit" ?disabled=${this.isSavingNote}>${this.isSavingNote ? 'Saving…' : 'Save vocabulary'}</button></div>
          </form>
        ` : nothing}
      </section>
    `;
  }

  private renderCaptureCreation(deck: DeckSummary) {
    const selectedCount = Object.keys(this.captureSelections).length;
    const captureDeck = this.decks.find((item) => item.id === this.captureDeckId);
    const selectedText = this.captureSentence.slice(this.captureSpanStart, this.captureSpanEnd);
    return html`
      <section class="workflow capture-workflow" aria-labelledby="capture-title">
        <h3 id="capture-title">Capture from a sentence</h3>
        <p class="muted">Paste or type a sentence, select its German word or phrase, then choose the cards to create.</p>
        <form @submit=${this.highlightCapture}>
          <label>Sentence text
            <textarea
              .value=${this.captureSentence}
              @input=${(event: InputEvent) => { this.captureSentence = (event.target as HTMLTextAreaElement).value; this.updateCaptureSpan(event); this.resetCapturePicker(); this.captureStatus = 'idle'; this.captureError = ''; }}
              @select=${this.updateCaptureSpan}
              @keyup=${this.updateCaptureSpan}
              @click=${this.updateCaptureSpan}
              ?disabled=${this.captureStatus === 'loading' || this.isCapturing}
              placeholder="Ich rufe dich morgen an."
            ></textarea>
          </label>
          <p class="selection-preview" aria-live="polite">${selectedText ? html`Selected: <strong>“${selectedText}”</strong>` : 'Select a German word or phrase in the sentence.'}</p>
          <label>Lesson label
            <input
              .value=${this.captureLessonLabel}
              @input=${(event: InputEvent) => { this.captureLessonLabel = (event.target as HTMLInputElement).value; this.resetCapturePicker(); this.captureStatus = 'idle'; this.captureError = ''; }}
              ?disabled=${this.captureStatus === 'loading' || this.isCapturing}
              autocomplete="off"
              placeholder="Lesson 4 · Telephone calls"
            />
          </label>
          <div class="actions">
            <button class="primary" type="submit" ?disabled=${this.captureStatus === 'loading' || this.isCapturing}>${this.captureStatus === 'loading' ? 'Finding candidates…' : 'Find candidates'}</button>
          </div>
        </form>
        ${this.captureStatus === 'loading' ? html`<p class="result" role="status">Checking the active dictionary…</p>` : nothing}
        ${this.captureStatus === 'error' ? html`<div class="capture-state error" role="alert"><p>${this.captureError}</p><button @click=${() => void this.highlightCapture()}>Try again</button></div>` : nothing}
        ${this.captureStatus === 'ready' && this.captureCandidates.length === 0 ? html`<div class="capture-state"><p>No dictionary candidates were found for “${selectedText}”. Adjust the selected text and try again.</p></div>` : nothing}
        ${this.captureCandidates.length ? html`
          <form class="capture-picker" @submit=${this.saveCapture}>
            <fieldset class="selection">
              <legend>Choose vocabulary <span class="muted">(select one or more)</span></legend>
              <p class="result">Each checked German candidate becomes its own card. You can select multiple candidates.</p>
              <ul class="candidate-list">
                ${this.captureCandidates.map((candidate) => {
                  const key = this.captureKey(candidate);
                  const selection = this.captureSelections[key];
                  return html`
                    <li class="capture-candidate ${selection ? 'chosen' : ''}">
                      <label class="candidate-choice">
                        <input
                          type="checkbox"
                          .checked=${Boolean(selection)}
                          @change=${(event: Event) => this.toggleCaptureCandidate(candidate, (event.target as HTMLInputElement).checked)}
                          ?disabled=${this.isCapturing}
                        />
                        <span><strong class="lemma">${candidate.lemma}</strong> <span class="caption">${candidate.pos}</span></span>
                      </label>
                      ${selection && candidate.status === 'resolved' ? (candidate.senses?.length ? html`
                        <fieldset class="sense-choices">
                          <legend>Dictionary meaning for ${candidate.lemma}</legend>
                          ${candidate.senses.map((sense) => html`
                            <label class="choice">
                              <input type="radio" name=${`capture-sense-${key}`} .value=${sense.sense_semantic_ref} .checked=${selection.senseRef === sense.sense_semantic_ref} @change=${() => this.setCaptureSense(candidate, sense.sense_semantic_ref)} ?disabled=${this.isCapturing} />
                              ${sense.gloss || `Meaning ${sense.ord}`}
                            </label>
                          `)}
                        </fieldset>
                      ` : html`<p class="result">This entry has no selectable dictionary meaning.</p>`) : nothing}
                      ${selection && candidate.status === 'derived_compound' ? html`<p class="result">The server will preserve the compound’s dictionary component bindings.</p>` : nothing}
                    </li>
                  `;
                })}
              </ul>
            </fieldset>
            ${this.captureDictionaryChanged ? html`
              <div class="capture-state warning" role="alert">
                <p>The dictionary changed while you were choosing cards. Your selections have not been saved.</p>
                <button type="button" @click=${() => void this.highlightCapture()}>Find fresh candidates</button>
              </div>
            ` : nothing}
            ${this.captureError ? html`<div class="capture-state error" role="alert"><p>${this.captureError}</p></div>` : nothing}
            <fieldset class="selection">
              <legend>Meaning languages</legend>
              <p class="result">Choose German, English, or both. At least one language stays selected.</p>
              <label class="choice"><input type="checkbox" .checked=${this.captureMeaningLanguages.includes('de')} @change=${(event: Event) => this.toggleCaptureMeaningLanguage('de', event.target as HTMLInputElement)} ?disabled=${this.isCapturing} /> German (DE)</label>
              <label class="choice"><input type="checkbox" .checked=${this.captureMeaningLanguages.includes('en')} @change=${(event: Event) => this.toggleCaptureMeaningLanguage('en', event.target as HTMLInputElement)} ?disabled=${this.isCapturing} /> English (EN)</label>
            </fieldset>
            ${this.captureMeaningLanguages.includes('de') ? html`<label>Your German meaning <span class="muted">(optional)</span><input .value=${this.captureUserMeaningDe} @input=${(event: InputEvent) => { this.captureUserMeaningDe = (event.target as HTMLInputElement).value; }} ?disabled=${this.isCapturing} autocomplete="off" /></label>` : nothing}
            ${this.captureMeaningLanguages.includes('en') ? html`<label>Your English meaning <span class="muted">(optional)</span><input .value=${this.captureUserMeaningEn} @input=${(event: InputEvent) => { this.captureUserMeaningEn = (event.target as HTMLInputElement).value; }} ?disabled=${this.isCapturing} autocomplete="off" /></label>` : nothing}
            <label>Destination deck
              <select .value=${captureDeck ? String(captureDeck.id) : String(deck.id)} @change=${(event: Event) => { const value = (event.target as HTMLSelectElement).value; this.captureDeckId = value ? Number(value) : null; }} ?disabled=${this.isCapturing}>
                <option value="">Select a deck</option>
                ${this.decks.map((item) => html`<option value=${item.id}>${item.name}</option>`)}
              </select>
            </label>
            <div class="actions create-actions">
              <button class="primary" type="submit" ?disabled=${selectedCount === 0 || this.isCapturing || this.captureDictionaryChanged}>${this.isCapturing ? 'Creating cards…' : `Create ${selectedCount || ''} card${selectedCount === 1 ? '' : 's'}`}</button>
              ${selectedCount === 0 ? html`<p class="disabled-explanation">Select at least one candidate to create cards.</p>` : nothing}
            </div>
          </form>
        ` : nothing}
      </section>
    `;
  }

  private renderImportExport(deck: DeckSummary) {
    const importDeck = this.decks.find((item) => item.id === this.importDeckId);
    const selectedImportDeckId = importDeck?.id ?? deck.id;
    return html`
      <section class="workflow" aria-labelledby="import-export-title">
        <h3 id="import-export-title">Import & export</h3>
        <form @submit=${this.importCsv}>
          <label>Destination deck
            <select
              .value=${String(selectedImportDeckId)}
              @change=${(event: Event) => { const value = (event.target as HTMLSelectElement).value; this.importDeckId = value ? Number(value) : null; }}
              ?disabled=${this.isImporting || this.isReadingImportFile}
            >
              <option value="">Select a deck</option>
              ${this.decks.map((item) => html`<option value=${item.id} ?selected=${item.id === selectedImportDeckId}>${item.name}</option>`)}
            </select>
          </label>
          <label>Vocabulary lines
            <textarea .value=${this.importText} @input=${(event: InputEvent) => { this.importText = (event.target as HTMLTextAreaElement).value; }} ?disabled=${this.isImporting || this.isReadingImportFile} placeholder="Haus&#10;anrufen&#10;Feierabend"></textarea>
          </label>
          <label>Or choose a CSV/text file
            <input type="file" accept=".csv,.txt,text/csv,text/plain" @change=${this.readImportFile} ?disabled=${this.isImporting || this.isReadingImportFile} />
          </label>
          ${this.isReadingImportFile ? html`<p class="result" role="status">Reading file…</p>` : nothing}
          ${this.importFileName ? html`<p class="result">Using text from ${this.importFileName}.</p>` : nothing}
          <div class="actions"><button class="primary" type="submit" ?disabled=${this.isImporting || this.isReadingImportFile || !importDeck}>${this.isImporting ? 'Importing vocabulary…' : 'Import CSV'}</button></div>
          ${this.isImporting ? html`
            <div class="import-busy" role="status" aria-live="polite">
              <progress class="import-progress"></progress>
              <p>Importing vocabulary…</p>
            </div>
          ` : nothing}
        </form>
        <div class="workflow-grid">
          <div>
            <h3>APKG export</h3>
            <p class="muted">Recommended — includes audio and richer card data.</p>
            <button class="primary" aria-label=${`Export “${deck.name}” to Anki (.apkg)`} @click=${() => void this.exportApkg(deck)} ?disabled=${this.exportingFormat !== null}>${this.exportingFormat === 'apkg' ? 'Preparing APKG…' : 'Export to Anki (.apkg)'}</button>
          </div>
          <div>
            <h3>TSV export</h3>
            <p class="muted">Text-only backup/interchange — audio is not included.</p>
            <button aria-label=${`Export “${deck.name}” as TSV`} @click=${() => void this.exportTsv(deck)} ?disabled=${this.exportingFormat !== null}>${this.exportingFormat === 'tsv' ? 'Preparing TSV…' : 'Export as TSV'}</button>
          </div>
        </div>
      </section>
    `;
  }

  /**
   * The simple "Play pronunciation" control. Always visible on the revealed
   * answer, outside Extra info — recording and custom-pronunciation
   * management live in {@link renderPronunciationManagement} instead.
   */
  private renderSimplePronunciation() {
    return html`
      <div class="pronunciation-simple">
        <div class="audio-actions">
          <button type="button" @click=${() => void this.playPronunciation()} ?disabled=${this.audioStatus === 'loading'}>
            ${this.audioStatus === 'loading' ? 'Loading pronunciation…' : this.audioStatus === 'playing' ? 'Playing pronunciation…' : 'Play pronunciation'}
          </button>
          <span class="caption">Press R to replay</span>
        </div>
        ${this.audioMessage ? html`<p class="inline-status ${this.audioStatus === 'unavailable' ? 'error' : ''}" role=${this.audioStatus === 'unavailable' ? 'alert' : 'status'}>${this.audioMessage}</p>` : nothing}
      </div>
    `;
  }

  /**
   * Recording and custom-pronunciation management. Secondary to ordinary
   * review, so it lives inside Extra info rather than on the default
   * revealed answer.
   */
  private renderPronunciationManagement() {
    const recordingFailed = this.recordingStatus === 'save-error';
    return html`
      <section class="pronunciation" aria-labelledby="pronunciation-title">
        <h3 id="pronunciation-title">Custom pronunciation</h3>
        ${this.hasCustomAudio ? html`
          <div class="audio-actions">
            <button type="button" @click=${() => { this.showRecordingControls = !this.showRecordingControls; this.revertConfirmation = false; }}>
              ${this.showRecordingControls ? 'Keep current pronunciation' : 'Replace pronunciation'}
            </button>
            ${this.revertConfirmation ? html`
              <span class="caption">Replace your custom pronunciation with automatic pronunciation?</span>
              <button class="danger" type="button" @click=${() => void this.revertCustomAudio()}>Confirm revert to automatic</button>
              <button type="button" @click=${() => { this.revertConfirmation = false; }}>Cancel</button>
            ` : html`<button class="danger" type="button" @click=${() => { this.revertConfirmation = true; this.showRecordingControls = false; }}>Revert to automatic</button>`}
          </div>
        ` : html`<button type="button" @click=${() => { this.showRecordingControls = !this.showRecordingControls; }}>Add your pronunciation</button>`}
        ${this.showRecordingControls ? html`
          <div class="local-take">
            <p class="muted">Record a take or choose an audio file. It stays only in this browser until you save it.</p>
            ${this.recordingBlob ? html`
              <p class="inline-status">Local recording ready to preview and save.</p>
              <audio class="audio-preview" controls src=${this.recordingPreviewUrl}></audio>
            ` : nothing}
            ${recordingFailed ? html`
              <p class="inline-status error" role="alert">${this.recordingError}</p>
              <div class="recording-actions">
                <button class="primary" type="button" @click=${() => void this.saveRecording()}>Try again</button>
                <button class="danger" type="button" @click=${this.discardRecording}>Discard recording</button>
              </div>
            ` : html`
              <div class="recording-actions">
                ${this.recordingStatus === 'recording'
                  ? html`<button class="danger" type="button" @click=${this.stopRecording}>Stop recording</button>`
                  : html`<button type="button" @click=${() => void this.startRecording()} ?disabled=${this.recordingStatus === 'saving'}>Record pronunciation</button>`}
                <label>Choose audio file
                  <input type="file" accept="audio/*" @change=${this.selectAudioFile} ?disabled=${this.recordingStatus === 'recording' || this.recordingStatus === 'saving'} />
                </label>
                ${this.recordingBlob ? html`
                  <button class="primary" type="button" @click=${() => void this.saveRecording()} ?disabled=${this.recordingStatus === 'saving'}>${this.recordingStatus === 'saving' ? 'Saving pronunciation…' : 'Save recording'}</button>
                  <button class="danger" type="button" @click=${this.discardRecording} ?disabled=${this.recordingStatus === 'saving'}>Discard recording</button>
                ` : nothing}
              </div>
              ${this.recordingError ? html`<p class="inline-status error" role="alert">${this.recordingError}</p>` : nothing}
            `}
          </div>
        ` : nothing}
      </section>
    `;
  }

  private renderMeaningEditor(card: NextCardData) {
    return html`
      <section class="edit-meanings" aria-labelledby="meaning-edit-title">
        <h3 id="meaning-edit-title">Your meanings</h3>
        <p class="muted">Save your wording for either language, or remove an existing personal meaning to return to the card’s available meaning.</p>
        ${(['de', 'en'] as MeaningLanguage[]).map((language) => {
          const meaning = this.meaningFor(card, language);
          const userAuthored = Boolean(meaning?.is_user_authored);
          const languageName = language === 'de' ? 'German' : 'English';
          return html`
            <div class="gloss-row">
              <label>Your ${languageName} meaning
                <input
                  .value=${this.glossDrafts[language]}
                  @input=${(event: InputEvent) => { this.glossDrafts = { ...this.glossDrafts, [language]: (event.target as HTMLInputElement).value }; }}
                  ?disabled=${this.glossSavingLanguage === language}
                  autocomplete="off"
                />
              </label>
              <button type="button" @click=${() => void this.saveGloss(language)} ?disabled=${this.glossSavingLanguage === language || !this.glossDrafts[language].trim()}>
                ${this.glossSavingLanguage === language ? 'Saving…' : 'Save'}
              </button>
              ${userAuthored ? html`<button class="danger" type="button" @click=${() => void this.deleteGloss(language)} ?disabled=${this.glossSavingLanguage === language}>Remove</button>` : nothing}
            </div>
          `;
        })}
        ${this.glossState ? html`<p class="inline-status" role="status">${this.glossState}</p>` : nothing}
        ${this.glossError ? html`<p class="inline-status error" role="alert">${this.glossError}</p>` : nothing}
      </section>
    `;
  }

  private renderStudyCard(card: NextCardData) {
    const deMeaning = this.meaningFor(card, 'de');
    const enMeaning = this.meaningFor(card, 'en');
    const extraMeaningLines = card.back.meanings.flatMap((meaning) => meaning.lines.slice(1).map((line) => `${meaning.heading}: ${line}`));
    return html`
      <div class="card-stage">
        <div class="card-side">
          <span class="front-label">German vocabulary</span>
          <h2 class="study-lemma">${card.front.display_headword}</h2>
          <p class="study-meta">${card.front.pos}${card.front.ipa ? ` · ${card.front.ipa}` : ''}</p>
          <div class="front-audio">${this.renderSimplePronunciation()}</div>
          ${!this.isRevealed ? html`
            <button class="primary reveal-action" type="button" @click=${this.revealCard}>Reveal answer <span class="caption">Space</span></button>
          ` : html`
            <div class="card-side" data-study-answer tabindex="-1">
              <hr class="answer-rule" />
              <span class="front-label">Answer</span>
              ${enMeaning ? html`
                <p class="meaning primary-meaning"><span class="meaning-label">English</span><br />${enMeaning.lines[0] ?? ''}</p>
                ${deMeaning?.lines[0] ? html`<p class="meaning"><span class="meaning-label">German</span><br />${deMeaning.lines[0]}</p>` : nothing}
              ` : (deMeaning?.lines[0] ? html`
                <p class="meaning primary-meaning"><span class="meaning-label">German</span><br />${deMeaning.lines[0]}</p>
              ` : nothing)}
              <p class="compact-grammar">${card.back.grammar.lines.join(' · ') || card.back.pos}</p>
              ${card.back.examples.slice(0, 2).map((example) => html`
                <p class="example">${example.de}${example.en ? html`<span class="example-translation">${example.en}</span>` : nothing}</p>
              `)}
              <div class="extra-info-row">
                <button
                  type="button"
                  aria-expanded=${this.extraInfoOpen ? 'true' : 'false'}
                  aria-controls="extra-info-panel"
                  @click=${this.toggleExtraInfo}
                >${this.extraInfoOpen ? 'Hide extra info' : 'Show extra info'}</button>
                <label class="always-extra-toggle">
                  <input
                    type="checkbox"
                    .checked=${this.alwaysShowExtraInfo}
                    @change=${(event: Event) => this.setAlwaysShowExtraInfo((event.target as HTMLInputElement).checked)}
                  />
                  Always show extra info
                </label>
              </div>
              ${this.extraInfoOpen ? html`
                <div class="extra-info" id="extra-info-panel">
                  ${extraMeaningLines.length ? html`<div class="detail-block"><span class="meaning-label">Extended notes</span><ul>${extraMeaningLines.map((line) => html`<li>${line}</li>`)}</ul></div>` : nothing}
                  ${this.renderPronunciationManagement()}
                  ${this.renderMeaningEditor(card)}
                </div>
              ` : nothing}
              <div>
                <p class="front-label">How well did you know it?</p>
                <div class="confidence-grid">
                  ${confidenceLabels.map(([number, label]) => html`
                    <button class="confidence" type="button" ?disabled=${this.isReviewing || Boolean(this.recordingBlob)} @click=${() => void this.submitConfidence(Number(number))}>
                      <span class="confidence-number">${number}</span><span class="confidence-text">${label}</span>
                    </button>
                  `)}
                </div>
              </div>
              ${this.isReviewing ? html`<p class="inline-status" role="status">Saving your confidence…</p>` : nothing}
              ${this.recordingBlob ? html`<p class="inline-status">Save or discard the local recording before choosing a confidence.</p>` : nothing}
            </div>
          `}
        </div>
      </div>
    `;
  }

  private renderStudy() {
    const deck = this.decks.find((item) => item.id === this.studyDeckId);
    return html`
      <main class="study" aria-labelledby="study-title">
        <div class="study-heading">
          <div>
            <button
              class="study-back"
              type="button"
              aria-label=${deck ? `Back to ${deck.name}` : 'Back to decks'}
              @click=${this.backFromStudy}
            >← ${deck ? `Back to ${deck.name}` : 'Back to decks'}</button>
            <p class="caption">Study</p>
            <h2 id="study-title">${deck ? deck.name : 'All due cards'}</h2>
          </div>
          <button type="button" @click=${() => void this.loadStudyCard()} ?disabled=${this.studyStatus === 'loading'}>${this.studyStatus === 'loading' ? 'Loading…' : 'Refresh'}</button>
        </div>
        ${this.studyStatus === 'ready' && this.studyError ? html`<p class="inline-status error" role="alert">${this.studyError}</p>` : nothing}
        ${this.studyStatus === 'loading' ? html`<div class="card-stage study-state" role="status">Loading the next due card…</div>` : nothing}
        ${this.studyStatus === 'error' ? html`<div class="card-stage study-state"><div><h2>Could not load a card</h2><p class="inline-status error" role="alert">${this.studyError}</p><button class="primary" type="button" @click=${() => void this.loadStudyCard()}>Try again</button></div></div>` : nothing}
        ${this.studyStatus === 'empty' ? html`
          <div class="card-stage study-state" data-study-empty tabindex="-1">
            <div>
              <h2>Study complete</h2>
              <p class="muted">
                ${deck
                  ? `Nothing else is due in “${deck.name}” right now.`
                  : 'Nothing else is due right now.'}
              </p>
              <div class="study-complete-actions">
                ${deck ? html`
                  <button class="primary" type="button" @click=${() => { this.selectedDeckId = deck.id; this.importDeckId = deck.id; this.view = 'deck'; }}>Back to ${deck.name}</button>
                  <button type="button" @click=${() => { this.selectedDeckId = null; this.view = 'decks'; }}>Decks</button>
                  <button type="button" @click=${() => void this.loadStudyCard()}>Check again</button>
                  <button type="button" @click=${() => { this.selectedDeckId = deck.id; this.manualDeckId = deck.id; this.importDeckId = deck.id; this.view = 'deck'; }}>Add vocabulary</button>
                ` : html`
                  <button class="primary" type="button" @click=${() => { this.selectedDeckId = null; this.view = 'decks'; }}>Back to decks</button>
                  <button type="button" @click=${() => void this.loadStudyCard()}>Check again</button>
                `}
              </div>
            </div>
          </div>
        ` : nothing}
        ${this.studyStatus === 'ready' && this.studyCard ? this.renderStudyCard(this.studyCard) : nothing}
      </main>
    `;
  }

  private async loadDictionarySettings(): Promise<void> {
    this.dictionarySettingsStatus = 'loading';
    this.dictionaryActionError = '';
    try {
      const info = await vocabClient.getDictionarySettings();
      this.dictionarySettings = info;
      this.dictionaryMode = info.mode;
      this.dictionarySettingsStatus = 'ready';
      // The chooser is the runtime's unconfigured view; UI surfaces it
      // when the server says so, and only then. On initial load with no
      // valid Offline asset, switch to the chooser view automatically.
      if (info.mode === 'unconfigured' && this.view !== 'study' && this.view !== 'chooser') {
        this.view = 'chooser';
      }
      // When a mode becomes active, leave the chooser automatically.
      if (info.mode !== 'unconfigured' && this.view === 'chooser') {
        this.view = 'decks';
      }
    } catch (error) {
      this.dictionarySettingsStatus = 'error';
      this.dictionaryActionError = this.messageFor(
        error,
        'Could not read the dictionary settings.',
      );
    }
  }

  private async useOnline(): Promise<void> {
    this.dictionaryAction = 'switching-online';
    this.dictionaryActionMessage = '';
    this.dictionaryActionError = '';
    try {
      await vocabClient.useOnline();
      this.dictionaryActionMessage =
        'Now using Online for this session. The canonical Offline dictionary will not be removed.';
      await this.loadDictionarySettings();
    } catch (error) {
      this.dictionaryActionError = this.messageFor(
        error,
        'Could not switch to Online for this session.',
      );
    } finally {
      this.dictionaryAction = 'idle';
    }
  }

  private async useOffline(): Promise<void> {
    this.dictionaryAction = 'switching-offline';
    this.dictionaryActionMessage = '';
    this.dictionaryActionError = '';
    try {
      await vocabClient.useOffline();
      this.dictionaryActionMessage = 'Now using Offline for this session.';
      await this.loadDictionarySettings();
    } catch (error) {
      this.dictionaryActionError = this.messageFor(
        error,
        'Could not switch to Offline for this session.',
      );
    } finally {
      this.dictionaryAction = 'idle';
    }
  }

  private async installOffline(): Promise<void> {
    this.dictionaryAction = 'installing';
    this.dictionaryActionMessage = '';
    this.dictionaryActionError = '';
    try {
      const result = await vocabClient.installOffline();
      if (result.status === 'started') {
        this.dictionaryActionMessage =
          'Download started. Progress is shown below; the Settings view refreshes automatically.';
        await this.pollInstallProgress();
      } else {
        this.dictionaryActionMessage = `Installed full Offline dictionary (status: ${result.status}).`;
      }
      await this.loadDictionarySettings();
    } catch (error) {
      this.dictionaryActionError = this.messageFor(
        error,
        'Could not install the full Offline dictionary.',
      );
    } finally {
      this.dictionaryAction = 'idle';
    }
  }

  private async pollInstallProgress(): Promise<void> {
    // Poll GET /vocab/settings/dictionary for live install progress.
    // The server owns the progress state; the client only reads it.
    for (let attempt = 0; attempt < 120; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      try {
        const info = await vocabClient.getDictionarySettings();
        this.dictionarySettings = info;
        this.dictionaryMode = info.mode;
        const progress = info.install_progress;
        if (!progress || progress.status === 'idle') return;
        if (progress.status === 'installed') {
          this.dictionaryActionMessage = 'Installed full Offline dictionary.';
          return;
        }
        if (progress.status === 'failed') {
          this.dictionaryActionError = progress.error || 'Offline download failed.';
          return;
        }
        const pct = progress.percent.toFixed(1);
        const dl = progress.downloaded_bytes.toLocaleString();
        const total = progress.total_bytes ? progress.total_bytes.toLocaleString() : 'unknown';
        this.dictionaryActionMessage = `Downloading… ${dl} / ${total} bytes (${pct}%).`;
      } catch {
        return;
      }
    }
  }

  private async removeOffline(): Promise<void> {
    this.dictionaryAction = 'removing';
    this.dictionaryActionMessage = '';
    this.dictionaryActionError = '';
    try {
      const result = await vocabClient.removeOffline();
      this.dictionaryActionMessage = `Removed Offline dictionary: ${result.detail}`;
      this.confirmRemoveOffline = false;
      await this.loadDictionarySettings();
    } catch (error) {
      this.dictionaryActionError = this.messageFor(
        error,
        'Could not remove the Offline dictionary.',
      );
    } finally {
      this.dictionaryAction = 'idle';
    }
  }

  private async clearOnlineCache(): Promise<void> {
    this.dictionaryAction = 'clearing';
    this.dictionaryActionMessage = '';
    this.dictionaryActionError = '';
    try {
      await vocabClient.clearOnlineCache();
      this.dictionaryActionMessage = 'Online cache cleared.';
      await this.loadDictionarySettings();
    } catch (error) {
      this.dictionaryActionError = this.messageFor(
        error,
        'Could not clear the Online cache.',
      );
    } finally {
      this.dictionaryAction = 'idle';
    }
  }

  private renderChooser() {
    return html`
      <main class="panel" aria-labelledby="chooser-title">
        <h2 id="chooser-title">Choose how to use the dictionary</h2>
        <p class="muted">
          No canonical full Offline dictionary is available yet. Pick how this
          process should serve the vocabulary:
        </p>
        <div class="workflow-grid">
          <section class="workflow" aria-labelledby="chooser-online-title">
            <h3 id="chooser-online-title">Use Online</h3>
            <p>Start now without downloading the full dictionary. Online applies
              to the current session only.</p>
            <button class="primary" type="button" @click=${() => void this.useOnline()} ?disabled=${this.dictionaryAction !== 'idle'}>
              ${this.dictionaryAction === 'switching-online' ? 'Switching…' : 'Use Online'}
            </button>
          </section>
          <section class="workflow" aria-labelledby="chooser-offline-title">
            <h3 id="chooser-offline-title">Download for Offline use</h3>
            <p>Download ~945 MB and work without internet afterward. The
              free-space preflight happens before any download begins.</p>
            <button type="button" @click=${() => void this.installOffline()} ?disabled=${this.dictionaryAction !== 'idle'}>
              ${this.dictionaryAction === 'installing' ? 'Starting install…' : 'Download for Offline use'}
            </button>
          </section>
        </div>
        ${this.dictionaryActionError ? html`<p class="inline-status error" role="alert">${this.dictionaryActionError}</p>` : nothing}
      </main>
    `;
  }

  private renderSettings() {
    const info = this.dictionarySettings;
    const showChooserBanner = this.dictionaryMode === 'unconfigured' && info?.canonical_offline_valid !== true;
    return html`
      <main class="panel" aria-labelledby="settings-title">
        <div class="toolbar"><h2 id="settings-title">Dictionary</h2><button @click=${() => void this.loadDictionarySettings()} ?disabled=${this.dictionarySettingsStatus === 'loading'}>${this.dictionarySettingsStatus === 'loading' ? 'Refreshing…' : 'Refresh'}</button></div>
        ${this.dictionarySettingsStatus === 'error' ? html`<p class="inline-status error" role="alert">${this.dictionaryActionError}</p>` : nothing}
        ${showChooserBanner ? this.renderChooserInline() : nothing}
        ${info ? html`
          <dl class="settings-meta">
            <dt>Mode</dt><dd data-testid="dictionary-mode">${info.mode}</dd>
            <dt>Canonical Offline</dt><dd><code>${info.canonical_offline_path}</code></dd>
            <dt>Present</dt><dd>${info.canonical_offline_present ? 'yes' : 'no'}</dd>
            <dt>Valid</dt><dd>${info.canonical_offline_valid ? 'yes' : 'no'}</dd>
            ${info.online_info ? html`
              <dt>Online dataset token</dt><dd><code>${info.online_info.dataset_token.slice(0, 16)}…</code></dd>
            ` : nothing}
            ${info.install_progress && info.install_progress.status !== 'idle' ? html`
              <dt>Download progress</dt><dd data-testid="install-progress">
                ${info.install_progress.downloaded_bytes.toLocaleString()} /
                ${info.install_progress.total_bytes ? info.install_progress.total_bytes.toLocaleString() : 'unknown'} bytes
                (${info.install_progress.percent.toFixed(1)}%) — ${info.install_progress.status}
              </dd>
            ` : nothing}
          </dl>
        ` : nothing}
        <div class="workflow-grid">
          <section class="workflow" aria-labelledby="online-action-title">
            <h3 id="online-action-title">Online</h3>
            <p>${info?.mode === 'online' ? 'Online is active for this session.' : 'Use the trusted Online dictionary for this session only.'}</p>
            <button class="primary" type="button" @click=${() => void this.useOnline()} ?disabled=${this.dictionaryAction !== 'idle' || info?.mode === 'online'}>
              ${this.dictionaryAction === 'switching-online' ? 'Switching…' : 'Use Online for this session'}
            </button>
            <button type="button" @click=${() => void this.clearOnlineCache()} ?disabled=${this.dictionaryAction !== 'idle' || !info?.online_active}>
              ${this.dictionaryAction === 'clearing' ? 'Clearing…' : 'Clear Online cache'}
            </button>
          </section>
          <section class="workflow" aria-labelledby="offline-action-title">
            <h3 id="offline-action-title">Offline</h3>
            <p>${info?.mode === 'offline' ? 'Offline is active for this session.' : 'Activate the trusted full Offline dictionary for this session.'}</p>
            <button class="primary" type="button" @click=${() => void this.useOffline()} ?disabled=${this.dictionaryAction !== 'idle' || info?.mode === 'offline' || !info?.canonical_offline_valid}>
              ${this.dictionaryAction === 'switching-offline' ? 'Switching…' : 'Use Offline'}
            </button>
            <button type="button" @click=${() => void this.installOffline()} ?disabled=${this.dictionaryAction !== 'idle' || info?.canonical_offline_valid === true}>
              ${this.dictionaryAction === 'installing' ? 'Starting install…' : 'Download for Offline use'}
            </button>
            ${!this.confirmRemoveOffline ? html`
              <button class="danger" type="button" @click=${() => { this.confirmRemoveOffline = true; }} ?disabled=${this.dictionaryAction !== 'idle'}>
                Remove Offline dictionary
              </button>
            ` : nothing}
            ${this.confirmRemoveOffline ? html`
              <div class="confirm" role="alertdialog">
                <p>Remove the canonical Offline dictionary while Online is active? Choose another mode (Online for this session) first if Offline is in use.</p>
                <button class="danger" type="button" @click=${() => void this.removeOffline()} ?disabled=${this.dictionaryAction !== 'idle'}>Confirm remove Offline</button>
                <button type="button" @click=${() => { this.confirmRemoveOffline = false; }}>Cancel</button>
              </div>
            ` : nothing}
          </section>
        </div>
        ${this.dictionaryActionMessage ? html`<p class="inline-status" role="status">${this.dictionaryActionMessage}</p>` : nothing}
        ${this.dictionaryActionError ? html`<p class="inline-status error" role="alert">${this.dictionaryActionError}</p>` : nothing}
      </main>
    `;
  }

  private renderChooserInline() {
    return html`
      <section class="panel" aria-labelledby="inline-chooser-title">
        <h3 id="inline-chooser-title">Choose how to use the dictionary</h3>
        <p class="muted">No canonical full Offline dictionary is available. Online applies to this session only.</p>
        <div class="actions">
          <button class="primary" type="button" @click=${() => void this.useOnline()} ?disabled=${this.dictionaryAction !== 'idle'}>
            ${this.dictionaryAction === 'switching-online' ? 'Switching…' : 'Use Online'}
          </button>
          <button type="button" @click=${() => void this.installOffline()} ?disabled=${this.dictionaryAction !== 'idle'}>
            ${this.dictionaryAction === 'installing' ? 'Starting install…' : 'Download for Offline use'}
          </button>
        </div>
      </section>
    `;
  }

  private renderDeckDetail(deck: DeckSummary) {
    const isOrphaned = this.isOrphanedDeck(deck);
    const canRename = !isOrphaned;
    const showCardsTab = this.deckTab === 'cards';
    if (showCardsTab && this.deckCardsStatus === 'idle' && this.selectedDeckId !== null) {
      void this.loadDeckCards(this.selectedDeckId);
    }
    return html`
      <section class="panel" aria-labelledby="deck-title">
        <div class="deck-heading">
          <div>
            <h2 id="deck-title">${deck.name}</h2>
            <p class="muted">${deck.card_count} ${deck.card_count === 1 ? 'card' : 'cards'} · ${deck.due_count} due · ${deck.mastery_percent}% mastered</p>
          </div>
          <div class="actions">
            <button class="primary" @click=${() => void this.openStudy(deck.id)}>Study this deck</button>
            <button @click=${() => { this.selectedDeckId = null; this.view = 'decks'; }}>All decks</button>
          </div>
        </div>
        <div class="actions" aria-label="Deck actions">
          <button @click=${() => this.openRenameDialog()} ?disabled=${!canRename || this.renameOpen}>Rename deck</button>
          ${isOrphaned ? html`<span class="muted">Rename is disabled for the protected recovery deck.</span>` : nothing}
        </div>
        <div class="deck-tabs" role="tablist" aria-label="Deck sections">
          <button class="deck-tab" role="tab" aria-selected=${this.deckTab === 'overview' ? 'true' : 'false'} @click=${() => this.setDeckTab('overview')}>Overview</button>
          <button class="deck-tab" role="tab" aria-selected=${this.deckTab === 'cards' ? 'true' : 'false'} @click=${() => this.setDeckTab('cards')}>Cards</button>
          <button class="deck-tab" role="tab" aria-selected=${this.deckTab === 'add' ? 'true' : 'false'} @click=${() => this.setDeckTab('add')}>Add</button>
          <button class="deck-tab" role="tab" aria-selected=${this.deckTab === 'import' ? 'true' : 'false'} @click=${() => this.setDeckTab('import')}>Import & Export</button>
        </div>
        ${this.deckTab === 'overview' ? this.renderDeckOverview(deck) : nothing}
        ${this.deckTab === 'cards' ? this.renderDeckCards(deck) : nothing}
        ${this.deckTab === 'add' ? this.renderDeckAdd(deck) : nothing}
        ${this.deckTab === 'import' ? this.renderDeckImport(deck) : nothing}
      </section>
      ${this.renderEditDialog()}
      ${this.renderMoveDialog()}
      ${this.renderRemoveDialog()}
      ${this.renderRenameDialog()}
      ${this.renderRestoreDialog()}
      ${this.renderSenseDialog()}
    `;
  }

  private renderDeckOverview(deck: DeckSummary) {
    return html`
      <div>
        <p>Card data and review scheduling remain on the server.</p>
        <p class="muted">${deck.card_count} ${deck.card_count === 1 ? 'card' : 'cards'} · ${deck.due_count} due · ${deck.mastery_percent}% mastered</p>
        <div class="actions">
          <button @click=${() => this.setDeckTab('cards')}>Manage cards</button>
          <button @click=${() => this.setDeckTab('add')}>Add vocabulary</button>
          <button @click=${() => this.setDeckTab('import')}>Import &amp; export</button>
        </div>
      </div>
    `;
  }

  private renderDeckAdd(deck: DeckSummary) {
    return html`
      <div class="workflow-grid">
        ${this.renderCaptureCreation(deck)}
        ${this.renderManualCreation()}
      </div>
    `;
  }

  private renderDeckImport(deck: DeckSummary) {
    return this.renderImportExport(deck);
  }

  private renderDeckCards(deck: DeckSummary) {
    const isOrphaned = this.isOrphanedDeck(deck);
    return html`
      <div aria-labelledby="cards-section-title">
        <h3 id="cards-section-title">Cards</h3>
        ${isOrphaned ? html`
          <p class="orphan-banner">
            <strong>Protected recovery deck.</strong>
            ${' '}Notes here are not organised by lesson and cannot be moved out. Use a regular deck for routine study.
          </p>
        ` : nothing}
        ${this.deckCardsStatus === 'loading' ? html`<p class="loading" role="status">Loading cards…</p>` : nothing}
        ${this.deckCardsStatus === 'error' ? html`
          <div class="empty">
            <p>${this.deckCardsError}</p>
            <button @click=${() => this.selectedDeckId !== null && void this.loadDeckCards(this.selectedDeckId)}>Try again</button>
          </div>
        ` : nothing}
        ${this.deckCardsStatus === 'ready' && this.deckCards.length === 0 ? html`
          <p class="empty">This deck has no cards yet.</p>
        ` : nothing}
        ${this.deckCardsStatus === 'ready' && this.deckCards.length > 0 ? html`
          <ul class="deck-card-list" aria-label="Cards in this deck">
            ${this.deckCards.map((card) => this.renderDeckCardRow(card, isOrphaned))}
          </ul>
        ` : nothing}
      </div>
    `;
  }

  private renderDeckCardRow(card: DeckCardRow, isOrphaned: boolean) {
    const posGender = [card.pos, card.gender].filter(Boolean).join(' ');
    const metaParts: string[] = [];
    if (card.review_count > 0) metaParts.push(`${card.review_count} review${card.review_count === 1 ? '' : 's'}`);
    if (card.review_count > 0 && card.last_confidence !== null) {
      const label = card.last_confidence.toFixed(1);
      metaParts.push(`last confidence ${label}`);
    } else if (card.last_confidence !== null) {
      metaParts.push(`confidence ${card.last_confidence.toFixed(1)}`);
    }
    if (card.due_at) {
      const dueDate = this.formatDue(card.due_at);
      if (dueDate) metaParts.push(`due ${dueDate}`);
    }
    metaParts.push(card.selected_languages.length === 0
      ? 'no display languages'
      : `languages: ${card.selected_languages.map((lang) => lang.toUpperCase()).join(' + ')}`);
    if (card.has_custom_audio) metaParts.push('custom pronunciation');
    if (card.other_deck_ids.length > 0) metaParts.push(`also in ${card.other_deck_ids.length} other deck${card.other_deck_ids.length === 1 ? '' : 's'}`);
    // M6 — selected-sense editing is exposed for resolved notes (and
    // needs_gloss stubs that have at least a lemma hint). derived_compound
    // and orphaned rows are not eligible; they are surfaced by the
    // server with HTTP 409 unsupported_selected_sense_edit when a user
    // reaches this action through any other path.
    const showSenseAction = canEditSelectedSense(card.status, isOrphaned);
    return html`
      <li class="deck-card-row" data-note-id=${card.note_id}>
        <div>
          <span class="deck-card-headword">${card.headword}</span>
          ${posGender ? html`<span class="muted"> · ${posGender}</span>` : nothing}
          <span class="deck-card-meta">${metaParts.join(' · ')}</span>
        </div>
        <div class="deck-card-actions">
          <button type="button" @click=${() => this.openEditDialog(card)}>Edit</button>
          ${showSenseAction
            ? html`<button type="button" @click=${() => this.openSenseDialog(card)} data-testid=${`edit-sense-${card.note_id}`}>Edit sense</button>`
            : nothing}
          <button type="button" @click=${() => this.openMoveDialog(card)} ?disabled=${isOrphaned}>Move</button>
          ${isOrphaned
            ? html`<button type="button" @click=${() => this.openRestoreDialog(card)} data-testid=${`restore-${card.note_id}`}>Restore to deck</button>`
            : nothing}
          <button class="danger" type="button" @click=${() => this.openRemoveConfirm(card)}>Remove from deck</button>
        </div>
      </li>
    `;
  }

  private formatDue(dueAt: string): string {
    const date = new Date(dueAt);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleDateString();
  }

  private renderEditDialog() {
    const card = this.editingCard;
    if (!card) return nothing;
    const saving = this.editState === 'saving-languages' || this.editState === 'saving-gloss';
    const dirtyLanguages = () => {
      const before = [...card.selected_languages].sort();
      const after = [...this.editLanguages].sort();
      return before.length !== after.length || before.some((value, index) => value !== after[index]);
    };
    const dirtyGloss = (language: MeaningLanguage): boolean => {
      const draft = this.editGlossDrafts[language].trim();
      const previous = card.user_meanings[language]?.trim() ?? '';
      return draft !== previous;
    };
    const hasDirtyChange = dirtyLanguages() || dirtyGloss('de') || dirtyGloss('en');
    return html`
      <div class="dialog-backdrop" @click=${(event: MouseEvent) => { if (event.target === event.currentTarget) this.closeEditDialog(); }}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="edit-card-title" data-edit-dialog tabindex="-1">
          <h2 id="edit-card-title">Edit card</h2>
          <p class="muted">Headword (read-only): <strong>${card.headword}</strong>${card.pos ? ` · ${card.pos}` : ''}${card.gender ? ` · ${card.gender}` : ''}</p>
          <fieldset class="selection">
            <legend>Meaning languages</legend>
            <p class="muted">Choose German, English, or both. At least one language stays selected.</p>
            <label class="choice">
              <input
                type="checkbox"
                .checked=${this.editLanguages.includes('de')}
                @change=${(event: Event) => this.toggleEditMeaningLanguage('de', event.target as HTMLInputElement)}
                ?disabled=${saving}
              />
              German (DE)
            </label>
            <label class="choice">
              <input
                type="checkbox"
                .checked=${this.editLanguages.includes('en')}
                @change=${(event: Event) => this.toggleEditMeaningLanguage('en', event.target as HTMLInputElement)}
                ?disabled=${saving}
              />
              English (EN)
            </label>
          </fieldset>
          <section aria-labelledby="edit-gloss-title">
            <h3 id="edit-gloss-title">Your meanings</h3>
            <p class="muted">Optional overrides for this note. Saved values appear in study; cleared values return to the card's available meaning.</p>
            ${(['de', 'en'] as MeaningLanguage[]).map((language) => {
              const languageName = language === 'de' ? 'German' : 'English';
              const previous = card.user_meanings[language]?.trim() ?? '';
              return html`
                <div class="edit-gloss-row">
                  <label>Your ${languageName} meaning
                    <input
                      .value=${this.editGlossDrafts[language]}
                      @input=${(event: InputEvent) => { this.editGlossDrafts = { ...this.editGlossDrafts, [language]: (event.target as HTMLInputElement).value }; }}
                      ?disabled=${this.editGlossBusy[language]}
                      autocomplete="off"
                    />
                  </label>
                  <div class="actions">
                    <button type="button" @click=${() => void this.saveEditGloss(language)} ?disabled=${this.editGlossBusy[language] || !this.editGlossDrafts[language].trim() || this.editGlossDrafts[language].trim() === previous}>${this.editGlossBusy[language] ? 'Saving…' : 'Save meaning'}</button>
                    <button class="danger" type="button" @click=${() => void this.deleteEditGloss(language)} ?disabled=${this.editGlossBusy[language] || !previous}>Remove meaning</button>
                  </div>
                </div>
              `;
            })}
          </section>
          ${this.renderManagementPronunciation(card)}
          ${this.editError ? html`<p class="inline-status error" role="alert">${this.editError}</p>` : nothing}
          ${this.editState === 'saved' ? html`<p class="inline-status" role="status">Changes saved.</p>` : nothing}
          <div class="actions">
            <button type="button" @click=${() => this.closeEditDialog()} ?disabled=${saving}>Cancel</button>
            <button class="primary" type="button" @click=${() => void this.commitEditDialog()} ?disabled=${saving || !hasDirtyChange}>${saving ? 'Saving…' : 'Save changes'}</button>
          </div>
        </div>
      </div>
    `;
  }

  private renderManagementPronunciation(card: DeckCardRow) {
    const recordingFailed = this.mgmtRecordingStatus === 'save-error';
    const hasCustom = card.has_custom_audio;
    return html`
      <section class="pronunciation" aria-labelledby="mgmt-pronunciation-title">
        <h3 id="mgmt-pronunciation-title">Custom pronunciation</h3>
        <div class="audio-actions">
          <button type="button" @click=${() => void this.playManagementPronunciation()} ?disabled=${this.mgmtAudioStatus === 'loading'}>
            ${this.mgmtAudioStatus === 'loading' ? 'Loading pronunciation…' : this.mgmtAudioStatus === 'playing' ? 'Playing pronunciation…' : 'Play pronunciation'}
          </button>
          ${hasCustom ? html`
            <button type="button" @click=${() => { this.mgmtShowRecordingControls = !this.mgmtShowRecordingControls; this.mgmtRevertConfirmation = false; }}>
              ${this.mgmtShowRecordingControls ? 'Keep current pronunciation' : 'Replace pronunciation'}
            </button>
            ${this.mgmtRevertConfirmation ? html`
              <span class="caption">Replace your custom pronunciation with automatic pronunciation?</span>
              <button class="danger" type="button" @click=${() => void this.revertManagementCustomAudio()}>Confirm revert to automatic</button>
              <button type="button" @click=${() => { this.mgmtRevertConfirmation = false; }}>Cancel</button>
            ` : html`<button class="danger" type="button" @click=${() => { this.mgmtRevertConfirmation = true; this.mgmtShowRecordingControls = false; }}>Revert to automatic</button>`}
          ` : html`<button type="button" @click=${() => { this.mgmtShowRecordingControls = !this.mgmtShowRecordingControls; }}>Add your pronunciation</button>`}
        </div>
        ${this.mgmtAudioMessage ? html`<p class="inline-status ${this.mgmtAudioStatus === 'unavailable' ? 'error' : ''}" role=${this.mgmtAudioStatus === 'unavailable' ? 'alert' : 'status'}>${this.mgmtAudioMessage}</p>` : nothing}
        ${this.mgmtShowRecordingControls ? html`
          <div class="local-take">
            <p class="muted">Record a take or choose an audio file. It stays only in this browser until you save it.</p>
            ${this.mgmtRecordingBlob ? html`
              <p class="inline-status">Local recording ready to preview and save.</p>
              <audio class="audio-preview" controls src=${this.mgmtRecordingPreviewUrl}></audio>
            ` : nothing}
            ${recordingFailed ? html`
              <p class="inline-status error" role="alert">${this.mgmtRecordingError}</p>
              <div class="recording-actions">
                <button class="primary" type="button" @click=${() => void this.saveManagementRecording()}>Try again</button>
                <button class="danger" type="button" @click=${() => this.discardManagementRecording()}>Discard recording</button>
              </div>
            ` : html`
              <div class="recording-actions">
                ${this.mgmtRecordingStatus === 'recording'
                  ? html`<button class="danger" type="button" @click=${() => this.stopManagementRecording()}>Stop recording</button>`
                  : html`<button type="button" @click=${() => void this.startManagementRecording()} ?disabled=${this.mgmtRecordingStatus === 'saving'}>Record pronunciation</button>`}
                <label>Choose audio file
                  <input type="file" accept="audio/*" @change=${this.selectManagementAudioFile} ?disabled=${this.mgmtRecordingStatus === 'recording' || this.mgmtRecordingStatus === 'saving'} />
                </label>
                ${this.mgmtRecordingBlob ? html`
                  <button class="primary" type="button" @click=${() => void this.saveManagementRecording()} ?disabled=${this.mgmtRecordingStatus === 'saving'}>${this.mgmtRecordingStatus === 'saving' ? 'Saving pronunciation…' : 'Save recording'}</button>
                  <button class="danger" type="button" @click=${() => this.discardManagementRecording()} ?disabled=${this.mgmtRecordingStatus === 'saving'}>Discard recording</button>
                ` : nothing}
              </div>
              ${this.mgmtRecordingError ? html`<p class="inline-status error" role="alert">${this.mgmtRecordingError}</p>` : nothing}
            `}
          </div>
        ` : nothing}
      </section>
    `;
  }

  private renderMoveDialog() {
    const card = this.moveTarget;
    if (!card) return nothing;
    const destinations = this.moveDestinations();
    const busy = this.moveState === 'saving';
    return html`
      <div class="dialog-backdrop" @click=${(event: MouseEvent) => { if (event.target === event.currentTarget) this.closeMoveDialog(); }}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="move-title" data-move-dialog tabindex="-1">
          <h2 id="move-title">Move to another deck</h2>
          <p>Move “<strong>${card.headword}</strong>” from this deck to one of your other decks.</p>
          <label>Destination deck
            <select
              .value=${this.moveDestinationDeckId === null ? '' : String(this.moveDestinationDeckId)}
              @change=${(event: Event) => { const value = (event.target as HTMLSelectElement).value; this.moveDestinationDeckId = value ? Number(value) : null; }}
              ?disabled=${busy || destinations.length === 0}
            >
              <option value="">Select a deck</option>
              ${destinations.map((deck) => html`<option value=${deck.id}>${deck.name}</option>`)}
            </select>
          </label>
          ${destinations.length === 0 ? html`<p class="muted">Create another deck first; there is nowhere else to move this card.</p>` : nothing}
          ${this.moveError ? html`<p class="inline-status error" role="alert">${this.moveError}</p>` : nothing}
          <div class="actions">
            <button type="button" @click=${() => this.closeMoveDialog()} ?disabled=${busy}>Cancel</button>
            <button class="primary" type="button" @click=${() => void this.performMove()} ?disabled=${busy || this.moveDestinationDeckId === null}>${busy ? 'Moving…' : 'Move'}</button>
          </div>
        </div>
      </div>
    `;
  }

  private renderRemoveDialog() {
    const card = this.removeTarget;
    if (!card) return nothing;
    const busy = this.removeState === 'saving';
    return html`
      <div class="dialog-backdrop" @click=${(event: MouseEvent) => { if (event.target === event.currentTarget) this.closeRemoveConfirm(); }}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="remove-title" data-remove-dialog tabindex="-1">
          <h2 id="remove-title">Remove “${card.headword}” from this deck?</h2>
          <p>Its study history and saved vocabulary data are preserved.</p>
          <div class="actions">
            <button type="button" @click=${() => this.closeRemoveConfirm()} ?disabled=${busy}>Cancel</button>
            <button class="danger" type="button" @click=${() => void this.performRemove()} ?disabled=${busy}>${busy ? 'Removing…' : 'Remove from deck'}</button>
          </div>
        </div>
      </div>
    `;
  }

  private renderRenameDialog() {
    if (!this.renameOpen) return nothing;
    const busy = this.renameState === 'saving';
    return html`
      <div class="dialog-backdrop" @click=${(event: MouseEvent) => { if (event.target === event.currentTarget) this.closeRenameDialog(); }}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="rename-title" data-rename-dialog tabindex="-1">
          <h2 id="rename-title">Rename deck</h2>
          <label>New deck name
            <input
              .value=${this.renameDraft}
              @input=${(event: InputEvent) => { this.renameDraft = (event.target as HTMLInputElement).value; this.renameError = ''; }}
              ?disabled=${busy}
              maxlength="200"
              autocomplete="off"
            />
          </label>
          ${this.renameError ? html`<p class="inline-status error" role="alert">${this.renameError}</p>` : nothing}
          <div class="actions">
            <button type="button" @click=${() => this.closeRenameDialog()} ?disabled=${busy}>Cancel</button>
            <button class="primary" type="button" @click=${() => void this.performRename()} ?disabled=${busy}>${busy ? 'Renaming…' : 'Rename deck'}</button>
          </div>
        </div>
      </div>
    `;
  }

  private renderRestoreDialog() {
    const card = this.restoreTarget;
    if (!card) return nothing;
    const destinations = this.restoreDestinations();
    const busy = this.restoreState === 'saving';
    return html`
      <div class="dialog-backdrop" @click=${(event: MouseEvent) => { if (event.target === event.currentTarget) this.closeRestoreDialog(); }}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="restore-title" data-restore-dialog tabindex="-1">
          <h2 id="restore-title">Restore to deck</h2>
          <p>Restore “<strong>${card.headword}</strong>” from the protected Orphaned deck into one of your normal decks. Review history and saved vocabulary data are preserved.</p>
          <label>Destination deck
            <select
              .value=${this.restoreDestinationDeckId === null ? '' : String(this.restoreDestinationDeckId)}
              @change=${(event: Event) => { const value = (event.target as HTMLSelectElement).value; this.restoreDestinationDeckId = value ? Number(value) : null; }}
              ?disabled=${busy}
            >
              <option value="">Select a deck</option>
              ${destinations.map((deck) => html`<option value=${deck.id}>${deck.name}</option>`)}
            </select>
          </label>
          ${destinations.length === 0 ? html`<p class="muted">Create a normal deck first.</p>` : nothing}
          ${this.restoreError ? html`<p class="inline-status error" role="alert">${this.restoreError}</p>` : nothing}
          <div class="actions">
            <button type="button" @click=${() => this.closeRestoreDialog()} ?disabled=${busy}>Cancel</button>
            <button class="primary" type="button" @click=${() => void this.performRestore()} ?disabled=${busy || this.restoreDestinationDeckId === null || destinations.length === 0}>${busy ? 'Restoring…' : 'Restore'}</button>
          </div>
        </div>
      </div>
    `;
  }

  private renderSenseDialog() {
    const card = this.senseTarget;
    if (!card) return nothing;
    const busy = this.senseState === 'saving';
    const candidate = this.senseCandidates[0];
    const senses = candidate?.senses?.length ? candidate.senses : [];
    return html`
      <div class="dialog-backdrop" @click=${(event: MouseEvent) => { if (event.target === event.currentTarget) this.closeSenseDialog(); }}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="sense-title" data-sense-dialog tabindex="-1">
          <h2 id="sense-title">Change selected dictionary sense</h2>
          <p class="muted">
            Headword (read-only): <strong>${card.headword}</strong>
            ${card.pos ? ` · ${card.pos}` : ''}${card.gender ? ` · ${card.gender}` : ''}
          </p>
          <p>Choose a different dictionary meaning for “<strong>${card.headword}</strong>”.</p>
          <p class="muted">Learning history, your meanings, and custom pronunciation stay with this card. Dictionary meaning/grammar/examples may change.</p>
          ${this.senseLookupStatus === 'loading'
            ? html`<p class="result" role="status">Looking up the active dictionary…</p>`
            : nothing}
          ${this.senseLookupStatus === 'ready' && senses.length === 0
            ? html`<p class="result">This word has no selectable direct senses in the active dictionary.</p>`
            : nothing}
          ${senses.length > 0 ? html`
            <fieldset class="selection">
              <legend>Dictionary meaning</legend>
              <ul class="choice-list">
                ${senses.map((sense) => html`
                  <li>
                    <label class="choice">
                      <input
                        type="radio"
                        name="sense"
                        .value=${sense.sense_semantic_ref}
                        .checked=${this.senseSelectedRef === sense.sense_semantic_ref}
                        @change=${() => { this.senseSelectedRef = sense.sense_semantic_ref; }}
                      />
                      <span>${senseDisplayLabel(sense)}</span>
                    </label>
                  </li>
                `)}
              </ul>
            </fieldset>
          ` : nothing}
          ${this.senseError ? html`<p class="inline-status error" role="alert">${this.senseError}</p>` : nothing}
          <div class="actions">
            <button type="button" @click=${() => this.closeSenseDialog()} ?disabled=${busy}>Cancel</button>
            <button
              class="primary"
              type="button"
              data-testid=${`confirm-sense-${card.note_id}`}
              @click=${() => void this.performSenseChange()}
              ?disabled=${busy || this.senseLookupStatus !== 'ready' || !this.senseSelectedRef}
            >${busy ? 'Saving…' : 'Change sense'}</button>
          </div>
        </div>
      </div>
    `;
  }

  render() {
    const selectedDeck = this.selectedDeck();
    const showDeckList = this.deckStatus !== 'ready' || !selectedDeck;
    return html`
      <div class="shell">
        <header>
          <div>
            <h1>Wortlaut</h1>
            <div class="subtitle">German vocabulary</div>
          </div>
          <nav class="primary-nav" aria-label="Main navigation">
            <button type="button" aria-current=${this.view === 'study' ? 'false' : 'page'} @click=${() => { this.view = 'decks'; this.selectedDeckId = null; }}>Decks</button>
            <button type="button" aria-current=${this.view === 'study' ? 'page' : 'false'} @click=${() => void this.openStudy()}>Study due</button>
            <button type="button" aria-current=${this.view === 'settings' || this.view === 'chooser' ? 'page' : 'false'} @click=${() => { this.view = 'settings'; void this.loadDictionarySettings(); }}>Settings</button>
            <button type="button" @click=${this.loadDecks} ?disabled=${this.deckStatus === 'loading'}>${this.deckStatus === 'loading' ? 'Refreshing…' : 'Refresh decks'}</button>
          </nav>
        </header>
        ${this.renderNotices()}
        ${this.view === 'chooser' ? this.renderChooser() :
          this.view === 'settings' ? this.renderSettings() :
          this.view === 'study' ? this.renderStudy() :
          showDeckList ? html`
          <main class="panel">
            <div class="toolbar">
              <h2>Your decks</h2>
              <div class="actions">
                <span class="muted" aria-live="polite">${this.deckStatus === 'ready' ? 'Server-synced' : ''}</span>
                <button type="button" @click=${this.openCreateFolderDialog}>New folder</button>
              </div>
            </div>
            <form class="form-row" @submit=${this.createDeck}>
              <label>New deck name
                <input .value=${this.newDeckName} @input=${(event: InputEvent) => { this.newDeckName = (event.target as HTMLInputElement).value; }} ?disabled=${this.isCreating} maxlength="200" autocomplete="off" />
              </label>
              <button class="primary" type="submit" ?disabled=${this.isCreating}>${this.isCreating ? 'Creating…' : 'Create deck'}</button>
            </form>
            ${this.renderDeckList()}
          </main>
        ` : this.renderDeckDetail(selectedDeck)}
        ${this.renderCreateFolderDialog()}
        ${this.renderRenameFolderDialog()}
        ${this.renderDeleteFolderDialog()}
        ${this.renderMoveDeckDialog()}
      </div>
      <nav class="bottom-nav" aria-label="Main navigation">
        <button type="button" aria-current=${this.view === 'study' ? 'false' : 'page'} @click=${() => { this.view = 'decks'; this.selectedDeckId = null; }}>Decks</button>
        <button type="button" aria-current=${this.view === 'study' ? 'page' : 'false'} @click=${() => void this.openStudy()}>Study due</button>
      </nav>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'flashcard-app': FlashcardApp;
  }
}
