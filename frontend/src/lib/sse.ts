import { API_BASE_URL } from '../config';
import { ApiError, ChatStreamEvent } from '../types';

export const CHAT_TIMEOUT_MS = 150000;

export interface StreamChatBody {
  message: string;
  session_id?: string | null;
}

export async function streamChat(
  projectId: string,
  body: StreamChatBody,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS);

  if (signal) {
    if (signal.aborted) {
      controller.abort();
      clearTimeout(timer);
      return;
    }
    signal.addEventListener('abort', () => controller.abort(), { once: true });
  }

  try {
    const url = `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/chat`;
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!response.ok) {
      let msg = 'Failed to send chat message.';
      if (response.status === 409) {
        msg = 'Another request is using the model. Try again in a moment.';
        try {
          const errData = await response.json();
          if (errData && typeof errData.detail === 'string') {
            if (errData.detail.toLowerCase().includes('busy')) {
              msg = 'Another request is using the model. Try again in a moment.';
            } else {
              msg = errData.detail;
            }
          }
        } catch {
          // ignore parse error
        }
      } else {
        try {
          const errData = await response.json();
          if (errData && typeof errData.detail === 'string') {
            msg = errData.detail;
          }
        } catch {
          // ignore parse error
        }
      }
      throw new ApiError(msg, response.status);
    }

    if (!response.body) {
      throw new ApiError('Response body is null.');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    const abortHandler = () => {
      try {
        reader.cancel();
      } catch {
        // ignore
      }
    };
    controller.signal.addEventListener('abort', abortHandler, { once: true });

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';

      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line || line.startsWith(':')) {
          // Ignore empty lines and comment lines (e.g. : keepalive)
          continue;
        }

        if (line.startsWith('data:')) {
          const jsonStr = line.slice(5).trim();
          try {
            const event = JSON.parse(jsonStr) as ChatStreamEvent;
            onEvent(event);
            if (event.type === 'done') {
              return;
            }
          } catch {
            // ignore JSON parse error in stream chunk
          }
        }
      }
    }

    // Flush remaining buffer if any
    if (buffer.trim()) {
      const line = buffer.trim();
      if (line.startsWith('data:')) {
        const jsonStr = line.slice(5).trim();
        try {
          const event = JSON.parse(jsonStr) as ChatStreamEvent;
          onEvent(event);
        } catch {
          // ignore
        }
      }
    }
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // Aborted cleanly by caller or timeout
      return;
    }
    if (err instanceof ApiError) {
      throw err;
    }
    throw new ApiError(
      err instanceof Error ? err.message : 'An error occurred while streaming chat.'
    );
  } finally {
    clearTimeout(timer);
  }
}
