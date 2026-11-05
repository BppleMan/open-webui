# Eighth review remediation

This batch repairs native history matching and tool-ID remapping at the real streaming consumer.

- Native tools match the canonical suffix, preserving legacy/imported output that has no envelope. Truncated or unterminated Gemini/CPA tools are excluded from the dispatched sequence. Continue reconstructs merged call boundaries without discarding the canonical prefix, surviving signatures or cancellation rules. Canonical reasoning-field choices remain unchanged.
- The consumer records duplicate-ID renumbering against a server-owned adapter state keyed by native call ID and raw position. The same positional mapping reaches request-local replay, saved envelopes and authenticated temporary capsules. Raw signed provider blocks remain unchanged; outgoing tool results use their corresponding provider IDs. Portable envelopes retain the mapping.
- Strict provider-function/argument matching is retained, including remapped and legacy calls. No stored history or archive is retrospectively rewritten.

Thirty-four synthetic regression cases cover the reported failure/control paths plus temporary parallel reused IDs, raw-position offsets, capsule roundtrips, portable replay, merged text-only legacy prefixes, reasoning-only calls and altered-function rejection, and cancellation after ID remapping. Supplier responses use MockTransport and any executed tool is an in-process fixture. Original private review evidence remains unchanged.

Checks and delivery are tracked separately in review-round8-remediation-acceptance.json. The required built-in browser remains unavailable; R67/R68 interaction checks and the combined preview publication are pending. R50/U49/R59 remain.

Final source `f8e8e2f5e09b8ba73af76b6dd26c3895f564bb85` passed [exact-source CI](https://github.com/BppleMan/open-webui/actions/runs/42023168308): 710 backend/79 frontend tests, zero new type/a11y diagnostics (5695 upstream remain), production build, all existing integration gates and image publication. The in-app browser is still not connected; browser acceptance and combined preview publication remain pending.
