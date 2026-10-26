# Validation status

Work in progress; nothing in this document means production deployment or complete acceptance.

- Upstream baseline: v0.12.3, 91ea4e3421d1d8069f13d4a463fcb5986d94ea60.
- 51 backend tests currently pass: additive migration, draft versioning/tombstones/isolation, decimal accounting, regeneration/fork boundaries, late settlement, catalog identity and encrypted credentials, protocol fixtures, CPA 429 and stable identity.
- 16 Vitest tests pass (draft orchestration, session races and upstream revision diff): long text, lost write response idempotency, input after send, offline acknowledgement cleanup, temporary device-only drafts, concurrency conflicts.
- Upstream has 5,696 type errors and 188 warnings with these locked dependencies. The baseline gate rejects additional per-file diagnostics and explicitly reports remaining debt; it is not a clean typecheck claim.
- Local synthetic UI: a 6,020-character Chinese draft survives reload; first model request runs through the new catalog/adapter/accounting path. Full cross-browser, mobile/PWA, stream cancellation, authentication renewal/revocation, and all 22 model entrypoint acceptance remain in progress.
- No production database, configuration, workload or GitOps declaration has been changed.

Protocol references used for implementation:

- [OpenAI Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [Anthropic Messages streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Gemini GenerateContent](https://ai.google.dev/api/generate-content)
- [Gemini Interactions v1](https://ai.google.dev/api/interactions-api-v1)
- [Gemini Interactions streaming](https://ai.google.dev/gemini-api/docs/streaming)

These describe protocol contracts, not live provider certification. Unknown values remain unknown.

### U18–U20 UI correction checks

- Composer ring opens a native Dropdown/Tooltip popover; flat conversation-wide panels have been removed.
- Desktop and 390px viewport inspected in browser. At390px the popover measured left8/right374/width366 and page scrollWidth390: no horizontal overflow.
- Escape closes the popover and returns focus to context-window-trigger. Unknown capacity uses a static empty track.
- Connection manager now appears once within the existing external-connection settings shell. Native components and subtle row hierarchy replace the rejected outlined panels.
- Add connection opens template selection. Gemini template visibly prefilled the native v1beta base URL; closing the dialog did not create a connection.
- A connection expands its models inline. Other22-entrypoint/model/protocol/PWA acceptance is still incomplete.

### Connection template acceptance (2026-10-25)

- 17 reusable templates now supply saved creation defaults. Seven compatible-provider root URLs were checked against the official sources below. Self-hosted gateways require the user's address; no model prices/capabilities are invented.
- HTTP/persistence tests: repeated Gemini template -> two connection identities -> two models each; template-version change and connection rename preserve existing model IDs, prices, parameters and address. Stale connection revisions return409.
- OpenAI, Claude and Gemini template-created connections reach their native request builders and authentication headers in synthetic tests. This does not certify real provider accounts.
- Model discovery reads Gemini/Claude pages before changing availability. A failed later page preserves existing models. User capability overrides remain separate from discovered capabilities.
- Browser: template picker, Gemini address prefill, save, automatic expansion, model discovery, manual second model and re-opened connection all exercised against the isolated local server. Mobile390px layout measured scrollWidth390; no page overflow. New fields and dialogs use upstream Open WebUI components.
- Production frontend build passed with Node22 and NODE_OPTIONS=--max-old-space-size=8192; the first attempt hit the default Node heap limit.
- Removed obsolete admin OpenAI/Ollama configuration fetch/write handlers. The admin completion action no longer rewrites hidden legacy configuration.
- Broader native protocol, all22 selector surfaces, personal browser-direct migration, PWA and production acceptance remain in progress. Template workflow acceptance does not complete those features.

Official sources for compatible template defaults:

- [DeepSeek quickstart](https://api-docs.deepseek.com/api/responses)
- [Groq API reference](https://console.groq.com/docs/api-reference)
- [Mistral migration guide](https://docs.mistral.ai/resources/migration-guides)
- [Moonshot/Kimi quickstart](https://platform.kimi.com/docs/get-api-key)
- [xAI quickstart](https://docs.x.ai/developers/quickstart)
- [Z.ai quickstart](https://docs.z.ai/guides/overview/quick-start)
- [Xiaomi MiMo official integration guide](https://github.com/XiaomiMiMo/awesome-mimo-agent/blob/main/docs/trae.md)
- [Gemini model pagination](https://ai.google.dev/api/models)
- [Claude model pagination](https://platform.claude.com/docs/en/api/models/list)

### Continuing integration checks

- Real local HTTP round trips through OI, a saved preset and each synthetic native provider (OpenAI Responses, Claude Messages, Gemini GenerateContent) completed. Each recorded one call with correct preset display ID, binding ID and connection ID. Real provider certification and full streaming/tool matrices remain pending.
- Native model preset resolution, base visibility/permissions, ambiguity/cycle rejection and request parameter precedence have focused tests.
- New-conversation editor initialization/model-only changes no longer create a server draft. Browser observed unchanged chat/draft counts after opening an empty new conversation.
- With the isolated backend stopped, reload retained the original conversation URL and displayed a retry action; after restart, retry restored the same conversation and message body. Bootstrap-expiry/MFA/PWA cases remain pending.
- Native numeric reasoning budget controls now validate and expand known ranges. Unit tests cover exact nested parameters, missing defaults and invalid selections; visual/outbound acceptance remains pending.
- The latest baseline gate reports no new type/a11y diagnostics; 5,870 upstream diagnostics remain. Latest-source production build passed for the U21 UI correction with Node22 and an8GB heap.


### U21 settings and model picker correction

- Personal interface settings use bounded rows with label/description on the left and a consistent trailing control column. Native Switch, save behavior and inheritance are retained. Expanded scale controls have a 24rem container; duplicate page/section heading removed. Shared admin rows use the same two-column alignment.
- Chat picker uses named, non-selectable connection group strips, nested model rows, a source label and a model-first trigger. Repeated external-link decoration is removed from grouped model rows. Original model IDs/actions are retained.
- Real browser checks: 390px mobile, 1275px desktop and 2560px wide settings; light/dark and 1.1 app scale; long Chinese labels wrap without shifting controls. At 1.1 scale, form width stayed 844.8px (48rem scaled) at both desktop widths. First ten controls share the same right edge.
- At 390px and 1.1 scale the picker stays within x=8..382 and document width remains 390px. This is responsive viewport testing, not a physical-device/PWA claim.
- Search by connection, Enter, arrow traversal across groups, comparison selection of two same-name models with different binding IDs, Escape and returned trigger focus exercised. Search initially retained the old selection; corrected so Enter selects the first search result. IME key handling is guarded but a real IME session has not yet been tested.
- Latest-source production build passed (Node22,8GB heap);16 existing frontend tests pass; no new type/a11y diagnostics (5,870 upstream remain). Native provider/22-surface/PWA/production acceptance remains separate.
- Private before/after screenshots and exact measurements are retained outside the public repository. Original light theme, default app scale and primary model were restored after checks.

Design references used for row and grouping semantics:

- [GNOME SwitchRow](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.SwitchRow.html)
- [Carbon toggle guidelines](https://www.carbondesignsystem.com/building-blocks/core/components/toggle/guidelines)
- [WAI grouped listbox](https://www.w3.org/WAI/ARIA/apg/patterns/listbox/examples/listbox-grouped/)

These sources informed hierarchy and interaction; they do not replace testing this implementation or constitute a full accessibility audit.


### U22 review fixes

- All8reported findings were reproduced before changes:9backend assertions failed and2draft recovery reproductions restored discarded/sent text.
- Catalog APIs now use model-level visibility and remove private request parameters from read-only projections. Administrative connection owners retain management access.
- Edit/copy reconciliation checks the unchanged server source. Native history is matched at the outbound binding/protocol boundary, survives repeated normalization, and is attached to active tool follow-ups, including temporary chats without persisting their text.
- Ollama stream tool indexes/argument conversion and reasoning fields use the existing upstream representation. Claude usage snapshots are observable before completion; EOF, error and cancellation retain confirmed fields without inventing absent values. Native stop, length, blocked, paused and failed outcomes remain distinct in the ledger and UI.
- Recovered draft replacement and retirement of handled versions are atomic in IndexedDB. Concurrent changed candidate versions remain available.
- A real local HTTP tool loop exposed an additional persistence gap: normalized chat_message reads omitted native fields. Migration oi0000000003 adds oi_state, backfills available blob state, and preserves replay state/finish reasons through normalized reads and partial updates. SQLite backup taken before the local migration.
- Synthetic HTTP acceptance through the full OI stack: two tool rounds plus final reply yield3calls/3persisted envelopes; a new follow-up loaded from storage still replays signatures. Edited Save As Copy sends EDITED and does not add charges. Repeated requests during and after completion return409without modifying history.
- Generation reservation now precedes placeholder/chat writes; fan-out reservation is transactional and stable for retries. Final generation status inherits non-complete provider-call outcomes.
- Browser shows the truncation notice and max_tokens source after reload.86backend tests/20frontend tests passed; type gate has no new diagnostics (5866upstream remain). Latest frontend production build is pending these changes.
- This is local synthetic verification. Real-provider certification, physical PWA termination, full22surface coverage, PostgreSQL recovery and production deployment remain separate gates.

Protocol stop-reason references: [Claude](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons) and [Gemini](https://ai.google.dev/api/generate-content#FinishReason). No automatic continuation/retry was added for paused or truncated results.


### PostgreSQL synthetic upgrade and restore rehearsal

- Local official PostgreSQL17 container, separate loopback port and synthetic data only. Full upstream migration chain reaches v0.12.3's head, then OI migrations1–3. Historical chat JSON and normalized message reads remain usable.
- Found an actual driver difference: successful psycopg async INSERT returned rowcount=-1. insert_once now uses RETURNING; draft/connection/annotation CAS writes use explicit returned rows.
- Six concurrent draft writers have one winner; six concurrent identical generation reservations have one winner and five409responses.
- Verified6,000Chinese draft characters, original encrypted test credential decryption, native signature replay, actual0.03+0.04+0.05=0.12 and fork+0.02=0.14. Dump restored to another database;12relevant tables have identical record counts and content hashes.
- Also reproduced snapshot timestamp ties selecting an older fee boundary (0.03instead of0.12). Snapshot selection and listing now order by ledger boundary first. Regression added.
- scripts/oi/postgres-acceptance.py and the backend CI job repeat migration/restore checks. Remote CI has not run; this is not a production-copy or release acceptance claim.

### U22: durable queue acceptance

Queued submissions now share the conversation draft record. Each entry keeps its model bindings, controls, tools, user/response IDs and idempotency key. Acknowledgement removes only the accepted immutable queue version; new composer input and other queued entries survive. Queue-only writes do not change the composer version. Local IDB commit precedes clearing the composer. Conflicting edits remain explicit. Temporary queues remain on the device.

Local browser acceptance: enqueue two models during a held synthetic response, continue typing immediately, reload, release the response, and observe the two queued replies with the original model bindings while retaining unsent text. Replaying an already accepted queue entry reconnected to the existing response: 3 generations and 8 calls (including existing background tasks) stayed unchanged; the full message-tree hash stayed identical. Unit tests cover lost acknowledgements, retyped identical text, local/remote conflicts, temporary drafts and selective queue acknowledgement. Physical PWA and cross-browser acceptance remain pending.

### U22 archive roundtrip

Local HTTP export/preview/import restored 9 messages, 2 revisions, the original attachment bytes and reading annotations. Source and imported totals/call IDs matched. Global call rows remained 26 before and after repeated imports; retrying the same import ID returned the same conversation. Browser settings preview, restore navigation and conversation-menu ZIP download also passed. 97 backend and 29 frontend tests passed; the latest type gate adds no diagnostics (5862 upstream remain). PostgreSQL archive extension and latest production frontend build remain pending.


## 2026-10-26 stage checkpoint

- Confirmed review findings CR01–CR08 remain fixed; full customization and production acceptance are not complete.
- 107 backend tests and 29 frontend tests pass. Type/a11y gate adds no diagnostics; 5,862 upstream diagnostics remain. Latest production build is deferred at this stage checkpoint; an earlier 8 GiB build passed.
- Connection-specific Embedding/rerank, TTS/STT, images and Realtime paths implemented. Synthetic HTTP/WebSocket verifies correct connection credentials/upstream ID, one TTS call across a cache hit, disabled connection rejection before cache, separate Realtime/ASR calls, and no billing metadata in provider payloads. Ten focused capability tests cover malformed responses retaining confirmed usage, duplicate model IDs, native embedding ordering, permission/429 boundaries and modality pricing. Broader settings UI and all 22 surfaces remain open.
- PostgreSQL 17 migration/backup/restore includes archive restore, native state, encrypted credentials, 6k-character drafts, concurrent CAS/reservation and fixed fork baselines. Twelve table content hashes match after restore. This is synthetic local evidence, not a rehearsal against a production database copy.
- Archive restoration repairs missing stored attachment bytes; asynchronous file previews cannot replace a newer selection. Legacy JSON import baseline runtime acceptance remains open.
- No production deployment or implementation push. Physical PWA, real providers, full authentication matrix, all model surfaces, immutable CI image and isolated GitOps preview remain required.


## 2026-10-26 source submission

Current stage is submitted as source only. Pre-commit checks cover the full changed-file inventory, credential/private-runtime pattern scan, excluded local/private assets, and whitespace errors. Prior stage evidence remains 107 backend and 29 frontend tests, no additional type/a11y diagnostics, and local synthetic HTTP/WebSocket/PostgreSQL acceptance. Latest production build, real providers, physical PWA and full-plan acceptance remain pending. No cluster deployment is part of this submission.


## 2026-10-26 stage 2 — capability selectors and retrieval boundaries

Audio/Images/Documents use bounded48rem forms and connection-qualified collapsed model labels. Desktop and390px layouts verified; TTS connectionB persists after reopening and HTTP readback. Native realtime transcription options stay on the same connection. Document selections were previewed without saving retrieval settings.

Specialized-only endpoints are excluded from conversational candidate stores and Arena selection; unknown legacy chat models remain selectable. Endpoint capabilities are distinct from workspace tool toggles and propagate through presets. Backend rejects specialized chat dispatch with422 before provider calls. Saved unavailable selections remain visible rather than becoming default substitutions; rejected send preserves draft and creates no generation/call.

Native/hybrid retrieval propagates HTTP errors instead of retrying through a fallback. Concurrent real retrieval adapters plus a synthetic HTTP transport and SQLite ledger verify six embedding/rerank calls retain their own generation/binding. Real vector-store upload/search remains unverified.

111 backend tests,32 frontend tests pass; no new type/a11y diagnostics (5862 upstream remain); final8GiB production Vite build passes. No deployment or source push in this stage; full22-surface, real provider, authentication/PWA and release acceptance remain open.


## Stage 3 — connection identity and legacy migration

OI5 adds explicit server/browser transport and durable migration receipts. Browser requests reuse native protocol parsing and accounting through authenticated session RPC. Ordinary catalogs/settings/profile serialization exclude direct credentials; the owner-only browser configuration endpoint is no-store. Soft removal preserves historical identities; server copy verifies reachability, returns an explicit mapping and keeps the source.

Local acceptance: 122 backend and 35 frontend tests; no new type/a11y diagnostics (5,798 inherited diagnostics remain); final 8 GiB Vite production build passes. Tests cover stale preview, duplicate-source acknowledgement, late transactional rollback, retained user overrides, credential encryption, duplicate discovery, preset/legacy alias collision, partial usage, HTTP429, UTF8 fragments and cancellation. Real local browser HTTP proves A/B/server same upstream IDs use their own bindings and execution locations; rename/reorder/removing A/reopening a B draft preserves B. HTTP permission probes reject another user's catalog/config/call access. A 429 makes one provider request; browser disconnection retains123 confirmed input tokens with failed status and unknown cost. Existing interactive title/follow-up calls remain attributed; independent tasks require server transport.

Synthetic PostgreSQL17 OI5 migration exercises six concurrent draft writes, generation reservations, identical migration applies and server copies. Final backup/restore verification is tracked separately below. This is not a production-database-copy rehearsal. Desktop/mobile390/430,1.1 application scale and keyboard selection were exercised; wide-screen2560 overflow metrics passed. Loading migration dialogs have an always-focusable close action.

Prior source CI failures were verified: Python's standard-library `test` package shadowed namespace imports on Linux; ANSI-colored svelte-check output defeated diagnostic parsing. Explicit project test packages and ANSI normalization fix the harness, with a parser regression test. Actual CI for this stage is pending source push; local success does not imply CI success or deployment.

Remaining full-plan gates: all22 entrypoint call/permission evidence, real vector indexing/search, full auth/PWA matrix, real provider/CPA native matrix, cost/archive edge cases and isolated GitOps preview/release. Browser direct currently preserves the existing OpenAI Chat/Responses protocols; other native protocols use server transport. Unsupported legacy proprietary engines stay explicit compatibility/manual items. No production deployment.

Final PostgreSQL17 OI5 dump/restore: all15 table counts/content hashes match, including users, configuration and migration receipts; six concurrent migrations produce one receipt/two browser connections, and six server-copy retries produce one mapped server copy.
