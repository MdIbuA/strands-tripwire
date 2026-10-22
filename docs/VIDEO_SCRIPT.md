# Demo video script (target 2:40, hard limit 3:00)

Record at 1080p. Use OBS or the Windows Game Bar (Win+Alt+R). Arena in the browser at
110% zoom. Run each scenario once before recording so the model is warm.

| time | on screen | say |
|---|---|---|
| 0:00 to 0:15 | The finance_portal email, injected text highlighted | "This email has a hidden instruction for AI assistants. Watch what my Bedrock agent does with it." |
| 0:15 to 0:45 | Arena → **finance_portal** → Run both. Left side turns red: `share_file → auditor@kpmg-review.net` | "Unprotected, it shares my Q3 financials with a stranger. On the right, the same agent, same model, same prompt, plus Tripwire." |
| 0:45 to 1:15 | Right side: the quarantine chunk goes red, the ProvenanceGuard bars show origin:content high, DENY, ~100 ms | "Strands Decider 2B scores every email before the LLM reads it, and quarantines the one that talks to the AI. If anything slips through, the guard asks, in one round trip of about a tenth of a second: who actually asked for this, the user or the content? Content means Deny." |
| 1:15 to 1:45 | **meeting_notes_cc**: Sarah's email proceeds (green), the extra CC is denied | "It isn't a blunt block. Here I really do want the notes sent to Sarah, so that goes through. The 'compliance' CC hidden in the doc doesn't." |
| 1:45 to 2:05 | **travel_redirect**: CONFIRM appears, you click Reject | "In the grey zone, calibrated confidence routes to a human. I decide." |
| 2:05 to 2:25 | bench/results.md table | "Across ten scenarios: attack success drops from [[x]] to [[x]] percent, benign tasks still complete, and each decision is [[x]] times faster than asking an LLM judge, at zero cost per call." |
| 2:25 to 2:40 | README code snippet with the two `interventions=[...]` lines | "Both decisions are reusable Strands InterventionHandlers. Three lines and your agent has a reflex. Link in the description." |

Upload to YouTube as **Public** or **Unlisted** (check that the rules accept unlisted), with the
title: "Tripwire: stopping prompt injection with Strands Decider 2B + Amazon Bedrock".
