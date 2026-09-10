"""Pi's compact structured-summary policy on ADK's event compaction."""

from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import BaseLlm

# Ported from earendil-works/pi commit 853a80d, packages/agent/src/harness/
# compaction/compaction.ts. ADK owns range persistence; this preserves Pi's
# structured continuation-summary policy at that boundary.
PI_SUMMARIZATION_PROMPT = """The following is a conversation history to compact.
Create a structured context checkpoint that another model will use to continue the work.
If the history contains a previous compaction summary, preserve its still-relevant facts
and update it with the newer messages.

Use this exact format:

## Goal
[What the user is trying to accomplish.]

## Constraints & Preferences
- [Constraints and preferences, or "(none)".]

## Progress
### Done
- [x] [Completed work]

### In Progress
- [ ] [Current work]

### Blocked
- [Current blockers]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered next step]

## Critical Context
- [Data needed to continue, or "(none)".]

Keep every section concise. Preserve exact file paths, function names, error messages,
and file operations. Do not continue the conversation or answer its questions.

{conversation_history}"""


def pi_event_summarizer(model: BaseLlm) -> LlmEventSummarizer:
    return LlmEventSummarizer(model, prompt_template=PI_SUMMARIZATION_PROMPT)


__all__ = ["PI_SUMMARIZATION_PROMPT", "pi_event_summarizer"]
