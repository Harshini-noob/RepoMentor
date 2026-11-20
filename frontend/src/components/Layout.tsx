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
          <span>RepoMentor</span>
          <span className="brand-badge">Alpha</span>
        </Link>
        <nav className="nav-links" aria-label="Main navigation">
          <Link to="/" aria-label="View all projects">
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
