# Meeting Copilot Core

Platform-neutral Copilot core for PC Local Web MVP and later desktop shells.

Current scope:

- Build a session snapshot from transcript report, LLM analysis and state events.
- Preserve EvidenceSpan references for formal states and suggestion cards.
- Apply suggestion card status changes.
- Export an evidence-backed Markdown report.

Run tests:

```bash
cd code/core
pytest -q
```

