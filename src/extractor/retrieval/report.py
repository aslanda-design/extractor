"""Human-readable view of one retrieval run: what each step did and why a passage is in the package."""

from ..contracts.evidence import EvidencePackage


def _label(passage_id: str, texts: dict[str, tuple[list[str], str]], width: int = 56) -> str:
    heading, text = texts.get(passage_id, ([], ""))
    where = " > ".join(heading) or "(no section)"
    snippet = " ".join(text.split())[:width]
    return f"{passage_id}  [{where}]  {snippet!r}"


def format_package(package: EvidencePackage, texts: dict[str, tuple[list[str], str]], top: int = 6) -> str:
    """`texts` maps passage id -> (heading_path, text) for passages mentioned in the trace."""
    t = package.trace
    lines = [f"task {package.task_id} - profile {t.profile}{' - SHORT DOCUMENT: all passages reranked' if t.short_document_mode else ''}", ""]
    lines.append("1. queries")
    for kind, queries in t.queries.items():
        for q in queries:
            lines.append(f"   {kind:<9} {q[:110]}")
    lines += ["", "2. candidate lists (best first)"]
    for name, ids in t.candidate_lists.items():
        lines.append(f"   {name:<22} {len(ids):>3} candidates   top: {', '.join(i.rsplit(':', 1)[-1] for i in ids[:top])}")
    lines += ["", f"3. fused: {len(t.fused)} unique candidates", "", "4. reranked (relevance 0-1)"]
    for pid, score in t.reranked[:top + 4]:
        marker = "*" if pid in t.selected else " "
        lines.append(f" {marker} {score:5.3f}  {_label(pid, texts)}")
    lines += ["", f"5. selected {len(t.selected)} primary passage(s)", "", "6. expansion"]
    for x in t.expansions:
        flags = ("promoted " if x.promoted else "") + ("" if x.included else "DROPPED(budget) ")
        score = f"{x.rerank_score:5.3f}" if x.rerank_score is not None else "  -  "
        lines.append(f"   {x.origin.value:<10} {score}  {flags}{x.detail + ' ' if x.detail else ''}<- {x.from_passage_id.rsplit(':', 1)[-1]}  "
                     f"{_label(x.passage_id, texts, 40)}")
    if not t.expansions:
        lines.append("   (none)")
    lines += ["", f"7. package: {len(package.passages)} passage(s), {t.used_tokens}/{t.budget_tokens} tokens"]
    for p in package.passages:
        lines.append(f"   {p.origin.value:<10} {p.role.value:<14} {p.token_count:>4} tok  p{p.pages}  {_label(p.passage_id, texts, 44)}")
    if package.gaps:
        lines += ["", "gaps (stay unprocessed until something reads them):"] + [f"   - {g}" for g in package.gaps]
    return "\n".join(lines)
