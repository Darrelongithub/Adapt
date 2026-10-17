export type VoiceIntent =
  | { type: "answer"; value: string; confidence: "high" | "low" }
  | { type: "next" | "back" | "repeat" | "slower" | "faster" | "stop" | "skip" }
  | { type: "explain" }
  | { type: "go"; page: string }
  | { type: "message"; text: string };

const clean = (value: string) => value.toLowerCase().trim().replace(/[.!?,]/g, "").replace(/\s+/g, " ");
const ordinals: Record<string, string> = { first: "A", one: "A", "1": "A", second: "B", two: "B", "2": "B", third: "C", three: "C", "3": "C", fourth: "D", four: "D", "4": "D" };

export function parseVoiceIntent(input: string): VoiceIntent {
  const text = clean(input);
  if (!text) return { type: "message", text: "" };
  if (/^(next|continue|go on|forward)$/.test(text)) return { type: "next" };
  if (/^(back|go back|previous)$/.test(text)) return { type: "back" };
  if (/^(repeat|say that again|again)$/.test(text)) return { type: "repeat" };
  if (/^(slower|slow down)$/.test(text)) return { type: "slower" };
  if (/^(faster|speed up)$/.test(text)) return { type: "faster" };
  if (/^(stop|stop talking|be quiet|mute)$/.test(text)) return { type: "stop" };
  if (/^(skip|skip this)$/.test(text)) return { type: "skip" };
  if (/^(i don't get it|i do not get it|explain again|explain that)$/.test(text)) return { type: "explain" };
  const go = text.match(/^go to (.+)$/);
  if (go) return { type: "go", page: go[1] };
  if (ordinals[text]) return { type: "answer", value: ordinals[text], confidence: "high" };
  const letter = text.match(/^(?:answer )?([a-d])$/);
  if (letter) return { type: "answer", value: letter[1].toUpperCase(), confidence: "high" };
  const maybe = text.match(/^(?:the )?(first|one|second|two|third|three|fourth|four)$/);
  if (maybe) return { type: "answer", value: ordinals[maybe[1]], confidence: "low" };
  return { type: "message", text: input.trim() };
}
