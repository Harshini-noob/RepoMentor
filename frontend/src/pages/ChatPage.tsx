import React, { useEffect, useState, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  getProject,
  listChatSessions,
  getChatSession,
} from '../api';
import { streamChat } from '../lib/sse';
import { AnswerText } from '../lib/renderAnswer';
import { EvidenceDrawer } from '../components/EvidenceDrawer';
import { ProjectTabs } from '../components/ProjectTabs';
import { parseEvidenceId } from '../lib/evidence';
import type {
  ProjectDetail,
  ChatSessionSummaryItem,
  ChatStep,
  ChatMessageUI,
} from '../types';

const MAX_CHAT_CHARS = 2000;

function formatArgsSummary(args: Record<string, any> | undefined): string {
  if (!args || Object.keys(args).length === 0) return '';
  return Object.entries(args)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(', ');
}

export const ChatPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const initialIdent = id || '';

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const canonicalId = project?.project_id || initialIdent;

  // Session & messages
  const storageKey = `repomentor_chat_session_${canonicalId}`;
  const [sessionId, setSessionId] = useState<string | null>(() => {
    return localStorage.getItem(storageKey) || null;
  });
  const [previousSessions, setPreviousSessions] = useState<ChatSessionSummaryItem[]>([]);
  const [messages, setMessages] = useState<ChatMessageUI[]>([]);

  // Trace
  const [currentEventText, setCurrentEventText] = useState<string | null>(null);
  const [activeTraceSteps, setActiveTraceSteps] = useState<ChatStep[]>([]);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Input & Error
  const [input, setInput] = useState<string>('');
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  // Evidence Drawer
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  // Sync session ID to localStorage
  useEffect(() => {
    if (sessionId) {
      localStorage.setItem(storageKey, sessionId);
    } else {
      localStorage.removeItem(storageKey);
    }
  }, [sessionId, storageKey]);

  // Load project info & sessions list
  useEffect(() => {
    if (!canonicalId) return;

    getProject(canonicalId)
      .then((p) => setProject(p))
      .catch(() => null);

    listChatSessions(canonicalId)
      .then((sessions) => setPreviousSessions(sessions || []))
      .catch(() => null);
  }, [canonicalId]);

  // Only load messages on mount if a session was already restored from localStorage
  const hasRestoredSessionRef = useRef(Boolean(localStorage.getItem(storageKey)));

  useEffect(() => {
    if (!canonicalId || !sessionId || !hasRestoredSessionRef.current) return;
    hasRestoredSessionRef.current = false;

    let mounted = true;
    getChatSession(canonicalId, sessionId)
      .then((data) => {
        if (!mounted || !data) return;
        const mapped: ChatMessageUI[] = (data.messages || []).map((m, idx) => ({
          id: `hist-${idx}`,
          role: m.role === 'user' ? 'user' : 'assistant',
          text: m.content,
        }));
        setMessages(mapped);
      })
      .catch(() => {
        // If session not found, clear it
        if (mounted) {
          setSessionId(null);
          setMessages([]);
        }
      });

    return () => {
      mounted = false;
    };
  }, [canonicalId, sessionId]);

  // Auto scroll messages
  useEffect(() => {
    if (typeof messagesEndRef.current?.scrollIntoView === 'function') {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, isStreaming, currentEventText]);

  const handleNewChat = () => {
    if (isStreaming) {
      abortControllerRef.current?.abort();
    }
    hasRestoredSessionRef.current = false;
    setSessionId(null);
    setMessages([]);
    setActiveTraceSteps([]);
    setCurrentEventText(null);
    setErrorBanner(null);
  };

  const handleSelectSession = async (sessId: string) => {
    if (!sessId) return;
    if (isStreaming) {
      abortControllerRef.current?.abort();
    }
    hasRestoredSessionRef.current = false;
    setSessionId(sessId);
    setActiveTraceSteps([]);
    setCurrentEventText(null);
    setErrorBanner(null);

    try {
      const data = await getChatSession(canonicalId, sessId);
      if (data) {
        const mapped: ChatMessageUI[] = (data.messages || []).map((m, idx) => ({
          id: `hist-${idx}`,
          role: m.role === 'user' ? 'user' : 'assistant',
          text: m.content,
        }));
        setMessages(mapped);
      }
    } catch {
      setMessages([]);
    }
  };

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsStreaming(false);
    setCurrentEventText(null);
  };

  const handleSend = async (messageText?: string) => {
    const textToSend = (messageText !== undefined ? messageText : input).trim();
    if (!textToSend || isStreaming) return;

    setErrorBanner(null);
    // Clear previous trace on new question
    setActiveTraceSteps([]);
    setCurrentEventText('thinking...');

    const userMsgId = `user-${Date.now()}`;
    const userMsg: ChatMessageUI = {
      id: userMsgId,
      role: 'user',
      text: textToSend,
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setIsStreaming(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    const accumulatedSteps: ChatStep[] = [];
    let receivedAnswer: { text: string; tokens: number; stop_reason: string } | null = null;
    let streamError: string | null = null;

    try {
      await streamChat(
        canonicalId,
        {
          message: textToSend,
          session_id: sessionId,
        },
        (event) => {
          if (event.type === 'session') {
            setSessionId(event.session_id);
          } else if (event.type === 'event') {
            setCurrentEventText(event.text);
          } else if (event.type === 'step') {
            accumulatedSteps.push(event);
            setActiveTraceSteps([...accumulatedSteps]);
          } else if (event.type === 'answer') {
            receivedAnswer = {
              text: event.text,
              tokens: event.tokens,
              stop_reason: event.stop_reason,
            };
          } else if (event.type === 'error') {
            streamError = event.message;
          }
        },
        controller.signal
      );

      // On completion
      if (receivedAnswer) {
        const ans = receivedAnswer as { text: string; tokens: number; stop_reason: string };
        const assistantMsg: ChatMessageUI = {
          id: `asst-${Date.now()}`,
          role: 'assistant',
          text: ans.text,
          steps: [...accumulatedSteps],
          tokens: ans.tokens,
          stop_reason: ans.stop_reason,
          isError: ans.stop_reason === 'error',
        };
        setMessages((prev) => [...prev, assistantMsg]);
      } else if (streamError) {
        const assistantMsg: ChatMessageUI = {
          id: `asst-err-${Date.now()}`,
          role: 'assistant',
          text: streamError,
          steps: [...accumulatedSteps],
          tokens: 0,
          stop_reason: 'error',
          isError: true,
        };
        setMessages((prev) => [...prev, assistantMsg]);
      }

      // Refresh previous sessions list in background
      listChatSessions(canonicalId)
        .then((s) => setPreviousSessions(s || []))
        .catch(() => null);
    } catch (err: any) {
      if (err.name === 'AbortError' || controller.signal.aborted) {
        // User aborted, keep what we have
      } else {
        const is409 = err?.status === 409 || err?.message?.toLowerCase().includes('busy');
        const displayMsg = is409
          ? 'Another request is using the model. Try again in a moment.'
          : err instanceof Error
          ? err.message
          : 'Failed to communicate with mentor.';

        setErrorBanner(displayMsg);

        // A 409 keeps the user's text
        if (is409) {
          setInput(textToSend);
          // Remove the tentatively added user message so they can re-send cleanly
          setMessages((prev) => prev.filter((m) => m.id !== userMsgId));
        } else {
          setMessages((prev) => [
            ...prev,
            {
              id: `asst-err-${Date.now()}`,
              role: 'assistant',
              text: displayMsg,
              steps: [...accumulatedSteps],
              tokens: 0,
              stop_reason: 'error',
              isError: true,
            },
          ]);
        }
      }
    } finally {
      setIsStreaming(false);
      setCurrentEventText(null);
      abortControllerRef.current = null;
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const examplePrompts = [
    'Why did I use Supabase?',
    'What API endpoints does this project expose?',
    'Is X really necessary?',
  ];

  return (
    <div className="chat-page-root">
      {/* Header & Tabs */}
      <div className="page-header">
        <div className="breadcrumbs">
          <Link to="/" className="breadcrumb-link">Projects</Link>
          <span className="breadcrumb-separator">/</span>
          <span className="breadcrumb-current">{project?.repo_full_name || canonicalId}</span>
        </div>
        <div className="header-main">
          <div>
            <h1 className="project-title">{project?.repo_full_name || canonicalId}</h1>
            <p className="project-subtitle">Chat with RepoMentor about architecture, decisions, and defenses</p>
          </div>
        </div>
      </div>

      <ProjectTabs projectId={canonicalId} />

      {errorBanner && (
        <div className="banner banner-error" role="alert">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <span>{errorBanner}</span>
        </div>
      )}

      {/* Chat Toolbar */}
      <div className="chat-toolbar" role="toolbar" aria-label="Chat options">
        <div className="chat-toolbar-left">
          <button
            type="button"
            className="action-btn secondary"
            onClick={handleNewChat}
            disabled={isStreaming}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            New chat
          </button>
        </div>

        <div className="chat-toolbar-right">
          <label htmlFor="prev-sessions-select" className="sr-only">Previous chats</label>
          <select
            id="prev-sessions-select"
            className="select-input"
            value={sessionId || ''}
            onChange={(e) => handleSelectSession(e.target.value)}
            disabled={isStreaming || previousSessions.length === 0}
          >
            <option value="" disabled>
              {previousSessions.length === 0 ? 'No previous chats' : 'Previous chats'}
            </option>
            {previousSessions.map((s) => (
              <option key={s.session_id} value={s.session_id}>
                {s.first_question
                  ? `${s.first_question.slice(0, 36)}... (${s.turns} turns)`
                  : `Session ${s.session_id.slice(0, 8)} (${s.turns} turns)`}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Main Two-Column Layout */}
      <div className="chat-two-column-layout">
        {/* Left Column: Conversation */}
        <div className="chat-conversation-column" role="region" aria-label="Conversation history" aria-live="polite">
          {messages.length === 0 ? (
            <div className="chat-empty-state">
              <div className="chat-empty-icon" aria-hidden="true">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
              </div>
              <h2 className="chat-empty-title">Ask RepoMentor anything about this repository</h2>
              <p className="chat-empty-text">
                Explore architectural trade-offs, clarify technical decisions, and get ready for interviews.
              </p>

              <div className="example-prompts-container" aria-label="Example prompts">
                <p className="example-prompts-label">Try an example prompt:</p>
                <div className="example-prompts-list">
                  {examplePrompts.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      className="example-prompt-btn"
                      onClick={() => setInput(prompt)}
                    >
                      <span>{prompt}</span>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <polyline points="9 18 15 12 9 6" />
                      </svg>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="chat-messages-list">
              {messages.map((m) => {
                const isUser = m.role === 'user';
                return (
                  <div
                    key={m.id}
                    className={`chat-bubble-row ${isUser ? 'user-row' : 'assistant-row'}`}
                  >
                    <div className={`chat-bubble ${isUser ? 'bubble-user' : 'bubble-assistant'} ${m.isError ? 'error-bubble' : ''}`}>
                      {isUser ? (
                        <p className="chat-user-text">{m.text}</p>
                      ) : (
                        <div>
                          <AnswerText
                            text={m.text}
                            onOpenEvidence={(evId) => setSelectedEvidenceId(evId)}
                          />
                          {!m.isError && (m.steps !== undefined || m.tokens !== undefined) && (
                            <div className="chat-meta-footer">
                              <span>
                                {m.steps !== undefined ? `${m.steps.length} step${m.steps.length === 1 ? '' : 's'}` : ''}
                                {m.steps !== undefined && m.tokens !== undefined ? ', ' : ''}
                                {m.tokens !== undefined ? `${m.tokens} tokens` : ''}
                              </span>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              <div ref={messagesEndRef} />
            </div>
          )}

          {/* Input Box */}
          <div className="chat-input-wrapper">
            <div className="chat-input-box">
              <label htmlFor="chat-textarea" className="sr-only">Type a message to RepoMentor</label>
              <textarea
                id="chat-textarea"
                className="chat-textarea"
                rows={3}
                placeholder="Ask a question about this repository... (Enter sends, Shift+Enter adds a line)"
                value={input}
                maxLength={MAX_CHAT_CHARS}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isStreaming}
              />
              <div className="chat-input-controls">
                <span className={`char-counter ${input.length >= MAX_CHAT_CHARS ? 'limit-reached' : ''}`} aria-live="polite">
                  {input.length} / {MAX_CHAT_CHARS}
                </span>

                <div className="chat-input-buttons">
                  {isStreaming ? (
                    <button
                      type="button"
                      className="action-btn danger stop-btn"
                      onClick={handleStop}
                      title="Stop generating"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true">
                        <rect x="6" y="6" width="12" height="12" rx="2" />
                      </svg>
                      Stop
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="action-btn primary send-btn"
                      onClick={() => handleSend()}
                      disabled={!input.trim() || isStreaming}
                    >
                      Send
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <line x1="22" y1="2" x2="11" y2="13" />
                        <polygon points="22 2 15 22 11 13 2 9 22 2" />
                      </svg>
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: Live Trace Panel */}
        <aside className="chat-trace-column" aria-label="Execution Trace" aria-live="polite">
          <div className="trace-panel-card">
            <div className="trace-panel-header">
              <h2 className="trace-panel-title">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                </svg>
                Trace
              </h2>
              {isStreaming && (
                <span className="live-status-pill">
                  <span className="spinner-icon small" aria-hidden="true" />
                  Live
                </span>
              )}
            </div>

            {/* Current running event */}
            {isStreaming && currentEventText && (
              <div className="trace-current-event">
                <span className="spinner-icon" aria-hidden="true" />
                <span className="trace-event-text">{currentEventText}</span>
              </div>
            )}

            {/* Steps list */}
            <div className="trace-steps-container">
              {activeTraceSteps.length === 0 && !isStreaming ? (
                <p className="trace-empty-message">
                  Trace details will appear here while the mentor investigates and reasons.
                </p>
              ) : (
                <div className="trace-steps-list">
                  {activeTraceSteps.map((step, idx) => (
                    <div key={`step-${idx}`} className="trace-step-item">
                      <div className="trace-step-top">
                        <span className="trace-step-number">#{step.step}</span>
                        <span className="trace-tool-badge">{step.tool}</span>
                        {step.args && Object.keys(step.args).length > 0 && (
                          <span className="trace-args-summary" title={formatArgsSummary(step.args)}>
                            {formatArgsSummary(step.args)}
                          </span>
                        )}
                      </div>

                      {step.preview && (
                        <p className="trace-step-preview" title={step.preview}>
                          {step.preview}
                        </p>
                      )}

                      {step.evidence && step.evidence.length > 0 && (
                        <div className="trace-step-evidence">
                          {step.evidence.slice(0, 3).map((evId, evIdx) => {
                            const parsed = parseEvidenceId(evId);
                            return (
                              <button
                                key={`ev-${evIdx}`}
                                type="button"
                                className="evidence-chip inline-evidence-chip"
                                onClick={() => setSelectedEvidenceId(evId)}
                                title={`View evidence: ${evId}`}
                              >
                                <span>{parsed.label}</span>
                              </button>
                            );
                          })}
                        </div>
                      )}

                      <div className="trace-step-metrics">
                        <span>{step.ms}ms</span>
                        <span>•</span>
                        <span>{step.tokens} tokens</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </aside>
      </div>

      {/* Evidence Drawer */}
      <EvidenceDrawer
        projectId={canonicalId}
        evidenceId={selectedEvidenceId}
        onClose={() => setSelectedEvidenceId(null)}
      />
    </div>
  );
};
