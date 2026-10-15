# Phase B handoff for Phase G

## 1. Register the prepare router

`backend/src/adapt/api/routes/content_prepare.py` defines a router whose prefix already includes `/api/v1/content/prepare`. Phase G should import and include `content_prepare.router` in the application router registry without adding a second `/api/v1` prefix. The required paths are `POST /api/v1/content/prepare` and `GET /api/v1/content/prepare/{job_id}`.

Import the router before the application's `Base.metadata.create_all()` startup path. Importing it loads `adapt.content.skeleton.cache` and registers `content_pack_cache`, `content_prepare_jobs`, and `content_pack_item_calibration` on the shared metadata. `app.py`/route registration was not changed here because it is outside Phase B ownership.

## 2. Update migration test bootstrap/schema expectations

The full backend suite currently has one migration failure:

```text
tests/test_migrations.py::test_an_existing_create_all_database_can_be_stamped_then_upgraded
```

Its subprocess runs `get_db().create_all()` after importing only `adapt.db.engine`; that does not import the new skeleton cache models, so the scratch database lacks all three P3 tables. The parent test process does see the models after the B package is collected, and its `_expected_schema()` therefore expects them. The later `stamp head` correctly does not replay P3, so the test reports that stamping “lost” the tables.

Please update the migration-test bootstrap (or the app bootstrap it exercises) to import/register `adapt.content.skeleton.cache` before `Base.metadata.create_all()`, and keep the expected schema/index comparisons aligned with the models. The revision itself applies cleanly; `alembic check` and downgrade-to-base passed. Running the migration test module alone can also show schema-set drift if its expected-schema process does not import the cache model module.

At this checkpoint Alembic reports `P3` as the sole head, chained from `P2`. If another phase contributes a revision branching from P2, resolve the branch/merge ordering in Phase G rather than renumbering or rewriting P3 here.

## 3. Keep scoring on the typed Pack path

Generated model items carry private `ItemCalibration` metadata in the in-process `Pack`, but it is intentionally absent from `Pack.as_dict()` to preserve the frozen contract. Downstream scoring/placement should consume `pack.scorable_items()` (or typed `item.scorable`) and persist outcomes through `record_item_attempt()`. A plain serialized item mapping cannot establish active calibration and the helper fails closed unless an explicit `active: true` accompanies it. Authored and simple items are not promoted to scoreable generated items.

## 4. Resolve the literal SymPy requirement

Part B uses `content/symbolic.py`'s exact rational `Fraction` checker, whose existing module documentation explicitly explains why it is not SymPy. The checker supports bounded exact arithmetic, polynomial/rational equivalence, solving, differentiation, and evaluation; unsupported expressions are rejected rather than treated as verified. The Phase B spec says “the SymPy path,” so Phase G should explicitly decide whether this existing exact checker is accepted as the equivalent symbolic verifier or whether the project will add and own a SymPy dependency. No dependency-manifest change was made by B.

## 5. Upload boundary

The frozen `get_pack` contract and the prepare endpoint accept topic text only. No file-upload field or upload extraction path exists in this Phase B route. If uploads are added later, keep them out of model prompts until they have their own bounded extraction, personal-data redaction, and untrusted-data delimiting path.

## 6. Existing suite failures outside B

After the B injection and leaked-hint tests, the full backend suite reports **1142 passed, 1 skipped, 4 failed**. Besides the migration bootstrap failure above, three failures are in Phase A-owned behavior:

- `tests/test_round2_a_public_privacy_and_logs.py::test_secrecy_filter_preserves_argument_shapes`
- `tests/test_round2_phase_a.py::test_mcq_nudge_uses_each_distractors_authored_misconception`
- `tests/test_round2_phase_a.py::test_closing_prompt_is_selected_by_item_kind_or_omitted`

B did not edit those files. They remain visible failures for integration; please resolve with their owning phase rather than suppressing or weakening tests.
