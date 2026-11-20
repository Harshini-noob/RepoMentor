import { ClaimTag, DossierClaim } from '../types';

export type EvidenceType = 'file' | 'commit' | 'dep' | 'fact' | 'repo' | 'unknown';

export interface ParsedEvidence {
  type: EvidenceType;
  label: string;
}

export function parseEvidenceId(id: string): ParsedEvidence {
  if (!id || typeof id !== 'string') {
    return { type: 'unknown', label: String(id || '') };
  }

  const trimmed = id.trim();

  // file:path#L31-109 or file:path#L31 or file:path
  if (trimmed.startsWith('file:')) {
    const rest = trimmed.slice(5);
    const hashIdx = rest.indexOf('#');
    if (hashIdx === -1) {
      return { type: 'file', label: rest };
    }
    const path = rest.slice(0, hashIdx);
    const rangePart = rest.slice(hashIdx + 1).replace(/^L/i, '').replace(/-L/i, '-');
    return { type: 'file', label: `${path}:${rangePart}` };
  }

  // commit:sha or commit:sha:path
  if (trimmed.startsWith('commit:')) {
    const parts = trimmed.slice(7).split(':');
    const sha = parts[0];
    const path = parts.slice(1).join(':');
    if (path) {
      return { type: 'commit', label: `${sha} ${path}` };
    }
    return { type: 'commit', label: sha };
  }

  // dep:name
  if (trimmed.startsWith('dep:')) {
    const name = trimmed.slice(4);
    return { type: 'dep', label: name };
  }

  // fact:kind:name -> "name (kind)"
  if (trimmed.startsWith('fact:')) {
    const parts = trimmed.slice(5).split(':');
    if (parts.length >= 2) {
      const kind = parts[0];
      const name = parts.slice(1).join(':');
      return { type: 'fact', label: `${name} (${kind})` };
    }
    return { type: 'fact', label: trimmed.slice(5) };
  }

  // repo:owner/name
  if (trimmed.startsWith('repo:')) {
    return { type: 'repo', label: trimmed.slice(5) };
  }

  return { type: 'unknown', label: trimmed };
}

export interface TagCounts {
  all: number;
  fact: number;
  inferred: number;
  uncertain: number;
}

export function countClaimsByTag(claims: DossierClaim[]): TagCounts {
  const counts: TagCounts = { all: claims.length, fact: 0, inferred: 0, uncertain: 0 };
  for (const c of claims) {
    const tag = (c.tag || '').toLowerCase() as ClaimTag;
    if (tag === 'fact' || tag === 'inferred' || tag === 'uncertain') {
      counts[tag]++;
    }
  }
  return counts;
}

export function filterClaims(
  claims: DossierClaim[],
  selectedTags: Set<ClaimTag>,
  searchQuery: string
): DossierClaim[] {
  const q = searchQuery.trim().toLowerCase();
  return claims.filter((claim) => {
    if (!selectedTags.has(claim.tag)) {
      return false;
    }
    if (q && !claim.claim.toLowerCase().includes(q)) {
      return false;
    }
    return true;
  });
}
