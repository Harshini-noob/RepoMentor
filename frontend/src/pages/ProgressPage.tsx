import React, { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import {
  getProject,
  getLearnerProfile,
  listInterviewSessions,
  getInterviewSessionReport,
  createInterviewSession,
} from '../api';
import { ProjectTabs } from '../components/ProjectTabs';
import type {
  ProjectDetail,
  ProfileResponse,
  InterviewSessionSummaryItem,
  InterviewReportResponse,
} from '../types';

export const ProgressPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const initialIdent = id || '';
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const canonicalId = project?.project_id || initialIdent;

  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [sessions, setSessions] = useState<InterviewSessionSummaryItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [startingInterview, setStartingInterview] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Viewing session report
  const [viewingReportId, setViewingReportId] = useState<string | null>(null);
  const [viewingReportData, setViewingReportData] = useState<InterviewReportResponse | null>(null);

  useEffect(() => {
    if (!canonicalId) return;

    let mounted = true;
    setLoading(true);
    setError(null);

    Promise.all([
      getProject(canonicalId).catch(() => null),
      getLearnerProfile(canonicalId).catch(() => null),
      listInterviewSessions(canonicalId).catch(() => []),
    ])
      .then(([p, prof, sessList]) => {
        if (!mounted) return;
        if (p) setProject(p);
        if (prof) setProfile(prof);
        if (sessList) setSessions(sessList);
      })
      .catch((err: any) => {
        if (mounted) {
          setError(err instanceof Error ? err.message : 'Failed to load progress.');
        }
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, [canonicalId]);

  const handlePracticeWeakest = async () => {
    if (!canonicalId || startingInterview) return;
    setStartingInterview(true);
    setError(null);

    try {
      const res = await createInterviewSession(canonicalId, {
        questions: 3,
        difficulty: 2,
        adaptive: true,
        teach: true,
        teach_below: 2,
        max_lessons: 2,
      });

      // Save to localStorage for interview resume
      const storageKey = `repomentor_interview_session_${canonicalId}`;
      localStorage.setItem(storageKey, res.session_id);

      navigate(`/projects/${encodeURIComponent(canonicalId)}/interview`);
    } catch (err: any) {
      setError(err instanceof Error ? err.message : 'Failed to start practice interview');
    } finally {
      setStartingInterview(false);
    }
  };

  const handleViewReport = async (sessId: string) => {
    if (!canonicalId) return;
    try {
      const rep = await getInterviewSessionReport(canonicalId, sessId);
      setViewingReportId(sessId);
      setViewingReportData(rep);
    } catch (err: any) {
      setError(err instanceof Error ? err.message : 'Failed to load report');
    }
  };

  // Sort concepts with weak concepts first
  const sortedConcepts = [...(profile?.concepts || [])].sort((a, b) => {
    if (a.score !== b.score) return a.score - b.score;
    return a.concept.localeCompare(b.concept);
  });

  return (
    <div className="progress-page-root">
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
            <p className="project-subtitle">Track your mastery, knowledge gaps, and defense improvements</p>
          </div>
          {profile?.concepts && profile.concepts.length > 0 && (
            <button
              type="button"
              className="action-btn primary"
              onClick={handlePracticeWeakest}
              disabled={startingInterview}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
              {startingInterview ? 'Starting...' : 'Practice weakest'}
            </button>
          )}
        </div>
      </div>

      <ProjectTabs projectId={canonicalId} />

      {error && (
        <div className="banner banner-error" role="alert">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="loading-state" role="status">
          <span className="spinner-icon" aria-hidden="true" />
          <span>Loading learner profile...</span>
        </div>
      ) : !profile || profile.concepts.length === 0 ? (
        <div className="empty-state-card" role="region" aria-label="No attempts yet">
          <div className="empty-state-icon" aria-hidden="true">
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
            </svg>
          </div>
          <h2 className="empty-state-title">No attempts yet. Run an interview first.</h2>
          <p className="empty-state-text">
            Start a mock interview to evaluate your knowledge across architecture, APIs, decisions, and data flows.
          </p>
          <Link
            to={`/projects/${encodeURIComponent(canonicalId)}/interview`}
            className="action-btn primary"
          >
            Start your first interview
          </Link>
        </div>
      ) : (
        <div className="progress-content-grid">
          {/* Revise First Box */}
          {profile.revise_first && profile.revise_first.length > 0 && (
            <div className="revise-first-card" role="region" aria-label="Revise First">
              <div className="card-top-header">
                <h2 className="card-heading">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="8" x2="12" y2="12" />
                    <line x1="12" y1="16" x2="12.01" y2="16" />
                  </svg>
                  Revise first
                </h2>
                <span className="info-tag">Priority study areas</span>
              </div>
              <p className="revise-intro">Concepts where recent answers missed key architectural evidence or claims:</p>
              <div className="chips-list">
                {profile.revise_first.map((item, idx) => (
                  <span key={idx} className="concept-chip revise-chip">
                    {item}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Concepts Section */}
          <div className="concepts-section" role="region" aria-label="Concept Mastery">
            <h2 className="card-heading">Concept Breakdown (Weakest first)</h2>
            <div className="concepts-grid">
              {sortedConcepts.map((item) => {
                const scorePercent = Math.min(100, Math.max(0, (item.score / 4) * 100));
                const statusClass =
                  item.status === 'mastered'
                    ? 'status-mastered'
                    : item.status === 'learning'
                    ? 'status-learning'
                    : 'status-unrated';

                return (
                  <div key={item.concept} className="concept-card">
                    <div className="concept-card-header">
                      <h3 className="concept-card-title">{item.concept}</h3>
                      <span className={`status-pill ${statusClass}`}>{item.status}</span>
                    </div>

                    {/* Score Bar */}
                    <div className="score-bar-container">
                      <div className="score-bar-header">
                        <span className="score-label">Score</span>
                        <span className="score-number">{item.score.toFixed(1)} / 4</span>
                      </div>
                      <div
                        className="score-bar-track"
                        role="progressbar"
                        aria-valuenow={item.score}
                        aria-valuemin={0}
                        aria-valuemax={4}
                        aria-label={`${item.concept} score: ${item.score.toFixed(1)} of 4`}
                      >
                        <div
                          className="score-bar-fill"
                          style={{ width: `${scorePercent}%` }}
                        />
                      </div>
                    </div>

                    {/* Metadata */}
                    <div className="concept-card-meta">
                      <span>{item.attempts} attempt{item.attempts === 1 ? '' : 's'}</span>
                      <span>•</span>
                      <span>Last seen: {item.last_seen ? new Date(item.last_seen).toLocaleDateString() : 'Recent'}</span>
                    </div>

                    {/* Top 3 Misses */}
                    {item.misses && item.misses.length > 0 && (
                      <div className="concept-misses">
                        <h4 className="misses-heading">Top misses:</h4>
                        <ul className="misses-items">
                          {item.misses.slice(0, 3).map((m, mIdx) => (
                            <li key={mIdx}>
                              <span className="miss-point">{m.point}</span>
                              <span className="miss-count">({m.count}x)</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Sessions History */}
          <div className="history-section" role="region" aria-label="Session History">
            <h2 className="card-heading">Interview History</h2>
            {sessions.length === 0 ? (
              <p className="no-sessions-msg">No completed sessions.</p>
            ) : (
              <div className="sessions-list">
                {sessions.map((s) => (
                  <div key={s.session_id} className="session-item-row">
                    <div className="session-item-info">
                      <span className={`status-pill ${s.status === 'finished' ? 'ready' : 'ingesting'}`}>
                        {s.status}
                      </span>
                      <span className="session-date">
                        {s.created_at ? new Date(s.created_at).toLocaleDateString() : 'Recent'}
                      </span>
                      <span className="session-answered">
                        {s.answered_count ?? s.answered} questions
                      </span>
                      {s.average_score !== null && s.average_score !== undefined && (
                        <span className="session-avg-score">
                          Score: {s.average_score.toFixed(1)} / 4
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      className="action-btn secondary small"
                      onClick={() => handleViewReport(s.session_id)}
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

      {/* Modal for report */}
      {viewingReportId && viewingReportData && (
        <div className="modal-backdrop" onClick={() => setViewingReportId(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <div className="modal-header">
              <h2 className="modal-title">Session Report</h2>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setViewingReportId(null)}
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
                        <td>{row.score !== null && row.score !== undefined ? `${row.score}/4` : 'Skipped'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
