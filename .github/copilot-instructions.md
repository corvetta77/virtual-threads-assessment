# Copilot Instructions

## Response Style
- Be extremely concise. Prefer 1-3 sentences. No preamble or summaries.
- Return only the minimum tokens needed to answer.
- No markdown headers unless the response has multiple sections.

## Terminal & Commands
- Do NOT run commands automatically. Instead, provide the command for the user to run.
- Do NOT poll terminal output, tail logs, or run background processes.
- Do NOT use async terminal execution or check terminal output after the fact.
- Output commands as a single code block the user can copy and run.

## Forbidden Actions
- No auto-polling or waiting for async results.
- No opening browser pages or screenshots unless explicitly asked.
- No checking out logs or monitoring processes in the background.
- No running tests or builds unless explicitly asked.
