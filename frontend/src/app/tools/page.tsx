"use client";

import { useState, type FormEvent } from "react";

import { Btn, Card, Err, Notice, PageHeader, Textarea } from "@/components/ui";
import { del, post, useApi } from "@/lib/api";

type StudyItem = { id: string; kind: "note" | "card"; text: string; answer: string | null; created_at: string };
type StudyItems = { notes: StudyItem[]; cards: StudyItem[] };

export default function StudyTools() {
  const items = useApi<StudyItems>("study/items");
  const [note, setNote] = useState("");
  const [front, setFront] = useState("");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState<"note" | "card" | string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  async function save(kind: "note" | "card", event: FormEvent) {
    event.preventDefault();
    setBusy(kind);
    setError(null);
    setSaved(null);
    try {
      await post("study/items", kind === "note" ? { kind, text: note } : { kind, text: front, answer });
      if (kind === "note") setNote("");
      else { setFront(""); setAnswer(""); }
      setSaved(kind === "note" ? "Note saved to your learner record." : "Flashcard saved to your learner record.");
      items.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save. Your text is still in the form.");
    } finally {
      setBusy(null);
    }
  }

  async function remove(item: StudyItem) {
    setBusy(item.id);
    setError(null);
    setSaved(null);
    try {
      await del(`study/items/${item.id}`);
      setSaved(item.kind === "note" ? "Note deleted." : "Flashcard deleted.");
      items.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete this item.");
    } finally {
      setBusy(null);
    }
  }

  const notes = items.data?.notes ?? [];
  const cards = items.data?.cards ?? [];
  return (
    <div className="stack-4">
      <PageHeader title="Study tools" subtitle="Write notes and make flashcards. Both are saved to your learner record." />
      {items.error ? <Err error={items.error} status={items.status} /> : null}
      {error ? <Notice tone="warn">{error}</Notice> : null}
      {saved ? <Notice tone="good">{saved}</Notice> : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="New note" tone="blue">
          <form className="stack-3" onSubmit={event => save("note", event)}>
            <Textarea aria-label="Note" value={note} onChange={event => setNote(event.target.value)} maxLength={4000} rows={5} placeholder="Write something you want to remember" />
            <div><Btn kind="primary" type="submit" disabled={busy !== null || !note.trim()}>{busy === "note" ? "Saving…" : "Save note"}</Btn></div>
          </form>
        </Card>
        <Card title="New flashcard" tone="mint">
          <form className="stack-3" onSubmit={event => save("card", event)}>
            <Textarea aria-label="Flashcard front" value={front} onChange={event => setFront(event.target.value)} maxLength={4000} rows={3} placeholder="Question or prompt" />
            <Textarea aria-label="Flashcard answer" value={answer} onChange={event => setAnswer(event.target.value)} maxLength={4000} rows={3} placeholder="Answer" />
            <div><Btn kind="primary" type="submit" disabled={busy !== null || !front.trim() || !answer.trim()}>{busy === "card" ? "Saving…" : "Save flashcard"}</Btn></div>
          </form>
        </Card>
      </div>

      <Card title={`Your notes (${notes.length})`} tone="plain">
        {!items.loading && notes.length === 0 ? <p className="text-sm text-[var(--muted)]">No notes saved yet.</p> : null}
        <ul className="stack-3">
          {notes.map(item => (
            <li key={item.id} className="border-b border-[var(--line)] pb-3">
              <p className="whitespace-pre-wrap text-sm">{item.text}</p>
              <div className="mt-2"><Btn kind="secondary" disabled={busy !== null} onClick={() => remove(item)} ariaLabel={`Delete note: ${item.text}`}>Delete note</Btn></div>
            </li>
          ))}
        </ul>
      </Card>

      <Card title={`Your flashcards (${cards.length})`} tone="plain">
        {!items.loading && cards.length === 0 ? <p className="text-sm text-[var(--muted)]">No flashcards saved yet.</p> : null}
        <div className="stack-3">
          {cards.map(item => (
            <div key={item.id} className="border-b border-[var(--line)] pb-3">
              <details>
                <summary className="cursor-pointer whitespace-pre-wrap text-sm">{item.text}</summary>
                <p className="mt-2 whitespace-pre-wrap text-sm" aria-label="Flashcard revealed answer">{item.answer}</p>
              </details>
              <div className="mt-2"><Btn kind="secondary" disabled={busy !== null} onClick={() => remove(item)} ariaLabel={`Delete flashcard: ${item.text}`}>Delete flashcard</Btn></div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
