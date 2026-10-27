# Stage5 checkpoint — 2026-10-27

User requested a pause after finishing current work. Stage5 remains incomplete. No production deployment. Physical PWA acceptance is explicitly not required; it was not performed.

## Implemented in this checkpoint
- Draft outbox retries on reconnect/visibility/poll, including failed new-chat allocation. Local durable input restores before network reads; edits during loading participate in version checks.
- Restored interrupted attachment uploads retain their references and expose explicit recovery instead of remaining indefinitely uploading.
- Bootstrap ignores stale authentication responses. HTTP renewal updates authenticated Socket.IO credentials; switching accounts disconnects the old socket so its rooms cannot survive.
- Native Realtime accepts only same-human-session renewed credentials, checks current revocation/model access, and does not forward credentials upstream. Newly created native bindings refresh the model registry.

## Verified
- Complete local regression: 154 backend and 43 frontend tests passed.
- Production build passed; no new type/a11y diagnostics, 5781 upstream diagnostics remain (not a clean typecheck).
- Synthetic real HTTP/Socket.IO/Realtime/PostgreSQL/Redis: expiry and refresh, unchanged live socket after old JWT expiry, cross-account disconnect, password/admin/logout revocation, MFA policy/factor changes, 6 competing draft writes with one winner and five conflicts, newer input survives acknowledgement, other-account access denied.
- Built-in browser: 7608 Chinese characters restored identically across two independent origins; offline local input synchronized after reconnect, server revision 2 and saved indicator verified.
- No real provider or physical PWA acceptance claimed. A network-emulation tool stalled; override was cleared and proxy rules reset.

## Remaining before stage5 completion
1. Built-in browser: close/reopen unsynced drafts; Chinese IME; both conflict resolutions; offline/502 startup/refresh; stale-tab responses; inline login recovery with open reply editor; input typed during send/queue acknowledgement; actual interrupted upload; temporary local-only drafts; account switch isolation.
2. Add isolated stage5 CI runner using PostgreSQL and Redis, integrate HTTP acceptance script, verify on fresh state. Do not run the state-mutating harness against production.
3. Re-run relevant checks after fixes, push finished stage5 code, verify actual CI and immutable image digest. This checkpoint is locally committed only; no new CI/image claimed.
4. Update three-layer records and NEXT-SESSION; proceed to stage6 only after stage5 acceptance is complete.

Public scripts: scripts/oi/stage5-http-acceptance.py and scripts/oi/fixtures/stage5_proxy.py. The HTTP harness is independently verified against the isolated local environment; CI automation remains pending.


Superseded by completed stage5 acceptance and stage5-release.json on 2026-10-27. This file preserves the earlier pause checkpoint; its remaining list is historical. Continue stage6.
