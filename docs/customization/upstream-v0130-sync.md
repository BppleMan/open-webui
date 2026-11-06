# v0.13.0 synchronization — R77 / P62

Official source: c38885a1a737da4968f157092f304f3b5481d0bf, merged from v0.12.5 with customization lineage intact. Twenty-nine conflict files are resolved; local compatibility checks pass: 2088 backend / 93 frontend tests, no new type diagnostics (5716 remain), build, three SQLite migration entry points and native HTTP/SideChat checks. Exact-source CI, PostgreSQL merge verification, built-in browser and controller review remain pending. Publication is gated on the controller review.

- Preserve official workspace tabs, side chat, comments/inbox, model-targeted subagents, MCP Apps and request/response conversions.
- Join OI7 and the three upstream revisions at OI8; do not rewrite released migrations. Validate old OI7, pure upstream and empty database paths, plus restore.
- Keep authenticated temporary context/copy transfer, model ACL and connection identity, separate call accounting, explicit compaction and provider error termination.
- Keep widget-only MCP data in UI embeds; model context uses the public tool context even after postprocessing failure/cancellation.
- SideChat intentionally retains the official user-invoked context-boundary instruction. Archives visibly state that the v1 format excludes independent comments, reactions and notifications.
- Pre-sync findings CTRL-PRE-01..03 are separate historical review inputs and must be verified on the merged source.

The pure upstream baseline has 5913 diagnostics and is byte-identical across independent Node 22 and Node 24 checkouts. No customized source was used to expand the baseline. Passing an incremental gate is not a clean typecheck. A new controller review remains required; this merge is not production authorization or a declaration that reviews are cleared.


## Post-sync review repairs

The controller review of `fdc34863fdda0a474554afb42a101f20c86e33e1` confirmed four findings. The local repairs preserve accepted side-task cancellation, integrate native model comments with owner/permission/list/count/hydration handling, reject unsuccessful summary terminal states before checkpoint writes, and use safe portable native context for side conversations. Public regression fixtures cover these paths and permission revocation.

Repair-workspace validation: 2103 backend and 94 frontend cases passed, with zero new type diagnostics (5716 existing). The original merge candidate CI and three-entry PostgreSQL migration/restore checks passed separately; these do not approve the newer repair source. Fixed-source re-review, updated CI and built-in-browser acceptance remain pending. The previous R76 preview remains unchanged.


The second independent review of `e8ad892dbd5e49a4d489b27a3b079d274aee6991` closed the four typical first-round cases and found one remaining cancellation boundary (CR16-01): a truncated acceptance receipt can hide an active server task behind a local error. SideChat now tracks attempted message dispatches until authoritative terminal events, independently of local `done`. Nineteen lifecycle/API-parser scenarios and 95 frontend tests pass locally. No backend behavior changes are included in this follow-up; fixed-source review and browser fault injection remain separate gates.
