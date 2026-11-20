import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  listProjects,
  getProject,
  buildStaticDossier,
  getEvidence,
} from "../api";
import { ApiError } from "../types";

describe("API client error mapping", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("maps network rejection to generic network ApiError", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("Network failed"));

    await expect(listProjects()).rejects.toThrow(ApiError);
    await expect(listProjects()).rejects.toThrow(
      "Unable to connect to the server. Please ensure the backend is running."
    );
  });

  it("maps 404 response to generic not found message without leaking raw server text", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Internal database key project_123 not in collection" }),
    } as Response);

    await expect(getProject("invalid_id")).rejects.toThrow(
      "The requested resource could not be found."
    );
  });

  it("maps 500 response to generic server error message without leaking stack trace", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({
        error: "Traceback (most recent call last): File main.py, line 45 in handle_request",
      }),
    } as Response);

    await expect(buildStaticDossier("proj_1")).rejects.toThrow(
      "An error occurred while communicating with the server. Please try again."
    );
  });

  it("maps 400 response to generic invalid request message", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: "Syntax error near token %23" }),
    } as Response);

    await expect(getEvidence("proj_1", "bad_evidence")).rejects.toThrow(
      "Invalid request. Please check your input."
    );
  });

  it("maps 409 response to generic conflict message", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: "Project status is ingesting, not ready" }),
    } as Response);

    await expect(buildStaticDossier("proj_1")).rejects.toThrow(
      "The project is currently busy or not ready."
    );
  });

  it("returns parsed JSON data on successful response", async () => {
    const fakeData = [{ id: "p1", repo_full_name: "test/repo", status: "ready" }];
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => fakeData,
    } as Response);

    const result = await listProjects();
    expect(result).toEqual(fakeData);
  });
});
