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

import { Btn, Card, Check, Err, Field, Notice, Select, Tag, Textarea, useSession } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { createDeduper, debounce, type Deduper } from "@/lib/debounce";
import { useVoice } from "@/components/voice/context";

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
  | { t: "throttle"; scope?: string; retry_after_s?: number }
  | { t: "deduped"; reason?: string }
  | { t: "busy"; state?: string; detail?: string }
  | Record<string, unknown>;

export default function VoicePage() {
  const { me } = useSession();
  const { enabled: voiceOn, setEnabled: setVoiceOn, speak: appSpeak } = useVoice();
  const caps = useApi<Caps>("voice/capabilities");
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<string>("idle");
  const [turns, setTurns] = useState<{ you: string; tutor: string; ms?: number; interrupted?: boolean }[]>([]);
  const [draft, setDraft] = useState("");
  const [partial, setPartial] = useState("");
  const [gender, setGender] = useState("female");
  const [barge, setBarge] = useState(true);
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [mic, setMic] = useState<string | null>(null);
  // A pacing refusal is not an error (nothing broke) and not one of the turn bubbles: the
  // learner asked twice, the server answered once, and the sentence that explains the gap
  // belongs on the screen rather than in a log that is closed by default.
  const [notice, setNotice] = useState<string | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const rec = useRef<{ stop: () => void } | null>(null);
  const rate = useRef(24000);

  const say = useCallback(async (text: string) => {
    if (!voiceOn || !text) return;
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
    await appSpeak(text);
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setLog((l) => [...l, "no speech engine at all — the text answer is still on screen"]);
    } else if (window.speechSynthesis.getVoices().length === 0) {
      setLog((l) => [...l, "browser has no speech voices installed; text only"]);
    }
  }, [appSpeak, gender, voiceOn]);

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
    if (!voiceOn) {
      setError("Turn voice on before connecting. When it is off, there is no microphone, audio, or voice recording.");
      return;
    }
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
      } else if (t === "throttle") {
        // The server's own guard answered before any work was done. Saying "slow down" is
        // friendlier than dropping the frame and letting the learner wonder.
        setLog((l) => [...l, `server asked for a pause (${String(f.scope ?? "frames")}, ${String(f.retry_after_s ?? "?")}s)`]);
        setNotice("that arrived faster than the server will answer - wait for the answer already on its way");
      } else if (t === "deduped") {
        setLog((l) => [...l, "the same message arrived twice; the second copy was ignored"]);
      } else if (t === "busy") {
        setLog((l) => [...l, `a turn is already running (${String(f.state ?? "working")})`]);
        setNotice("one turn at a time: stop the answer that is running, or wait for it");
      } else if (t === "partial") setPartial(String(f.text ?? ""));
      else if (t === "error") setError(String(f.detail ?? f.message ?? "server error"));
    };
    socket.onclose = (ev) => {
      setOpen(false);
      setState("idle");
      if (ev.code === 4401) setError("the websocket refused the session (4401): sign in again — an unauthenticated voice stream is refused, not silently ignored");
      else setLog((l) => [...l, `closed (${ev.code}${ev.reason ? ` ${ev.reason}` : ""})`]);
    };
    socket.onerror = () => setError("websocket transport error — the text path below still works without it");
    socket.onopen = () => {
      setOpen(true);
      socket.send(JSON.stringify({ t: "config", voice_on: voiceOn, gender, barge_in: barge }));
      sends.current = createDeduper(900);
    };
    ws.current = socket;
  }

  const lastSpoken = useRef("");
  // -- one send per intention, and no frame per keystroke ------------------------------- #
  // The socket is the cheapest thing on this page to call and the most expensive thing on the
  // server to answer: a text frame is a *turn*, and a model call. Two guards, both mirroring
  // the server's (`ws_duplicate_window_ms` is the same 900 ms on purpose, so a learner's
  // double-click is dropped here rather than explained by a refusal frame):
  //
  //  * `sends` refuses the same words twice inside the window -- a double-click, or a held
  //    Enter on the send button;
  //  * `configFrame` debounces the settings frames, which are sent from control handlers and
  //    would otherwise go out once per pixel of a drag.
  const sends = useRef<Deduper | null>(null);
  const configFrame = useRef<((frame: Record<string, unknown>) => void) | null>(null);
  const sendConfig = (frame: Record<string, unknown>) => {
    (configFrame.current ??= debounce((next: Record<string, unknown>) => {
      if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(next));
    }, 250))(frame);
  };

  useEffect(() => {
    if (!voiceOn) {
      rec.current?.stop();
      rec.current = null;
      ws.current?.close();
      ws.current = null;
      setOpen(false);
      setState("idle");
      if (typeof window !== "undefined") window.speechSynthesis?.cancel();
      setMic(null);
    } else if (ws.current?.readyState === WebSocket.OPEN) {
      sendConfig({ t: "config", voice_on: voiceOn, gender, barge_in: barge });
    }
  }, [barge, gender, voiceOn]);

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
    if (!voiceOn || !text || !ws.current || ws.current.readyState !== WebSocket.OPEN) return;
    if ((sends.current ??= createDeduper(900)).seen(text)) {
      // The same words again inside the window: a double-click on send, or Enter held down.
      // One turn was started; the second copy is dropped here, where it costs nothing.
      setDraft("");
      setLog((l) => [...l, "that was the same message twice - one turn sent"]);
      return;
    }
    lastSpoken.current = text;
    ws.current.send(JSON.stringify({ t: "text", text }));
    setDraft("");
    setNotice(null);
    setState("thinking");
  }

  async function dictate() {
    if (!voiceOn) return;
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
      setMic("this browser has no dictation API — type instead (Chrome, Edge and Safari have it). Nothing was sent anywhere to find that out.");
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
          <h1 className="t-h1">Talk to it</h1>
          <p className="t-small">Greeting, read-aloud, and listen-and-answer.</p>
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
            <Btn kind="primary" onClick={start} disabled={!voiceOn} title={!voiceOn ? "Turn voice on before connecting" : undefined}>
              connect
            </Btn>
          )}
        </div>
      </div>

      {error ? <Err error={error} status={null} /> : null}
      {notice ? (
        <Notice>
          {notice}
          <button className="ml-2 underline" onClick={() => setNotice(null)}>
            dismiss
          </button>
        </Notice>
      ) : null}

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
              <Btn onClick={dictate} disabled={!voiceOn || Boolean(mic)} title={voiceOn ? "uses your browser's own dictation; no audio goes to the server unless the local engine is installed and the websocket is streaming" : "Turn voice on before using the microphone"}>
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
          <Card title="Voice and speech" tone="muted">
            {caps.error ? <p className="text-sm text-[var(--bad)]">Voice settings could not be loaded just now.</p> : null}
            <ul className="space-y-2 text-sm">
              <li>
                <Tag tone={c?.stt?.local_stt ? "good" : "warn"}>listening: {c?.stt?.local_stt ? "on this machine" : "in the browser"}</Tag>
                <p className="mt-1 text-xs text-[var(--muted)]">
                  {c?.stt?.local_stt
                    ? "Listening is set up on this machine. Nothing you say is sent away."
                    : "Listening uses the browser. Nothing you say is sent away to be transcribed."}
                </p>
              </li>
              <li>
                <Tag tone={c?.tts?.local_tts ? "good" : "warn"}>speaking: {c?.tts?.local_tts ? "on this machine" : "in the browser"}</Tag>
                <p className="mt-1 text-xs text-[var(--muted)]">
                  {c?.tts?.local_tts
                    ? "Speaking is set up on this machine. Replies are not stored as audio."
                    : "Speaking uses the browser. The audio stays on this device."}
                </p>
              </li>
              <li className="text-xs text-[var(--muted)]">A live connection is used when it is available. Text still works without it.</li>
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
                  // Debounced, not sent straight down the socket: a double-click here used to
                  // be two config frames, and the server answers each of them with a frame of
                  // its own. The window is short enough that a deliberate second change still
                  // arrives.
                  onClick={() => sendConfig({ t: "config", voice_on: voiceOn, gender, barge_in: barge })}
                >
                  apply to this session
                </Btn>
              ) : null}
              <p className="text-xs text-[var(--muted)]">
                The voice switch is app-wide and is also available in the top bar. When it is off, no microphone, speech audio,
                or voice turn is used; the keyboard path remains available.
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
