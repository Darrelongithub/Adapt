# Part A handoff to Phase D: onboarding copy

`frontend/src/app/onboarding/page.tsx` was intentionally left unchanged because Phase D owns that page. Please apply these learner-facing replacements there.

## Sharing consent

Replace the checkbox label with:

> Share a short learning summary when it would help

Replace its hint with:

> If you choose to share, the summary includes your topic mastery and learning suggestions, never your name or contact details.

## Saved preferences

Do not print rejected preference names or backend reasons to the learner. When the save result has rejected preferences, show:

> Some preferences could not be saved. Please review them and try again.

When the save succeeds without rejected preferences, show:

> Your preferences were saved.

## Preference explainer

Remove the whole “Allowed preference keys” card, including the “from the API” label and the `ignored_keys` explanation. Replace it with this static explainer:

```tsx
<Card title="How your preferences help" tone="muted">
  <p className="text-sm text-[var(--muted)]">
    These choices help tailor your topics and the pace of your practice. You can change them later in Settings.
  </p>
</Card>
```

The card should not list internal preference field names or describe how the browser sends them.
