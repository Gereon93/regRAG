# regRAG

## Stack & Conventions
- Stack: Python + FastAPI + ChromaDB + DeepEval
- Tests: `pytest`
- Lint: `ruff check .` (ruff==0.16.3, as in CI)

# graphify
- **graphify** (`~/.claude/skills/graphify/SKILL.md`) - any input to knowledge graph. Trigger: `/graphify`
When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.
See `graphify-out/GRAPH_REPORT.md` for architecture hubs and entry points. Update via `graphify update .`.
