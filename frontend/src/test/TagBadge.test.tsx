import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TagBadge } from "../components/TagBadge";

describe("TagBadge", () => {
  it("renders Fact tag with correct label and class", () => {
    const { container } = render(<TagBadge tag="fact" />);
    expect(screen.getByText("Fact")).toBeInTheDocument();
    expect(container.firstChild).toHaveClass("tag-badge", "fact");
  });

  it("renders Inferred tag with correct label and class", () => {
    const { container } = render(<TagBadge tag="inferred" />);
    expect(screen.getByText("Inferred")).toBeInTheDocument();
    expect(container.firstChild).toHaveClass("tag-badge", "inferred");
  });

  it("renders Uncertain tag with correct label and class", () => {
    const { container } = render(<TagBadge tag="uncertain" />);
    expect(screen.getByText("Uncertain")).toBeInTheDocument();
    expect(container.firstChild).toHaveClass("tag-badge", "uncertain");
  });
});
