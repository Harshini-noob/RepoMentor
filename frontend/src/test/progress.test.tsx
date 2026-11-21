import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ProgressPage } from '../pages/ProgressPage';
import * as api from '../api';

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
    getLearnerProfile: vi.fn(),
    createInterviewSession: vi.fn(),
  };
});

describe('ProgressPage', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it('renders empty state when no concepts exist', async () => {
    vi.mocked(api.getLearnerProfile).mockResolvedValueOnce({
      concepts: [],
      revise_first: [],
    });

    render(
      <MemoryRouter initialEntries={['/projects/proj_test/progress']}>
        <Routes>
          <Route path="/projects/:id/progress" element={<ProgressPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('No attempts yet. Run an interview first.')).toBeInTheDocument();
    });
  });

  it('renders weak concepts first and displays scores and misses', async () => {
    vi.mocked(api.getLearnerProfile).mockResolvedValueOnce({
      concepts: [
        {
          concept: 'Distributed Caching',
          status: 'mastered',
          score: 3.8,
          attempts: 5,
          last_seen: '2026-11-20T10:00:00Z',
          misses: [],
        },
        {
          concept: 'Async Architecture',
          status: 'learning',
          score: 1.5,
          attempts: 2,
          last_seen: '2026-11-20T11:00:00Z',
          misses: [{ point: 'ASGI worker configuration', kind: 'repo', count: 2 }],
        },
        {
          concept: 'Database Migration',
          status: 'unrated',
          score: 2.2,
          attempts: 1,
          last_seen: '2026-11-20T12:00:00Z',
          misses: [],
        },
      ],
      revise_first: ['Async Architecture'],
    });

    render(
      <MemoryRouter initialEntries={['/projects/proj_test/progress']}>
        <Routes>
          <Route path="/projects/:id/progress" element={<ProgressPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Concept Breakdown (Weakest first)')).toBeInTheDocument();
    });

    // Check Revise first section
    expect(screen.getByText('Revise first')).toBeInTheDocument();

    // Verify concepts order: Async Architecture (1.5) must appear before Distributed Caching (3.8)
    const conceptTitles = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent);
    expect(conceptTitles).toEqual([
      'Async Architecture',
      'Database Migration',
      'Distributed Caching',
    ]);

    // Check score values
    expect(screen.getByText('1.5 / 4')).toBeInTheDocument();
    expect(screen.getByText('2.2 / 4')).toBeInTheDocument();
    expect(screen.getByText('3.8 / 4')).toBeInTheDocument();

    // Check top misses
    expect(screen.getByText('ASGI worker configuration')).toBeInTheDocument();
    expect(screen.getByText('(2x)')).toBeInTheDocument();
  });

  it('Practice weakest button starts an interview and saves session to localStorage', async () => {
    vi.mocked(api.getLearnerProfile).mockResolvedValueOnce({
      concepts: [
        {
          concept: 'Async Architecture',
          status: 'learning',
          score: 1.5,
          attempts: 2,
          misses: [],
        },
      ],
      revise_first: ['Async Architecture'],
    });

    vi.mocked(api.createInterviewSession).mockResolvedValueOnce({
      session_id: 'sess-new-weakest',
      plan: [],
      status: 'active',
    });

    render(
      <MemoryRouter initialEntries={['/projects/proj_test/progress']}>
        <Routes>
          <Route path="/projects/:id/progress" element={<ProgressPage />} />
          <Route path="/projects/:id/interview" element={<div>Interview Tab View</div>} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Practice weakest')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Practice weakest'));

    await waitFor(() => {
      expect(api.createInterviewSession).toHaveBeenCalledWith('proj_test', {
        questions: 3,
        difficulty: 2,
        adaptive: true,
        teach: true,
        teach_below: 2,
        max_lessons: 2,
      });
    });

    expect(localStorage.getItem('repomentor_interview_session_proj_test')).toBe('sess-new-weakest');
    expect(screen.getByText('Interview Tab View')).toBeInTheDocument();
  });
});
