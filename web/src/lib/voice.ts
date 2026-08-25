/**
 * Dictation.
 *
 * Walking somewhere is exactly when you have a thought and least want to
 * type. The Web Speech API runs on the device, so this adds no server round
 * trip and works the same whether or not Cortex is reachable — the transcript
 * lands in the textarea and follows the normal offline-first capture path.
 *
 * Support is uneven, so callers check `voiceSupported()` and simply do not
 * show the button where it is missing.
 */

type SpeechRecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  onresult: ((event: any) => void) | null;
  onerror: ((event: any) => void) | null;
  onend: (() => void) | null;
};

function constructor(): (new () => SpeechRecognitionLike) | null {
  const scope = window as unknown as Record<string, unknown>;
  return (scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null) as
    | (new () => SpeechRecognitionLike)
    | null;
}

export function voiceSupported(): boolean {
  return typeof window !== "undefined" && constructor() !== null;
}

export interface Dictation {
  stop: () => void;
}

/**
 * Start dictating.
 *
 * `onFinal` fires with each settled phrase, `onInterim` with the provisional
 * text so the person can see it is listening. Both are cleared by the caller.
 */
export function dictate(handlers: {
  onFinal: (text: string) => void;
  onInterim?: (text: string) => void;
  onError?: (message: string) => void;
  onEnd?: () => void;
}): Dictation | null {
  const Recognition = constructor();
  if (!Recognition) return null;

  const recognition = new Recognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = navigator.language || "en-US";

  recognition.onresult = (event: any) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      const text = result[0]?.transcript ?? "";
      if (result.isFinal) handlers.onFinal(text);
      else interim += text;
    }
    handlers.onInterim?.(interim);
  };

  recognition.onerror = (event: any) => {
    // "no-speech" and "aborted" are normal ways for a session to end, not
    // failures worth putting in front of someone.
    const code = event?.error ?? "unknown";
    if (code === "no-speech" || code === "aborted") return;
    handlers.onError?.(
      code === "not-allowed"
        ? "Microphone access was refused."
        : `Dictation stopped: ${code}`,
    );
  };

  recognition.onend = () => handlers.onEnd?.();

  try {
    recognition.start();
  } catch {
    return null;
  }

  return { stop: () => recognition.stop() };
}
