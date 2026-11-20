import React, { useEffect, useRef, useState } from 'react';
import { getEvidence } from '../api';
import { EvidenceResult } from '../types';

interface EvidenceDrawerProps {
  isOpen?: boolean;
  projectId: string;
  evidenceId: string | null;
  onClose: () => void;
  triggerRef?: React.RefObject<HTMLElement | null>;
  triggerElement?: HTMLElement | null;
}

export const EvidenceDrawer: React.FC<EvidenceDrawerProps> = ({
  isOpen = true,
  projectId,
  evidenceId,
  onClose,
  triggerRef,
  triggerElement,
}) => {
  const [data, setData] = useState<EvidenceResult | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!isOpen || !evidenceId) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }

    let isMounted = true;
    setLoading(true);
    setError(null);

    getEvidence(projectId, evidenceId)
      ?.then((res) => {
        if (isMounted) {
          setData(res);
          setLoading(false);
        }
      })
      ?.catch(() => {
        if (isMounted) {
          setError('Could not load this evidence');
          setLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [isOpen, projectId, evidenceId]);

  // Focus close button on open
  useEffect(() => {
    if (isOpen && evidenceId) {
      setTimeout(() => {
        closeButtonRef.current?.focus();
      }, 0);
    }
  }, [isOpen, evidenceId]);

  // Handle escape key
  useEffect(() => {
    if (!isOpen || !evidenceId) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, evidenceId, triggerElement, triggerRef]);

  const handleClose = () => {
    onClose();
    const elem = triggerRef?.current || triggerElement;
    if (elem) {
      setTimeout(() => {
        elem.focus();
      }, 0);
    }
  };

  if (!isOpen || !evidenceId) {
    return null;
  }

  return (
    <div className="drawer-overlay" onClick={handleClose}>
      <div
        className="drawer-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="evidence-drawer-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-header">
          <h2 id="evidence-drawer-title" className="drawer-title">
            {evidenceId}
          </h2>
          <button
            ref={closeButtonRef}
            type="button"
            className="drawer-close-btn"
            onClick={handleClose}
            aria-label="Close evidence details"
          >
            &times;
          </button>
        </div>

        <div className="drawer-body">
          {loading && <div className="state-box">Loading evidence...</div>}

          {error && <div className="state-error">{error}</div>}

          {!loading && !error && data && (
            <>
              {/* FILE EVIDENCE */}
              {data.kind === 'file' && (
                <div>
                  <h3 style={{ marginTop: 0, marginBottom: '0.75rem' }}>File Evidence</h3>
                  {data.available === false ? (
                    <div className="state-box">
                      Content not available: {data.reason || 'content was not stored'}
                    </div>
                  ) : (
                    <div>
                      <div className="meta-grid">
                        <div className="meta-row">
                          <span className="meta-label">Path:</span>
                          <span>{data.path}</span>
                        </div>
                        <div className="meta-row">
                          <span className="meta-label">Range:</span>
                          <span>Lines {data.start} - {data.end}</span>
                        </div>
                      </div>
                      <div className="code-block" data-testid="code-block">
                        {data.lines.map((line) => (
                          <div
                            key={line.n}
                            className={`code-line ${line.in_range ? 'in-range' : ''}`}
                            data-testid={line.in_range ? 'highlighted-line' : undefined}
                          >
                            <span className="code-line-num">{line.n}</span>
                            <span className="code-line-text">{line.text}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* COMMIT EVIDENCE */}
              {data.kind === 'commit' && (
                <div>
                  <h3 style={{ marginTop: 0, marginBottom: '0.75rem' }}>Commit Evidence</h3>
                  <div className="meta-grid">
                    <div className="meta-row">
                      <span className="meta-label">Commit SHA:</span>
                      <span style={{ fontFamily: 'var(--font-mono)' }}>{data.sha}</span>
                    </div>
                    {data.author && (
                      <div className="meta-row">
                        <span className="meta-label">Author:</span>
                        <span>{data.author}</span>
                      </div>
                    )}
                    {data.date && (
                      <div className="meta-row">
                        <span className="meta-label">Date:</span>
                        <span>{new Date(data.date).toLocaleString()}</span>
                      </div>
                    )}
                    <div className="meta-row">
                      <span className="meta-label">Changes:</span>
                      <span>
                        <strong style={{ color: 'var(--tag-fact-text)' }}>+{data.additions}</strong> /{' '}
                        <strong style={{ color: 'var(--status-failed-text)' }}>-{data.deletions}</strong>
                      </span>
                    </div>
                    {data.path && (
                      <div className="meta-row">
                        <span className="meta-label">Target File:</span>
                        <span>
                          {data.path} {data.path_in_commit ? '(in commit)' : '(not modified)'}
                        </span>
                      </div>
                    )}
                  </div>

                  <div style={{ marginBottom: '1rem' }}>
                    <div className="meta-label" style={{ marginBottom: '0.25rem' }}>Message:</div>
                    <div style={{ whiteSpace: 'pre-wrap', fontFamily: 'var(--font-mono)', fontSize: '0.8125rem' }}>
                      {data.message}
                    </div>
                  </div>

                  <div>
                    <div className="meta-label" style={{ marginBottom: '0.375rem' }}>Changed Files:</div>
                    <ul style={{ paddingLeft: '1.25rem', fontSize: '0.8125rem' }}>
                      {data.files_changed.map((f, idx) => (
                        <li
                          key={idx}
                          style={{
                            fontWeight: data.path && f === data.path ? '700' : 'normal',
                            color: data.path && f === data.path ? 'var(--accent-color)' : 'inherit',
                          }}
                        >
                          {f} {data.path && f === data.path && ' (selected)'}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}

              {/* DEPENDENCY EVIDENCE */}
              {data.kind === 'dependency' && (
                <div>
                  <h3 style={{ marginTop: 0, marginBottom: '0.75rem' }}>Dependency Evidence</h3>
                  <table className="evidence-table">
                    <thead>
                      <tr>
                        <th>Package</th>
                        <th>Version Spec</th>
                        <th>Kind</th>
                        <th>Source File</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((item, idx) => (
                        <tr key={idx}>
                          <td><strong>{item.name}</strong></td>
                          <td>{item.version_spec || 'any'}</td>
                          <td>{item.dep_kind}</td>
                          <td>{item.source_file || 'unknown'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* FACT EVIDENCE */}
              {data.kind === 'fact' && (
                <div>
                  <h3 style={{ marginTop: 0, marginBottom: '0.75rem' }}>Fact Evidence</h3>
                  <table className="evidence-table">
                    <thead>
                      <tr>
                        <th>File Path</th>
                        <th>Lines</th>
                        <th>In Test</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((item, idx) => (
                        <tr key={idx}>
                          <td>{item.file_path}</td>
                          <td>{item.line_start} - {item.line_end}</td>
                          <td>{item.in_test ? 'Yes' : 'No'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* REPO EVIDENCE */}
              {data.kind === 'repo' && (
                <div>
                  <h3 style={{ marginTop: 0, marginBottom: '0.75rem' }}>Repository Evidence</h3>
                  <div className="meta-grid">
                    <div className="meta-row">
                      <span className="meta-label">Repository:</span>
                      <span>{data.repo}</span>
                    </div>
                    {data.default_branch && (
                      <div className="meta-row">
                        <span className="meta-label">Default Branch:</span>
                        <span>{data.default_branch}</span>
                      </div>
                    )}
                    <div className="meta-row">
                      <span className="meta-label">Total Commits:</span>
                      <span>{data.commits}</span>
                    </div>
                    <div className="meta-row">
                      <span className="meta-label">Total Files:</span>
                      <span>{data.files}</span>
                    </div>
                  </div>

                  <div>
                    <div className="meta-label" style={{ marginBottom: '0.375rem' }}>Top Languages:</div>
                    <ul style={{ paddingLeft: '1.25rem', fontSize: '0.8125rem' }}>
                      {Object.entries(data.languages).map(([lang, count]) => (
                        <li key={lang}>
                          {lang}: {count} files
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
