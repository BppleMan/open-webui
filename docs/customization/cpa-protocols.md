# CPA client-facing protocols

CPA is the gateway. Antigravity (AGY), Codex and other credentials are upstream channels. The protocol used by OI to call CPA is a separate choice; a model name alone does not identify that route.

| Client protocol | API base | Main endpoints relative to the base |
| --- | --- | --- |
| OpenAI Chat Completions | `/v1` | `POST /chat/completions`; legacy `POST /completions` |
| OpenAI Responses | `/v1` | `POST /responses`; `GET /responses` for WebSocket; `POST /responses/compact` |
| Anthropic Messages | `/v1` | `POST /messages`; `POST /messages/count_tokens` |
| Gemini GenerateContent | `/v1beta` | `POST /models/{model}:generateContent` and `:streamGenerateContent`; `GET /models` |
| Gemini Interactions | `/v1beta` | `POST /interactions` |

Additional registered surfaces include image generation/editing, speech, video and Realtime. Their availability depends on the selected upstream, model and server version; registering a route does not prove full feature compatibility.

R52 confirms `/v1beta` as the default address suffix for the **CPA / AGY native Gemini template**. The form shows this suffix and explains the address behavior. Existing `/v1` input for that mode is normalized before discovery/generation while retaining a reverse-proxy prefix. No failed request is retried against another protocol. Explicit OpenAI Chat/Responses requests remain on `/v1`; do not generalize the CPA/AGY normalization to all gateway traffic.

Verification on 2026-10-29: local CPA route registration was inspected; no-credential live probes reached authentication for the listed protocol routes, while the incorrect `/v1/models/{model}:generateContent` route returned 404. The native Gemini route also completed one bounded synthetic OI generation with usage recorded. Other capabilities were not all re-tested in this refinement. Existing protocol acceptance records remain scoped to their tested suppliers. The server-side compaction endpoint is not enabled automatically in OI; the default complete-history policy remains unchanged.

## R53 address-entry update

R53 replaces manual `/v1beta` entry with a service root. The Gemini protocol resolver adds `/v1beta`; legacy versioned addresses remain compatible. CPA RP behavior is a persisted compatibility profile, independent of the four common wire protocols.
