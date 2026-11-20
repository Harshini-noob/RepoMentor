import { describe, it, expect } from "vitest";
import { parseEvidenceId } from "../lib/evidence";

describe("parseEvidenceId", () => {
  it("parses file evidence with line range", () => {
    const res = parseEvidenceId("file:main.py#L31-109");
    expect(res.type).toBe("file");
    expect(res.label).toBe("main.py:31-109");
  });

  it("parses file evidence with single line", () => {
    const res = parseEvidenceId("file:src/app.py#L42");
    expect(res.type).toBe("file");
    expect(res.label).toBe("src/app.py:42");
  });

  it("parses file evidence without line numbers", () => {
    const res = parseEvidenceId("file:README.md");
    expect(res.type).toBe("file");
    expect(res.label).toBe("README.md");
  });

  it("parses commit evidence without path", () => {
    const res = parseEvidenceId("commit:02a5c7b");
    expect(res.type).toBe("commit");
    expect(res.label).toBe("02a5c7b");
  });

  it("parses commit evidence with path", () => {
    const res = parseEvidenceId("commit:02a5c7b:logger.py");
    expect(res.type).toBe("commit");
    expect(res.label).toBe("02a5c7b logger.py");
  });

  it("parses dependency evidence", () => {
    const res = parseEvidenceId("dep:groq");
    expect(res.type).toBe("dep");
    expect(res.label).toBe("groq");
  });

  it("parses fact evidence", () => {
    const res = parseEvidenceId("fact:database:supabase");
    expect(res.type).toBe("fact");
    expect(res.label).toBe("supabase (database)");
  });

  it("parses repo evidence", () => {
    const res = parseEvidenceId("repo:facebook/react");
    expect(res.type).toBe("repo");
    expect(res.label).toBe("facebook/react");
  });

  it("handles invalid or unknown evidence IDs gracefully", () => {
    const res1 = parseEvidenceId("invalid-format-id");
    expect(res1.type).toBe("unknown");
    expect(res1.label).toBe("invalid-format-id");

    const res2 = parseEvidenceId("");
    expect(res2.type).toBe("unknown");
    expect(res2.label).toBe("");

    const res3 = parseEvidenceId("random:xyz:123");
    expect(res3.type).toBe("unknown");
    expect(res3.label).toBe("random:xyz:123");
  });
});
