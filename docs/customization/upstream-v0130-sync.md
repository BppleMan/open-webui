# v0.13.0 synchronization — R77 / P62

Official source: c38885a1a737da4968f157092f304f3b5481d0bf, merged from v0.12.5 with customization lineage intact. Twenty-nine conflict files are resolved; local compatibility checks pass: 2088 backend / 93 frontend tests, no new type diagnostics (5716 remain), build, three SQLite migration entry points and native HTTP/SideChat checks. Exact-source CI, PostgreSQL merge verification, built-in browser and controller review remain pending. Publication is gated on the controller review.

- Preserve official workspace tabs, side chat, comments/inbox, model-targeted subagents, MCP Apps and request/response conversions.
- Join OI7 and the three upstream revisions at OI8; do not rewrite released migrations. Validate old OI7, pure upstream and empty database paths, plus restore.
- Keep authenticated temporary context/copy transfer, model ACL and connection identity, separate call accounting, explicit compaction and provider error termination.
- Keep widget-only MCP data in UI embeds; model context uses the public tool context even after postprocessing failure/cancellation.
- SideChat intentionally retains the official user-invoked context-boundary instruction. Archives visibly state that the v1 format excludes independent comments, reactions and notifications.
- Pre-sync findings CTRL-PRE-01..03 are separate historical review inputs and must be verified on the merged source.

The pure upstream baseline has 5913 diagnostics and is byte-identical across independent Node 22 and Node 24 checkouts. No customized source was used to expand the baseline. Passing an incremental gate is not a clean typecheck. A new controller review remains required; this merge is not production authorization or a declaration that reviews are cleared.
