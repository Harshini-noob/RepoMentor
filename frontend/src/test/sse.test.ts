import { describe, it, expect, vi } from 'vitest';
import { streamChat } from '../lib/sse';
import type { ChatStreamEvent } from '../types';

describe('SSE streamChat parser', () => {
  it('parses events split across chunk boundaries', async () => {
    const chunk1 = 'data: {"type": "session", "session_id": "sess-1"}\n\ndata: {"type": "eve';
    const chunk2 = 'nt", "text": "thinking..."}\n\ndata: {"type": "done"}\n\n';

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(chunk1));
        controller.enqueue(encoder.encode(chunk2));
        controller.close();
      },
    });

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: stream,
    } as any);

    const received: ChatStreamEvent[] = [];
    await streamChat('proj_test', { message: 'hello' }, (evt) => {
      received.push(evt);
    });

    expect(received).toEqual([
      { type: 'session', session_id: 'sess-1' },
      { type: 'event', text: 'thinking...' },
      { type: 'done' },
    ]);
  });

  it('ignores keepalive comments and empty lines', async () => {
    const payload =
      ': keepalive\n\n' +
      'data: {"type": "step", "step": 1, "tool": "get_dossier", "args": {}, "preview": "ok", "evidence": [], "ms": 100, "tokens": 50}\n\n' +
      ': another comment\n\n' +
      'data: {"type": "done"}\n\n';

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(payload));
        controller.close();
      },
    });

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: stream,
    } as any);

    const received: ChatStreamEvent[] = [];
    await streamChat('proj_test', { message: 'hello' }, (evt) => {
      received.push(evt);
    });

    expect(received).toHaveLength(2);
    expect(received[0].type).toBe('step');
    expect(received[1].type).toBe('done');
  });

  it('handles abort cleanly without uncaught errors', async () => {
    const controller = new AbortController();

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(encoder.encode('data: {"type": "session", "session_id": "sess-abort"}\n\n'));
      },
      cancel() {
        // called when reader.cancel() is executed on abort
      },
    });

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: stream,
    } as any);

    const received: ChatStreamEvent[] = [];
    const chatPromise = streamChat(
      'proj_test',
      { message: 'hello' },
      (evt) => {
        received.push(evt);
        controller.abort();
      },
      controller.signal
    );

    await expect(chatPromise).resolves.toBeUndefined();
    expect(received).toHaveLength(1);
    expect(received[0]).toEqual({ type: 'session', session_id: 'sess-abort' });
  });
});
