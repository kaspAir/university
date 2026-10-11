# Phronesis Research Campus: development handoff

## Read first
1. `docs/ARCHITECTURE.md` (architecture proposal V0.1, based on constitution V0.2).
2. `docs/CONSTITUTION_V0.2.md` (extracted text, with source hash; the original DOCX is authoritative).
3. `docs/ACCEPTANCE.md`, `contracts/README.md`, `config/README.md`.
4. `contracts/*.schema.json` and synthetic fixtures.

Implement in German-facing UI language, with English code identifiers. Preserve the distinctions and test IDs of the constitution. Do not silently simplify normative or technical requirements.

## Delivery order
S0: domain contracts, migrations, local real authentication, authorization, policy, audit/outbox, budget and mock adapters.
S1: real configurable multi-provider gateway + task routing + distinct-model review. Two real approved models are a gate, not merely two mock labels.
S2: one durable research flow from approved mandate to versioned sources, claims, review and dossier.
S3: specialized agent/skill candidates, independent qualification, automatic activation and complete registry.
S4: actually isolated execution and separately protected evaluation material.
S5: the controlled research pilot and independent scientific evaluation.

For each slice provide code, migrations, unit/integration tests, run instructions, known gaps and the next decision. First propose the repository structure and the smallest complete S0 slice. Do not attempt to implement all institutes in a single unreviewable patch.

## Non-negotiable implementation boundaries
- Agents are profiles; runs, roles, skills, models and deployments are separate objects.
- Domain code never contains provider model names or provider keys. Only gateway adapters import provider SDKs.
- LLM selection is autonomous among qualified, authorized candidates, with a recorded pre-call rationale and alternatives. The trusted core authorizes the route.
- Selector bootstrap is bounded; no recursive self-selection loop.
- No invisible cross-model fallback or false review independence through aliases.
- Required review capacity is budgeted before substantive generation.
- Untrusted code is never imported into the API/worker process. A missing sandbox disables that capability, not the guard.
- Open public research retrieval is allowed by policy without per-domain human approval. SSRF, data export and all subrequests remain controlled.
- External sources are data, never new instructions or privileges.
- New agents/skills can be activated automatically after independent, scoped qualification under unchanged authority.
- Permissions, protected tests and evaluation thresholds cannot be changed by the candidate being evaluated.
- Claims, evidence, work status and publication authorization remain separate.
- Real experiments, synthetic fixtures and model opinions are never relabelled as each other.
- Approval binds exact artifact version, purpose and external target. No autonomous publication without the specified human approval.
- No hidden exception swallowing. Unknown invocation outcome and unsettled cost are first-class states.
- Never claim exact replay of nondeterministic external models or exactly-once delivery without real support.

## Proposed repository
```
apps/api/                    # FastAPI transport, auth, commands
apps/web/                    # React/TypeScript UI
workers/research/            # durable workflow activities
services/llm_gateway/        # approved provider adapters
services/retrieval/          # fetch/browser/ingestion
services/sandbox_controller/ # isolated job execution
packages/domain/            # research, claims, registry, review, policies
packages/contracts/         # typed contracts and compatibility tests
packages/infrastructure/    # persistence, blob store, telemetry
migrations/                 # Alembic, one controlled schema chain
config/                     # templates only; no credentials
skills/                     # versioned declarative packages, not core imports
tests/unit/
tests/integration/
tests/acceptance/
tests/adapter_contracts/
tests/security/
docs/adr/
```

## Quality and reporting
Use the documented T01-T17 and A01-A14 cases. An unimplemented test is not passed. Keep scientific evaluation separate from software acceptance. Use real Postgres for integration tests (not a SQLite substitute). Record pinned dependencies and container digests after actual testing; do not invent verified versions.

No secret, private source, account purchase, online publication, production deployment or repository deletion is authorized by this file. The engineering plan authorizes development proposals; operating credentials and research mandates are separate decisions.

---

## Repository-specific additions (kaspAir/university)

The text above is the unchanged development handoff from `00_Basisdokumente/Phronesis_Campus_V0.1/CLAUDE.md` (hash-manifested). The additions below bind it to this repository.

- **Normative sources** live under `00_Basisdokumente/` and are never edited. Cite them by article/chapter.
- **Proposal and progress:** `docs/S0_VORSCHLAG.md` is the slice proposal the handoff asks for; implementation decisions go to `docs/adr/`.
- **UI:** `design_handoff_phronesis_campus_ui/` is the binding design handoff (Modernist tokens, views, non-negotiable UI rules in its README §6.5). Target: React + TypeScript; implemented after S0 core.
- **Language:** German (CH spelling, no ß) for UI, docs and user-facing errors; English for code identifiers and code comments.
- **Internal code names** of other products of the suite (names of the manifestos' tools) appear only in the basis documents. Do not use them for packages, identifiers, hostnames or documentation of this product. The product is «Phronesis-Forschungscampus».
- **No real persons or customers** in seeds, fixtures or examples; the five institutes from the constitution are the only seeded master data.
- **Database:** PostgreSQL 16 for development, integration tests and CI. No SQLite substitute.
- **Secrets:** only via environment variables / `.env` (ignored by git). `config/` holds templates with providers disabled.
- **CI:** Jenkins (`Jenkinsfile`, `docs/JENKINS.md`). Deploy stages stay disabled until the target host is decided (see `docs/S0_VORSCHLAG.md`, gap L2).
- **Commits/pushes** are made by the project owner, not by the development assistant, unless explicitly asked.
