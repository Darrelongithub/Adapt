"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { post } from "@/lib/api";
import { parseVoiceIntent, type VoiceIntent } from "./grammar";

export type VoiceListenOptions = { lang?: string; timeoutMs?: number };
export type VoiceApi = {
  enabled: boolean;
  setEnabled: (enabled: boolean) => void;
  speaking: boolean;
  listening: boolean;
  speak: (text: string, options?: { interrupt?: boolean }) => Promise<void>;
  listenOnce: (options?: VoiceListenOptions) => Promise<string>;
  say: (text: string, options?: { interrupt?: boolean }) => Promise<void>;
  onCommand: (handler: (text: string, intent: VoiceIntent) => void) => () => void;
  captions: string;
};

const VoiceContext = createContext<VoiceApi | null>(null);
const STORAGE = "adapt:voice-enabled";

type Recognition = {
  lang: string; continuous: boolean; interimResults: boolean;
  start: () => void; stop: () => void; abort: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null; onerror: (() => void) | null;
};
type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const value = (window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor });
  return value.SpeechRecognition ?? value.webkitSpeechRecognition ?? null;
}

export function VoiceProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabledState] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [listening, setListening] = useState(false);
  const [captions, setCaptions] = useState("");
  const handlers = useRef(new Set<(text: string, intent: VoiceIntent) => void>());
  const recognition = useRef<Recognition | null>(null);

  useEffect(() => { setEnabledState(window.localStorage.getItem(STORAGE) === "1"); }, []);
  useEffect(() => () => { recognition.current?.abort(); window.speechSynthesis?.cancel(); }, []);

  const setEnabled = useCallback((value: boolean) => {
    setEnabledState(value);
    if (typeof window !== "undefined") window.localStorage.setItem(STORAGE, value ? "1" : "0");
    if (!value) { recognition.current?.abort(); recognition.current = null; window.speechSynthesis?.cancel(); setListening(false); setSpeaking(false); }
    // Persistence is best-effort: text mode remains usable if a guest session has just expired.
    void post("me/voice", { enabled: value }).catch(() => undefined);
  }, []);

  const speak = useCallback((text: string, options: { interrupt?: boolean } = {}) => new Promise<void>((resolve) => {
    const cleanText = text.trim();
    if (!enabled || !cleanText || typeof window === "undefined" || !window.speechSynthesis) { resolve(); return; }
    if (options.interrupt !== false) window.speechSynthesis.cancel();
    setCaptions(cleanText);
    const utterance = new SpeechSynthesisUtterance(cleanText);
    utterance.onstart = () => setSpeaking(true);
    utterance.onend = () => { setSpeaking(false); resolve(); };
    utterance.onerror = () => { setSpeaking(false); resolve(); };
    window.speechSynthesis.speak(utterance);
  }), [enabled]);

  const listenOnce = useCallback((options: VoiceListenOptions = {}) => new Promise<string>((resolve) => {
    if (!enabled) { resolve(""); return; }
    const Ctor = recognitionCtor();
    if (!Ctor) { resolve(""); return; }
    recognition.current?.abort();
    const instance = new Ctor();
    recognition.current = instance;
    instance.lang = options.lang ?? "en-US";
    instance.continuous = false;
    instance.interimResults = false;
    let settled = false;
    const finish = (text: string) => { if (settled) return; settled = true; setListening(false); recognition.current = null; if (text) { setCaptions(text); const intent = parseVoiceIntent(text); handlers.current.forEach((handler) => handler(text, intent)); } resolve(text); };
    instance.onresult = (event) => finish(String(event.results[0]?.[0]?.transcript ?? ""));
    instance.onend = () => finish("");
    instance.onerror = () => finish("");
    setListening(true);
    try { instance.start(); } catch { finish(""); return; }
    const timeout = window.setTimeout(() => { try { instance.stop(); } catch { /* already ended */ } finish(""); }, options.timeoutMs ?? 10000);
    void timeout;
  }), [enabled]);

  const onCommand = useCallback((handler: (text: string, intent: VoiceIntent) => void) => { handlers.current.add(handler); return () => handlers.current.delete(handler); }, []);
  const value = useMemo<VoiceApi>(() => ({ enabled, setEnabled, speaking, listening, speak, listenOnce, say: speak, onCommand, captions }), [enabled, setEnabled, speaking, listening, speak, listenOnce, onCommand, captions]);
  return <VoiceContext.Provider value={value}>{children}</VoiceContext.Provider>;
}

export function useVoice(): VoiceApi {
  const value = useContext(VoiceContext);
  if (!value) throw new Error("useVoice must be used inside VoiceProvider");
  return value;
}

/** Optional integration hook for screens that can also render without the shell. */
export function useOptionalVoice(): VoiceApi | null { return useContext(VoiceContext); }
