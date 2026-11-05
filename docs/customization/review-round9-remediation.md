# Ninth review remediation

This batch repairs one early authentication defect, two R70 replay regressions and one Arena integration omission. It preserves the previous review evidence and separates source, checks and preview delivery.

- A stable login `sid` links refresh siblings while each JWT still receives a new `jti`. Logout revokes that family, including older and concurrently issued tokens, while independent logins remain valid. Socket disconnection is scoped to the login; native voice refresh checks the same identity. Authentication age, MFA and account session stamps remain unchanged. Legacy tokens use their original `jti` when beginning a renewal chain.
- Native tool-boundary detection covers all seven protocols. Lookahead respects tool/result boundaries before consuming preceding reasoning. Canonical cancellation still controls pruning; opaque-only reasoning is retained even when its visible projection is empty.
- The live tag parser is extracted without changing its state machine. History matching reuses it and accepts only a visible-text match accompanied by the recorded reasoning/attributes. Whole-frame, tag-boundary and witnessed-block chunk projections cover chunk-sensitive parsing. Genuine edits still invalidate native state and altered recorded reasoning is rejected.
- Arena retains the wrapped response status and end-to-end headers, including the native call ID. Actual routing, accounting, consumer/tool execution and subsequent history/capsule recovery are tested for direct, selected-Arena and fallback-Arena paths.

Authentication retains the existing Redis availability policy. Session-family logout markers intentionally have no token-derived TTL: a shorter-lived token must not resurrect a longer-lived sibling when its revocation entry expires. They contain only opaque login identifiers, not JWTs. Already independently issued pre-lineage JWTs cannot be retroactively linked; no account-wide revocation or migration is performed.

All new fixtures use synthetic accounts and MockTransport or loopback suppliers. Cross-feature tests also retain fork/archive/late-settlement controls. Exact validation and delivery status are recorded in review-round9-remediation-acceptance.json. R50/U49/R59 remain; production is unchanged.

Built-in-browser acceptance additionally found a generated image sent twice after a model switch: one stored-file URL and one native inline fallback resolved to the same data URI. Assistant media now deduplicates after authorized URL hydration; distinct images, detail settings and user-message multiplicity remain. The real HTTP media regression asserts exactly one image, alongside four focused cases.
