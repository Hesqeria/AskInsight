"""Pure-Python ontology reasoning engine.

Kept DB-free so the transitive-closure and impact-analysis algorithms
are unit-testable without spinning up Doris. The repository loads rows
into the dataclasses defined here, then the engine operates on them in
memory.

The PRD §6.1 algorithm (`impact_analysis` BFS) lives here, along with
helpers for:
  - Transitive closure over `is_transitive=TRUE` relations
    (e.g. category3 -> category2 -> category1)
  - Class-hierarchy traversal (parent_class_id chains)
  - Symmetric-relation expansion (related_to goes both ways)
"""
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class ClassNode:
    class_id: str
    class_name: str
    class_name_zh: str
    parent_class_id: str | None = None
    is_transitive: bool = False
    pii_level: int = 0


@dataclass(frozen=True)
class RelationEdge:
    """One directed edge in the ontology graph.

    For symmetric relations (`is_symmetric=True`), the engine
    automatically adds the reverse edge when building the graph."""
    src_class_id: str
    dst_class_id: str
    relation_type: str
    is_transitive: bool = False
    is_symmetric: bool = False
    cardinality: str = "N:1"


@dataclass(frozen=True)
class InstanceRef:
    """A physical column mapped to an ontology class."""
    instance_id: str           # e.g. "dw.dwd_order_info_inc.final_amount"
    db_name: str
    table_name: str
    column_name: str | None
    class_id: str
    property_id: str | None = None
    role: str | None = None    # PK/FK/MEASURE/DIMENSION


@dataclass(frozen=True)
class LineageTechnicalEdge:
    lineage_id: str
    src_db: str
    src_table: str
    src_column: str | None
    dst_db: str
    dst_table: str
    dst_column: str | None
    transformation: str = "DIRECT"


@dataclass(frozen=True)
class BusinessLineage:
    business_id: str
    term_name: str           # e.g. "GMV"
    related_columns: tuple[str, ...] = ()


class OntologyGraph:
    """In-memory directed graph over `ClassNode`s, with auto-expanded
    symmetric + transitive edges.

    The graph is the workhorse for all class-level reasoning. Build it
    once per request (cheap at PRD's 55-table scale) then run as many
    queries as needed.

    Adjacency is stored in **two** maps:
      - `_fwd`: declared forward edges (+ auto-reverse of symmetric)
      - `_rev`: reverse of all declared edges (used by impact analysis)

    The split lets `neighbors()` honor directionality (placed_by is
    one-way) while still letting impact analysis walk upstream (cancel
    Campaign -> impact Orders, where promoted_by is declared Order ->
    Campaign).
    """

    def __init__(
        self,
        classes: Iterable[ClassNode],
        relations: Iterable[RelationEdge],
    ):
        self.classes: dict[str, ClassNode] = {c.class_id: c for c in classes}

        # Forward adjacency: declared direction + auto-reverse of symmetric.
        self._fwd: dict[str, list[tuple[str, RelationEdge]]] = defaultdict(list)
        # Reverse adjacency: dst -> src (for impact analysis upstream walk).
        self._rev: dict[str, list[tuple[str, RelationEdge]]] = defaultdict(list)

        for edge in relations:
            self._fwd[edge.src_class_id].append((edge.dst_class_id, edge))
            self._rev[edge.dst_class_id].append((edge.src_class_id, edge))
            # Symmetric relations are also forward-traversable in reverse.
            if edge.is_symmetric and edge.src_class_id != edge.dst_class_id:
                self._fwd[edge.dst_class_id].append((edge.src_class_id, edge))

    # ------------------------------------------------------------------ #
    # Direct neighbors
    # ------------------------------------------------------------------ #
    def neighbors(
        self,
        class_id: str,
        relation_types: Iterable[str] | None = None,
        reverse: bool = False,
    ) -> list[tuple[str, RelationEdge]]:
        """Direct neighbors of `class_id`, optionally filtered by
        relation type. Set `reverse=True` to walk edges in their
        declared anti-direction (used by impact analysis)."""
        wanted = set(relation_types) if relation_types else None
        adj = self._rev if reverse else self._fwd
        out = []
        for dst, edge in adj.get(class_id, []):
            if wanted is None or edge.relation_type in wanted:
                out.append((dst, edge))
        return out

    # ------------------------------------------------------------------ #
    # Transitive closure (e.g. category3 -> category2 -> category1)
    # ------------------------------------------------------------------ #
    def transitive_closure(
        self,
        start_class_id: str,
        relation_type: str = "parent_of",
        max_depth: int = 16,
    ) -> list[str]:
        """BFS over transitive edges of one relation type.

        Returns the list of reachable class IDs (excluding start) in BFS
        order. `max_depth` is a safety cap to prevent infinite loops on
        accidentally-cyclic data.
        """
        seen = {start_class_id}
        out = []
        queue: deque[tuple[str, int]] = deque([(start_class_id, 0)])
        while queue:
            cur, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for dst, edge in self._fwd.get(cur, []):
                if edge.relation_type != relation_type:
                    continue
                # Only follow edges marked transitive (or self-loops on
                # the same class, like parent_of on Category).
                if not edge.is_transitive:
                    continue
                if dst in seen:
                    continue
                seen.add(dst)
                out.append(dst)
                queue.append((dst, depth + 1))
        return out

    # ------------------------------------------------------------------ #
    # Class hierarchy (parent_class_id chains on ont_class itself)
    # ------------------------------------------------------------------ #
    def ancestors(self, class_id: str, max_depth: int = 16) -> list[str]:
        """Walk up `parent_class_id` to the root.

        Useful for PII inheritance: a class inherits pii_level from its
        ancestors (PRD §6.2)."""
        seen = set()
        out = []
        cur = self.classes.get(class_id)
        depth = 0
        while cur and cur.parent_class_id and depth < max_depth:
            parent_id = cur.parent_class_id
            if parent_id in seen:
                break  # cycle guard
            seen.add(parent_id)
            out.append(parent_id)
            cur = self.classes.get(parent_id)
            depth += 1
        return out

    def effective_pii_level(self, class_id: str) -> int:
        """Max pii_level across this class and all its ancestors (PRD
        §6.2: policy centralized at the ontology layer, propagated by
        inheritance)."""
        cls = self.classes.get(class_id)
        if cls is None:
            return 0
        levels = [cls.pii_level]
        for ancestor_id in self.ancestors(class_id):
            anc = self.classes.get(ancestor_id)
            if anc:
                levels.append(anc.pii_level)
        return max(levels)


# ------------------------------------------------------------------ #
# Impact analysis (PRD §6.3 algorithm, in-memory implementation)
# ------------------------------------------------------------------ #
@dataclass
class ImpactResult:
    """Result of an impact-analysis query. Counts and lists are kept as
    plain Python collections so the API layer can JSON-serialize freely."""
    start_instance_id: str
    impacted_instances: set[str] = field(default_factory=set)
    impacted_tables: set[str] = field(default_factory=set)
    impacted_metrics: set[str] = field(default_factory=set)
    impacted_classes: set[str] = field(default_factory=set)
    traversal_path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "start_instance_id": self.start_instance_id,
            "impact_count": len(self.impacted_instances),
            "instances": sorted(self.impacted_instances),
            "tables": sorted(self.impacted_tables),
            "metrics": sorted(self.impacted_metrics),
            "classes": sorted(self.impacted_classes),
            "traversal_path": self.traversal_path,
        }


class ImpactAnalyzer:
    """BFS over the merged technical + business + semantic graph.

    Three edge sources are layered:
      1. `lineage_technical` - physical column -> physical column.
      2. `lineage_business`  - column -> metric (term_name).
      3. Ontology classes    - all instances of related classes are
         pulled in, so e.g. canceling one Campaign pulls in all Orders
         via `promoted_by`.

    The analyzer runs entirely in memory given the preloaded edges. At
    the PRD's 55-table / 800-field scale this is sub-200ms (per §6.3).
    """

    def __init__(
        self,
        graph: OntologyGraph,
        instances: Iterable[InstanceRef],
        technical_edges: Iterable[LineageTechnicalEdge] | None = None,
        business_edges: Iterable[BusinessLineage] | None = None,
    ):
        self.graph = graph
        self.instances_by_id: dict[str, InstanceRef] = {
            i.instance_id: i for i in instances
        }
        self.instances_by_class: dict[str, list[str]] = defaultdict(list)
        for inst_id, inst in self.instances_by_id.items():
            self.instances_by_class[inst.class_id].append(inst_id)

        # Build forward adjacency for technical lineage.
        self._tech_fwd: dict[str, list[str]] = defaultdict(list)
        for e in (technical_edges or []):
            if e.src_column and e.dst_column:
                src = f"{e.src_db}.{e.src_table}.{e.src_column}"
                dst = f"{e.dst_db}.{e.dst_table}.{e.dst_column}"
                self._tech_fwd[src].append(dst)

        # Build column -> metrics reverse index.
        self._metric_by_col: dict[str, list[str]] = defaultdict(list)
        for b in (business_edges or []):
            for col in b.related_columns:
                self._metric_by_col[col].append(b.term_name)

    def analyze(
        self,
        start_instance_id: str,
        max_depth: int = 8,
    ) -> ImpactResult:
        """BFS from `start_instance_id`, returning the full downstream
        impact set per PRD §6.3."""
        result = ImpactResult(start_instance_id=start_instance_id)
        if start_instance_id not in self.instances_by_id:
            return result

        # Seed: the start instance + its class.
        start_inst = self.instances_by_id[start_instance_id]
        result.impacted_instances.add(start_instance_id)
        result.impacted_classes.add(start_inst.class_id)
        result.traversal_path.append(start_instance_id)

        queue: deque[tuple[str, int]] = deque([(start_instance_id, 0)])
        while queue:
            cur_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            # 1) Technical downstream.
            for dst in self._tech_fwd.get(cur_id, []):
                if dst not in result.impacted_instances:
                    result.impacted_instances.add(dst)
                    result.traversal_path.append(dst)
                    queue.append((dst, depth + 1))

            # 2) Business term downstream (this column participates in
            # which metrics?).
            for term in self._metric_by_col.get(cur_id, []):
                result.impacted_metrics.add(term)

            # 3) Ontology-class downstream: pull in all instances of
            # neighboring classes (this is the semantic part - the PRD
            # example is canceling a Campaign cascades to Orders).
            # Walk reverse edges because most relations are declared
            # downstream->upstream (e.g. `Order -> Campaign promoted_by`
            # but impact flows Campaign -> Order).
            cur_inst = self.instances_by_id.get(cur_id)
            if cur_inst is None:
                continue
            for dst_class_id, _edge in self.graph.neighbors(
                cur_inst.class_id, reverse=True,
            ):
                # Traverse every instance of the related class.
                for related_inst_id in self.instances_by_class.get(dst_class_id, []):
                    if related_inst_id not in result.impacted_instances:
                        result.impacted_instances.add(related_inst_id)
                        result.impacted_classes.add(dst_class_id)
                        result.traversal_path.append(related_inst_id)
                        queue.append((related_inst_id, depth + 1))

        # Derive impacted tables from the instance set.
        for inst_id in result.impacted_instances:
            inst = self.instances_by_id.get(inst_id)
            if inst:
                result.impacted_tables.add(f"{inst.db_name}.{inst.table_name}")

        return result
