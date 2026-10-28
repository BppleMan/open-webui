# Stage 8A prelaunch acceptance

The current user instruction limits this stage to prelaunch preparation and an isolated preview for further tuning. Production replacement, maintenance activation, production data migration and the CPA production release require a new decision.

Completed gates are recorded individually; source changes, local tests, remote CI, preview runtime and production status are different evidence.

## Preview findings

- Legacy connections at the exact official OpenRouter URL now retain the OpenRouter template, so provider-reported USD cost is recognized. Explicit template choices and compatible proxy URLs are preserved. Migration idempotency does not silently rewrite previously imported connections.
- Model discovery now reads advertised shared context windows and maximum output limits. Unknown or invalid values remain unknown; a refresh preserves model identity and user overrides. A supported reasoning parameter does not imply undocumented effort levels.
- Local regression: 209 backend tests, 50 frontend tests; production build passed; no new type diagnostics against the upstream baseline (5,765 existing diagnostics, not a clean typecheck).

## Acceptance in progress

- Immutable CI image and isolated runtime verification.
- Production-copy migration, original data/attachment integrity and a second restore after custom writes.
- Authenticated HTTP/WebSocket, drafts, migrated routing, actual/unknown costs, fork/archive and browser paths.
- Upload, indexing and retrieval against an isolated vector store.
- Offline release/maintenance materials and a tuning handoff.

## Preserved boundaries

Production remains on the upstream image. Legacy model aliases with multiple candidate connections remain unresolved until the user chooses. Real provider tests use synthetic short text only; the prior stage-specific official-provider, native IME and media/tool evidence limits remain explicit. Physical-device PWA testing was waived by the user.

The final settings audit found unbounded ordinary panels outside the previously corrected interface/audio/document pages. The shared settings shell now applies the 48rem boundary to standard forms and preserves workspace/table widths; final browser verification is in progress.
