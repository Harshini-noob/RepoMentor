import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { InterviewPage } from '../pages/InterviewPage';
import * as api from '../api';
import { ApiError } from '../types';

vi.mock('../api', async () => {
  const actual = await vi.importActual<any>('../api');
  return {
    ...actual,
    getProject: vi.fn().mockResolvedValue({
      id: '1',
      project_id: 'proj_test',
      repo_full_name: 'test-org/repo-test',
      status: 'ready',
      counts: { files: 1, commits: 1, prs: 0, issues: 0 },
    }),
    listInterviewSessions: vi.fn().mockResolvedValue([]),
    getInterviewPlan: vi.fn().mockResolvedValue({ slots: [] }),
    createInterviewSession: vi.fn(),
    getInterviewSessionState: vi.fn().mockResolvedValue({
      session_id: 'sess-int-1',
      stage: 'awaiting_answer',
      pending: { kind: 'initial', question_id: 'q1', text: 'Why did you choose FastAPI?' },
      slot: 1,
      total: 2,
      status: 'active',
      lessons_given: 0,
      max_lessons: 2,
    }),
    nextInterviewAction: vi.fn(),
    submitInterviewAnswer: vi.fn(),
    skipInterviewQuestion: vi.fn(),
  };
});

describe('InterviewPage flow and protections', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it('walks question -> result -> lesson -> follow-up -> retry (before and after) -> next question -> finished', async () => {
    vi.mocked(api.createInterviewSession).mockResolvedValueOnce({
      session_id: 'sess-int-1',
      plan: [],
      status: 'active',
    });

    // 1. First next action returns question 1
    vi.mocked(api.nextInterviewAction).mockResolvedValueOnce({
      action: 'question',
      kind: 'initial',
      question_id: 'q1',
      text: 'Why did you choose FastAPI?',
      slot: 1,
      total: 2,
      round: 'tech',
      difficulty: 2,
      concept: 'framework',
    });

    render(
      <MemoryRouter initialEntries={['/projects/proj_test/interview']}>
        <Routes>
          <Route path="/projects/:id/interview" element={<InterviewPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Start interview')).toBeInTheDocument();
    });

    // Click Start interview
    fireEvent.click(screen.getByText('Start interview'));

    // Question 1 appears
    await waitFor(() => {
      expect(screen.getByText('Why did you choose FastAPI?')).toBeInTheDocument();
    });
    expect(screen.getByText('Question 1 of 2')).toBeInTheDocument();

    // Submit answer 1 with low score to trigger lesson
    vi.mocked(api.submitInterviewAnswer).mockResolvedValueOnce({
      score: 1,
      coverage: 0.25,
      points: [{ point: 'ASGI concurrency', status: 'missing', kind: 'repo' }],
      misconceptions: [],
      unverified: [],
      strengths: [],
      advice: 'Review asynchronous route handlers.',
      notes: [],
    });

    const textarea = screen.getByPlaceholderText(/Provide your defense/i);
    fireEvent.change(textarea, { target: { value: 'FastAPI is fast.' } });

    const submitBtn = screen.getByRole('button', { name: /Submit/i });
    fireEvent.click(submitBtn);

    // Evaluation result appears
    await waitFor(() => {
      expect(screen.getByText('1/4')).toBeInTheDocument();
    });
    expect(screen.getByText('25% coverage')).toBeInTheDocument();
    expect(screen.getAllByText('ASGI concurrency').length).toBeGreaterThanOrEqual(1);

    // 2. Click Continue -> Server returns Lesson
    vi.mocked(api.nextInterviewAction).mockResolvedValueOnce({
      action: 'lesson',
      concept: 'framework',
      text:
        'Good: You highlighted execution speed.\n' +
        'Idea: FastAPI provides asynchronous concurrency via ASGI.\n' +
        'In your project: Route handlers in [file:main.py#L1-10] leverage async def.\n' +
        'Say it like this: We chose FastAPI for non-blocking I/O.',
      notes: ['Validated against main.py'],
    });

    const continueBtn1 = screen.getByRole('button', { name: /Continue/i });
    fireEvent.click(continueBtn1);

    // Lesson view appears
    await waitFor(() => {
      expect(screen.getByText(/Mastering framework/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/We chose FastAPI for non-blocking I\/O/)).toBeInTheDocument();

    // 3. Click Continue in Lesson -> Server returns Follow-up
    vi.mocked(api.nextInterviewAction).mockResolvedValueOnce({
      action: 'question',
      kind: 'followup',
      question_id: 'q1_fu',
      text: 'What enables FastAPI to handle non-blocking I/O?',
      slot: 1,
      total: 2,
      round: 'tech',
      difficulty: 1,
      concept: 'framework',
    });

    const continueBtn2 = screen.getByRole('button', { name: /Continue/i });
    fireEvent.click(continueBtn2);

    // Follow-up question appears with "Easier follow-up" badge
    await waitFor(() => {
      expect(screen.getByText('What enables FastAPI to handle non-blocking I/O?')).toBeInTheDocument();
    });
    expect(screen.getByText('Easier follow-up')).toBeInTheDocument();

    // 4. Submit retry answer with high score (Before -> After)
    vi.mocked(api.submitInterviewAnswer).mockResolvedValueOnce({
      score: 4,
      coverage: 1.0,
      before: 1,
      after: 4,
      points: [{ point: 'ASGI event loop', status: 'covered', kind: 'repo' }],
      misconceptions: [],
      unverified: [],
      strengths: ['Great technical depth'],
      advice: '',
      notes: [],
    });

    const fuTextarea = screen.getByPlaceholderText(/Provide your defense/i);
    fireEvent.change(fuTextarea, { target: { value: 'FastAPI runs on Uvicorn using ASGI event loop.' } });

    const fuSubmitBtn = screen.getByRole('button', { name: /Submit/i });
    fireEvent.click(fuSubmitBtn);

    // Result card appears with Before -> After pill
    await waitFor(() => {
      expect(screen.getByText('4/4')).toBeInTheDocument();
    });
    expect(screen.getByText('Before: 1/4 → After: 4/4')).toBeInTheDocument();

    // 5. Click Continue -> Server returns Question 2
    vi.mocked(api.nextInterviewAction).mockResolvedValueOnce({
      action: 'question',
      kind: 'initial',
      question_id: 'q2',
      text: 'Explain the MongoDB database schema.',
      slot: 2,
      total: 2,
      round: 'data',
      difficulty: 2,
      concept: 'database',
    });

    const continueBtn3 = screen.getByRole('button', { name: /Continue/i });
    fireEvent.click(continueBtn3);

    // Question 2 appears
    await waitFor(() => {
      expect(screen.getByText('Explain the MongoDB database schema.')).toBeInTheDocument();
    });
    expect(screen.getByText('Question 2 of 2')).toBeInTheDocument();

    // 6. Skip question 2 -> advances to Finished
    vi.mocked(api.skipInterviewQuestion).mockResolvedValueOnce({
      action: 'skipped',
      status: 'skipped',
      stage: 'finished',
    });
    vi.mocked(api.nextInterviewAction).mockResolvedValueOnce({
      action: 'finished',
      summary: {
        answered: 1,
        skipped: 1,
        average_score: 4.0,
        rows: [
          { round: 'tech', difficulty: 2, concept: 'framework', score: 4 },
          { round: 'data', difficulty: 2, concept: 'database', score: null },
        ],
        weakest_concepts: ['database'],
        revise: [],
        before_after: [{ concept: 'framework', before: 1, after: 4 }],
        unverified_count: 0,
        lessons_given: 1,
      },
    });

    const skipBtn = screen.getByRole('button', { name: /Skip/i });
    fireEvent.click(skipBtn);

    // Finished summary appears
    await waitFor(() => {
      expect(screen.getByText('Interview Complete')).toBeInTheDocument();
    });
    expect(screen.getByText(/Average Score: 4.0 \/ 4/)).toBeInTheDocument();
    expect(screen.getByText('Practice weakest concepts')).toBeInTheDocument();
  });

  it('prevents double submit and disables button while request runs', async () => {
    vi.mocked(api.createInterviewSession).mockResolvedValueOnce({
      session_id: 'sess-int-busy',
      plan: [],
      status: 'active',
    });
    vi.mocked(api.nextInterviewAction).mockResolvedValueOnce({
      action: 'question',
      kind: 'initial',
      question_id: 'q-busy',
      text: 'Test question',
      slot: 1,
      total: 1,
    });

    let resolveSubmit: (val: any) => void;
    const slowSubmitPromise = new Promise((resolve) => {
      resolveSubmit = resolve;
    });
    vi.mocked(api.submitInterviewAnswer).mockReturnValueOnce(slowSubmitPromise as any);

    render(
      <MemoryRouter initialEntries={['/projects/proj_test/interview']}>
        <Routes>
          <Route path="/projects/:id/interview" element={<InterviewPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Start interview')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Start interview'));

    await waitFor(() => {
      expect(screen.getByText('Test question')).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(/Provide your defense/i);
    fireEvent.change(textarea, { target: { value: 'My answer' } });

    const submitBtn = screen.getByRole('button', { name: /Submit/i });
    fireEvent.click(submitBtn);

    // Button must be disabled and cannot be submitted again
    expect(submitBtn).toBeDisabled();
    expect(screen.getByText('This can take up to a minute on the free tier.')).toBeInTheDocument();
    expect(api.submitInterviewAnswer).toHaveBeenCalledTimes(1);

    // Attempt second click while busy
    fireEvent.click(submitBtn);
    expect(api.submitInterviewAnswer).toHaveBeenCalledTimes(1);

    // Resolve submit
    resolveSubmit!({
      score: 3,
      coverage: 0.8,
      points: [],
      misconceptions: [],
      unverified: [],
      strengths: [],
      advice: '',
      notes: [],
    });

    await waitFor(() => {
      expect(screen.getByText('3/4')).toBeInTheDocument();
    });
  });

  it('refetches state on 409 busy and preserves user text', async () => {
    vi.mocked(api.createInterviewSession).mockResolvedValueOnce({
      session_id: 'sess-int-409',
      plan: [],
      status: 'active',
    });
    vi.mocked(api.nextInterviewAction).mockResolvedValueOnce({
      action: 'question',
      kind: 'initial',
      question_id: 'q-409',
      text: 'Another question',
      slot: 1,
      total: 1,
    });

    vi.mocked(api.submitInterviewAnswer).mockRejectedValueOnce(
      new ApiError('Another request is using the model. Try again in a moment.', 409)
    );
    vi.mocked(api.getInterviewSessionState).mockResolvedValueOnce({
      session_id: 'sess-int-409',
      stage: 'awaiting_answer',
      pending: { kind: 'initial', question_id: 'q-409', text: 'Another question' },
      slot: 1,
      total: 1,
      status: 'active',
      lessons_given: 0,
      max_lessons: 2,
    });

    render(
      <MemoryRouter initialEntries={['/projects/proj_test/interview']}>
        <Routes>
          <Route path="/projects/:id/interview" element={<InterviewPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Start interview')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Start interview'));

    await waitFor(() => {
      expect(screen.getByText('Another question')).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(/Provide your defense/i) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'My preserved answer text' } });

    const submitBtn = screen.getByRole('button', { name: /Submit/i });
    fireEvent.click(submitBtn);

    // Banner appears
    await waitFor(() => {
      expect(screen.getByText('Another request is using the model. Try again in a moment.')).toBeInTheDocument();
    });

    // User text is kept
    expect(textarea.value).toBe('My preserved answer text');

    // State refetch was triggered
    expect(api.getInterviewSessionState).toHaveBeenCalledWith('proj_test', 'sess-int-409');
  });
});
