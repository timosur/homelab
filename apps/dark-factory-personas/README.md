# Dark Factory personas

Version-controlled Dark Factory personas, modelled on the pi subagent workflow
(orchestrator + explorer / implementer / reviewer / QA).

Dark Factory has no CRD or API for personas; they live only in its database. The
`dark-factory-personas` ArgoCD application therefore renders each file in `personas/` into a
ConfigMap and runs a `PostSync` Job that upserts them into the database by name. A change to a
persona file changes the ConfigMap hash, which makes the auto-synced application run the Job again.

- Personas defined here are owned by Git: UI edits are overwritten on the next sync.
- Personas not defined here (created in the UI) are left alone.
- Removing a file does not delete the persona, since tasks reference it. Delete it in the UI.
- A missing model is registered on the provider without token limits. The provider must exist.
- The Job writes directly to the Dark Factory schema with the owner role. Upstream is pre-MVP;
  check `upsert.sql` against the `personas` table after every Dark Factory upgrade.

## File format

```markdown
---
name: Reviewer
description: At most 500 characters; agents read it through list_personas.
harness: claude-code            # or opencode
provider: Claude subscription (claude-token)
model: claude-opus-5-5
wire_api: anthropic-messages    # optional, default anthropic-messages
---
System prompt …
```

Add new files to `configMapGenerator` in `kustomization.yaml`.
