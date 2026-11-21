import React, { useEffect, useState, useRef, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  getProject,
  getInterviewPlan,
  listInterviewSessions,
  createInterviewSession,
  getInterviewSessionState,
  getInterviewSessionReport,
  nextInterviewAction,
  submitInterviewAnswer,
  skipInterviewQuestion,
} from '../api';
import { AnswerText } from '../lib/renderAnswer';
import { EvidenceDrawer } from '../components/EvidenceDrawer';
import { ProjectTabs } from '../components/ProjectTabs';
import type {
  ProjectDetail,
  InterviewPlanSlot,
  InterviewSessionSummaryItem,
  InterviewActionResponse,
  InterviewEvaluationResponse,
  InterviewReportResponse,
  InterviewStateResponse,
} from '../types';

const MAX_ANSWER_CHARS = 3000;

interface ParsedLessonParts {
  good: string;
  idea: string;
  project: string;
  say: string;
}

function parseLessonText(rawText: string): ParsedLessonParts {
  const result: ParsedLessonParts = {
    good: '',
    idea: '',
    project: '',
    say: '',
  };

  if (!rawText) return result;

  const patterns = [
    { key: 'good', regex: /(?:^|\n)\s*Good:\s*([\s\S]*?)(?=(?:\n\s*Idea:|\n\s*In your project:|\n\s*Say it like this:|$))/i },
    { key: 'idea', regex: /(?:^|\n)\s*Idea:\s*([\s\S]*?)(?=(?:\n\s*Good:|\n\s*In your project:|\n\s*Say it like this:|$))/i },
    { key: 'project', regex: /(?:^|\n)\s*In your project:\s*([\s\S]*?)(?=(?:\n\s*Good:|\n\s*Idea:|\n\s*Say it like this:|$))/i },
    { key: 'say', regex: /(?:^|\n)\s*Say it like this:\s*([\s\S]*?)(?=(?:\n\s*Good:|\n\s*Idea:|\n\s*In your project:|$))/i },
  ];

  let matchedAny = false;
  for (const p of patterns) {
    const match = rawText.match(p.regex);
    if (match && match[1]) {
      result[p.key as keyof ParsedLessonParts] = match[1].trim();
      matchedAny = true;
    }
  }

  if (!matchedAny) {
    // If text does not use headings, put it all in idea
    result.idea = rawText.trim();
  }

  return result;
}

export const InterviewPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const initialIdent = id || '';

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const canonicalId = project?.project_id || initialIdent;

  // Session persistence
  const storageKey = `repomentor_interview_session_${canonicalId}`;
  const [sessionId, setSessionId] = useState<string | null>(() => {
    return localStorage.getItem(storageKey) || null;
  });

  // State machine view state
  const [, setSessionState] = useState<InterviewStateResponse | null>(null);
  const [currentAction, setCurrentAction] = useState<InterviewActionResponse | null>(null);
  const [evaluation, setEvaluation] = useState<InterviewEvaluationResponse | null>(null);
  const [report, setReport] = useState<InterviewReportResponse | null>(null);

  // Setup view state
  const [numQuestions, setNumQuestions] = useState<number>(3);
  const [difficulty, setDifficulty] = useState<number>(2);
  const [adaptive, setAdaptive] = useState<boolean>(true);
  const [teach, setTeach] = useState<boolean>(true);
  const [planSlots, setPlanSlots] = useState<InterviewPlanSlot[] | null>(null);
  const [loadingPlan, setLoadingPlan] = useState<boolean>(false);
  const [previousSessions, setPreviousSessions] = useState<InterviewSessionSummaryItem[]>([]);
  const [viewingReportSessionId, setViewingReportSessionId] = useState<string | null>(null);
  const [viewingReportData, setViewingReportData] = useState<InterviewReportResponse | null>(null);

  // Active view input
  const [answerInput, setAnswerInput] = useState<string>('');
  const [isBusy, setIsBusy] = useState<boolean>(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  // Evidence Drawer
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Sync session ID to localStorage
  useEffect(() => {
    if (sessionId) {
      localStorage.setItem(storageKey, sessionId);
    } else {
      localStorage.removeItem(storageKey);
    }
  }, [sessionId, storageKey]);

  // Load project & session list
  useEffect(() => {
    if (!canonicalId) return;

    getProject(canonicalId)
      .then((p) => setProject(p))
      .catch(() => null);

    listInterviewSessions(canonicalId)
      .then((list) => setPreviousSessions(list || []))
      .catch(() => null);
  }, [canonicalId]);

  // Resume active session on mount
  useEffect(() => {
    if (!canonicalId || !sessionId) return;

    let mounted = true;
    setIsBusy(true);
    setErrorBanner(null);

    Promise.resolve(getInterviewSessionState(canonicalId, sessionId))
      .then(async (state) => {
        if (!mounted) return;
        setSessionState(state);

        if (state.stage === 'finished') {
          // Finished: call next to get summary
          try {
            const nextRes = await nextInterviewAction(canonicalId, sessionId);
            if (mounted) {
              setCurrentAction(nextRes);
              if (nextRes.summary) {
                setReport(nextRes.summary);
              }
            }
          } catch {
            // fallback to get report directly
            const r = await getInterviewSessionReport(canonicalId, sessionId).catch(() => null);
            if (mounted && r) setReport(r);
          }
        } else if (state.stage === 'awaiting_answer' && state.pending) {
          // Show pending question without calling model
          const nextRes = await nextInterviewAction(canonicalId, sessionId).catch(() => null);
          if (mounted && nextRes) {
            setCurrentAction(nextRes);
          } else if (mounted) {
            setCurrentAction({
              action: 'question',
              kind: state.pending.kind,
              question_id: state.pending.question_id,
              text: state.pending.text,
              slot: state.slot,
              total: state.total,
            });
          }
        } else {
          // Model stage: advance
          const nextRes = await nextInterviewAction(canonicalId, sessionId);
          if (mounted) {
            setCurrentAction(nextRes);
            if (nextRes.action === 'finished' && nextRes.summary) {
              setReport(nextRes.summary);
            }
          }
        }
      })
      .catch((err: any) => {
        if (!mounted) return;
        if (err?.status === 404) {
          // Forget unknown session
          setSessionId(null);
          setSessionState(null);
          setCurrentAction(null);
        } else {
          setErrorBanner(err instanceof Error ? err.message : 'Failed to resume session');
        }
      })
      .finally(() => {
        if (mounted) setIsBusy(false);
      });

    return () => {
      mounted = false;
    };
  }, [canonicalId, sessionId]);

  // Handle Show Plan
  const handleShowPlan = async () => {
    if (!canonicalId || isBusy) return;
    setLoadingPlan(true);
    setErrorBanner(null);
    try {
      const res = await getInterviewPlan(canonicalId, numQuestions, difficulty);
      setPlanSlots(res.slots || []);
    } catch (err: any) {
      setErrorBanner(err instanceof Error ? err.message : 'Failed to load plan');
    } finally {
      setLoadingPlan(false);
    }
  };

  // Handle Start Interview
  const handleStartInterview = async (customQuestions?: number) => {
    if (!canonicalId || isBusy) return;
    setIsBusy(true);
    setErrorBanner(null);
    setEvaluation(null);
    setReport(null);
    setAnswerInput('');

    try {
      const qCount = customQuestions || numQuestions;
      const res = await createInterviewSession(canonicalId, {
        questions: qCount,
        difficulty,
        adaptive,
        teach,
        teach_below: 2,
        max_lessons: 2,
      });

      const newSessionId = res.session_id;
      setSessionId(newSessionId);

      // Call next action to begin the first question
      const nextRes = await nextInterviewAction(canonicalId, newSessionId);
      setCurrentAction(nextRes);
      if (nextRes.action === 'finished' && nextRes.summary) {
        setReport(nextRes.summary);
      }
    } catch (err: any) {
      const is409 = err?.status === 409 || err?.message?.toLowerCase().includes('busy');
      setErrorBanner(
        is409
          ? 'Another request is using the model. Try again in a moment.'
          : err instanceof Error
          ? err.message
          : 'Failed to start interview.'
      );
    } finally {
      setIsBusy(false);
    }
  };

  // Handle Next Action progression
  const handleNext = async () => {
    if (!canonicalId || !sessionId || isBusy) return;
    setIsBusy(true);
    setErrorBanner(null);
    setEvaluation(null);
    setAnswerInput('');

    try {
      const res = await nextInterviewAction(canonicalId, sessionId);
      setCurrentAction(res);
      if (res.action === 'finished' && res.summary) {
        setReport(res.summary);
      }
    } catch (err: any) {
      const is409 = err?.status === 409 || err?.message?.toLowerCase().includes('busy');
      setErrorBanner(
        is409
          ? 'Another request is using the model. Try again in a moment.'
          : err instanceof Error
          ? err.message
          : 'Failed to advance interview.'
      );
      if (is409) {
        // Refetch state on 409
        getInterviewSessionState(canonicalId, sessionId)
          .then((st) => setSessionState(st))
          .catch(() => null);
      }
    } finally {
      setIsBusy(false);
    }
  };

  // Handle Submit Answer
  const handleAnswerSubmit = async () => {
    if (!canonicalId || !sessionId || !currentAction?.question_id || !answerInput.trim() || isBusy) {
      return;
    }

    setIsBusy(true);
    setErrorBanner(null);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const res = await submitInterviewAnswer(
        canonicalId,
        sessionId,
        currentAction.question_id,
        answerInput.trim(),
        controller.signal
      );
      setEvaluation(res);
      // Clear input only on success
      setAnswerInput('');
    } catch (err: any) {
      const is409 = err?.status === 409 || err?.message?.toLowerCase().includes('busy');
      setErrorBanner(
        is409
          ? 'Another request is using the model. Try again in a moment.'
          : err instanceof Error
          ? err.message
          : 'Failed to submit answer.'
      );
      if (is409) {
        // 409 keeps user text and refetches state
        getInterviewSessionState(canonicalId, sessionId)
          .then((st) => setSessionState(st))
          .catch(() => null);
      }
    } finally {
      setIsBusy(false);
      abortControllerRef.current = null;
    }
  };

  // Handle Skip
  const handleSkip = async () => {
    if (!canonicalId || !sessionId || isBusy) return;
    setIsBusy(true);
    setErrorBanner(null);

    try {
      await skipInterviewQuestion(canonicalId, sessionId);
      // Advance to next
      const res = await nextInterviewAction(canonicalId, sessionId);
      setCurrentAction(res);
      if (res.action === 'finished' && res.summary) {
        setReport(res.summary);
      }
    } catch (err: any) {
      setErrorBanner(err instanceof Error ? err.message : 'Failed to skip question.');
    } finally {
      setIsBusy(false);
    }
  };

  // Handle viewing a report from the sessions list
  const handleViewReport = async (sessId: string) => {
    if (!canonicalId || isBusy) return;
    setIsBusy(true);
    setErrorBanner(null);
    try {
      const rep = await getInterviewSessionReport(canonicalId, sessId);
      setViewingReportSessionId(sessId);
      setViewingReportData(rep);
    } catch (err: any) {
      setErrorBanner(err instanceof Error ? err.message : 'Failed to load report.');
    } finally {
      setIsBusy(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      handleAnswerSubmit();
    }
  };

  const wordCount = useMemo(() => {
    const trimmed = answerInput.trim();
    return trimmed ? trimmed.split(/\s+/).length : 0;
  }, [answerInput]);

  // Determine kind label
  const getKindLabel = (kind?: string | null) => {
    if (kind === 'followup') return 'Easier follow-up';
    if (kind === 'retry') return 'Original question again';
    return 'Question';
  };

  return (
    <div className="interview-page-root">
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
            <p className="project-subtitle">Defend your architectural and code decisions in an interactive mock interview</p>
          </div>
          {sessionId && (
            <button
              type="button"
              className="action-btn secondary"
              onClick={() => {
                setSessionId(null);
                setSessionState(null);
                setCurrentAction(null);
                setEvaluation(null);
                setReport(null);
              }}
              disabled={isBusy}
            >
              Exit Interview
            </button>
          )}
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

      {/* Main Views Container */}
      <div className="interview-main-content">
        {/* 1. SETUP VIEW (No active session) */}
        {!sessionId && (
          <div className="interview-setup-view">
            <div className="setup-card">
              <h2 className="setup-title">Interview Configuration</h2>
              <p className="setup-description">
                Tailor the mock interview to test your project knowledge, design trade-offs, and implementation details.
              </p>

              <div className="setup-form-grid">
                <div className="form-group">
                  <label htmlFor="num-questions-select" className="form-label">
                    Number of Questions
                  </label>
                  <select
                    id="num-questions-select"
                    className="select-input"
                    value={numQuestions}
                    onChange={(e) => setNumQuestions(Number(e.target.value))}
                    disabled={isBusy}
                  >
                    {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
                      <option key={n} value={n}>{n} question{n === 1 ? '' : 's'}</option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="difficulty-select" className="form-label">
                    Difficulty Level
                  </label>
                  <select
                    id="difficulty-select"
                    className="select-input"
                    value={difficulty}
                    onChange={(e) => setDifficulty(Number(e.target.value))}
                    disabled={isBusy}
                  >
                    <option value={1}>1 - Beginner (Surface & Facts)</option>
                    <option value={2}>2 - Intermediate (Implementation details)</option>
                    <option value={3}>3 - Advanced (Architecture & Trade-offs)</option>
                    <option value={4}>4 - Expert (Edge cases & Resiliency)</option>
                    <option value={5}>5 - Mastery (System-level decisions)</option>
                  </select>
                </div>

                <div className="form-group toggle-group">
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={adaptive}
                      onChange={(e) => setAdaptive(e.target.checked)}
                      disabled={isBusy}
                    />
                    <span>Adaptive (Focus on weak concepts from your profile)</span>
                  </label>
                </div>

                <div className="form-group toggle-group">
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={teach}
                      onChange={(e) => setTeach(e.target.checked)}
                      disabled={isBusy}
                    />
                    <span>Teach (Generate grounded lessons when answers miss key points)</span>
                  </label>
                </div>
              </div>

              <div className="setup-actions">
                <button
                  type="button"
                  className="action-btn secondary"
                  onClick={handleShowPlan}
                  disabled={isBusy || loadingPlan}
                >
                  {loadingPlan ? 'Loading plan...' : 'Show plan'}
                </button>
                <button
                  type="button"
                  className="action-btn primary"
                  onClick={() => handleStartInterview()}
                  disabled={isBusy}
                >
                  Start interview
                </button>
              </div>

              {/* Adaptive Plan Table */}
              {planSlots && (
                <div className="plan-preview-container">
                  <h3 className="plan-preview-title">Generated Interview Plan</h3>
                  <div className="table-wrapper">
                    <table className="plan-table">
                      <thead>
                        <tr>
                          <th>Slot</th>
                          <th>Concept</th>
                          <th>Difficulty</th>
                          <th>Reason</th>
                          <th>First seed claim</th>
                        </tr>
                      </thead>
                      <tbody>
                        {planSlots.map((slot) => (
                          <tr key={slot.slot}>
                            <td>#{slot.slot}</td>
                            <td><span className="concept-chip">{slot.concept || 'General'}</span></td>
                            <td>Level {slot.difficulty}</td>
                            <td className="reason-cell">{slot.reason}</td>
                            <td className="claim-cell">{slot.first_seed_claim || slot.claim || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>

            {/* Last Sessions List */}
            <div className="previous-sessions-card">
              <h3 className="previous-sessions-title">Previous Interview Sessions</h3>
              {previousSessions.length === 0 ? (
                <p className="no-sessions-msg">No interview sessions found. Start your first mock interview above!</p>
              ) : (
                <div className="sessions-list">
                  {previousSessions.map((s) => (
                    <div key={s.session_id} className="session-item-row">
                      <div className="session-item-info">
                        <span className={`status-pill ${s.status === 'finished' ? 'ready' : 'ingesting'}`}>
                          {s.status}
                        </span>
                        <span className="session-date">
                          {s.created_at ? new Date(s.created_at).toLocaleDateString() : 'Recent'}
                        </span>
                        <span className="session-answered">
                          {s.answered_count ?? s.answered} answered
                        </span>
                        {s.average_score !== null && s.average_score !== undefined && (
                          <span className="session-avg-score">
                            Avg: {s.average_score.toFixed(1)} / 4
                          </span>
                        )}
                      </div>
                      <button
                        type="button"
                        className="action-btn secondary small"
                        onClick={() => handleViewReport(s.session_id)}
                        disabled={isBusy}
                      >
                        View report
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* 2. ACTIVE VIEW / LESSON VIEW / RESULT VIEW / FINISHED VIEW */}
        {sessionId && (
          <div className="interview-active-container">
            {/* Busy Banner */}
            {isBusy && (
              <div className="model-call-indicator" role="status" aria-live="polite">
                <span className="spinner-icon" aria-hidden="true" />
                <span>This can take up to a minute on the free tier.</span>
              </div>
            )}

            {/* A. LESSON CARD (action "lesson") */}
            {currentAction?.action === 'lesson' && !evaluation && (
              <div className="lesson-card" role="region" aria-label="Grounded Lesson" aria-live="polite">
                <div className="lesson-header">
                  <span className="lesson-badge">Grounded Lesson</span>
                  <h2 className="lesson-title">Mastering {currentAction.concept || 'Key Architecture Concept'}</h2>
                </div>

                {(() => {
                  const parts = parseLessonText(currentAction.text || '');
                  return (
                    <div className="lesson-sections-grid">
                      {parts.good && (
                        <div className="lesson-part good-part">
                          <h3 className="part-title">Good:</h3>
                          <AnswerText
                            text={parts.good}
                            onOpenEvidence={(evId) => setSelectedEvidenceId(evId)}
                          />
                        </div>
                      )}
                      {parts.idea && (
                        <div className="lesson-part idea-part">
                          <h3 className="part-title">Idea:</h3>
                          <AnswerText
                            text={parts.idea}
                            onOpenEvidence={(evId) => setSelectedEvidenceId(evId)}
                          />
                        </div>
                      )}
                      {parts.project && (
                        <div className="lesson-part project-part">
                          <h3 className="part-title">In your project:</h3>
                          <AnswerText
                            text={parts.project}
                            onOpenEvidence={(evId) => setSelectedEvidenceId(evId)}
                          />
                        </div>
                      )}
                      {parts.say && (
                        <div className="lesson-part say-part">
                          <h3 className="part-title">Say it like this:</h3>
                          <AnswerText
                            text={parts.say}
                            onOpenEvidence={(evId) => setSelectedEvidenceId(evId)}
                          />
                        </div>
                      )}
                    </div>
                  );
                })()}

                {currentAction.notes && currentAction.notes.length > 0 && (
                  <details className="lesson-notes-details">
                    <summary className="lesson-notes-summary">How this lesson was checked</summary>
                    <ul className="lesson-notes-list">
                      {currentAction.notes.map((n, i) => (
                        <li key={i}>{n}</li>
                      ))}
                    </ul>
                  </details>
                )}

                <div className="lesson-actions">
                  <button
                    type="button"
                    className="action-btn primary continue-btn"
                    onClick={handleNext}
                    disabled={isBusy}
                  >
                    Continue
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <polyline points="9 18 15 12 9 6" />
                    </svg>
                  </button>
                </div>
              </div>
            )}

            {/* B. EVALUATION RESULT CARD */}
            {evaluation && (
              <div className="result-card" role="region" aria-label="Answer Evaluation" aria-live="polite">
                <div className="result-header">
                  <div className="result-score-block">
                    <span className="result-score-large">
                      {evaluation.score !== null && evaluation.score !== undefined ? `${evaluation.score}/4` : '—'}
                    </span>
                    <span className="result-coverage">
                      {evaluation.coverage !== null && evaluation.coverage !== undefined
                        ? `${Math.round(evaluation.coverage * 100)}% coverage`
                        : ''}
                    </span>
                  </div>
                  {evaluation.copied && (
                    <div className="banner banner-warning">
                      ⚠️ Answer was copied from the lesson.
                    </div>
                  )}
                  {/* Retry Before/After Card */}
                  {evaluation.before !== null && evaluation.before !== undefined && (
                    <div className="retry-comparison-pill">
                      <span>
                        Before: {evaluation.before}/4 → After:{' '}
                        {evaluation.copied
                          ? 'copied from the lesson (not counted)'
                          : `${evaluation.after}/4`}
                      </span>
                    </div>
                  )}
                </div>

                {/* Rubric Points */}
                {evaluation.points && evaluation.points.length > 0 && (
                  <div className="rubric-points-section">
                    <h3 className="section-label">Rubric Points</h3>
                    <div className="rubric-points-list">
                      {evaluation.points.map((pt, idx) => {
                        const statusClass = pt.status === 'covered' ? 'status-covered' : pt.status === 'partial' ? 'status-partial' : 'status-missing';
                        return (
                          <div key={idx} className={`rubric-point-item ${statusClass}`}>
                            <div className="rubric-point-status">
                              <span className="status-indicator-icon" aria-hidden="true">
                                {pt.status === 'covered' ? '✓' : pt.status === 'partial' ? '◐' : '✗'}
                              </span>
                              <span className="status-indicator-text">{pt.status}</span>
                              {pt.kind && <span className="kind-pill">{pt.kind}</span>}
                            </div>
                            <p className="point-text">{pt.point}</p>
                            {pt.quote && (
                              <blockquote className="point-quote">"{pt.quote}"</blockquote>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* What you missed */}
                {evaluation.points && evaluation.points.some((p) => p.status === 'missing' || p.status === 'partial') && (
                  <div className="missed-section">
                    <h3 className="section-label missed-label">What you missed</h3>
                    <ul className="missed-list">
                      {evaluation.points
                        .filter((p) => p.status === 'missing' || p.status === 'partial')
                        .map((p, i) => (
                          <li key={i}>{p.point}</li>
                        ))}
                    </ul>
                  </div>
                )}

                {/* Misconceptions */}
                {evaluation.misconceptions && evaluation.misconceptions.length > 0 && (
                  <div className="misconceptions-section">
                    <h3 className="section-label misconceptions-label">Misconceptions</h3>
                    <div className="misconceptions-list">
                      {evaluation.misconceptions.map((m, idx) => (
                        <div key={idx} className="misconception-item">
                          {m.quote && <blockquote className="misconception-quote">"{m.quote}"</blockquote>}
                          {m.why && <p className="misconception-why">{m.why}</p>}
                          {m.seed_line && (
                            <span className="contradiction-badge">contradicts your repo</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Unverified Statements */}
                {evaluation.unverified && evaluation.unverified.length > 0 && (
                  <div className="unverified-section">
                    <h3 className="section-label unverified-label">Check these against your repo (not scored)</h3>
                    <ul className="unverified-list">
                      {evaluation.unverified.map((u, idx) => (
                        <li key={idx}>
                          <span>{u.statement || u.claim}</span>
                          {u.reason && <span className="unverified-reason"> — {u.reason}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Strengths */}
                {evaluation.strengths && evaluation.strengths.length > 0 && (
                  <div className="strengths-section">
                    <h3 className="section-label strengths-label">Strengths</h3>
                    <ul className="strengths-list">
                      {evaluation.strengths.map((s, idx) => (
                        <li key={idx}>{s}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Advice */}
                {evaluation.advice && (
                  <div className="advice-section">
                    <h3 className="section-label advice-label">Advice</h3>
                    <p className="advice-text">{evaluation.advice}</p>
                  </div>
                )}

                {/* Notes */}
                {evaluation.notes && evaluation.notes.length > 0 && (
                  <div className="notes-section">
                    <h3 className="section-label notes-label">Notes</h3>
                    <ul className="notes-list">
                      {evaluation.notes.map((n, idx) => (
                        <li key={idx}>{n}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="result-actions">
                  <button
                    type="button"
                    className="action-btn primary continue-btn"
                    onClick={handleNext}
                    disabled={isBusy}
                  >
                    Continue
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <polyline points="9 18 15 12 9 6" />
                    </svg>
                  </button>
                </div>
              </div>
            )}

            {/* C. ACTIVE QUESTION VIEW (when not in lesson and not in evaluation) */}
            {currentAction?.action === 'question' && !evaluation && (
              <div className="active-question-card" role="region" aria-label="Current Question" aria-live="polite">
                <div className="active-question-top">
                  <div className="active-question-meta">
                    <span className="question-slot-pill">
                      Question {currentAction.slot || 1} of {currentAction.total || 3}
                    </span>
                    {currentAction.round && (
                      <span className="meta-chip round-chip">{currentAction.round}</span>
                    )}
                    {currentAction.difficulty && (
                      <span className="meta-chip diff-chip">Level {currentAction.difficulty}</span>
                    )}
                    {currentAction.concept && (
                      <span className="meta-chip concept-chip">{currentAction.concept}</span>
                    )}
                    <span className="kind-badge">{getKindLabel(currentAction.kind)}</span>
                  </div>
                </div>

                <div className="question-prompt-box">
                  <h2 className="question-prompt-text">{currentAction.text}</h2>
                </div>

                <div className="answer-input-container">
                  <label htmlFor="interview-answer-textarea" className="sr-only">Your answer</label>
                  <textarea
                    id="interview-answer-textarea"
                    className="interview-answer-textarea"
                    rows={6}
                    placeholder="Provide your defense, architecture rationale, or code details... (Ctrl+Enter to submit)"
                    value={answerInput}
                    maxLength={MAX_ANSWER_CHARS}
                    onChange={(e) => setAnswerInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    disabled={isBusy}
                  />

                  <div className="answer-input-footer">
                    <div className="answer-helper-text">
                      <span className="hint-text">Cite specific files and decisions.</span>
                      <span className="word-counter">• {wordCount} words</span>
                      <span className={`char-counter ${answerInput.length >= MAX_ANSWER_CHARS ? 'limit-reached' : ''}`}>
                        • {answerInput.length} / {MAX_ANSWER_CHARS}
                      </span>
                    </div>

                    <div className="answer-input-buttons">
                      <button
                        type="button"
                        className="action-btn secondary skip-btn"
                        onClick={handleSkip}
                        disabled={isBusy}
                      >
                        Skip
                      </button>
                      <button
                        type="button"
                        className="action-btn primary submit-btn"
                        onClick={handleAnswerSubmit}
                        disabled={!answerInput.trim() || isBusy}
                      >
                        Submit
                        <span className="kbd-shortcut" aria-hidden="true">(Ctrl+↵)</span>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* D. FINISHED VIEW */}
            {(currentAction?.action === 'finished' || report) && !evaluation && (
              <div className="interview-finished-view" role="region" aria-label="Interview Summary" aria-live="polite">
                <div className="finished-summary-card">
                  <div className="finished-header">
                    <h2 className="finished-title">Interview Complete</h2>
                    <div className="finished-avg-badge">
                      Average Score: {(report?.average_score ?? currentAction?.summary?.average_score ?? 0).toFixed(1)} / 4
                    </div>
                  </div>

                  {/* Per Question Table */}
                  <h3 className="summary-section-title">Question Breakdown</h3>
                  <div className="table-wrapper">
                    <table className="breakdown-table">
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>Round</th>
                          <th>Difficulty</th>
                          <th>Concept</th>
                          <th>Score</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(report?.rows || currentAction?.summary?.rows || []).map((row, idx) => (
                          <tr key={idx}>
                            <td>#{idx + 1}</td>
                            <td>{row.round || 'tech'}</td>
                            <td>Level {row.difficulty || 2}</td>
                            <td><span className="concept-chip">{row.concept || 'General'}</span></td>
                            <td>
                              <span className="score-value">
                                {row.score !== null && row.score !== undefined
                                  ? `${row.score}/4`
                                  : (row as any).status === 'no_question'
                                  ? 'No question'
                                  : 'Skipped'}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {/* Weakest Concepts & Revise List */}
                  {(() => {
                    const weakList = (report?.weakest_concepts || currentAction?.summary?.weakest_concepts || [])
                      .filter((c) => c.toLowerCase() !== 'general');
                    if (weakList.length === 0) return null;
                    return (
                      <div className="weak-concepts-box">
                        <h3 className="summary-section-title">Weakest Concepts</h3>
                        <div className="chips-list">
                          {weakList.map((c) => (
                            <span key={c} className="concept-chip weak">{c}</span>
                          ))}
                        </div>
                      </div>
                    );
                  })()}

                  {/* Lessons given & before/after */}
                  {((report?.before_after && report.before_after.length > 0) ||
                    (currentAction?.summary?.before_after && currentAction.summary.before_after.length > 0)) && (
                    <div className="lessons-summary-box">
                      <h3 className="summary-section-title">Grounded Lessons & Improvements</h3>
                      <div className="before-after-list">
                        {(report?.before_after || currentAction?.summary?.before_after || []).map((ba, idx) => (
                          <div key={idx} className="before-after-item">
                            <span>Concept: <strong>{ba.concept || 'General'}</strong></span>
                            <span>
                              Before: {ba.before}/4 → After:{' '}
                              {ba.copied
                                ? 'copied from the lesson (not counted)'
                                : `${ba.after}/4`}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="finished-actions">
                    <button
                      type="button"
                      className="action-btn primary"
                      onClick={() => handleStartInterview(3)}
                      disabled={isBusy}
                    >
                      Practice weakest concepts
                    </button>
                    <Link
                      to={`/projects/${encodeURIComponent(canonicalId)}/progress`}
                      className="action-btn secondary"
                    >
                      View Learner Progress
                    </Link>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Modal / Report Viewer from setup history */}
      {viewingReportSessionId && viewingReportData && (
        <div className="modal-backdrop" onClick={() => setViewingReportSessionId(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <div className="modal-header">
              <h2 className="modal-title">Session Report</h2>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setViewingReportSessionId(null)}
                aria-label="Close report"
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <div className="finished-avg-badge">
                Average Score: {viewingReportData.average_score?.toFixed(1) ?? '—'} / 4
              </div>
              <div className="table-wrapper">
                <table className="breakdown-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Round</th>
                      <th>Difficulty</th>
                      <th>Concept</th>
                      <th>Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {viewingReportData.rows.map((row, idx) => (
                      <tr key={idx}>
                        <td>#{idx + 1}</td>
                        <td>{row.round || 'tech'}</td>
                        <td>Level {row.difficulty || 2}</td>
                        <td><span className="concept-chip">{row.concept || 'General'}</span></td>
                        <td>
                          {row.score !== null && row.score !== undefined
                            ? `${row.score}/4`
                            : (row as any).status === 'no_question'
                            ? 'No question'
                            : 'Skipped'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Evidence Drawer */}
      <EvidenceDrawer
        projectId={canonicalId}
        evidenceId={selectedEvidenceId}
        onClose={() => setSelectedEvidenceId(null)}
      />
    </div>
  );
};
