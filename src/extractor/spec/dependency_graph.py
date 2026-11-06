"""Dependency graph helpers (pure functions, no knowledge of tasks).

`dependencies` maps a node to the nodes it waits for. Nodes are kept in the
order given, so results are deterministic.
"""


class CycleError(ValueError):
    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__("dependency cycle: " + " -> ".join(cycle))


def find_cycle(dependencies: dict[str, list[str]]) -> list[str] | None:
    """Return one cycle as [a, b, ..., a], or None. Unknown nodes are ignored."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in dependencies}

    def visit(node: str, path: list[str]) -> list[str] | None:
        color[node] = GREY
        path.append(node)
        for dep in dependencies[node]:
            if dep not in color:
                continue
            if color[dep] == GREY:
                return path[path.index(dep):] + [dep]
            if color[dep] == WHITE:
                found = visit(dep, path)
                if found:
                    return found
        path.pop()
        color[node] = BLACK
        return None

    for node in dependencies:
        if color[node] == WHITE:
            found = visit(node, [])
            if found:
                return found
    return None


def build_layers(dependencies: dict[str, list[str]]) -> list[list[str]]:
    """Group nodes into layers; every node only depends on earlier layers.

    Nodes inside one layer are independent and can run concurrently.
    Raises CycleError if the graph is not acyclic.
    """
    placed: set[str] = set()
    remaining = list(dependencies)
    layers: list[list[str]] = []
    while remaining:
        ready = [n for n in remaining if all(d in placed for d in dependencies[n])]
        if not ready:
            raise CycleError(find_cycle({n: dependencies[n] for n in remaining}) or remaining)
        layers.append(ready)
        placed.update(ready)
        remaining = [n for n in remaining if n not in placed]
    return layers
