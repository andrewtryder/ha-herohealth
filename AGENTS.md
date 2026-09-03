# Hero Health agent guidance

Hero Health is an unofficial integration using a reverse-engineered mobile protocol.
Do not casually change authentication, REST, or WebSocket details; compare against
`~/code/cloudflare-hero` read-only and add a regression test for any protocol fix.

Remote dispense is safety-sensitive. Never commit Hero credentials, tokens, cookies,
account IDs, or real medication data. Use Conventional Commits. Before considering a
change complete, run Ruff, pytest, pre-commit, and applicable validation. The Worker
repository is historical/reference only and must never be modified from this project.
