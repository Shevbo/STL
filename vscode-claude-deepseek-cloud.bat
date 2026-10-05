@echo off

set ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
rem Do not store the key here: the repo was public and the key leaked (15.07.2026). Taken from Windows env DEEPSEEK_API_KEY.
set ANTHROPIC_AUTH_TOKEN=%DEEPSEEK_API_KEY%

set ANTHROPIC_MODEL=deepseek-v4-pro
set ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-pro
set ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-pro
set ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-pro

start "" code