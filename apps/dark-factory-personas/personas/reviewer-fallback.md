---
name: Reviewer (fallback)
description: Fallback for Reviewer on Platform Lab via agentgateway (Qwen 3.8 Flash on the Spark). Use only while the Claude subscription is at its usage limit. Independent read-only review of an implementation for correctness, regressions, security and missing tests. Never changes files.
harness: opencode
provider: agentgateway
model: platform-lab-qwen38-flash-spark
wire_api: openai-chat
context: 262144
max_output: 8192
prompt_from: reviewer.md
---
