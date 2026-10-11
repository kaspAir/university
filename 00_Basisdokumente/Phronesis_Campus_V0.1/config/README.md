# Configuration boundaries

`model_catalog.example.yaml` is a disabled configuration template, not a list of automatically authorized providers. `routing_policy.example.yaml` is a draft. Neither is a runnable application configuration.

To activate a real deployment: configure its native model ID, canonical identity, adapter revision, data-processing profile, observed capabilities, qualification scope, current pricing and secret reference. Test the adapter and establish operating limits first. Secrets must never be in prompts or exports. Provider discovery is not authorization.

The user-selected development model (Claude Fable 5.1) is separate from the campus runtime. No runtime model ID is inferred from the development tool.

The constitutional requirement is actual multi-model work and cross-model review of substantive artifacts. Native SDK retries/fallbacks must not silently substitute another model or data-processing location. Choose again through the campus router.
