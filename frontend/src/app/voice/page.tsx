"use client";

/** Voice, with its limitations on the screen rather than in a README.
 *
 * The transport is a websocket whose path the server hands over (`GET /voice/capabilities`
 * → `path`), so this page never hard-codes where the audio goes. Two things are measured
 * separately, exactly as the backend does: whether the words were heard correctly, and
 * whether the answer was any good. A mis-heard "over two" is a speech problem, not a
 * teaching problem, and the transcript box is always editable so the difference is fixable
 * by the learner rather than absorbed by the model.
 *
 * If the local engines are not installed the page says so and degrades deliberately:
 * browser dictation for input, browser speechSynthesis for output, no audio leaving the
 * machine either way.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Btn, Card, Check, Err, Field, humanize, Notice, Select, Tag, Textarea, useSession } from "@/components/ui";
import { post, useApi } from "@/lib/api";

type Caps = {
  stt?: { local_stt?: boolean; detail?: string; fallback?: string; model_size?: string };
  tts?: { local_tts?: boolean; detail?: string; fallback?: string; voices?: Record<string, string>; sample_rate?: number; interruption?: string };
  voices?: Record<string, string>;
  barge_in?: boolean;
  transport?: string;
  path?: string;
  note?: string;
};

type Frame =
  | { t: "hello"; sample_rate_in?: number; sample_rate_out?: number; encoding?: string; session_id?: string; status?: Record<string, unknown> }
  | { t: "state"; state: string }
  | { t: "reply"; text: string; ms?: number; meta?: Record<string, unknown> }
  | { t: "speak"; audio?: string; text?: string; seq?: number }
  | { t: "speak_unavailable"; reason?: string; text?: string }
  | { t: "config_ack"; voice_enabled?: boolean; gender?: string; barge_in?: boolean }
  | { t: "partial"; text?: string }
  | { t: "error"; detail?: string; message?: string }
  | Record<string, unknown>;

export default function VoicePage() {
  const { me } = useSession();
  const caps = useApi<Caps>("voice/capabilities");
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<string>("idle");
  const [turns, setTurns] = useState<{ you: string; tutor: string; ms?: number; interrupted?: boolean }[]>([]);
  const [draft, setDraft] = useState("");
  const [partial, setPartial] = useState("");
  const [voiceOn, setVoiceOn] = useState(true);
  const [gender, setGender] = useState("female");
  const [barge, setBarge] = useState(true);
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [mic, setMic] = useState<string | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const rec = useRef<{ stop: () => void } | null>(null);
  const rate = useRef(24000);

  const say = useCallback(async (text: string) => {
    if (!text) return;
    // Prefer the local server voice if it is installed; fall back to the browser's.
    try {
      const out = await post<{ available?: boolean; audio?: string | null; reason?: string; fallback?: string }>("voice/speak", { text, gender });
      if (out?.available && out.audio && ctx.current) {
        const bin = Uint8Array.from(atob(out.audio), (c) => c.charCodeAt(0));
        const pcm = new Int16Array(bin.buffer);
        const buf = ctx.current.createBuffer(1, pcm.length, rate.current);
        buf.copyToChannel(Float32Array.from(pcm, (v) => v / 32768) as unknown as Float32Array<ArrayBuffer>, 0);
        const src = ctx.current.createBufferSource();
        src.buffer = buf;
        src.connect(ctx.current.destination);
        src.start();
        return;
      }
    } catch {
      /* the browser voice is the point of the fallback, not an error */
    }
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setLog((l) => [...l, "no speech engine at all — the text answer is still on screen"]);
      return;
    }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    const voices = window.speechSynthesis.getVoices();
    const want = voices.find((v) => (gender === "female" ? /female|zira|samantha|serena|google uk english female/i.test(v.name) : /male|david|daniel|google uk english male/i.test(v.name)));
    if (want) u.voice = want;
    u.rate = 0.98;
    window.speechSynthesis.speak(u);
    if (voices.length === 0) setLog((l) => [...l, "browser has no speech voices installed; text only"]);
  }, [gender]);

  const stopTalking = useCallback(() => {
    if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
    setBarge(true);
  }, []);

  const close = useCallback(() => {
    ws.current?.close();
    ws.current = null;
    setOpen(false);
    setState("idle");
    rec.current?.stop();
    rec.current = null;
  }, []);

  useEffect(() => () => close(), [close]);

  async function start() {
    setError(null);
    if (!me) {
      setError("voice needs a learner who is signed in: the session it joins is your session, and an anonymous microphone has no place to be recorded to.");
      return;
    }
    try {
      ctx.current = new AudioContext({ sampleRate: 24000 });
      rate.current = ctx.current.sampleRate;
    } catch {
      /* playback falls back to the browser voice */
    }
    const path = caps.data?.path ?? "/ws/voice";
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
    let socket: WebSocket;
    try {
      socket = new WebSocket(url);
    } catch (err) {
      setError(`could not open ${path}: ${String(err)}`);
      return;
    }
    socket.onmessage = (ev) => {
      let frame: Frame;
      try {
        frame = JSON.parse(String(ev.data)) as Frame;
      } catch {
        // Binary frame: raw PCM16 out of the server, played as it arrives.
        void playBinary(ev.data as ArrayBuffer);
        return;
      }
      const f = frame as Record<string, unknown>;
      const t = f.t as string;
      if (t === "hello") {
        setState(String((f.status as { state?: string } | undefined)?.state ?? "listening"));
        if (f.sample_rate_out) rate.current = Number(f.sample_rate_out);
        setLog((l) => [...l, `connected · in ${String(f.sample_rate_in)}Hz out ${String(f.sample_rate_out)}Hz · session ${String(f.session_id ?? "").slice(0, 12)}`]);
      } else if (t === "state") setState(String(f.state));
      else if (t === "reply") {
        const text = String(f.text ?? "");
        setTurns((turnsNow) => [...turnsNow, { you: lastSpoken.current, tutor: text, ms: f.ms as number | undefined, interrupted: Boolean(f.interrupted) }]);
        if (voiceOn) void say(text);
      } else if (t === "speak_unavailable") {
        setLog((l) => [...l, `server voice off (${String(f.reason)}): ${String(f.text ?? "").slice(0, 60)}…`]);
      } else if (t === "config_ack") {
        setLog((l) => [...l, `config: voice=${String(f.voice_enabled)} gender=${String(f.gender)} barge_in=${String(f.barge_in)}`]);
      } else if (t === "partial") setPartial(String(f.text ?? ""));
      else if (t === "error") setError(String(f.detail ?? f.message ?? "server error"));
    };
    socket.onclose = (ev) => {
      setOpen(false);
      setState("idle");
      if (ev.code === 4401) setError("the voice connection needs you signed in: sign in again — a signed-out voice session is refused, not silently ignored");
      else setLog((l) => [...l, "the voice connection closed"]);
    };
    socket.onerror = () => setError("the voice connection hit a problem — the text path below still works without it");
    socket.onopen = () => {
      setOpen(true);
      socket.send(JSON.stringify({ t: "config", voice_on: voiceOn, gender, barge_in: barge }));
    };
    ws.current = socket;
  }

  const lastSpoken = useRef("");

  async function playBinary(buf: ArrayBuffer) {
    if (!ctx.current) return;
    const pcm = new Int16Array(buf);
    const audio = ctx.current.createBuffer(1, pcm.length || 1, rate.current);
    const chan = audio.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) chan[i] = pcm[i] / 32768;
    const src = ctx.current.createBufferSource();
    src.buffer = audio;
    src.connect(ctx.current.destination);
    src.onended = () => setState("listening");
    if (barge) {
      const kill = () => {
        src.stop();
        setState("listening");
        setLog((l) => [...l, "interrupted by you"]);
        window.removeEventListener("keydown", kill);
      };
      window.addEventListener("keydown", kill, { once: true });
    }
    src.start();
  }

  function sendText() {
    const text = draft.trim();
    if (!text || !ws.current || ws.current.readyState !== WebSocket.OPEN) return;
    lastSpoken.current = text;
    ws.current.send(JSON.stringify({ t: "text", text }));
    setDraft("");
    setState("thinking");
  }

  async function dictate() {
    const SR = (window as unknown as { SpeechRecognition?: new () => unknown; webkitSpeechRecognition?: new () => unknown });
    const Impl = (SR.SpeechRecognition ?? SR.webkitSpeechRecognition) as (new () => {
      lang: string;
      interimResults: boolean;
      continuous: boolean;
      maxAlternatives: number;
      onresult: (e: { resultIndex: number; results: ArrayLike<{ 0: { transcript: string }; isFinal: boolean }> }) => void;
      onerror: (e: { error: string }) => void;
      onend: () => void;
      start: () => void;
      stop: () => void;
    }) | undefined;
    if (!Impl) {
      setMic("this browser cannot listen — type instead (Chrome, Edge and Safari can). Nothing was sent anywhere to find that out.");
      return;
    }
    const r = new Impl();
    r.lang = "en-GB";
    r.interimResults = true;
    r.continuous = false;
    r.maxAlternatives = 1;
    let collected = "";
    r.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const txt = e.results[i][0].transcript;
        collected += txt;
        if (e.results[i].isFinal) {
          setPartial("");
          setDraft((d) => (d ? `${d} ${txt}` : txt));
        } else setPartial(txt);
      }
    };
    r.onerror = (e) => setMic(`dictation error: ${e.error}${e.error === "not-allowed" ? " — the microphone permission was refused, which is a legitimate answer" : ""}`);
    r.onend = () => {
      setMic(null);
      rec.current = null;
      setPartial("");
    };
    rec.current = { stop: () => r.stop() };
    setMic("listening…");
    r.start();
  }

  const c = caps.data;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>§ 07 · Voice</div>
          <h1 className="text-xl" style={{ fontFamily: "var(--font-serif)" }}>Talk to it</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Same tutor, same memory, different input. {c?.note ?? "transcription accuracy and teaching accuracy are measured separately."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Tag tone={open ? "good" : "line"}>{open ? state : "not connected"}</Tag>
          {open ? (
            <Btn kind="ghost" onClick={close}>
              hang up
            </Btn>
          ) : (
            <Btn kind="primary" onClick={start}>
              connect
            </Btn>
          )}
        </div>
      </div>

      {error ? <Err error={error} status={null} /> : null}

      <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
        <Card title="Turn" tone="ask">
          <div className="space-y-3">
            {turns.map((t, i) => (
              <div key={i} className="space-y-1">
                <p className="text-sm">
                  <span className="text-xs text-[var(--muted)]">you</span> {t.you}
                </p>
                <p className="text-sm">
                  <span className="text-xs text-[var(--muted)]">tutor</span> {t.tutor}
                  {t.ms !== undefined ? <span className="ml-2 text-xs text-[var(--muted)]">{t.ms} ms</span> : null}
                  {t.interrupted ? <Tag tone="warn">interrupted</Tag> : null}
                </p>
              </div>
            ))}
            {turns.length === 0 ? (
              <p className="text-sm text-[var(--muted)]">
                Nothing said yet. Ask about the question you are stuck on. The tutor sees the same plan and the same
                estimates as the text screens.
              </p>
            ) : null}
            <Field label={partial ? "heard so far…" : "What you said (edit it, because this is what the tutor actually reads)"}>
              <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="why do I have to flip the second fraction" rows={2} />
            </Field>
            {partial ? <p className="text-sm text-[var(--think)]">{partial}…</p> : null}
            <div className="flex flex-wrap gap-2">
              <Btn kind="primary" onClick={sendText} disabled={!open || !draft.trim()}>
                send
              </Btn>
              <Btn onClick={dictate} disabled={Boolean(mic)} title="uses your browser's own dictation; no audio leaves this page unless listening on this machine is set up and the voice connection is live">
                {mic ?? "hold to talk"}
              </Btn>
              {mic ? (
                <Btn
                  kind="ghost"
                  onClick={() => {
                    rec.current?.stop();
                    setMic(null);
                  }}
                >
                  stop
                </Btn>
              ) : null}
              <Btn kind="ghost" onClick={stopTalking} title="stops the current speech without ending the session">
                stop talking
              </Btn>
            </div>
            {mic ? <p className="text-xs text-[var(--muted)]">{mic}</p> : null}
          </div>
        </Card>

        <div className="space-y-4">
          <Card title="What the voice path is" tone="muted">
            {caps.error ? <Err error={caps.error} status={caps.status} /> : null}
            <ul className="space-y-2 text-sm">
              <li>
                <Tag tone={c?.stt?.local_stt ? "good" : "warn"}>listening: {c?.stt?.local_stt ? "on this machine" : "browser dictation"}</Tag>
                {c?.stt?.local_stt ? null : <p className="mt-1 text-xs text-[var(--muted)]">{c?.stt?.detail ?? "listening on this machine is not set up here"}. Your browser can listen instead, on your device.</p>}
                {c?.stt?.local_stt ? <p className="mt-1 text-xs text-[var(--muted)]">{c.stt.detail}</p> : null}
              </li>
              <li>
                <Tag tone={c?.tts?.local_tts ? "good" : "warn"}>speaking: {c?.tts?.local_tts ? "on this machine" : "browser speechSynthesis"}</Tag>
                {c?.tts?.local_tts ? null : <p className="mt-1 text-xs text-[var(--muted)]">{c?.tts?.detail ?? "speaking from this machine is not set up here"}. Your browser can read aloud instead, on your device.</p>}
                {c?.tts?.local_tts ? (
                  <p className="mt-1 text-xs text-[var(--muted)]">
                    voices: {humanize(c.tts.voices ?? {}) || "none reported"} · {c.tts.sample_rate} Hz · {c.tts.interruption ?? "chunked"}
                  </p>
                ) : null}
              </li>
              <li className="text-xs text-[var(--muted)]">connection: live while this page is open</li>
            </ul>
            <div className="mt-3 space-y-2 border-t border-[var(--line)] pt-3">
              <Check label="Let it speak out loud" checked={voiceOn} onChange={setVoiceOn} hint="off = text bubbles only, and the server stops synthesising anything" />
              <Check label="Interrupt me when I start talking" checked={barge} onChange={setBarge} hint="any keypress stops the current utterance — being able to cut in is the difference between a tutor and a lecture" />
              <Field label="Which voice">
                <Select
                  value={gender}
                  onChange={(e) => setGender(e.target.value)}
                >
                  <option value="female">female</option>
                  <option value="male">male</option>
                </Select>
              </Field>
              {open ? (
                <Btn
                  onClick={() => {
                    ws.current?.send(JSON.stringify({ t: "config", voice_on: voiceOn, gender, barge_in: barge }));
                  }}
                >
                  apply to this session
                </Btn>
              ) : null}
              <p className="text-xs text-[var(--muted)]">
                The same three settings live on your account, on the settings screen. What you change here lasts for this session
                only, so a borrowed laptop does not leave your voice preference switched off for the next person.
              </p>
            </div>
          </Card>

          {log.length ? (
            <Card title="Session log" tone="muted">
              <ul className="space-y-1 text-xs text-[var(--muted)]">
                {log.slice(-12).map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            </Card>
          ) : null}

          <Card title="The local engines are optional" tone="muted">
            <p className="text-xs text-[var(--muted)]">
              Voice works without them: your browser dictates and speaks, and nothing is uploaded.
              An operator who wants the on-machine engines installs them once at the console -
              the steps are in <code>docs/VOICE.md</code>, and this page always reports which path
              is actually in use above.
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}
