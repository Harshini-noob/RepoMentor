import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "../pages/Dashboard";
import * as api from "../api";

vi.mock("../api", () => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  getProject: vi.fn(),
}));

describe("Dashboard project links", () => {
  const mockListProjects = vi.mocked(api.listProjects);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("builds project links using canonical project_id, never Mongo _id or raw repo name", async () => {
    mockListProjects.mockResolvedValueOnce([
      {
        id: "6ad9f6664514b8aa25183178",
        project_id: "proj_harshini_noob_llm_cost_copilot",
        repo_full_name: "Harshini-noob/llm-cost-copilot",
        status: "ready",
        dossier_version: 1,
        counts: {
          files: 28,
          commits: 15,
          dependencies: 12,
          facts: 20,
        },
      },
    ]);

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    // Wait for project card link to render
    await waitFor(() => {
      expect(screen.getByText("Harshini-noob/llm-cost-copilot")).toBeInTheDocument();
    });

    const link = screen.getByRole("link", { name: /Harshini-noob\/llm-cost-copilot/i });

    // Must use project_id in URL path
    expect(link).toHaveAttribute(
      "href",
      "/projects/proj_harshini_noob_llm_cost_copilot/dossier"
    );

    // Must NEVER use the mongo _id
    expect(link.getAttribute("href")).not.toContain("6ad9f6664514b8aa25183178");
  });
});
