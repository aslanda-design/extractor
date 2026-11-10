"""S08 step 5 - Checks by code: does the evidence really back the label the model chose?

Pure code, no model. The model lists quotes and a label; here the engine decides what that is worth:

  located     a quote the engine found in the evidence. An unlocated quote never counts.
  backed      the chosen label has at least one located quote that supports it.
  competing   located quotes also support OTHER labels (an exception, a condition, an amendment, or a contradiction).
              That is not an error by itself (the criteria may make the exception the answer); the judge decides
              and, if it cannot run, the answer is `uncertain` instead of a guess.
"""

from dataclasses import dataclass

from ..contracts.candidate import Stance

PROBLEM_FLAGS = {"quote_not_located", "no_quote"}


@dataclass
class Assessment:
    located: list[Stance]
    dropped: list[Stance]  # quotes that could not be found in the evidence
    backing: list[Stance]  # located quotes supporting the chosen label
    competing: list[Stance]  # located quotes supporting another label
    superseding: list[Stance]  # located quotes marked as replacing earlier wording

    @property
    def competing_labels(self) -> list[str]:
        return sorted({s.label for s in self.competing})


def assess(label: str | None, stances: list[Stance]) -> Assessment:
    located = [s for s in stances if not set(s.flags) & PROBLEM_FLAGS]
    dropped = [s for s in stances if set(s.flags) & PROBLEM_FLAGS]
    return Assessment(
        located=located, dropped=dropped,
        backing=[s for s in located if s.label == label],
        competing=[s for s in located if label is not None and s.label != label],
        superseding=[s for s in located if s.role == "supersedes"])
