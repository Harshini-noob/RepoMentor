import React, { useEffect, useState, useRef } from "react";
import { Link } from "react-router-dom";
import { listProjects, createProject, getProject } from "../api";
import type { ProjectSummary, ProjectDetail } from "../types";

export const Dashboard: React.FC = () => {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [repoUrl, setRepoUrl] = useState<string>("");
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Polling tracker for newly created projects
  const pollingIntervals = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());

  const fetchProjects = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await listProjects();
      setProjects(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load projects. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProjects();

    const currentIntervals = pollingIntervals.current;
    return () => {
      currentIntervals.forEach((intervalId) => clearInterval(intervalId));
      currentIntervals.clear();
    };
  }, []);

  const startPolling = (projectId: string) => {
    if (pollingIntervals.current.has(projectId)) return;

    const intervalId = setInterval(async () => {
      try {
        const detail: ProjectDetail = await getProject(projectId);
        if (detail.status === "ready" || detail.status === "failed") {
          clearInterval(intervalId);
          pollingIntervals.current.delete(projectId);
          const updated = await listProjects();
          setProjects(updated);
        }
      } catch {
        clearInterval(intervalId);
        pollingIntervals.current.delete(projectId);
      }
    }, 3000);

    pollingIntervals.current.set(projectId, intervalId);
  };

  const handleAddRepo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!repoUrl.trim()) return;

    setSubmitting(true);
    setSubmitError(null);

    try {
      const res = await createProject(repoUrl.trim());
      setRepoUrl("");
      const updated = await listProjects();
      setProjects(updated);

      if (res.status !== "ready" && res.status !== "failed") {
        startPolling(res.project_id);
      }
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : "Failed to add repository. Please check the URL and try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="dashboard-container">
      {/* Add Repository Panel */}
      <div className="add-repo-panel">
        <h2 className="add-repo-title">Add a Repository</h2>
        <p className="add-repo-subtitle">
          Connect a public GitHub repository to index code, parse commits and dependencies, and generate an interactive defense dossier.
        </p>

        <form onSubmit={handleAddRepo} className="add-repo-form-row">
          <input
            type="text"
            className="add-repo-text-input"
            placeholder="https://github.com/owner/repository"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            disabled={submitting}
            aria-label="GitHub Repository URL"
            required
          />
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            {submitting ? "Analyzing..." : "Add Repository"}
          </button>
        </form>

        {submitError && (
          <div className="banner banner-error" style={{ marginTop: "1rem" }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            {submitError}
          </div>
        )}
      </div>

      {/* Projects Section Header */}
      <div className="projects-section-header">
        <h2 className="projects-section-title">Repositories</h2>
        <button
          className="btn btn-secondary"
          onClick={fetchProjects}
          disabled={loading}
          aria-label="Refresh repository list"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
            <path d="M3 3v5h5" />
            <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
            <path d="M16 21h5v-5" />
          </svg>
          Refresh
        </button>
      </div>

      {loading && (
        <div className="state-box">
          Loading repositories...
        </div>
      )}

      {error && !loading && (
        <div className="banner banner-error">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          {error}
        </div>
      )}

      {!loading && !error && projects.length === 0 && (
        <div className="state-box">
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ margin: "0 auto 0.75rem", color: "var(--text-tertiary)" }}>
            <rect width="20" height="14" x="2" y="3" rx="2" />
            <line x1="8" y1="21" x2="16" y2="21" />
            <line x1="12" y1="17" x2="12" y2="21" />
          </svg>
          <div style={{ fontWeight: 600, color: "var(--text-primary)", marginBottom: "0.25rem" }}>
            No repositories analyzed yet
          </div>
          <div style={{ fontSize: "0.8125rem", color: "var(--text-secondary)" }}>
            Add a public GitHub repository URL above to get started.
          </div>
        </div>
      )}

      {!loading && !error && projects.length > 0 && (
        <div className="projects-cards-grid">
          {projects.map((p) => {
            const statusClass =
              p.status === "ready"
                ? "status-ready"
                : p.status === "failed"
                ? "status-failed"
                : "status-ingesting";

            const analyzedDate = p.analysed_at
              ? new Date(p.analysed_at).toLocaleDateString(undefined, {
                  year: "numeric",
                  month: "short",
                  day: "numeric",
                })
              : "Not yet analysed";

            const dossierLabel =
              p.dossier_version !== undefined && p.dossier_version !== null
                ? `v${p.dossier_version}`
                : "No dossier";

            return (
              <div key={p.project_id} className="project-summary-card">
                <div className="project-summary-top">
                  <Link
                    to={`/projects/${p.project_id}/dossier`}
                    className="project-title-link"
                    title={`View dossier for ${p.repo_full_name}`}
                  >
                    {p.repo_full_name}
                  </Link>
                  <span className={`status-chip ${statusClass}`}>
                    {p.status}
                  </span>
                </div>

                <div className="project-stats-pills">
                  <span className="project-stat-item">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
                    </svg>
                    Dossier {dossierLabel}
                  </span>
                  <span>•</span>
                  <span className="project-stat-item">
                    {p.counts ? `${p.counts.files} files` : "0 files"}
                  </span>
                  <span>•</span>
                  <span className="project-stat-item">
                    {p.counts ? `${p.counts.commits} commits` : "0 commits"}
                  </span>
                </div>

                <div className="project-card-footer">
                  Analyzed: {analyzedDate}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
