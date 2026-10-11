# Phronesis Research Campus - architecture handoff V0.1

This package is a design and contract handoff for development with Claude. It is NOT an implemented research campus.

Start with `CLAUDE.md` and `docs/ARCHITECTURE.md`. The editable Word architecture and the original constitution DOCX are also included under `docs/`. The Markdown contains the same primary architecture content. The original constitution DOCX is the normative source, while the extracted Markdown makes it easier to inspect during development. Further background sources are identified with checksums in `docs/source_manifest.json`; their original files are not copied into this package.

## Contents
- Architecture and constitutional source text.
- Configurable model catalog and routing-policy templates (real providers disabled).
- Six initial JSON Schemas and synthetic contract examples.
- Mermaid diagram sources.
- Acceptance matrix preserving T01-T17 plus architecture cases A01-A14.
- Contract checker with positive and negative fixture tests.

## Check the examples
```sh
python -m pip install -r requirements-contract-tests.txt
python tests/validate_contracts.py
```
This validates the supplied contract examples only. It makes no API calls and does not test a campus implementation, model capability, security boundary or scientific result. Exact dependencies for a production build must be pinned and tested by the developer.

Open operating decisions include hosting/isolation, real providers/models, data-processing permissions, budgets, independent review roles and the concrete pilot study.
