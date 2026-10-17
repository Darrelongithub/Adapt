"""The consent choice keeps one meaning across the two vocabularies it is written in.

The learner's sharing choice is stored as ``consent_share_with_llm`` and answered as
``consent_share_with_ai``, on purpose: the second name is learner copy and says nothing about
providers (§3 of the brief), and the first is the column and the migration. The split is
documented in :mod:`adapt.security.consent` and enforced here, because the two ways to get it
wrong are both quiet:

* a read path that returns the storage name puts provider vocabulary in front of a learner --
  a policy failure the banned-phrase tests on the frontend would only catch by accident;
* a write path that accepts the learner-facing name and ignores it tells the learner "saved"
  while the choice never changed. That is the direction that matters, so the request bodies
  accept both spellings and this file proves it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from adapt.security import consent

SRC = Path(__file__).resolve().parents[1] / "src" / "adapt"


def test_the_three_names_are_one_module_s_decision():
    assert consent.STORED_FIELD == "consent_share_with_llm"
    assert consent.LEARNER_FIELD == "consent_share_with_ai"
    assert consent.ONBOARDING_FIELD == "share_summary_with_ai"
    assert consent.WRITE_FIELDS == (consent.STORED_FIELD, consent.LEARNER_FIELD)


def test_a_read_path_emits_the_learner_name_and_never_the_column_name():
    assert consent.learner_view(True) == {"consent_share_with_ai": True}
    assert consent.learner_view(0) == {"consent_share_with_ai": False}
    assert consent.STORED_FIELD not in consent.learner_view(True)
    assert consent.onboarding_echo(True) == {"share_summary_with_ai": True}


def test_read_body_takes_whichever_spelling_the_client_used():
    assert consent.read_body({"consent_share_with_llm": False}) is False
    assert consent.read_body({"consent_share_with_ai": True}) is True
    assert consent.read_body({"consent_share_with_ai": False}) is False
    assert consent.read_body({}) is None, "an omitted field means 'unchanged', not 'enabled'"
    assert consent.read_body(None) is None
    # Both spellings and disagreeing is a client bug; the storage name wins, deterministically.
    assert consent.read_body({"consent_share_with_llm": True, "consent_share_with_ai": False}) is True


def test_the_profile_a_learner_reads_uses_the_pinned_name(client, learner):
    reply = client.get("/api/v1/me", headers=learner["headers"])
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert consent.LEARNER_FIELD in body, "the learner's own profile must carry the learner-facing name"
    assert consent.STORED_FIELD not in body, (
        "the column name on a learner-visible payload is provider vocabulary in the learner's face"
    )


def test_onboarding_answers_in_the_onboarding_vocabulary(client, learner):
    reply = client.post(
        "/api/v1/me/onboarding",
        json={"preferences": {}, "consent_analytics": True, "consent_share_with_llm": False},
        headers={**learner["headers"], "x-csrf-token": learner["csrf"]},
    )
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["consent"][consent.ONBOARDING_FIELD] is False
    assert body["consent"][consent.ONBOARDING_FIELD] is not None


def test_a_client_can_write_the_choice_using_the_name_it_read(client, learner):
    """The alias test. Reading ``consent_share_with_ai`` and writing it back must not be a no-op.

    Before the alias existed, a client that round-tripped the profile it had just read would
    post a field name the request model did not declare -- and ``extra="ignore"`` swallowed it.
    The learner would see "saved", the column would keep its old value, and a learner who meant
    "do not share my work with a model" would have said it to a request that ignored them.
    """
    headers = {**learner["headers"], "x-csrf-token": learner["csrf"]}
    off = client.post(
        "/api/v1/me/onboarding",
        json={"preferences": {}, "consent_share_with_ai": False},
        headers=headers,
    )
    assert off.status_code == 200, off.text
    assert client.get("/api/v1/me", headers=headers).json()[consent.LEARNER_FIELD] is False

    on = client.post(
        "/api/v1/me/onboarding",
        json={"preferences": {}, "consent_share_with_ai": True},
        headers=headers,
    )
    assert on.status_code == 200, on.text
    assert client.get("/api/v1/me", headers=headers).json()[consent.LEARNER_FIELD] is True


def test_the_storage_name_is_never_hard_written_into_a_response_again():
    """The rule the module exists for, checked against the source rather than trusted.

    A literal ``"consent_share_with_ai":`` in a dict is what this guards: it works today, and
    the day a second endpoint writes it with a typo or a different name nothing notices. The
    name has to arrive from :mod:`adapt.security.consent` -- so it is spelled once.
    """
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "consent.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value in {
                    consent.LEARNER_FIELD,
                    consent.ONBOARDING_FIELD,
                }:
                    offenders.append(f"{path.relative_to(SRC.parent.parent)}:{node.lineno}: {key.value!r}")
    assert not offenders, (
        "learner-facing consent keys must come from adapt.security.consent (learner_view / "
        f"onboarding_echo), not from a literal that can drift: {offenders}"
    )


#: The modules allowed to name the column directly: the definition, the two writes, the
#: consent lookup the provider is given, and the helper that owns the vocabulary. A new one
#: is not automatically wrong -- but it is a new place the split can be got wrong, and this
#: list is where somebody has to say so.
PERMITTED_STORAGE_CONTACT = {
    "db/models.py",  # the column
    "api/routes/auth.py",  # register + onboarding bodies
    "services/sessions.py",  # complete_onboarding writes, profile_summary reads
    "services/__init__.py",  # learner_consented(), the fail-closed lookup
    "security/consent.py",  # the names themselves
}


def test_the_column_is_touched_only_where_it_is_defined_written_or_checked():
    """Every write goes to the column; nothing else is allowed to reach past the helper."""
    offenders: set[str] = set()
    seen: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        relative = str(path.relative_to(SRC))
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names_it = (
                isinstance(node, ast.Attribute) and node.attr == consent.STORED_FIELD
            ) or (
                isinstance(node, ast.Name) and node.id == consent.STORED_FIELD
            ) or (
                isinstance(node, ast.Constant) and node.value == consent.STORED_FIELD
            )
            if names_it:
                seen.add(relative)
                if relative not in PERMITTED_STORAGE_CONTACT:
                    offenders.add(f"{relative}:{getattr(node, 'lineno', 0)}")
    assert seen, "the storage name must still exist somewhere, or the column has been renamed under this test"
    assert not offenders, f"new module naming the consent column directly: {sorted(offenders)}"
