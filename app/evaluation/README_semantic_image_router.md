# Semantic image intent router evaluation

This package defines the read-only architecture for `SemanticImageIntentRouter`.
Production execution is intentionally **not enabled**: `image_generation.pipeline_v2_enabled`,
`image_generation.pipeline_v2_shadow_mode`, `image_generation.pipeline_v2_production_approved`,
and `image_generation.semantic_router_shadow_mode` all default to `false`.

Selected provider/model for evaluation: Venice `qwen-3-6-plus`. The router uses a single
structured semantic call returning route/action, visual constraints, confidence, ambiguity,
and safety-relevant semantic signals. Deterministic code remains responsible for entitlement,
adult policy, source ownership/TTL, exact resend retrievability, billing, persistence, and provider execution.

Rollout sequence: offline semantic evaluation; read-only route shadow; compare legacy vs semantic
routing; user-scoped canary execution; limited percentage rollout; explicit production approval.

Estimated latency/cost per semantic decision: ~900 ms and ~$0.0007 before provider variance.
