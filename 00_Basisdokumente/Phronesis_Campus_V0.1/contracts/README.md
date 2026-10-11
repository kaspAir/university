# Initial contract set (design version 1.0)

These schemas define initial document-level contracts, not a full database schema or production API. They validate syntax; they do not authorize actions, prove identity, establish scientific competence or enforce safety by themselves.

- `model_catalog`: administrative registry snapshot. Enabling is separate from qualification.
- `task_need`: task requirements derived from mandate, data scope and skills.
- `route_proposal`: LLM-generated selection among eligible IDs; no approval or budget fields.
- `routing_decision`: persisted audit record AFTER proposal and trusted-core validation. A model cannot submit `state=authorized` as authority.
- `agent_profile`: declarative candidate with requested capabilities. Actual grants are separate trusted objects.
- `skill_manifest`: declarative package metadata, not executable core privileges.

References such as `qualification_refs`, `reservation_ref` and `pricing_ref` must resolve to real, versioned, authorized records in the implementation. The fixture IDs under `examples/` are intentionally synthetic and must not enter a research run.

The implementation should make OpenAPI and domain schemas the operational source of truth, test migration compatibility, and update these design contracts through review. Unknown extra properties are rejected.
