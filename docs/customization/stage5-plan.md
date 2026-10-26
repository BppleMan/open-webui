# Stage5: authentication and durable drafts

Scope: complete stage5 with real built-in browser and isolated HTTP/DB evidence. Physical PWA testing is explicitly waived by the user; do not claim it passed. Production deployment remains excluded.

| ID | Acceptance |
| --- | --- |
| A01 | Active session renews before expiry; auth_time/MFA retained; cookie flags/no-store |
| A02 | Old tab and delayed success/401 never overwrite or clear newer login |
| A03 | Offline/502 on startup, refresh and authenticated requests preserve credentials and current URL |
| A04 | Password change, account/session revocation and MFA policy/factor change reject old sessions |
| A05 | Expired session recovers inside current page; draft and open response editor survive |
| D01 | New unsent chat allocates stable ID; >5000 Chinese chars persist across independent browser storage |
| D02 | Close/reopen and reload recover server and unsynced local input |
| D03 | Chinese IME composition cannot accidentally submit or lose the composing text |
| D04 | Concurrent tabs/origins produce visible CAS conflict; either resolution keeps chosen version without resurrection |
| D05 | Offline/502 reconnect synchronizes pending input without overwriting remote edits |
| D06 | Sending/queued acceptance clears only submitted version; input typed during request survives |
| D07 | Interrupted uploads preserve text and attachment references; stalled uploads have explicit recovery |
| D08 | Temporary chats recover only locally and never create a server draft |
| D09 | Different accounts cannot read/write drafts or replay previous account local work |
| D10 | PostgreSQL concurrent writes/acknowledgement preserve unique versioned records |

Finish: focused regressions, complete backend/frontend suite, type delta, production build; browser evidence per relevant path; push and verify CI/image. Keep implementation/verification/deployment separate and hand off stages6–8.
