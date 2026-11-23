---
name: QA (fallback)
description: Fallback for QA on Platform Lab via agentgateway (Qwen 3.8 27B on the Macs). Use only while the Claude subscription is at its usage limit. Independently verifies an implementation with proportionate checks and reports exact commands and outcomes. Never changes source files.
harness: opencode
provider: agentgateway
model: platform-lab-qwen38-macs
wire_api: openai-chat
context: 262144
max_output: 8192
prompt_from: qa.md
---
