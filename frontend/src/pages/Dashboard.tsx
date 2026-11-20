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
          // Refresh project list to reflect completed status
          const updated = await listProjects();
          setProjects(updated);
        }
      } catch {
        // Stop polling on recurring error
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
      // Add or update projects list
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
    <div className="container">
      <div className="card" style={{ marginBottom: "2rem" }}>
        <h2 style={{ marginTop: 0, marginBottom: "0.5rem" }}>Add a repository</h2>
        <p style={{ color: "var(--color-text-secondary)", marginTop: 0, marginBottom: "1rem" }}>
          Provide a public GitHub repository URL to analyze and generate a dossier.
        </p>

        <form onSubmit={handleAddRepo} style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <input
            type="text"
            className="input"
            placeholder="https://github.com/owner/repository"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            disabled={submitting}
            style={{ flex: "1 1 300px" }}
            aria-label="GitHub Repository URL"
            required
          />
          <button type="submit" className="button button-primary" disabled={submitting}>
            {submitting ? "Adding..." : "Add Repository"}
          </button>
        </form>

        {submitError && (
          <div className="banner banner-error" style={{ marginTop: "1rem" }}>
            {submitError}
          </div>
        )}
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h2 style={{ margin: 0 }}>Repositories</h2>
        <button
          className="button"
          onClick={fetchProjects}
          disabled={loading}
          aria-label="Refresh repository list"
        >
          Refresh
        </button>
      </div>

      {loading && (
        <div style={{ textAlign: "center", padding: "3rem", color: "var(--color-text-secondary)" }}>
          Loading projects...
        </div>
      )}

      {error && !loading && (
        <div className="banner banner-error" style={{ marginBottom: "1rem" }}>
          {error}
        </div>
      )}

      {!loading && !error && projects.length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: "3rem 1rem" }}>
          <p style={{ color: "var(--color-text-secondary)", margin: 0 }}>
            No repositories analyzed yet. Add one above to get started.
          </p>
        </div>
      )}

      {!loading && !error && projects.length > 0 && (
        <div className="project-grid">
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
                ? `Dossier v${p.dossier_version}`
                : "No dossier";

            return (
              <div key={p.id} className="card project-card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.5rem" }}>
                  <Link
                    to={`/projects/${p.id}/dossier`}
                    className="project-name"
                    title={`View dossier for ${p.repo_full_name}`}
                  >
                    {p.repo_full_name}
                  </Link>
                  <span className={`status-chip ${statusClass}`}>{p.status}</span>
                </div>

                <div className="project-meta">
                  <span>{dossierLabel}</span>
                  <span>•</span>
                  <span>{p.counts ? `${p.counts.files} files` : "0 files"}</span>
                  <span>•</span>
                  <span>{p.counts ? `${p.counts.commits} commits` : "0 commits"}</span>
                </div>

                <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginTop: "0.5rem" }}>
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
