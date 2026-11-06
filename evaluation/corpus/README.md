# Evaluation corpus (human-labeled)

This is where **you** add the labeled data. Nothing here is generated; ground
truth must be human-reviewed (ARCHITECTURE.md section 10).

```
evaluation/corpus/
  documents/   your source files (pdf, scans, docx, ...)
  specs/       the extraction YAML(s) the cases use
  cases/       one YAML per case: documents + spec + expected answers
  _template/   copy-and-edit examples (ignored by the loader)
```

## How to add a case

1. Put the document(s) in `documents/`.
2. Put (or reuse) a spec in `specs/` - start from `_template/spec.template.yaml`.
3. Copy `_template/case.template.yaml` to `cases/<case_id>.yaml` and fill in the
   expected answer for **every** task in the spec.
4. Run `python -m extractor eval`. It lists every problem at once (missing
   files, specs that do not compile, unknown task ids, tasks without an expected
   result, wrong status/value combinations).

## Labeling rules

- `status` uses the engine vocabulary: `supported`, `not_found`, `uncertain`, `failed`.
- A business answer is a **value** (`value: rejected`, status `supported`), never a status.
- `value` is allowed only with `status: supported`.
- `not_found` means the answer is genuinely absent from the documents. Use it on
  purpose: missing answers are part of what the corpus must test.
- Records tasks need `completeness` and the **full** list of records. Missing a
  record in the labels makes record-recall metrics wrong.
- `evidence` uses document + page + a short verbatim quote, because block ids
  only exist after parsing.

## Cases the corpus should include

Unseen document families and field concepts, missing answers, misleading
alternatives, contradictions, poor scans, amendments, and long repeated lists.
Use `tags` to mark them so results can be broken down later. Keep related
templates/source families together when you decide which cases are held out.

## Scoring

`python -m extractor eval --score` runs the engine over every case and reports
status and value accuracy. Stage-specific metrics are added by later phases.
