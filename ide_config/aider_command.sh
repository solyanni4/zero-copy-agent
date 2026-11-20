#!/usr/bin/env bash
# ⚡ Запуск Aider с zero-copy-agent шлюзом
aider \
  --openai-api-base http://127.0.0.1:8000/v1 \
  --openai-api-key sk-zero-copy \
  --model openai/gemini-advanced
