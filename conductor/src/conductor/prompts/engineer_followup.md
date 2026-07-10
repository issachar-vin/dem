The reviewer and/or QA found problems with your work on ticket `{ticket_id}`. Address every finding
below, then commit the fixes to the same branch (`ticket/{ticket_id}`) with conventional commit
messages. Do not push or open a PR — the conductor handles that.

## Findings to address

{findings}

## Rules

- Fix the substance of each finding; do not merely suppress it.
- Re-run the repository's test suite and make it pass before you finish.
- Stay in scope — only change what these findings require.
- Leave the working tree clean (everything committed) when you are done.

When finished, reply with a one-paragraph summary of what you changed in response to the findings.
