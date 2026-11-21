import React from 'react';
import { NavLink } from 'react-router-dom';

interface ProjectTabsProps {
  projectId: string;
}

export const ProjectTabs: React.FC<ProjectTabsProps> = ({ projectId }) => {
  const encId = encodeURIComponent(projectId);

  return (
    <div className="project-tabs" role="tablist" aria-label="Project Views">
      <NavLink
        to={`/projects/${encId}/dossier`}
        className={({ isActive }) => `project-tab-btn ${isActive ? 'active' : ''}`}
        role="tab"
        aria-selected="false"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
          <polyline points="14 2 14 8 20 8" />
        </svg>
        Dossier
      </NavLink>

      <NavLink
        to={`/projects/${encId}/chat`}
        className={({ isActive }) => `project-tab-btn ${isActive ? 'active' : ''}`}
        role="tab"
        aria-selected="false"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
        Chat
      </NavLink>

      <NavLink
        to={`/projects/${encId}/interview`}
        className={({ isActive }) => `project-tab-btn ${isActive ? 'active' : ''}`}
        role="tab"
        aria-selected="false"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="10" />
          <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
        Interview
      </NavLink>

      <NavLink
        to={`/projects/${encId}/progress`}
        className={({ isActive }) => `project-tab-btn ${isActive ? 'active' : ''}`}
        role="tab"
        aria-selected="false"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
        Progress
      </NavLink>
    </div>
  );
};
