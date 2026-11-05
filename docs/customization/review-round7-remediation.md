# Seventh review remediation

This batch repairs three protocol-field and tool-identity boundaries while preserving original stored history and mismatch rejection.

- Explicit `thinking` changes participate in request-filter consistency checks. Ollama conversion retains the selected value, including an empty native field. The supported `reasoning` alias follows the same detection/forwarding boundary; sampling-only changes retain original native state.
- Legacy tool preparation uses the existing `get_provider_function` normalization, matching the canonical converter. A displayed/executed target therefore cannot replace the original `call_tool` function when replaying signed Gemini/CPA history. Cancelled calls remain cancelled.
- Ollama streaming and nonstreaming generation persist canonical tool IDs in the existing positional sidecar, leaving raw provider blocks untouched. Native replay restores those IDs or validates a legacy canonical sequence. It rejects missing/changed identity rather than generating a new historical tool ID. Pruning remaps sidecar positions for surviving calls.

Temporary API capsules also distinguish transport indexes from message content. Projection v2 ignores only the outer tool-call `index` used to assemble stream deltas; tool IDs, names, arguments and message order remain authenticated. Existing v1 capsules retain their original validation behavior. UI output-item capsules retain their full-content contract.

The original 44 review cases pass. Additional cases cover explicit field clearing, the supported reasoning alias, old/new sidecars, dispatch metadata, cancelled dispatched calls followed by Continue, parallel/split-stream Ollama calls, temporary API roundtrips, v1 compatibility and rejected content edits. Suppliers are MockTransport or loopback fixtures; no real tools or supplier accounts are exercised by the new regressions.

Implementation, checks and delivery are tracked separately in `review-round7-remediation-acceptance.json`. R67/R68 browser acceptance and the combined preview release remain pending the required in-app browser. R50/U49/R59 remain unchanged; no production or historical-data rewrite is part of this batch.
