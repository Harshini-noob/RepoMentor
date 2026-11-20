import React from 'react';
import { Link, Outlet } from 'react-router-dom';

interface LayoutProps {
  children?: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  return (
    <div>
      <header className="top-bar">
        <Link to="/" className="brand-link" aria-label="RepoMentor home">
          <div className="brand-icon-wrapper" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z"/>
              <path d="M6 6h10"/>
              <path d="M6 10h10"/>
            </svg>
          </div>
          <span>RepoMentor</span>
          <span className="brand-badge">Studio</span>
        </Link>
        <nav className="nav-links" aria-label="Main navigation">
          <Link to="/" className="nav-link" aria-label="View all projects">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect width="7" height="7" x="3" y="3" rx="1" />
              <rect width="7" height="7" x="14" y="3" rx="1" />
              <rect width="7" height="7" x="14" y="14" rx="1" />
              <rect width="7" height="7" x="3" y="14" rx="1" />
            </svg>
            Projects
          </Link>
        </nav>
      </header>
      <main className="main-container">
        {children || <Outlet />}
      </main>
    </div>
  );
};
