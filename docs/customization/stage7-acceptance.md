# Stage 7 — cost and archive contract

Stage 7 covers F03/F06/F07/F17 and the associated F18 integration gates. Local and CI evidence is recorded separately in `stage7-acceptance.json` and `stage7-release.json`. Stage 8 staging and production GitOps are separate work. No production configuration is changed here.

## Accounting

- Each generation and real provider call retains its existing stable identity. Repeated cumulative usage frames replace known counters, and partial/final cost-only frames preserve previously confirmed counters. A late actual amount replaces its estimate; the two columns cannot both contribute.
- Amounts are exact decimal strings. Accepted individual amounts are non-negative and finite, below `1e24`, with at most 36 fractional decimal places; oversized exponents are rejected before formatting. Summation uses a 128-digit decimal context. Explicit foreign-currency pricing is unknown in the USD ledger, never silently relabelled USD. Missing prices/usage remain unknown; explicit zero is distinct.
- Materialized round snapshots are corrected only when their fixed call boundary includes the changed event, including inherited descendants. Baseline replacement updates snapshots that reference that baseline. Parent calls after a fork anchor never enter the fork.
- OI6 adds a per-user transaction lock, import receipts and portable origin identities. The lock serializes ledger mutations and imports; provider inference runs outside that transaction. Export keeps a consistent ledger snapshot. Large exports can briefly delay that user's ledger mutations while files are collected; no global user lock or provider retry is introduced.
- Editing, deleting or selecting a reply does not erase paid calls. Continued responses, tool follow-ups and existing background tasks keep independent call records. Statistics deduplicate original call IDs even after cross-account archive forwarding.

## Ordinary JSON import and historical costs

- Settings → Data → Import opens a native modal. Each imported conversation can begin with actual or estimated historical USD; blank means unknown. Its historical per-message costs are not replayed as new charges. Costs supplied here are owner-reported baselines, not verified invoices.
- The file UI uses a SHA-256 receipt key for the exact input bytes. Repeating that file returns its existing conversations and preserves later cost edits. An explicit new-batch option creates a deliberate copy. Different file bytes are different import batches; semantic deduplication of independently edited legacy files is not claimed.
- API callers can supply `import_id`; omitting it preserves the existing deliberate-copy behavior. Reusing a key for different chat content is rejected. The digest excludes the initial fee, so a retry cannot overwrite a subsequently edited baseline. Chat rows, message rows, baselines and receipt commit together or roll back together.
- Existing cost controls send a baseline revision. Concurrent edits return 409; the current input remains visible, and selecting the newly loaded server version is required before retrying. The same batch is replaced, never added twice.

## Portable ZIP archives

- Preserve the full message tree, revisions (including revisions of deleted replies), annotations, stable model references, native payloads, generation/call records, fixed snapshots and baselines. Archive snapshots are recomputed from records instead of trusting supplied totals.
- Original billing identities survive import → export → import. A new view references an anchor rather than creating another charge. Higher portable settlement revisions can update an already imported event; files cannot overwrite native local bills or user-edited baselines.
- Explicit model remapping affects the restored view and future dispatch. The usage API's `view_model_id` is a display projection; the original `model_id`, connection and captured price are unchanged. Portable model aliases compose across repeated forwarding, and owner forks retain them.
- Restores serialize canonical file installation and billing records for one owner. Identical concurrent restore IDs return one chat. New restore views can share the same original charges and files. Credentials and original file permissions are never restored from an archive.
- File permission is checked on export. Available original bytes and extracted text are included with size/hash validation. Unavailable or external assets are explicit; no external URL is fetched, and vector indexes are not claimed as portable. Remapped file references invalidate native continuation payloads when necessary.
- Reject duplicate/unsafe ZIP members, symlinks, unlisted traversal paths, invalid checksums/sizes, duplicate assets, broken message/cost relations, cyclic snapshot inheritance, invalid usage counters and malformed revisions. ZIP and manifest sizes are bounded. Failure rolls back database changes and cleans newly created file objects.

## Acceptance method and limits

- Real FastAPI requests, PostgreSQL 17 transactions, file upload/readback and `pg_dump`/`pg_restore` are exercised with synthetic users and a loopback supplier. Supplier amounts are synthetic settlement fixtures, not real invoices or live price verification.
- Built-in browser checks use the real production frontend, native modal/components and real HTTP endpoints. Synthetic `File` objects are supplied to actual file inputs and dispatch their normal change handler; the operating-system file chooser is not claimed tested. This avoids a previously observed native picker automation hang.
- Attachment fixtures upload real bytes with processing disabled and supply synthetic extracted text. This stage verifies portability and access, not a new embedding/indexing run; retrieval acceptance remains in stage 4.
- No extra inference is made for import, export, annotations or accounting. Existing stage 4–6 request-content, protocol, auth and draft regressions remain required. No implicit RP context, automatic compression or history trimming is added.

Reproducible entrypoints: `test/oi/test_stage7_integrity.py`, `scripts/oi/stage7-acceptance.py`, `scripts/oi/stage7-db-manifest.py`, and the PostgreSQL migration/restore sequence in `.github/workflows/oi.yml`. The stage 7 runner requires an empty loopback `oi_stage7_*` database owned by `oi_test` and fails closed on another target.
