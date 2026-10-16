"""ADAPT backend: adaptive learning platform with two cooperating AI roles.

Blueprint contract (the web-application prompt set shipped in
``adapt-web-application-prompts.zip`` at the repository root):

* AI 1 (Learning Intelligence) analyses evidence, traces knowledge, and proposes
  adaptations -- it can never persist an adaptation without a student decision.
* AI 2 (Educational Intelligence) teaches: explanations, hints, examples,
  practice, feedback, remediation and resources, conditioned on AI 1's profile.
* One shared, persistent learner database (SQLite, encrypted with SQLCipher when
  available, field-level AES-256-GCM always).
* Event-level telemetry, not just final scores.
* No paid services: local speech models + free-tier APIs with graceful fallbacks.
"""

__version__ = "0.1.0"
