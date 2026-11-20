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
}

export type DossierSummarySection = DossierSectionSummary;

export interface DossierSummary {
  version: number;
  generated_from_sha?: string | null;
  sections: DossierSectionSummary[];
}

export type ClaimTag = 'fact' | 'inferred' | 'uncertain';
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
  sections: Array<{
    name: string;
    claims: DossierClaim[];
  }>;
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

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}
