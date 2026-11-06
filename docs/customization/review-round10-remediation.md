# Tenth review remediation

This batch repairs four existing integration or incomplete-remediation boundaries. The original review evidence remains unchanged; it does not classify all four as new R71 regressions.

Each native call now retains its actual consumer output after stream filters, tag parsing and optional Markdown media storage. Replay validates ordered text, reasoning and provider-function projections against saved output before reattaching the unchanged native body. This removes chunk-partition guessing from newly recorded calls, including Continue, portable history and authenticated temporary capsules. Legacy envelopes retain conservative compatibility handling; this batch does not invent missing historical transformation evidence or rewrite old messages.

Stream filters compare request-relevant event fields. A changed call becomes non-replayable in live continuation, stored history and temporary state; metering-only changes preserve replay. Private diagnostic raw bodies remain separate, and portable projections omit the invalidated raw body. Cancellation closes and records the active stream so filtered partial output cannot restore the original text.

Canonical/provider tool-ID sidecars now cover all seven protocols. Outbound results use the identity of their preceding call without rewriting signed provider blocks. Responses added/done/completed events share a per-call identity map, including sparse output indexes and repeated provider IDs. The same map reaches execution, immediate continuation and saved reconstruction. Existing legacy function aliases and cancelled tool pairs remain covered.

The canonical media projection records the actual authorized file-URL conversion while leaving the corresponding raw native content intact. Optional Markdown image storage remains enabled in its integration fixture; no user setting is disabled to bypass replay validation.

Validation includes the original 104 cases, 100 additional combination regressions, 200 deterministic stream partitions, the existing backend/frontend suites, real loopback HTTP/WebSocket/database paths and built-in-browser continuation/filter/reload checks. Exact CI and isolated-preview delivery status are separate in review-round10-remediation-acceptance.json. R50/U49/R59 remain.


2026-11-06: R72/P57 is verified on isolated preview. Source `6e4f1500d9d0a32810286f22af3b1181be221951` passed 1017 backend / 79 frontend cases, zero new type diagnostics, the original 104 review cases, 100 added combination cases and 200 deterministic partitions. Real HTTP/socket/database fixtures and built-in-browser tag/Continue/filter/reload paths passed. Exact-source CI and GitOps `4cd29f43a41c60d7d837dc38a5d4578b57bb7882` delivered the recorded immutable image with Synced/Healthy status and no restarts. Original review evidence remains unchanged; R50 and the R59 real-provider gap remain. See review-round10-remediation-acceptance.json.
