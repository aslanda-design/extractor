# Business document extraction corpus — proposed labels

This package follows the supplied evaluation/corpus layout and extraction-spec vocabulary. It contains 10 source PDFs, 10 extraction specs and 10 proposed cases. The 141 top-level tasks comprise 100 values, 30 interpretations and 11 record tasks with 116 individual records. Source documents total 74 pages; each has 6–9 pages. Six inputs are digital PDFs and four are image-only PDFs.

## Review status

**These expected answers were proposed by an AI assistant and have not been human-reviewed. They are not human-labeled ground truth.** They are deliberately placed under evaluation/corpus/_template/pending_review/cases/, which your documented loader ignores. The active cases/ directory is empty. The source PDFs and reusable specs are already in documents/ and specs/.

The 10 original CUAD annotation files are also included for comparison. They are unchanged source annotations, not certification of these new labels. New fields, numeric normalization, decisions, page mappings and record boundaries require review. Do not present this package as a human-reviewed benchmark until that review is complete.

## Contents

- evaluation/corpus/documents/: the ten source PDFs. Nine are byte-identical to the preceding pack; the damaged outsourcing PDF has been rebuilt from the same original source with all seven pages preserved.
- evaluation/corpus/specs/: one YAML spec per document family, with no model settings.
- evaluation/corpus/cases/: empty until reviewed cases are promoted.
- evaluation/corpus/_template/pending_review/cases/: ten proposed case YAMLs.
- evaluation/corpus/_template/pending_review/review/: per-task review checklist and rationale.
- evaluation/corpus/_template/pending_review/REVIEW_WORKBOOK.md: readable labels and evidence, including every record.
- evaluation/corpus/_template/pending_review/coverage.csv: one row per top-level task.
- evaluation/corpus/_template/pending_review/proposed_splits.yaml: tentative document-group train/validation/test allocation.
- evaluation/corpus/_template/pending_review/source_reference/: original CUAD annotations and page-indexed source text for evidence checking.
- evaluation/corpus/_template/pending_review/validate_drafts.py: structural, coverage, source-hash and quote checks.

Do not feed review files, expected answers, CUAD annotations, or reference transcripts into the extractor. They are evaluation-only material. In particular, the reference text for image-only PDFs comes from the corresponding original digital source; providing it to the engine would bypass OCR.

## Use in your repository

1. Merge the evaluation/corpus/ tree into your repository without overwriting unrelated files.
2. Review the assumed case schema below against your actual case.template.yaml and loader. Only the extraction-spec template was supplied; exact case-loader compatibility is unverified.
3. Inspect each source PDF and all its proposed answers using REVIEW_WORKBOOK.md. Review absent labels against the entire supplied PDF; a short quote cannot prove absence. For records, independently enumerate the complete scoped set and check every field, not just the proposed count.
4. Correct case/spec YAML as needed. In review/business_XX.yaml, record reviewer and reviewed_at for every task, change task review_status to approved, and set human_reviewed to true only after all tasks have been reviewed. Keep the review record as provenance.
5. Copy only reviewed case files from _template/pending_review/cases/ into cases/. Remove pending_human_review from their tags. This is a manual promotion step; no script auto-approves labels.
6. Run python -m extractor compile on each spec, then python -m extractor eval and python -m extractor eval --score in your repository.

Run the included draft checks with:

    python evaluation/corpus/_template/pending_review/validate_drafts.py

This helper requires PyYAML. It is an independent package check, not a substitute for your compiler/evaluator. The extractor package was unavailable when preparing this corpus, so those engine commands have not been run.

## Assumed case schema — check this before importing

The supplied README defines semantics but not the complete case YAML syntax. This package assumes:

    id: business_01
    documents:
      - id: doc_01
        path: documents/01_consulting_agreements_digital.pdf
    spec: specs/01_consulting_agreements.yaml
    tags: [consulting_agreements, digital]
    expected:
      agreement_date:
        status: supported
        value: '2017-03-27'
        evidence:
          - document: doc_01
            page: 1
            quote: 27th day of March, 2017

Paths are relative to evaluation/corpus/, including while cases are staged for review. Evidence document names refer to the document id, and page is the 1-based physical PDF page index, not printed page numbering. No parser block IDs are used.

For records, expected.<task_id> has status: supported, completeness: complete, value: a list of plain field dictionaries, and evidence: a list of supporting excerpts. Fields inside each record do not contain nested status envelopes. For nonsupported answers, value is omitted entirely. Review rationales and provenance are sidecars, not extra expected-result keys. Adapt document-reference syntax, evidence keys, record representation or completeness vocabulary if your actual loader differs.

## Label conventions

- supported: the requested value or conclusion is evidenced in the supplied document.
- not_found: the requested answer is absent, blank, or not established by the supplied document. A hypothetical payment trigger is not proof that approval happened.
- uncertain: decisive content is visibly redacted or unresolved conflicting statements prevent the requested answer.
- failed: reserved for execution failure; no expected answer is labeled failed merely because a source is difficult.

A printed value can be supported even when inconsistent with another figure if the task explicitly asks for that printed value. A task asking to resolve the true amount can instead be uncertain. Zero is used only for an explicit zero/no-payment obligation, never as a missing-value default. All dates are quoted ISO strings; identifiers remain strings; percentages use percent units (3.5, not 0.035). Business answers such as prohibited are values under supported, never statuses.

Record lists are deliberately bounded: top-level clauses, payment milestones, required documents, territory countries, licensed actions, invoice triggers, notice recipients, party obligations, definitions, workstreams and payment stages. Complete means complete within that task’s stated scope, not every possible obligation in the whole contract. There are two separate record tasks for the outsourcing contract. Three record schemas also include a nested interpretation field: installment trigger type, primary/copy notice role, and payment fee type.

## Scoring and splits

Keep the original, rasterized versions, alternate field sets and future variants of one source in the same split. The proposal assigns 5 documents to train, 2 to validation and 3 to test; it is metadata, not an engine runtime setting. Review source/template similarity before treating the groups as independent. Shared concepts recur across splits, so this is not an unseen-field benchmark. All inputs are public CUAD contracts and may have appeared in model pretraining; this is not a contamination-free test set.

The corpus is small: use it for smoke tests and debugging, not statistical claims of general accuracy. It contains real within-document contradictions and an amendment, but no original-plus-amendment bundle. Add new labeled examples for multi-document precedence, genuinely poor scans/photos, languages other than English, and non-contract business documents.

For additional scoring, match records by their declared identity before comparing fields; do not penalize record ordering unless ordering is an explicit requirement. Report record precision and recall as well as task-level exact match, and score status separately from supported values. Long verbatim record fields intentionally exercise full-span extraction; report normalized string matching separately from meaning preservation. The included helper does not score model predictions. Your README says your current engine reports status/value accuracy; this package does not claim it adds stage-specific metrics.

Do not tune on test expected answers and later report the same cases as untouched holdout results. The original four image-only PDFs are clean 200-DPI rasterizations without text layers, not physical scans or camera photos. Reference transcripts are PDF-extracted text, not human-verified OCR ground truth; they contain source artifacts. Do not use them as certified CER/WER labels without review.

## Provenance

Source: Contract Understanding Atticus Dataset (CUAD), The Atticus Project.
https://www.atticusprojectai.org/cuad/
https://huggingface.co/datasets/theatticusproject/cuad

Exact source names, URLs, hashes and transformations are retained in _template/pending_review/source_manifest.json. Preserve source attribution and consult source terms for intended redistribution. The supplied files are complete source PDFs but may reference omitted schedules or external agreements; never fabricate their contents.
