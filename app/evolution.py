"""Evolution lines, from PokéAPI's species list (evolution_chain_id and
evolves_from_species_id). Shown on a Pokémon's set page and used to group
followed Pokémon of the same line on Chases."""

from . import pokeapi


class Lines:
    def __init__(self, folder=None, get=None):
        self.folder, self.get = folder, get
        self._parent: dict[int, int | None] | None = None
        self._chain: dict[int, int] = {}
        self._order: dict[int, int] = {}

    def _load(self) -> None:
        if self._parent is not None:
            return
        parent, chain, order = {}, {}, {}
        for r in pokeapi.rows("pokemon_species", self.folder, self.get):
            dex = int(r["id"])
            if dex > pokeapi.SPECIES:
                continue
            parent[dex] = int(r["evolves_from_species_id"]) if r["evolves_from_species_id"] else None
            chain[dex] = int(r["evolution_chain_id"]) if r["evolution_chain_id"] else dex
            order[dex] = int(r["order"] or dex)
        self._parent, self._chain, self._order = parent, chain, order

    def available(self) -> bool:
        return self._parent is not None or pokeapi.have("pokemon_species", self.folder)

    def chain_of(self, dex: int) -> int:
        self._load()
        return self._chain.get(dex, dex)

    def members(self, dex: int) -> list[int]:
        """Every Pokémon in dex's line, in evolution order."""
        self._load()
        chain = self._chain.get(dex)
        if chain is None:
            return [dex]
        return sorted((d for d, c in self._chain.items() if c == chain), key=lambda d: (self._depth(d), self._order.get(d, d)))

    def _depth(self, dex: int) -> int:
        depth, seen = 0, set()
        while self._parent.get(dex) and dex not in seen:
            seen.add(dex)
            dex = self._parent[dex]
            depth += 1
        return depth

    def children(self, dex: int) -> list[int]:
        self._load()
        return sorted((d for d, p in self._parent.items() if p == dex), key=lambda d: self._order.get(d, d))

    def parent(self, dex: int) -> int | None:
        self._load()
        return self._parent.get(dex)

    def line(self, dex: int) -> dict | None:
        """The whole line, the same on every Pokémon's page in it, or None when it has no line:
        {'trunk': [d1, d2], 'branches': [[b1], [b2], ...], 'here': dex}.
        The trunk runs from the first stage to where the line splits (Eevee; Ralts › Kirlia). Each branch is
        its own list of stages after the split (Wurmple: [[Silcoon, Beautifly], [Cascoon, Dustox]]). A branch
        that splits again ends in a list of its alternatives. A straight line has no branches."""
        members = self.members(dex)
        if len(members) < 2:
            return None
        root = members[0]
        while self.parent(root) is not None:
            root = self.parent(root)
        trunk = [root]
        while len(self.children(trunk[-1])) == 1:
            trunk.append(self.children(trunk[-1])[0])
        branches = []
        for child in self.children(trunk[-1]):
            stages = [child]
            while True:
                after = self.children(stages[-1] if isinstance(stages[-1], int) else stages[-1][0])
                if not after or isinstance(stages[-1], list):
                    break
                stages.append(after[0] if len(after) == 1 else after)
            branches.append(stages)
        return {"trunk": trunk, "branches": branches, "here": dex}
