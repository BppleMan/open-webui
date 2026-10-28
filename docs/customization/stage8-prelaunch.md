# Stage 8A — prelaunch verified, waiting for user tuning

The user asked to finish the prelaunch work and keep production unchanged. Implementation and acceptance for stage8A are complete under the methods and boundaries in [stage8-acceptance.json](stage8-acceptance.json). The fixed source/CI/image references are in [stage8-release.json](stage8-release.json). This is not production deployment authorization.

## Changes found during acceptance

- Legacy connections at the exact official OpenRouter URL retain their template, allowing confirmed USD usage to settle correctly. Explicit templates and proxy URLs are preserved. A fresh production copy was migrated using the final backend; a bounded live request confirmed actual cost without a manual currency override.
- Model discovery reads advertised shared context and output limits. Invalid/unknown values remain unknown; stable model IDs and explicit overrides survive refresh. Reasoning levels are not invented from a supported parameter name.
- All 17 ordinary personal/admin setting panels share the 48rem boundary. Connection/model lists, analytics, database tables and large workspace editors retain their width. Actual measurements cover 390/430/1280/2560px with 1.1 application scale. The wide general form changed from 2172px to 845px at that scale.
- The isolated preview uses a persistent attachment volume. Its initialization handles empty Slim data and NFS ownership restrictions; source attachment and later archive bytes survive rollout.

## Verified paths

209 backend tests, 50 frontend tests, no new type diagnostics (5,765 inherited diagnostics remain), local production build and all final CI jobs pass. Final CI includes prior stage4–7 integration suites.

An original database copy restored with all 49 table fingerprints matching. Additive migration preserved the original business data; expected clone changes disabled copied scheduled work/functions. After custom writes, a second backup restored 63 matching table fingerprints and three attachments into an independent directory. The candidate image actually signed in, refreshed a session, read a long draft and cost totals, retained model identity and decrypted eleven connections on the restored database.

Actual preview HTTP/browser paths cover authenticated sockets, long drafts/CAS, modal edits, bookmarks, model grouping, the composer context popup, real reported/unknown fees, regeneration/fork isolation, JSON baseline replacement, ZIP restore and real vector indexing/retrieval using synthetic text. User interface metadata did not create extra inference calls. Maintenance and cutover overlays were prepared and checked offline only.

## Next boundary

The preview is available for user tuning. Production OI/CPA, production routing and the original business schema were not replaced. The legacy duplicate-model alias still needs an explicit choice. Official-provider credentials/live Ollama/provider media and tools retain the stage6 limits. Physical PWA testing was waived; native IME and native browser-chrome zoom are not claimed. All exact methods and limitations remain in the acceptance record.

After tuning, production authorization, fresh backups, drain and actual production TLS/routing acceptance belong to stage8B. Do not roll back new custom data to the upstream image.
