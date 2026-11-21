import React from 'react';
import { TagBadge } from '../components/TagBadge';
import { parseEvidenceId, EvidenceType } from './evidence';

interface RenderAnswerProps {
  text: string;
  onOpenEvidence?: (evidenceId: string) => void;
  className?: string;
}

function getEvidenceIcon(type: EvidenceType) {
  switch (type) {
    case 'file':
      return (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
          <polyline points="14 2 14 8 20 8" />
        </svg>
      );
    case 'commit':
      return (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="4" />
          <line x1="1.05" y1="12" x2="7" y2="12" />
          <line x1="17.01" y1="12" x2="22.96" y2="12" />
        </svg>
      );
    case 'dep':
      return (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="m7.5 4.27 9 5.15" />
          <path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" />
          <path d="m3.3 7 8.7 5 8.7-5" />
          <path d="M12 22V12" />
        </svg>
      );
    case 'fact':
      return (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <path d="m9 12 2 2 4-4" />
        </svg>
      );
    case 'repo':
      return (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4" />
          <path d="M9 18c-4.51 2-5-2-7-2" />
        </svg>
      );
    default:
      return null;
  }
}

/**
 * Tokenize and render inline formatted segments: bold, inline code, and recognized evidence chips.
 */
function renderInlineContent(
  text: string,
  onOpenEvidence?: (evidenceId: string) => void,
  keyPrefix = 'inline'
): React.ReactNode[] {
  // Regex matches:
  // 1. Evidence chip: `\[([^\]]+)\]`
  // 2. Bold text: `\*\*([^*]+)\*\*`
  // 3. Inline code: `` `([^`]+)` ``
  const tokenRegex = /(\[([^\]]+)\]|\*\*([^*]+)\*\*|`([^`]+)`)/g;

  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let tokenIdx = 0;

  while ((match = tokenRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const bracketContent = match[2];
    const boldContent = match[3];
    const codeContent = match[4];

    if (bracketContent !== undefined) {
      const parsed = parseEvidenceId(bracketContent);
      if (parsed.type !== 'unknown') {
        const evId = bracketContent.trim();
        nodes.push(
          <button
            key={`${keyPrefix}-ev-${tokenIdx++}`}
            type="button"
            className="evidence-chip inline-evidence-chip"
            onClick={() => onOpenEvidence?.(evId)}
            title={`View evidence: ${evId}`}
            aria-label={`Evidence: ${parsed.label}`}
          >
            {getEvidenceIcon(parsed.type)}
            <span>{parsed.label}</span>
          </button>
        );
      } else {
        nodes.push(`[${bracketContent}]`);
      }
    } else if (boldContent !== undefined) {
      nodes.push(
        <strong key={`${keyPrefix}-bold-${tokenIdx++}`}>
          {boldContent}
        </strong>
      );
    } else if (codeContent !== undefined) {
      nodes.push(
        <code key={`${keyPrefix}-code-${tokenIdx++}`} className="inline-code">
          {codeContent}
        </code>
      );
    }

    lastIndex = tokenRegex.lastIndex;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
}

/**
 * Renders answer text line-by-line / paragraph-by-paragraph:
 * - Detects leading [Fact], [Inferred], [Uncertain], [General]
 * - Converts recognized bracketed tokens to clickable evidence chips
 * - Formats bold **text** and inline `code`
 * - Leaves unknown bracketed text as plain text
 * - Completely avoids dangerouslySetInnerHTML
 */
export function renderAnswerNodes(
  text: string,
  onOpenEvidence?: (evidenceId: string) => void
): React.ReactNode {
  if (!text) return null;

  const lines = text.split('\n');

  return (
    <div className="answer-content">
      {lines.map((line, lineIdx) => {
        const trimmed = line.trim();
        if (!trimmed) {
          return <div key={`line-${lineIdx}`} className="answer-spacer" aria-hidden="true" />;
        }

        // Check for leading bullet or tag:
        // Group 1: bullet prefix (e.g. "* " or "- ")
        // Group 2: tag name (Fact, Inferred, Uncertain, General)
        // Group 3: rest of line
        const leadingTagMatch = line.match(/^(\s*(?:[-*]\s+)?)\[(Fact|Inferred|Uncertain|General)\]\s*(.*)$/i);

        if (leadingTagMatch) {
          const bullet = leadingTagMatch[1]?.trim();
          const tag = leadingTagMatch[2];
          const rest = leadingTagMatch[3];

          return (
            <div key={`line-${lineIdx}`} className="answer-line with-tag">
              {bullet && <span className="answer-bullet" aria-hidden="true">•</span>}
              <TagBadge tag={tag} />
              <span className="answer-text-body">
                {renderInlineContent(rest, onOpenEvidence, `line-${lineIdx}`)}
              </span>
            </div>
          );
        }

        // Check for normal bullet without tag
        const normalBulletMatch = line.match(/^(\s*[-*]\s+)(.*)$/);
        if (normalBulletMatch) {
          const rest = normalBulletMatch[2];
          return (
            <div key={`line-${lineIdx}`} className="answer-line with-bullet">
              <span className="answer-bullet" aria-hidden="true">•</span>
              <span className="answer-text-body">
                {renderInlineContent(rest, onOpenEvidence, `line-${lineIdx}`)}
              </span>
            </div>
          );
        }

        return (
          <div key={`line-${lineIdx}`} className="answer-line">
            <span className="answer-text-body">
              {renderInlineContent(line, onOpenEvidence, `line-${lineIdx}`)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export const AnswerText: React.FC<RenderAnswerProps> = ({
  text,
  onOpenEvidence,
  className = '',
}) => {
  return (
    <div className={`answer-text-root ${className}`.trim()}>
      {renderAnswerNodes(text, onOpenEvidence)}
    </div>
  );
};
