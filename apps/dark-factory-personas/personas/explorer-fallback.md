---
name: Explorer (fallback)
description: Fallback for Explorer on Platform Lab via agentgateway (Qwen 3.8 27B on the Macs). Use only while the Claude subscription is at its usage limit. Read-only codebase exploration; reports paths, behavior and risks as an artifact. Never changes files and opens no pull request.
harness: opencode
provider: agentgateway
model: platform-lab-qwen38-macs
wire_api: openai-chat
context: 262144
max_output: 8192
prompt_from: explorer.md
---
