import { API_BASE_URL } from './config';
import {
  ApiError,
  ChatSessionDetailResponse,
  ChatSessionSummaryItem,
  DossierSectionDetail,
  DossierSummary,
  EvidenceResult,
  InterviewActionResponse,
  InterviewEvaluationResponse,
  InterviewPlanResponse,
  InterviewReportResponse,
  InterviewSessionCreateRequest,
  InterviewSessionCreateResponse,
  InterviewSessionSummaryItem,
  InterviewSkipResponse,
  InterviewStateResponse,
  ProfileResponse,
  ProjectDetail,
  ProjectSummary,
  StaticDossierBuildResult,
} from './types';

export const TIMEOUT_MS = 15000;
export const MODEL_TIMEOUT_MS = 150000;

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

export async function fetchWithTimeout<T>(
  url: string,
  options: RequestInit = {},
  timeoutMs: number = TIMEOUT_MS
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  if (options.signal) {
    if (options.signal.aborted) {
      controller.abort();
    } else {
      options.signal.addEventListener('abort', () => controller.abort(), { once: true });
    }
  }

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });

    if (!response.ok) {
      let serverMsg: string | undefined;
      let existingVal: number | undefined;
      let newVal: number | undefined;

      if (response.status === 409) {
        try {
          const errData = await response.json();
          if (errData && typeof errData.detail === 'string') {
            serverMsg = errData.detail;
            if (errData.detail.toLowerCase().includes('busy')) {
              serverMsg = 'Another request is using the model. Try again in a moment.';
            } else if (errData.existing !== undefined && errData.new !== undefined) {
              existingVal = errData.existing;
              newVal = errData.new;
              serverMsg = `${errData.detail} (existing: ${errData.existing}, new: ${errData.new})`;
            }
          }
        } catch {
          // ignore JSON parse error
        }
      }

      throw new ApiError(
        serverMsg || mapStatusToGenericMessage(response.status),
        response.status,
        existingVal,
        newVal
      );
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

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Dossier
// ---------------------------------------------------------------------------

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
  project_id: string,
  force: boolean = false
): Promise<StaticDossierBuildResult> {
  const query = force ? '?force=true' : '';
  return fetchWithTimeout<StaticDossierBuildResult>(
    `${API_BASE_URL}/projects/${encodeURIComponent(project_id)}/dossier/static${query}`,
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

// ---------------------------------------------------------------------------
// Chat / Mentor
// ---------------------------------------------------------------------------

export async function listChatSessions(
  project_id: string
): Promise<ChatSessionSummaryItem[]> {
  return fetchWithTimeout<ChatSessionSummaryItem[]>(
    `${API_BASE_URL}/projects/${encodeURIComponent(project_id)}/chat/sessions`
  );
}

export async function getChatSession(
  project_id: string,
  session_id: string
): Promise<ChatSessionDetailResponse> {
  return fetchWithTimeout<ChatSessionDetailResponse>(
    `${API_BASE_URL}/projects/${encodeURIComponent(
      project_id
    )}/chat/sessions/${encodeURIComponent(session_id)}`
  );
}

export async function createChatSession(
  project_id: string
): Promise<{ session_id: string }> {
  return fetchWithTimeout<{ session_id: string }>(
    `${API_BASE_URL}/projects/${encodeURIComponent(project_id)}/chat/sessions`,
    {
      method: 'POST',
    }
  );
}

// ---------------------------------------------------------------------------
// Learner Profile / Progress
// ---------------------------------------------------------------------------

export async function getLearnerProfile(
  project_id: string
): Promise<ProfileResponse> {
  return fetchWithTimeout<ProfileResponse>(
    `${API_BASE_URL}/projects/${encodeURIComponent(project_id)}/profile`
  );
}

// ---------------------------------------------------------------------------
// Interview
// ---------------------------------------------------------------------------

export async function getInterviewPlan(
  project_id: string,
  questions: number = 3,
  difficulty: number = 2
): Promise<InterviewPlanResponse> {
  return fetchWithTimeout<InterviewPlanResponse>(
    `${API_BASE_URL}/projects/${encodeURIComponent(
      project_id
    )}/interview/plan?questions=${encodeURIComponent(questions)}&difficulty=${encodeURIComponent(difficulty)}`
  );
}

export async function listInterviewSessions(
  project_id: string
): Promise<InterviewSessionSummaryItem[]> {
  return fetchWithTimeout<InterviewSessionSummaryItem[]>(
    `${API_BASE_URL}/projects/${encodeURIComponent(project_id)}/interview/sessions`
  );
}

export async function getInterviewSessionReport(
  project_id: string,
  session_id: string
): Promise<InterviewReportResponse> {
  return fetchWithTimeout<InterviewReportResponse>(
    `${API_BASE_URL}/projects/${encodeURIComponent(
      project_id
    )}/interview/sessions/${encodeURIComponent(session_id)}/report`
  );
}

export async function createInterviewSession(
  project_id: string,
  payload: InterviewSessionCreateRequest
): Promise<InterviewSessionCreateResponse> {
  return fetchWithTimeout<InterviewSessionCreateResponse>(
    `${API_BASE_URL}/projects/${encodeURIComponent(project_id)}/interview/sessions`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    }
  );
}

export async function getInterviewSessionState(
  project_id: string,
  session_id: string
): Promise<InterviewStateResponse> {
  return fetchWithTimeout<InterviewStateResponse>(
    `${API_BASE_URL}/projects/${encodeURIComponent(
      project_id
    )}/interview/sessions/${encodeURIComponent(session_id)}/state`
  );
}

// Model-calling request: 150s timeout
export async function nextInterviewAction(
  project_id: string,
  session_id: string,
  signal?: AbortSignal
): Promise<InterviewActionResponse> {
  return fetchWithTimeout<InterviewActionResponse>(
    `${API_BASE_URL}/projects/${encodeURIComponent(
      project_id
    )}/interview/sessions/${encodeURIComponent(session_id)}/next`,
    {
      method: 'POST',
      signal,
    },
    MODEL_TIMEOUT_MS
  );
}

// Model-calling request: 150s timeout
export async function submitInterviewAnswer(
  project_id: string,
  session_id: string,
  question_id: string,
  answer: string,
  signal?: AbortSignal
): Promise<InterviewEvaluationResponse> {
  return fetchWithTimeout<InterviewEvaluationResponse>(
    `${API_BASE_URL}/projects/${encodeURIComponent(
      project_id
    )}/interview/sessions/${encodeURIComponent(session_id)}/answer`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ question_id, answer }),
      signal,
    },
    MODEL_TIMEOUT_MS
  );
}

export async function skipInterviewQuestion(
  project_id: string,
  session_id: string
): Promise<InterviewSkipResponse> {
  return fetchWithTimeout<InterviewSkipResponse>(
    `${API_BASE_URL}/projects/${encodeURIComponent(
      project_id
    )}/interview/sessions/${encodeURIComponent(session_id)}/skip`,
    {
      method: 'POST',
    }
  );
}
