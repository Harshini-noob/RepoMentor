export interface ProjectCounts {
  files: number;
  commits: number;
  dependencies: number;
  facts: number;
}

export interface ProjectDetailCounts {
  files: number;
  commits: number;
  prs: number;
  issues: number;
}

export interface ProjectSummary {
  id: string;
  project_id: string;
  repo_full_name: string;
  status: 'queued' | 'ingesting' | 'ready' | 'failed' | string;
  facts_status?: string | null;
  facts_error?: string | null;
  default_branch?: string | null;
  analysed_at?: string | null;
  error?: string | null;
  dossier_version?: number | null;
  counts: ProjectCounts;
}

export interface ProjectDetail {
  id: string;
  project_id: string;
  repo_full_name: string;
  status: 'queued' | 'ingesting' | 'ready' | 'failed' | string;
  facts_status?: string | null;
  facts_error?: string | null;
  default_branch?: string | null;
  analysed_at?: string | null;
  error?: string | null;
  counts: ProjectDetailCounts;
}

export interface DossierSectionSummary {
  name: string;
  claims: number;
  facts: number;
  inferred: number;
  uncertain: number;
  user_edited: number;
  agent?: number;
}

export type DossierSummarySection = DossierSectionSummary;

export interface DossierSummary {
  version: number;
  generated_from_sha?: string | null;
  sections: DossierSectionSummary[];
}

export type ClaimTag = 'fact' | 'inferred' | 'uncertain' | 'general';
export type ClaimSource = 'static' | 'agent' | 'user';

export interface DossierClaim {
  id?: string;
  section?: string;
  claim: string;
  tag: ClaimTag;
  evidence: string[];
  confidence?: number;
  source: ClaimSource;
  commit?: string | null;
  commit_sha?: string | null;
}

export interface DossierSectionDetail {
  name: string;
  claims: DossierClaim[];
}

export interface StaticDossierBuildResult {
  version: number;
  sections: Record<string, number>;
}

export interface FileEvidenceLine {
  n: number;
  text: string;
  in_range: boolean;
}

export interface FileEvidenceAvailable {
  kind: 'file';
  path: string;
  language?: string;
  start: number;
  end: number;
  lines: FileEvidenceLine[];
  available?: true;
}

export interface FileEvidenceUnavailable {
  kind: 'file';
  path: string;
  available: false;
  reason: string;
}

export type FileEvidence = FileEvidenceAvailable | FileEvidenceUnavailable;

export interface CommitEvidence {
  kind: 'commit';
  sha: string;
  date?: string | null;
  author?: string | null;
  message: string;
  additions: number;
  deletions: number;
  files_changed: string[];
  path?: string;
  path_in_commit?: boolean;
}

export interface DependencyEvidenceItem {
  name: string;
  version_spec?: string | null;
  dep_kind: string;
  source_file?: string | null;
}

export interface DependencyEvidence {
  kind: 'dependency';
  items: DependencyEvidenceItem[];
}

export interface FactEvidenceItem {
  file_path: string;
  line_start: number;
  line_end: number;
  in_test: boolean;
}

export interface FactEvidence {
  kind: 'fact';
  items: FactEvidenceItem[];
}

export interface RepoEvidence {
  kind: 'repo';
  repo: string;
  default_branch?: string | null;
  languages: Record<string, number>;
  commits: number;
  files: number;
}

export type EvidenceResult =
  | FileEvidence
  | CommitEvidence
  | DependencyEvidence
  | FactEvidence
  | RepoEvidence;

export class ApiError extends Error {
  status?: number;
  existing?: number;
  newCount?: number;

  constructor(message: string, status?: number, existing?: number, newCount?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.existing = existing;
    this.newCount = newCount;
  }
}

// ---------------------------------------------------------------------------
// Phase 8: Chat & Mentor Models
// ---------------------------------------------------------------------------

export interface ChatSessionSummaryItem {
  session_id: string;
  created_at?: string | null;
  updated_at?: string | null;
  turns: number;
  first_question: string;
}

export interface ChatMessageItem {
  role: string;
  content: string;
  ts?: string | null;
}

export interface ChatSessionDetailResponse {
  session_id: string;
  created_at?: string | null;
  messages: ChatMessageItem[];
}

export interface ChatStep {
  step: number;
  tool: string;
  args: Record<string, any>;
  preview: string;
  evidence: string[];
  ms: number;
  tokens: number;
}

export type ChatStreamEvent =
  | { type: 'session'; session_id: string }
  | { type: 'event'; text: string }
  | { type: 'step'; step: number; tool: string; args: Record<string, any>; preview: string; evidence: string[]; ms: number; tokens: number }
  | { type: 'answer'; text: string; stop_reason: string; tokens: number }
  | { type: 'error'; message: string }
  | { type: 'done' };

export interface ChatMessageUI {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  steps?: ChatStep[];
  tokens?: number;
  stop_reason?: string;
  isError?: boolean;
}

// ---------------------------------------------------------------------------
// Phase 8: Interview Models
// ---------------------------------------------------------------------------

export interface InterviewPlanSlot {
  slot: number;
  concept?: string | null;
  difficulty: number;
  reason: string;
  first_seed_claim?: string;
  claim?: string;
}

export interface InterviewPlanResponse {
  slots: InterviewPlanSlot[];
}

export interface InterviewSessionSummaryItem {
  session_id: string;
  status: string;
  plan_mode: string;
  created_at?: string | null;
  answered: number;
  answered_count: number;
  average_score?: number | null;
}

export interface InterviewSessionCreateRequest {
  questions: number;
  difficulty: number;
  adaptive: boolean;
  teach: boolean;
  teach_below?: number;
  max_lessons?: number;
}

export interface InterviewSessionCreateResponse {
  session_id: string;
  plan: Array<Record<string, any>>;
  status: string;
}

export interface InterviewPendingState {
  kind: 'initial' | 'followup' | 'retry' | string;
  question_id: string;
  text?: string;
}

export interface InterviewStateResponse {
  session_id: string;
  stage: 'need_question' | 'awaiting_answer' | 'need_lesson' | 'need_followup' | 'need_retry' | 'finished' | string;
  pending?: InterviewPendingState | null;
  cycle?: Record<string, any> | null;
  slot: number;
  total: number;
  status: string;
  lessons_given: number;
  max_lessons: number;
}

export interface InterviewReportRow {
  round?: string;
  difficulty?: number;
  concept?: string;
  score?: number | null;
}

export interface InterviewReportResponse {
  answered: number;
  skipped: number;
  average_score?: number | null;
  rows: InterviewReportRow[];
  per_concept?: Record<string, any>;
  concept_scores?: Record<string, any>;
  weakest_concepts?: string[];
  revise?: Array<Record<string, any>>;
  unverified_count?: number;
  lessons_given?: number;
  before_after?: Array<Record<string, any>>;
  average_improvement?: number | null;
}

export interface InterviewActionResponse {
  action: 'question' | 'lesson' | 'finished' | string;
  kind?: 'initial' | 'followup' | 'retry' | string | null;
  question_id?: string | null;
  text?: string | null;
  slot?: number | null;
  total?: number | null;
  round?: string | null;
  difficulty?: number | null;
  concept?: string | null;
  reason?: string | null;
  notes?: string[] | null;
  summary?: InterviewReportResponse | null;
}

export interface RubricPoint {
  point: string;
  kind?: 'repo' | 'general' | string;
  status: 'covered' | 'partial' | 'missing' | string;
  quote?: string;
}

export interface Misconception {
  quote?: string;
  why?: string;
  seed_line?: string;
}

export interface UnverifiedStatement {
  claim?: string;
  statement?: string;
  reason?: string;
}

export interface InterviewEvaluationResponse {
  score?: number | null;
  coverage?: number | null;
  points?: RubricPoint[];
  misconceptions?: Misconception[];
  unverified?: UnverifiedStatement[];
  strengths?: string[];
  advice?: string;
  notes?: string[];
  role?: string;
  next?: string;
  copied?: boolean | null;
  before?: number | null;
  after?: number | null;
}

export interface InterviewSkipResponse {
  action: string;
  status: string;
  stage: string;
}

// ---------------------------------------------------------------------------
// Phase 8: Learner Profile Models
// ---------------------------------------------------------------------------

export interface ProfileMissItem {
  point: string;
  kind: string;
  count: number;
  last_seen?: string | null;
}

export interface ProfileConceptItem {
  concept: string;
  status: string;
  score: number;
  attempts: number;
  last_seen?: string | null;
  misses: ProfileMissItem[];
}

export interface ProfileResponse {
  concepts: ProfileConceptItem[];
  revise_first: string[];
}
