import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { AnswerText } from '../lib/renderAnswer';

describe('renderAnswer & AnswerText', () => {
  it('turns leading [Fact], [Inferred], [Uncertain], [General] into badges', () => {
    const text =
      '[Fact] Main entry point in main.py\n' +
      '[Inferred] Likely uses Redis\n' +
      '[Uncertain] Unclear DB schema\n' +
      '[General] Architectural overview';

    const { container } = render(<AnswerText text={text} />);

    expect(screen.getByText('Fact')).toBeInTheDocument();
    expect(screen.getByText('Inferred')).toBeInTheDocument();
    expect(screen.getByText('Uncertain')).toBeInTheDocument();
    expect(screen.getByText('General')).toBeInTheDocument();

    const generalBadge = container.querySelector('.tag-badge.general');
    expect(generalBadge).toBeInTheDocument();
  });

  it('turns valid evidence ID into a clickable chip and leaves unknown bracket as text', () => {
    const onOpenEvidence = vi.fn();
    const text =
      'Implemented in [file:main.py#L31-109] with [some random note] and [dep:fastapi].';

    render(<AnswerText text={text} onOpenEvidence={onOpenEvidence} />);

    // Recognized chips
    const fileChip = screen.getByTitle('View evidence: file:main.py#L31-109');
    expect(fileChip).toBeInTheDocument();
    expect(fileChip).toHaveTextContent('main.py:31-109');

    const depChip = screen.getByTitle('View evidence: dep:fastapi');
    expect(depChip).toBeInTheDocument();
    expect(depChip).toHaveTextContent('fastapi');

    // Unknown bracket stays plain text
    expect(screen.getByText(/\[some random note\]/)).toBeInTheDocument();

    // Clicking chip invokes callback
    fireEvent.click(fileChip);
    expect(onOpenEvidence).toHaveBeenCalledWith('file:main.py#L31-109');
  });

  it('formats bold and inline code correctly without dangerouslySetInnerHTML', () => {
    const text = 'Here is **important** information with `export const PORT = 8000;`.';
    const { container } = render(<AnswerText text={text} />);

    const strongEl = container.querySelector('strong');
    expect(strongEl).toHaveTextContent('important');

    const codeEl = container.querySelector('code');
    expect(codeEl).toHaveTextContent('export const PORT = 8000;');

    // Verify dangerouslySetInnerHTML was never used
    const allHtml = container.innerHTML;
    expect(allHtml).not.toContain('dangerouslySetInnerHTML');
  });

  it('safely escapes HTML characters and does not execute scripts', () => {
    const text = 'Safe text <script>alert("xss")</script> and <img src="x" onerror="alert(1)"/>';
    const { container } = render(<AnswerText text={text} />);

    // Script tag is not created in DOM as an executable script element
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/<script>alert\("xss"\)<\/script>/)).toBeInTheDocument();
  });
});
