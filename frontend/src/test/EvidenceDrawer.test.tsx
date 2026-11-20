import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import * as api from "../api";

vi.mock("../api", () => ({
  getEvidence: vi.fn(),
}));

describe("EvidenceDrawer", () => {
  const mockGetEvidence = vi.mocked(api.getEvidence);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not render when isOpen is false", () => {
    const { container } = render(
      <EvidenceDrawer
        isOpen={false}
        evidenceId="file:main.py#L3-4"
        projectId="proj_test"
        onClose={vi.fn()}
      />
    );
    expect(container.firstChild).toBeNull();
    expect(mockGetEvidence).not.toHaveBeenCalled();
  });

  it("renders line numbers and highlights lines in range for file evidence", async () => {
    mockGetEvidence.mockResolvedValueOnce({
      kind: "file",
      path: "main.py",
      language: "python",
      start: 3,
      end: 4,
      lines: [
        { n: 1, text: "import sys", in_range: false },
        { n: 2, text: "import os", in_range: false },
        { n: 3, text: "app = FastAPI()", in_range: true },
        { n: 4, text: "@app.get('/')", in_range: true },
        { n: 5, text: "def home():", in_range: false },
      ],
    });

    render(
      <EvidenceDrawer
        isOpen={true}
        evidenceId="file:main.py#L3-4"
        projectId="proj_test"
        onClose={vi.fn()}
      />
    );

    // Shows loading indicator initially
    expect(screen.getByText(/Loading evidence/i)).toBeInTheDocument();

    // Wait for file details to be rendered
    await waitFor(() => {
      expect(screen.getByText("File Evidence")).toBeInTheDocument();
    });

    expect(screen.getByText("main.py")).toBeInTheDocument();
    expect(screen.getByText("Lines 3 - 4")).toBeInTheDocument();

    // Verify lines text
    expect(screen.getByText("app = FastAPI()")).toBeInTheDocument();
    expect(screen.getByText("@app.get('/')")).toBeInTheDocument();

    // Verify line numbers
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();

    // Verify in-range highlighting
    const highlightedLines = screen.getAllByTestId("highlighted-line");
    expect(highlightedLines).toHaveLength(2);
    expect(highlightedLines[0]).toHaveClass("code-line", "in-range");
    expect(highlightedLines[0]).toHaveTextContent("app = FastAPI()");
  });

  it("triggers onClose when clicking close button or pressing Escape", async () => {
    mockGetEvidence.mockResolvedValueOnce({
      kind: "dependency",
      items: [{ name: "groq", version_spec: "^0.4.0", dep_kind: "standard", source_file: "pyproject.toml" }],
    });

    const handleClose = vi.fn();
    render(
      <EvidenceDrawer
        isOpen={true}
        evidenceId="dep:groq"
        projectId="proj_test"
        onClose={handleClose}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Dependency Evidence")).toBeInTheDocument();
    });

    const closeButton = screen.getByRole("button", { name: /Close evidence details/i });
    fireEvent.click(closeButton);
    expect(handleClose).toHaveBeenCalledTimes(1);

    // Test Escape key
    fireEvent.keyDown(window, { key: "Escape" });
    expect(handleClose).toHaveBeenCalledTimes(2);
  });
});
