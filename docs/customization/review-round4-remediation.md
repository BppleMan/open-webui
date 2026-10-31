# Fourth review remediation — R66 / P51

The six findings are independently reproduced and repaired together. Original review reports remain historical evidence; local verification, CI and preview publication are distinct states.

- **CR4-01**: native response access follows the recorded call actor and connection configuration owner, not chat ownership or an edited message author alone. Ordinary responses, shared reads and JSON exports use the same boundary. New saved envelopes also retain caller/configuration provenance. Full server records remain private.
- **CR4-02**: forks, shared clones and archives use a detached protocol-output projection. Structured assistant blocks, media, tools and signatures survive; top-level private instructions, request configuration and continuation handles do not cross ownership boundaries. Model permissions and same-binding replay checks still apply.
- **CR4-03**: saved and temporary outlet changes invalidate native state. Text-only rewrites update structured prose while retaining tools and media; output-only changes also reach the browser. Unchanged responses keep their native state.
- **CR4-04**: valid native media makes a turn nonempty in both database loading and canonical reconstruction. Pure-image replies reach the next request, including after a non-owner fork.
- **CR4-05**: chat and specialized Gemini image calls use one metering projection. Observed media without sufficient modality classification stays unknown; metering stores only the classification marker, never media bytes.
- **CR4-06**: a temporary save captures its source before the first await and checks a navigation epoch, manager and user after each asynchronous boundary. Global mode/ID updates and navigation occur only after the last guard. An obsolete operation can finish its own cleanup without redirecting the current page.

Validation uses synthetic HTTP/socket providers, isolated PostgreSQL and the built-in browser. A previous unit test's fake ChatResponse was removed so its existing Save As Copy assertions now exercise real response serialization. No schema migration or retrospective history rewrite is introduced. R50 and the separate R59 supplier gap remain in effect.
