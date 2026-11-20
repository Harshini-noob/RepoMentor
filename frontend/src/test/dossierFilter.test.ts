import { describe, it, expect } from "vitest";
import { countClaimsByTag, filterClaims } from "../lib/evidence";
import type { DossierClaim, ClaimTag } from "../types";

describe("dossier claim helpers", () => {
  const sampleClaims: DossierClaim[] = [
    {
      id: "claim-1",
      section: "apis",
      tag: "fact",
      claim: "FastAPI handles requests via /query endpoint",
      evidence: ["file:main.py#L31-109"],
      source: "static",
    },
    {
      id: "claim-2",
      section: "apis",
      tag: "inferred",
      claim: "Authentication uses Bearer token in header",
      evidence: ["file:auth.py#L10-25"],
      source: "agent",
    },
    {
      id: "claim-3",
      section: "apis",
      tag: "uncertain",
      claim: "Database connections might not be pooled",
      evidence: [],
      source: "user",
    },
    {
      id: "claim-4",
      section: "apis",
      tag: "fact",
      claim: "CORS middleware is registered in main.py",
      evidence: ["file:main.py#L12-20"],
      source: "static",
    },
  ];

  describe("countClaimsByTag", () => {
    it("correctly counts claims by their tag", () => {
      const counts = countClaimsByTag(sampleClaims);
      expect(counts).toEqual({
        all: 4,
        fact: 2,
        inferred: 1,
        uncertain: 1,
      });
    });

    it("returns zeros for empty claims list", () => {
      const counts = countClaimsByTag([]);
      expect(counts).toEqual({
        all: 0,
        fact: 0,
        inferred: 0,
        uncertain: 0,
      });
    });
  });

  describe("filterClaims", () => {
    it("filters claims based on selected tags", () => {
      const tags: Set<ClaimTag> = new Set(["fact"]);
      const filtered = filterClaims(sampleClaims, tags, "");
      expect(filtered).toHaveLength(2);
      expect(filtered.map((c) => c.id)).toEqual(["claim-1", "claim-4"]);
    });

    it("filters claims with search query case-insensitively", () => {
      const tags: Set<ClaimTag> = new Set(["fact", "inferred", "uncertain"]);
      const filtered = filterClaims(sampleClaims, tags, "FASTAPI");
      expect(filtered).toHaveLength(1);
      expect(filtered[0].id).toBe("claim-1");
    });

    it("combines tag filtering and search query", () => {
      const tags: Set<ClaimTag> = new Set(["inferred"]);
      const filtered = filterClaims(sampleClaims, tags, "token");
      expect(filtered).toHaveLength(1);
      expect(filtered[0].id).toBe("claim-2");

      const noMatch = filterClaims(sampleClaims, tags, "FastAPI");
      expect(noMatch).toHaveLength(0);
    });

    it("returns empty list if no tags are selected", () => {
      const tags: Set<ClaimTag> = new Set();
      const filtered = filterClaims(sampleClaims, tags, "");
      expect(filtered).toHaveLength(0);
    });
  });
});
