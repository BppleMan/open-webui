# Second review remediation (R64 / P49)

This batch addresses the second review’s eleven confirmed findings. The earlier fourteen-finding report remains a historical record, not a claim that every edge case was covered. Changes apply to the existing customization on upstream v0.12.5.

- **CR2-01**: Separate new temporary conversations from recoverable drafts; reject different transcripts sharing an idempotent save identity.
- **CR2-02**: Finish empty terminal native responses, preserving reasoning, finish reason and usage.
- **CR2-03**: Propagate retrieval errors and Retry-After through the full chat path; do not invoke the main model after failed retrieval.
- **CR2-04**: Exclude Save As Copy projections from historical statistics.
- **CR2-05**: Freeze trusted pre-ledger usage before continuing an old reply; count new calls separately.
- **CR2-06**: Delete associated content for all authors by owned chat IDs; account deletion also removes that author’s content elsewhere.
- **CR2-07**: Use one canonical projection for sealing and restoring temporary native messages, including provider extensions.
- **CR2-08**: Map Gemini inline images into the existing media channel in streaming and nonstreaming responses; temporary images remain inline.
- **CR2-09**: Compare effective inherited protocols when reusing migration targets.
- **CR2-10**: Reject stale server discovery results under a revision check and database write lock.
- **CR2-11**: Clear transient usage errors on successful 304 responses without replacing cached data.

Temporary conversation identity is distinct from the identity of an unsent recovery record. Explicit new conversations get new IDs; consumed empty drafts are not restored. Saving a temporary conversation preserves its billing boundary without duplicating calls. Same-content retries are idempotent; different content with the same saved identity is a 409 conflict. Unsent editor content is persisted to the destination draft before navigation.

Legacy usage captured during first takeover is server-derived metadata, not a client-provided accounting input. Copy projections and inherited/imported projections remain excluded from duplicate global statistics. Content deletion and cost retention have separate scopes.

No schema migration is required. Synthetic integration coverage includes actual HTTP/socket completion and media delivery, full RAG error propagation, and PostgreSQL discovery interleaving. Validation and deployment evidence are recorded separately in `review-round2-remediation-acceptance.json`. Production remains unchanged under R50; the separate real-provider readable-summary gap remains open.
