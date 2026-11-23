---
name: Implementer (fallback)
description: Fallback for Implementer on Platform Lab via agentgateway (Qwen 3.8 Flash on the Spark). Use only while the Claude subscription is at its usage limit. Implements one approved, scoped change on its task branch, runs the relevant checks and opens a pull request. Does not delegate.
harness: opencode
provider: agentgateway
model: platform-lab-qwen38-flash-spark
wire_api: openai-chat
context: 262144
max_output: 8192
prompt_from: implementer.md
---
