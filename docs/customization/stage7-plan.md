# Stage 7 — costs and portable archives

Complete the accepted stage, no production deployment. Preserve exact decimal actual/estimated separation, unknown costs and original call identity. No extra model calls for statistics or archive features.

1. Reproduce ledger edge cases: partial/late usage, actual replacing estimates, snapshot boundaries, regen/continue/tool/background calls, baseline replacement and fork inheritance.
2. Make ordinary JSON import atomic and retry-safe, with explicit historical cost input and unknown default. Preserve edited baselines; version-check concurrent changes. Repeated imports must not create duplicate charges.
3. Harden ZIP/manifest validation, lineage ownership, file permission/redaction, model mapping, duplicate restore and concurrent restores. Keep message trees, revisions, annotations, native data and attachments; restore original bytes and extracted text, never silently fetch external URLs or claim vector indexes restored.
4. Verify .03+.04+.05=.12, fork+.02=.14; late corrections affect only covered snapshots; parent later consumption stays outside fork; global usage deduplicates canonical calls. Validate import/edit/export/restore via real HTTP and PostgreSQL, including malformed archives, failure rollback and concurrency. Use synthetic data only.
5. Reuse native OI components for cost editing/import UI. Verify built-in browser path, mobile/desktop, no request mutation by UI metadata. Complete full backend/frontend/type delta/build, exact CI/image, three-layer records and stage8 handoff.
