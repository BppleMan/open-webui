# Fifth review remediation

The fifth review identified four boundaries that ordinary success-path checks missed. This batch restores existing behavior without changing production routing, schema or supplier requests.

- Full-chat responses from pin, folder and import routes use the same caller/configuration-owner projection as detail reads. The projection operates on detached responses; stored native history and the owning caller's metadata remain available.
- Portable Anthropic, Gemini, CPA and Interactions envelopes retain terminal semantics alongside signed output blocks. Continue reconstruction can recover tool boundaries after a fork or archive restore. Incomplete and synthetic terminal markers do not enable tool execution.
- Generated Gemini tool IDs are stored as an envelope sidecar, including temporary authenticated capsules. Provider-native parts remain unchanged. Legacy envelopes recover missing IDs from the unchanged canonical function sequence only when name and arguments match; mismatch checks remain active.
- A sidebar refresh error after successful temporary-chat persistence no longer aborts the transition to the saved chat. Existing navigation and account guards still protect a newer page. Subsequent typing uses the saved conversation's durable draft manager.

Coverage includes route privacy and owning-caller retention, four-protocol tool/result/Continue/fork/archive follow-ups, generated tool IDs across stream/nonstream and saved/temporary transport, legacy mismatch rejection, and sidebar HTTP/network errors followed by typing and reload. Full-chat response routes were enumerated; title-only, statistics and boolean routes do not return native envelopes. Import replay responses are projected because idempotent imports can return a conversation that has since acquired collaborator replies.

Implementation, checks and isolated-preview delivery are tracked separately in `review-round5-remediation-acceptance.json`. Original review evidence remains historical. This is a forward fix: prior exports whose terminal fields were stripped cannot be reconstructed without an intact source. R50 and the separate R59 supplier-summary acceptance gap remain.
