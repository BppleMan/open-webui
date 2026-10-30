# Review remediation contract

R63 / P48 restores existing behavior and does not authorize production deployment.

- **CR30-01:** Retain OpenAI reasoning_details/provider fields on native replay; opaque state stays scoped to its binding.
- **CR30-02:** Recover multiple call boundaries after Continue merges displayed assistant outputs.
- **CR30-03:** Resolve earlier fork anchors across the bounded inherited snapshot chain.
- **CR30-04:** Return authenticated encrypted client-held native state for temporary chats; validate user, chat, message and content before replay.
- **CR30-05:** Exclude individually accounted messages, including composite message keys, rather than all legacy usage in a chat.
- **CR30-06:** Persist OpenAI length/content_filter endings as incomplete/blocked and display exceptional statuses.
- **CR30-07:** Display unknown historical cost batches in both reply summaries and the info panel.
- **CR30-08:** Preserve migrated ACL provenance through ordinary model edits.
- **CR30-09:** Dispatch public embeddings through the selected native binding, permissions, raw model ID and ledger.
- **CR30-10:** Reconcile missing models and aliases when reusing a migrated connection; reject ambiguous mappings before retiring the source.
- **CR30-11:** Clear drafts, revisions and annotations through chat deletion paths; purge matching device drafts and hide old orphan drafts.
- **CR30-12:** Atomically save temporary conversations with a fixed inherited billing boundary, stable retry identity and no duplicated calls.
- **CR30-13:** Keep capability_params out of chat provider bodies, including provider-option merges.
- **CR30-14:** Build snapshot totals from one call-prefix index and return conditional usage responses rather than repeated historical queries.

Temporary native capsules are encrypted using a domain-separated key derived from the persistent server secret. They contain no server-side temporary transcript; explicit edits invalidate replay, and incompatible opaque bindings fail explicitly. Saving uses the caller transaction even when database session sharing is disabled.

No schema migration is introduced. Existing orphan drafts are filtered from listings; future deletions erase their content records. Accounting remains independent of transcript deletion. The provider thought-summary acceptance gap is unrelated and remains open.

Verification and deployment status: review-remediation-acceptance.json.
