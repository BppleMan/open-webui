# Sixth review remediation

This batch repairs three request/history integration boundaries. It retains the original stored history and preserves unchanged native blocks.

- Pipeline inlet, function inlet and request filters compare the canonical message and native envelope before/after each filter. Changed request copies lose stale replay state; sampling-only changes preserve signatures. No original database message is rewritten.
- Outbound native function blocks are restricted to the surviving canonical tool chain. Final pairing also accounts for filters that remove results. Native-only API history is decoded and paired before replay. Retained signed blocks are unchanged; legacy Gemini parallel IDs are resolved from the saved output order, including cancelled calls.
- Generated image files are retained as generic image content alongside native envelopes. Editing a caption or changing to a compatible binding therefore preserves the image and current text without restoring invalid signatures. Empty text does not discard image-only history. Explicitly disabled vision fails before generation.

Responses fallback uses input-media message content validated against the installed SDK schema. Chat Completions does not define image parts in assistant `content`, so incompatible fallback fails explicitly instead of dropping the image or sending an invalid payload ([official schema](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)). Native responses on their original supported connection retain their established replay path. No user/assistant roles or writing prompts are silently changed to accommodate an unsupported target.

Regression coverage includes the original 34 review cases, complete filter dispatch/request preparation with unchanged-history controls, partially cancelled/parallel tools, repeated continuation, native-only API pairing, actual stored reply edits and media conversion across target protocols. HTTP acceptance generates an unsigned image, changes bindings or edits its caption through the real endpoint, then checks the next outbound request. All suppliers are loopback fixtures; no paid requests were made.

See `review-round6-remediation-acceptance.json` for separate source, check and delivery status. R67 built-in-browser acceptance remains required before the combined isolated-preview publication. Existing exports/history are not retrospectively rewritten, and R50/U49/R59 remain in force.

Final source `1f672196fab3c2e830772ade96cc6d0f295c0b7c` passed [exact-source CI](https://github.com/BppleMan/open-webui/actions/runs/41993951781): 581 backend/79 frontend tests, zero new type/a11y diagnostics (5695 upstream remain), build, all existing integration gates and image publication. The batch also covers ordered identical Ollama tool calls. Browser acceptance and the combined R67/R68 isolated-preview publication remain pending; production is unchanged.
