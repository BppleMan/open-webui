# Fourteenth-review remediation — R76 / P61

| Finding | Repair | Verification |
| --- | --- | --- |
| CR14-01 | Finalize actual consumer output after stream cleanup on success, EOF, cancellation and transport exceptions. | Real AsyncByteStream failures between argument deltas, temporary/saved replay, valid controls; actual HTTP socket interruption and followup. |
| CR14-02 | Persist partial output and completed tool results on ordinary processing failures; preserve execution status with a separate postprocess error. | Seven protocols, actual MCP output, attachment/processing/event/publication faults, actual SQLite and temporary capsule replay. |
| CR14-03 | Persist cancellation output/native state before publishing the queue-releasing event; failed notifications cannot skip persistence. | SQLite reads at notification time, failed notification, actual HTTP immediate followup after cancellation. |
| CR14-04 | Only nonempty assistant rows with missing terminal keys need legacy fallback. | User/current/null/new/legacy rows, repeated reads and 1 MiB fixtures; actual SQL capture. |

Local checks passed: original 154 cases, 270 expanded regressions, 1943 backend / 88 frontend tests, zero new type diagnostics (5695 existing), build and actual HTTP/socket/state checks. Built-in browser verified network-error replay, tool Stop, retained result, followup and reload. Exact-source CI and isolated-preview delivery are pending. The new read regression is attributable to R75; the other findings are exception recovery gaps with differing prior failure behavior, not evidence that R75 lost its previously verified improvements. Original review evidence remains unchanged. R50/U49/R59 and built-in browser only remain.
