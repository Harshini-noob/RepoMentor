import { API_BASE_URL } from './config';
import {
  ApiError,
  DossierSectionDetail,
  DossierSummary,
  EvidenceResult,
  ProjectDetail,
  ProjectSummary,
  StaticDossierBuildResult,
} from './types';

const TIMEOUT_MS = 15000;

function mapStatusToGenericMessage(status?: number): string {
  if (status === 404) {
    return 'The requested resource could not be found.';
  }
  if (status === 409) {
    return 'The project is currently busy or not ready.';
  }
  if (status === 400) {
    return 'Invalid request. Please check your input.';
  }
  return 'An error occurred while communicating with the server. Please try again.';
}

async function fetchWithTimeout<T>(
  url: string,
  options: RequestInit = {}
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new ApiError(mapStatusToGenericMessage(response.status), response.status);
    }

    return (await response.json()) as T;
  } catch (error: unknown) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('Request timed out. Please check your connection and try again.');
    }
    throw new ApiError('Unable to connect to the server. Please ensure the backend is running.');
  } finally {
    clearTimeout(timer);
  }
}

export async function listProjects(): Promise<ProjectSummary[]> {
  return fetchWithTimeout<ProjectSummary[]>(`${API_BASE_URL}/projects`);
}

export async function createProject(
  repo_url: string
): Promise<{ project_id: string; status: string }> {
  return fetchWithTimeout<{ project_id: string; status: string }>(
    `${API_BASE_URL}/projects`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ repo_url }),
    }
  );
}

export async function getProject(project_id: string): Promise<ProjectDetail> {
  return fetchWithTimeout<ProjectDetail>(
    `${API_BASE_URL}/projects/${encodeURIComponent(project_id)}`
  );
}

export async function getDossierSummary(
  project_id: string
): Promise<DossierSummary> {
  return fetchWithTimeout<DossierSummary>(
    `${API_BASE_URL}/projects/${encodeURIComponent(project_id)}/dossier/summary`
  );
}

export async function getDossierSection(
  project_id: string,
  section_name: string
): Promise<DossierSectionDetail> {
  return fetchWithTimeout<DossierSectionDetail>(
    `${API_BASE_URL}/projects/${encodeURIComponent(
      project_id
    )}/dossier/sections/${encodeURIComponent(section_name)}`
  );
}

export async function buildStaticDossier(
  project_id: string
): Promise<StaticDossierBuildResult> {
  return fetchWithTimeout<StaticDossierBuildResult>(
    `${API_BASE_URL}/projects/${encodeURIComponent(project_id)}/dossier/static`,
    {
      method: 'POST',
    }
  );
}

export async function getEvidence(
  project_id: string,
  evidence_id: string
): Promise<EvidenceResult> {
  return fetchWithTimeout<EvidenceResult>(
    `${API_BASE_URL}/projects/${encodeURIComponent(
      project_id
    )}/evidence?id=${encodeURIComponent(evidence_id)}`
  );
}
