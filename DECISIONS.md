# Design decisions

The decisions that shaped this engine, each with the reason and, where there is one, the measurement behind it. The full design
is in [ARCHITECTURE.md](ARCHITECTURE.md). Numbers come from a private corpus of 10 commercial contracts read with a local Qwen3 8B
model; they describe that corpus, not document extraction in general.

## 1. A model proposes; the engine verifies and decides

**Decision.** No answer is accepted because a model was confident. A value must come with a quote, the quote is located in the
source by code, the value is normalized by code, and an acceptance policy owned by the engine gives the status.

**Why.** On the corpus neither the model's self-reported confidence nor the retrieval score separated right answers from wrong
ones. Checks that can be done exactly (is this quote in the document? does "NTD 12 million" equal 12?) should be done exactly.

**Cost.** Some right answers are withheld (`uncertain`) because their quote cannot be located, for example when OCR damaged the
sentence. That is the intended trade: a withheld answer costs a review; a wrong accepted answer costs trust.

## 2. Four statuses, and "not found" has to be earned

**Decision.** `supported`, `not_found`, `uncertain`, `failed`. `not_found` is returned only when the inspection the task
required is complete. A failed model call, an unreadable page or evidence that did not fit can never become `not_found`.

**Why.** "The contract has no termination fee" and "we could not read page 7" are different facts, and a system that merges
them produces the most expensive kind of error: a confident absence.

## 3. Findings are facts; which ones block is a setting

**Decision.** Validation produces findings ("the quote matched only approximately"). Whether a finding blocks acceptance is
decided by a profile (`strict`, `standard`, `lenient`) plus per-category overrides, not by the code that found it.

**Why.** The same evidence has to serve a compliance review and a quick triage. Saved proposals can be re-judged under another
policy in seconds, without calling a model again.

## 4. Records are read exhaustively, never by top-k retrieval

**Decision.** For "list every clause / milestone / definition", the whole scope is read window by window, and an audit
(numbering gaps, numbered text no record covers, failed windows) decides whether the collection may be called complete.

**Why.** A record that retrieval did not return is a record nobody knows is missing. A numbered list is self-checking: the
document says how many items to expect.

## 5. The model points, the engine cuts

**Decision.** In record discovery the model returns block tags and the first words of each record, never the record's text.
The engine finds those words and cuts the span itself. A record's fields are then read from that span only.

**Why.** Text the model did not produce cannot be hallucinated, and a value from one record cannot be assigned to another.

## 6. A second model call may only make the answer safer

**Decision.** The verification step can remove candidates, and the interpretation judge can downgrade a decision. Neither can
add a value or change one.

**Why, with a failure.** A model check of discovered records looked like an obvious quality filter. On the corpus it rejected
75 entries, real records included, and recall collapsed. It is now off by default and kept as an option. A deterministic rule
from the document's own structure (the list ends where its numbering family changes) did the job without that cost.

## 7. Context between records goes through the narrowest channel that fixes the measured cases

**Decision.** A record's evidence starts with one line, its key as discovery found it (for example `12.1(2)`). A general
"previous record" context passage was not added.

**Why.** Identifier fields needed the parent clause number, which is printed above the item. Passing the key fixed that
(field exact 60% to 70%). A general context passage would reopen the leak that reading each record alone prevents. One task
still suffers from this choice (the owner of a "copy" notice block); it is listed as open.

## 8. Recovery goes through the same gate, is bounded, and its risky actions are off

**Decision.** A task that was not accepted gets targeted second tries. Each try runs extraction and validation again. A result
is replaced only by a better one; two attempts that found different values stay `uncertain`. Actions that complete an
inspection (a failed call, omitted evidence, a region the audit named) are on; actions that change how an answer is produced
(a deeper profile, a stronger model) are off until measured.

**Why.** More attempts must not become more chances to accept a wrong answer, and a quality-affecting change is adopted after
it is measured, not because it sounds right (see 6). Status: built and unit-tested; not yet measured on the corpus.

## 9. A retry must change something

**Decision.** A failed model call is retried according to why it failed: a transport error waits and re-sends, bad JSON asks
for JSON only, a cut-off answer asks for a shorter one, a prompt that does not fit is not retried at all.

**Why.** At temperature 0 with a fixed seed, the same prompt returns the same failure. The engine did exactly that for
several phases before this was noticed.

## 10. Model calls are cached by content

**Decision.** A call that produced a usable answer is stored under a fingerprint of the model identity, its limits and the
whole prompt. Failed calls are never stored.

**Why.** A measurement run is hundreds of calls. A crash should cost the missing calls, not all of them, and changing one
prompt should re-pay only that prompt. Cost: a cached run cannot show run-to-run variation, so variation is measured with the
cache off (`eval --repeat N`).

## 11. Threads and a per-adapter limit, not an asyncio rewrite

**Decision.** Independent tasks run in a bounded thread pool. The limit is the smaller of a setting and what the model
adapter says its backend can serve.

**Why.** The work is waiting on HTTP calls, where threads and asyncio give the same speed-up; asyncio would have changed
every adapter and pipeline signature. One local GPU serves one call at a time, a hosted API many: only the adapter knows.

## 12. YAML says what; runtime configuration says how

**Decision.** The extraction specification cannot name a model, a threshold or a depth; the compiler rejects it.

**Why.** A specification should mean the same thing on a laptop model and on a hosted one, and a model swap should never
require touching a specification that a domain expert wrote.

## 13. Corrections are stored beside predictions, never over them

**Decision.** A review (accept, correct, reject) is appended to a file next to the run, with a snapshot of what the engine said. The run is never edited, a review of a
prediction that has since changed is shown as stale and not applied, and the view a person ends up with keeps the engine's answer beside the reviewer's.

**Why.** The difference between what the engine said and what a person decided is the only measurement of real-world accuracy, and it is destroyed by overwriting. Reviews
can then be exported as evaluation labels (only human decisions: the engine's answers never grade themselves).

## 14. Documents stay on this machine unless someone says otherwise

**Decision.** A model reached through a host that is not loopback is refused unless `privacy.allow_remote_models: true`, and a run lists which capabilities went remote.

**Why.** The engine is built for confidential contracts. A hosted model is a legitimate choice only when it is a deliberate one, and the result should show it happened.

## Known limits

- One document family (commercial contracts), ten documents, one model measured.
- Fixes were tuned on the same documents they were measured on; held-out numbers are reported separately from now on.
- Re-OCR of a damaged page as a recovery action is not built.
- Interpretation tasks return labels only; free-text answers with claim-by-claim citations are not built.
- Nested records are in the contract and not implemented.
- Review of one record or field is available from the command line and the API; the web screen reviews whole answers.
- No authentication or multi-user service: the review screen is a local tool.
