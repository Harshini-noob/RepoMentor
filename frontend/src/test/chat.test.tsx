import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ChatPage } from '../pages/ChatPage';
import * as sseLib from '../lib/sse';

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
    listChatSessions: vi.fn().mockResolvedValue([]),
    getChatSession: vi.fn().mockResolvedValue({
      session_id: 'sess-1',
      messages: [],
    }),
  };
});

describe('ChatPage', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it('fills textarea when clicking an example prompt', async () => {
    render(
      <MemoryRouter initialEntries={['/projects/proj_test/chat']}>
        <Routes>
          <Route path="/projects/:id/chat" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'test-org/repo-test' })).toBeInTheDocument();
    });

    const exampleBtn = screen.getByText('Why did I use Supabase?');
    fireEvent.click(exampleBtn);

    const textarea = screen.getByPlaceholderText(/Ask a question about this repository/i) as HTMLTextAreaElement;
    expect(textarea.value).toBe('Why did I use Supabase?');
  });

  it('shows steps as they arrive and keeps them after the answer', async () => {
    vi.spyOn(sseLib, 'streamChat').mockImplementation(
      async (_projId, _body, onEvent) => {
        onEvent({ type: 'session', session_id: 'sess-abc' });
        onEvent({ type: 'event', text: 'thinking... (step 1/6)' });
        onEvent({
          type: 'step',
          step: 1,
          tool: 'get_dossier',
          args: { section: 'apis' },
          preview: 'POST /query in main.py',
          evidence: ['file:main.py#L31-109'],
          ms: 120,
          tokens: 50,
        });
        onEvent({
          type: 'answer',
          text: '[Fact] The POST /query endpoint is ready.',
          stop_reason: 'answered',
          tokens: 250,
        });
        onEvent({ type: 'done' });
      }
    );

    render(
      <MemoryRouter initialEntries={['/projects/proj_test/chat']}>
        <Routes>
          <Route path="/projects/:id/chat" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'test-org/repo-test' })).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(/Ask a question about this repository/i);
    fireEvent.change(textarea, { target: { value: 'What APIs exist?' } });

    const sendBtn = screen.getByRole('button', { name: /Send/i });
    fireEvent.click(sendBtn);

    // Wait for the answer to appear
    await waitFor(() => {
      expect(screen.getByText(/The POST \/query endpoint is ready/)).toBeInTheDocument();
    });

    // Check that step is displayed under the Trace panel and kept after answer
    expect(screen.getByText('get_dossier')).toBeInTheDocument();
    expect(screen.getByText('POST /query in main.py')).toBeInTheDocument();
    expect(screen.getByText(/120ms/)).toBeInTheDocument();
    expect(screen.getByText(/\b50 tokens/)).toBeInTheDocument();

    // Check footer meta under assistant answer
    expect(screen.getByText(/1 step, 250 tokens/)).toBeInTheDocument();
  });
});
