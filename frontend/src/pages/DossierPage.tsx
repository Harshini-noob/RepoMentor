import React, { useEffect, useState, useRef, useMemo } from "react";
import { useParams, Link } from "react-router-dom";
import { getProject, getDossierSummary, getDossierSection, buildStaticDossier } from "../api";
import { parseEvidenceId, filterClaims, countClaimsByTag } from "../lib/evidence";
import { TagBadge } from "../components/TagBadge";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { ProjectTabs } from "../components/ProjectTabs";
import type { ProjectDetail, DossierSectionSummary, DossierClaim, ClaimTag } from "../types";

export function formatSectionName(name: string): string {
  const lower = (name || "").toLowerCase().trim();
  if (lower === "apis" || lower === "api") return "APIs";
  if (lower === "interview_map") return "Interview map";
  return lower.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export const DossierPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const initialIdent = id || "";

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [dossierVersion, setDossierVersion] = useState<number | null>(null);
  const [sections, setSections] = useState<DossierSectionSummary[]>([]);
  const [selectedSection, setSelectedSection] = useState<string>("");
  const [claims, setClaims] = useState<DossierClaim[]>([]);

  // Canonical project ID (resolved from project document if available)
  const canonicalId = project?.project_id || initialIdent;

  // UI state
  const [loadingSummary, setLoadingSummary] = useState<boolean>(true);
  const [loadingClaims, setLoadingClaims] = useState<boolean>(false);
  const [buildingDossier, setBuildingDossier] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  // Filters
  const [selectedTags, setSelectedTags] = useState<Set<ClaimTag>>(
    new Set(["fact", "inferred", "uncertain"])
  );
  const [searchQuery, setSearchQuery] = useState<string>("");

  // Expanded evidence tracking per claim id
  const [expandedEvidence, setExpandedEvidence] = useState<Record<string, boolean>>({});

  // Evidence Drawer state
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  // Load project & dossier summary
  useEffect(() => {
    if (!initialIdent) return;

    let mounted = true;
    setLoadingSummary(true);
    setError(null);

    Promise.all([
      getProject(initialIdent).catch(() => null),
      getDossierSummary(initialIdent).catch(() => null),
    ])
      .then(([projData, summaryData]) => {
        if (!mounted) return;
        if (projData) setProject(projData);

        if (summaryData) {
          setDossierVersion(summaryData.version ?? null);
          setSections(summaryData.sections || []);
          if (summaryData.sections && summaryData.sections.length > 0) {
            setSelectedSection(summaryData.sections[0].name);
          }
        }
      })
      .catch((err: unknown) => {
        if (mounted) {
          setError(err instanceof Error ? err.message : "Failed to load dossier data.");
        }
      })
      .finally(() => {
        if (mounted) setLoadingSummary(false);
      });

    return () => {
      mounted = false;
    };
  }, [initialIdent]);

  // Load section claims when selectedSection or canonicalId changes
  useEffect(() => {
    if (!canonicalId || !selectedSection) return;

    let mounted = true;
    setLoadingClaims(true);
    setError(null);

    getDossierSection(canonicalId, selectedSection)
      .then((res) => {
        if (!mounted) return;
        setClaims(res.claims || []);
      })
      .catch((err: unknown) => {
        if (mounted) {
          setError(err instanceof Error ? err.message : `Failed to load section ${selectedSection}`);
          setClaims([]);
        }
      })
      .finally(() => {
        if (mounted) setLoadingClaims(false);
      });

    return () => {
      mounted = false;
    };
  }, [canonicalId, selectedSection]);

  const handleBuildStaticDossier = async (force: boolean = false) => {
    if (!canonicalId) return;

    // Check if dossier contains agent or user claims
    const totalUserClaims = sections.reduce((sum, s) => sum + (s.user_edited || 0), 0);
    const totalAgentClaims = sections.reduce((sum, s) => sum + (s.agent || 0), 0);
    const totalClaimsCount = sections.reduce((sum, s) => sum + (s.claims || 0), 0);

    if (!force && (totalUserClaims > 0 || totalAgentClaims > 0)) {
      const parts: string[] = [];
      if (totalUserClaims > 0) parts.push(`${totalUserClaims} user claim${totalUserClaims === 1 ? "" : "s"}`);
      if (totalAgentClaims > 0) parts.push(`${totalAgentClaims} agent claim${totalAgentClaims === 1 ? "" : "s"}`);
      const confirmed = window.confirm(
        `This dossier currently contains ${totalClaimsCount} claims (${parts.join(" and ")}). Rebuilding will refresh static claims while preserving your non-static claims. Do you want to proceed?`
      );
      if (!confirmed) return;
    }

    setBuildingDossier(true);
    setError(null);
    setActionMessage(null);

    try {
      const res = await buildStaticDossier(canonicalId, force);
      setDossierVersion(res.version);

      // Format: "Built vN: <total> claims (technologies X, apis Y, ...)"
      const sectionEntries = Object.entries(res.sections || {});
      const totalBuiltClaims = sectionEntries.reduce((sum, [_, cnt]) => sum + (typeof cnt === "number" ? cnt : 0), 0);
      const breakdown = sectionEntries
        .filter(([_, cnt]) => typeof cnt === "number" && cnt > 0)
        .map(([sec, cnt]) => `${sec} ${cnt}`)
        .join(", ");
      const msg = `Built v${res.version}: ${totalBuiltClaims} claims${breakdown ? ` (${breakdown})` : ""}`;
      setActionMessage(msg);

      // Refresh summary
      const summary = await getDossierSummary(canonicalId);
      setSections(summary.sections || []);

      // Refresh current section claims
      if (selectedSection) {
        const secRes = await getDossierSection(canonicalId, selectedSection);
        setClaims(secRes.claims || []);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to rebuild static dossier.";
      setError(msg);
    } finally {
      setBuildingDossier(false);
    }
  };

  const handleTagToggle = (tag: ClaimTag) => {
    setSelectedTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) {
        next.delete(tag);
      } else {
        next.add(tag);
      }
      return next;
    });
  };

  const toggleEvidenceExpand = (claimKey: string) => {
    setExpandedEvidence((prev) => ({
      ...prev,
      [claimKey]: !prev[claimKey],
    }));
  };

  const openEvidence = (evidenceId: string, event: React.MouseEvent<HTMLElement>) => {
    triggerRef.current = event.currentTarget;
    setSelectedEvidenceId(evidenceId);
  };

  const closeEvidence = () => {
    setSelectedEvidenceId(null);
  };

  // Filtered claims and tag counts
  const filteredClaims = useMemo(() => {
    return filterClaims(claims, selectedTags, searchQuery);
  }, [claims, selectedTags, searchQuery]);

  const tagCounts = useMemo(() => {
    return countClaimsByTag(claims);
  }, [claims]);

  return (
    <div>
      {/* Breadcrumbs & Header Row */}
      <div className="project-header-section">
        <div>
          <nav className="breadcrumb-nav" aria-label="Breadcrumb">
            <Link to="/">Projects</Link>
            <span className="breadcrumb-separator">/</span>
            <span>{project ? project.repo_full_name : canonicalId}</span>
          </nav>
          <div className="project-title-row">
            <h1 className="project-title">
              {project ? project.repo_full_name : "Repository Dossier"}
            </h1>
            {dossierVersion !== null && (
              <span className="version-pill">
                v{dossierVersion}
              </span>
            )}
          </div>
        </div>

        <div>
          <button
            className="btn btn-primary"
            onClick={() => handleBuildStaticDossier(false)}
            disabled={buildingDossier || loadingSummary}
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ animation: buildingDossier ? "spin 1s linear infinite" : "none" }}
            >
              <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
              <path d="M3 3v5h5" />
              <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
              <path d="M16 21h5v-5" />
            </svg>
            {buildingDossier ? "Building Dossier..." : "Build static dossier"}
          </button>
        </div>
      </div>

      {/* Project Views Tabs */}
      <ProjectTabs projectId={canonicalId} />

      {actionMessage && (
        <div className="banner banner-success">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
          {actionMessage}
        </div>
      )}

      {error && (
        <div className="banner banner-error">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <span>{error}</span>
          {error.includes("new dossier looks incomplete") && (
            <button
              className="btn btn-secondary"
              style={{ marginLeft: "auto", fontSize: "0.75rem", padding: "0.25rem 0.5rem" }}
              onClick={() => handleBuildStaticDossier(true)}
              disabled={buildingDossier}
            >
              Force Rebuild
            </button>
          )}
        </div>
      )}

      {loadingSummary ? (
        <div className="state-box">
          Loading repository dossier...
        </div>
      ) : sections.length === 0 ? (
        <div className="state-box" style={{ padding: "4rem 2rem" }}>
          <h3 style={{ fontSize: "1.25rem", marginBottom: "0.5rem", color: "var(--text-primary)" }}>No dossier found</h3>
          <p style={{ maxWidth: "480px", margin: "0 auto 1.5rem" }}>
            This repository has not generated a dossier yet. Click &quot;Build static dossier&quot; above to index and extract verified facts, dependencies, and architecture insights.
          </p>
          <button className="btn btn-primary" onClick={() => handleBuildStaticDossier(false)} disabled={buildingDossier}>
            Build Static Dossier Now
          </button>
        </div>
      ) : (
        <div className="dossier-layout-grid">
          {/* Sidebar */}
          <nav className="sidebar-panel" aria-label="Dossier Sections">
            <div className="sidebar-title">Dossier Sections</div>
            <ul className="sidebar-menu">
              {sections.map((sec) => {
                const isSelected = sec.name === selectedSection;
                return (
                  <li key={sec.name}>
                    <button
                      className={`sidebar-item-btn ${isSelected ? "active" : ""}`}
                      onClick={() => setSelectedSection(sec.name)}
                      aria-current={isSelected ? "page" : undefined}
                    >
                      <span style={{ textTransform: "none" }}>{formatSectionName(sec.name)}</span>
                      <span className="sidebar-count-pill">{sec.claims}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>

          {/* Main Area */}
          <main className="dossier-content-panel">
            {/* Filter controls toolbar */}
            <div className="filter-toolbar">
              <div className="toolbar-controls">
                <div className="filter-tags-group">
                  <span className="filter-label-title">Filter:</span>
                  <label className="filter-chip-checkbox">
                    <input
                      type="checkbox"
                      checked={selectedTags.has("fact")}
                      onChange={() => handleTagToggle("fact")}
                    />
                    <TagBadge tag="fact" />
                    <span style={{ color: "var(--text-tertiary)", fontSize: "0.75rem", fontFamily: "var(--font-mono)" }}>
                      ({tagCounts.fact})
                    </span>
                  </label>

                  <label className="filter-chip-checkbox">
                    <input
                      type="checkbox"
                      checked={selectedTags.has("inferred")}
                      onChange={() => handleTagToggle("inferred")}
                    />
                    <TagBadge tag="inferred" />
                    <span style={{ color: "var(--text-tertiary)", fontSize: "0.75rem", fontFamily: "var(--font-mono)" }}>
                      ({tagCounts.inferred})
                    </span>
                  </label>

                  <label className="filter-chip-checkbox">
                    <input
                      type="checkbox"
                      checked={selectedTags.has("uncertain")}
                      onChange={() => handleTagToggle("uncertain")}
                    />
                    <TagBadge tag="uncertain" />
                    <span style={{ color: "var(--text-tertiary)", fontSize: "0.75rem", fontFamily: "var(--font-mono)" }}>
                      ({tagCounts.uncertain})
                    </span>
                  </label>
                </div>

                <div className="search-input-wrapper">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="11" cy="11" r="8" />
                    <line x1="21" y1="21" x2="16.65" y2="16.65" />
                  </svg>
                  <input
                    type="search"
                    className="styled-search-input"
                    placeholder="Search section claims..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    aria-label="Search section claims"
                  />
                </div>
              </div>

              <div className="toolbar-status-text">
                <span>Showing {filteredClaims.length} of {claims.length} claims in &quot;{formatSectionName(selectedSection)}&quot;</span>
              </div>
            </div>

            {/* Claims list */}
            {loadingClaims ? (
              <div className="state-box">
                Loading claims for &quot;{formatSectionName(selectedSection)}&quot;...
              </div>
            ) : filteredClaims.length === 0 ? (
              <div className="state-box">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ margin: "0 auto 0.75rem", color: "var(--text-tertiary)" }}>
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
                <div style={{ fontWeight: 600, color: "var(--text-primary)", marginBottom: "0.25rem" }}>
                  {claims.length === 0
                    ? `No claims recorded in "${formatSectionName(selectedSection)}" yet.`
                    : "No claims match the active filters or search query."}
                </div>
                <div style={{ fontSize: "0.8125rem", color: "var(--text-secondary)" }}>
                  {claims.length === 0
                    ? "Click 'Build static dossier' to analyze the repository facts."
                    : "Try selecting additional tags or clearing your search term."}
                </div>
              </div>
            ) : (
              <div className="claims-container">
                {filteredClaims.map((claim, idx) => {
                  const claimKey = claim.id || `claim-${idx}`;
                  const commitSha = claim.commit || claim.commit_sha;
                  const evidenceList = claim.evidence || [];
                  const isExpanded = !!expandedEvidence[claimKey];
                  const visibleEvidence = isExpanded ? evidenceList : evidenceList.slice(0, 3);
                  const remainingCount = evidenceList.length - 3;

                  return (
                    <article key={claimKey} className="claim-card">
                      <div className="claim-meta-header">
                        <TagBadge tag={claim.tag} />

                        {claim.source === "user" && (
                          <span className="source-badge user" title="Claim provided by user">
                            you
                          </span>
                        )}
                        {claim.source === "agent" && (
                          <span className="source-badge agent" title="Claim generated by agent">
                            agent
                          </span>
                        )}

                        {commitSha && (
                          <span className="commit-chip" title={`Associated with commit ${commitSha}`}>
                            {commitSha.slice(0, 7)}
                          </span>
                        )}
                      </div>

                      <div className="claim-body-text">{claim.claim}</div>

                      {evidenceList.length > 0 && (
                        <div className="evidence-chips-wrapper">
                          {visibleEvidence.map((evId) => {
                            const parsed = parseEvidenceId(evId);
                            return (
                              <button
                                key={evId}
                                className="evidence-chip-btn"
                                onClick={(e) => openEvidence(evId, e)}
                                title={`Inspect ${evId}`}
                                aria-haspopup="dialog"
                              >
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                                  <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                                </svg>
                                {parsed.label}
                              </button>
                            );
                          })}

                          {!isExpanded && remainingCount > 0 && (
                            <button
                              className="more-expander-btn"
                              onClick={() => toggleEvidenceExpand(claimKey)}
                              aria-label={`Show ${remainingCount} more evidence items`}
                            >
                              +{remainingCount} more
                            </button>
                          )}

                          {isExpanded && remainingCount > 0 && (
                            <button
                              className="more-expander-btn"
                              onClick={() => toggleEvidenceExpand(claimKey)}
                              aria-label="Show fewer evidence items"
                            >
                              show less
                            </button>
                          )}
                        </div>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </main>
        </div>
      )}

      {/* Evidence Drawer */}
      <EvidenceDrawer
        isOpen={selectedEvidenceId !== null}
        evidenceId={selectedEvidenceId}
        projectId={canonicalId}
        onClose={closeEvidence}
        triggerRef={triggerRef}
      />
    </div>
  );
};
