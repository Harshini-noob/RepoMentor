import React, { useEffect, useState, useRef, useMemo } from "react";
import { useParams, Link } from "react-router-dom";
import { getProject, getDossierSummary, getDossierSection, buildStaticDossier } from "../api";
import { parseEvidenceId, filterClaims, countClaimsByTag } from "../lib/evidence";
import { TagBadge } from "../components/TagBadge";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import type { ProjectDetail, DossierSectionSummary, DossierClaim, ClaimTag } from "../types";

export const DossierPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const projectId = id || "";

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [dossierVersion, setDossierVersion] = useState<number | null>(null);
  const [sections, setSections] = useState<DossierSectionSummary[]>([]);
  const [selectedSection, setSelectedSection] = useState<string>("");
  const [claims, setClaims] = useState<DossierClaim[]>([]);

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
    if (!projectId) return;

    let mounted = true;
    setLoadingSummary(true);
    setError(null);

    Promise.all([
      getProject(projectId).catch(() => null),
      getDossierSummary(projectId).catch(() => null),
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
  }, [projectId]);

  // Load section claims when selectedSection changes
  useEffect(() => {
    if (!projectId || !selectedSection) return;

    let mounted = true;
    setLoadingClaims(true);
    setError(null);

    getDossierSection(projectId, selectedSection)
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
  }, [projectId, selectedSection]);

  const handleBuildStaticDossier = async () => {
    if (!projectId) return;
    setBuildingDossier(true);
    setError(null);
    setActionMessage(null);

    try {
      const res = await buildStaticDossier(projectId);
      setDossierVersion(res.version);
      setActionMessage(`Static dossier rebuilt successfully (v${res.version}).`);

      // Refresh summary
      const summary = await getDossierSummary(projectId);
      setSections(summary.sections || []);

      // Refresh current section claims
      if (selectedSection) {
        const secRes = await getDossierSection(projectId, selectedSection);
        setClaims(secRes.claims || []);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to rebuild static dossier.");
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
    <div className="container">
      {/* Breadcrumb / Header */}
      <div style={{ marginBottom: "1rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.875rem", color: "var(--color-text-secondary)", marginBottom: "0.5rem" }}>
          <Link to="/" style={{ color: "var(--color-primary)" }}>Projects</Link>
          <span>/</span>
          <span>{project ? project.repo_full_name : projectId}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "1rem" }}>
          <h1 style={{ margin: 0 }}>
            {project ? project.repo_full_name : "Repository Dossier"}
            {dossierVersion !== null && (
              <span style={{ fontSize: "1rem", fontWeight: 400, marginLeft: "0.75rem", color: "var(--color-text-secondary)" }}>
                v{dossierVersion}
              </span>
            )}
          </h1>
          <button
            className="button button-primary"
            onClick={handleBuildStaticDossier}
            disabled={buildingDossier || loadingSummary}
          >
            {buildingDossier ? "Building..." : "Build static dossier"}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="tabs" role="tablist" aria-label="Project Views">
        <button
          role="tab"
          aria-selected="true"
          className="tab active"
        >
          Dossier
        </button>
        <button
          role="tab"
          aria-selected="false"
          className="tab"
          disabled
          title="Coming next"
        >
          Chat <span style={{ fontSize: "0.75rem", opacity: 0.6 }}>(coming next)</span>
        </button>
        <button
          role="tab"
          aria-selected="false"
          className="tab"
          disabled
          title="Coming next"
        >
          Interview <span style={{ fontSize: "0.75rem", opacity: 0.6 }}>(coming next)</span>
        </button>
        <button
          role="tab"
          aria-selected="false"
          className="tab"
          disabled
          title="Coming next"
        >
          Progress <span style={{ fontSize: "0.75rem", opacity: 0.6 }}>(coming next)</span>
        </button>
      </div>

      {actionMessage && (
        <div className="banner banner-success" style={{ marginBottom: "1rem" }}>
          {actionMessage}
        </div>
      )}

      {error && (
        <div className="banner banner-error" style={{ marginBottom: "1rem" }}>
          {error}
        </div>
      )}

      {loadingSummary ? (
        <div style={{ textAlign: "center", padding: "3rem", color: "var(--color-text-secondary)" }}>
          Loading dossier...
        </div>
      ) : sections.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: "3rem 1rem" }}>
          <h3>No dossier found</h3>
          <p style={{ color: "var(--color-text-secondary)" }}>
            This repository has not generated a dossier yet. Click &quot;Build static dossier&quot; to build one from the indexed data.
          </p>
        </div>
      ) : (
        <div className="dossier-layout">
          {/* Sidebar */}
          <nav className="card sidebar" aria-label="Dossier Sections">
            <h3 style={{ marginTop: 0, marginBottom: "0.75rem", fontSize: "1rem" }}>Sections</h3>
            <ul className="sidebar-list">
              {sections.map((sec) => {
                const isSelected = sec.name === selectedSection;
                return (
                  <li key={sec.name}>
                    <button
                      className={`sidebar-item ${isSelected ? "active" : ""}`}
                      onClick={() => setSelectedSection(sec.name)}
                      aria-current={isSelected ? "page" : undefined}
                    >
                      <span style={{ textTransform: "capitalize" }}>{sec.name}</span>
                      <span className="sidebar-count">{sec.claims}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>

          {/* Main Area */}
          <main className="dossier-main">
            {/* Filter controls */}
            <div className="card" style={{ marginBottom: "1rem", padding: "0.75rem 1rem" }}>
              <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: "0.75rem" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "1rem", flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.875rem", fontWeight: 600 }}>Filter:</span>
                  <label style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem", fontSize: "0.875rem", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={selectedTags.has("fact")}
                      onChange={() => handleTagToggle("fact")}
                    />
                    <TagBadge tag="fact" />
                    <span style={{ color: "var(--color-text-secondary)", fontSize: "0.75rem" }}>({tagCounts.fact})</span>
                  </label>

                  <label style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem", fontSize: "0.875rem", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={selectedTags.has("inferred")}
                      onChange={() => handleTagToggle("inferred")}
                    />
                    <TagBadge tag="inferred" />
                    <span style={{ color: "var(--color-text-secondary)", fontSize: "0.75rem" }}>({tagCounts.inferred})</span>
                  </label>

                  <label style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem", fontSize: "0.875rem", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={selectedTags.has("uncertain")}
                      onChange={() => handleTagToggle("uncertain")}
                    />
                    <TagBadge tag="uncertain" />
                    <span style={{ color: "var(--color-text-secondary)", fontSize: "0.75rem" }}>({tagCounts.uncertain})</span>
                  </label>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flex: "1 1 200px", maxWidth: "300px" }}>
                  <input
                    type="search"
                    className="input"
                    placeholder="Search section claims..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    style={{ fontSize: "0.875rem", padding: "0.35rem 0.6rem" }}
                    aria-label="Search section claims"
                  />
                </div>
              </div>

              <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginTop: "0.5rem" }}>
                Showing {filteredClaims.length} of {claims.length} claims in &quot;{selectedSection}&quot;
              </div>
            </div>

            {/* Claims list */}
            {loadingClaims ? (
              <div style={{ textAlign: "center", padding: "2rem", color: "var(--color-text-secondary)" }}>
                Loading section claims...
              </div>
            ) : filteredClaims.length === 0 ? (
              <div className="card" style={{ textAlign: "center", padding: "2rem", color: "var(--color-text-secondary)" }}>
                {claims.length === 0
                  ? "No claims in this section."
                  : "No claims match the active filters or search query."}
              </div>
            ) : (
              <div className="claims-list">
                {filteredClaims.map((claim, idx) => {
                  const claimKey = claim.id || `claim-${idx}`;
                  const commitSha = claim.commit || claim.commit_sha;
                  const evidenceList = claim.evidence || [];
                  const isExpanded = !!expandedEvidence[claimKey];
                  const visibleEvidence = isExpanded ? evidenceList : evidenceList.slice(0, 3);
                  const remainingCount = evidenceList.length - 3;

                  return (
                    <article key={claimKey} className="card claim-card">
                      <div className="claim-header">
                        <TagBadge tag={claim.tag} />

                        {claim.source === "user" && (
                          <span className="source-marker source-user" title="Claim provided by user">
                            you
                          </span>
                        )}
                        {claim.source === "agent" && (
                          <span className="source-marker source-agent" title="Claim generated by agent">
                            agent
                          </span>
                        )}

                        {commitSha && (
                          <span className="commit-chip" title={`Associated with commit ${commitSha}`}>
                            {commitSha.slice(0, 7)}
                          </span>
                        )}
                      </div>

                      <div className="claim-text">{claim.claim}</div>

                      {evidenceList.length > 0 && (
                        <div className="evidence-chips">
                          {visibleEvidence.map((evId) => {
                            const parsed = parseEvidenceId(evId);
                            return (
                              <button
                                key={evId}
                                className="evidence-chip"
                                onClick={(e) => openEvidence(evId, e)}
                                title={`Inspect ${evId}`}
                                aria-haspopup="dialog"
                              >
                                {parsed.label}
                              </button>
                            );
                          })}

                          {!isExpanded && remainingCount > 0 && (
                            <button
                              className="more-expander"
                              onClick={() => toggleEvidenceExpand(claimKey)}
                              aria-label={`Show ${remainingCount} more evidence items`}
                            >
                              +{remainingCount} more
                            </button>
                          )}

                          {isExpanded && remainingCount > 0 && (
                            <button
                              className="more-expander"
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
        projectId={projectId}
        onClose={closeEvidence}
        triggerRef={triggerRef}
      />
    </div>
  );
};
