"""Ontology reasoning engine tests.

Covers the pure-Python algorithms:
  - OntologyGraph transitive closure (parent_of recursion)
  - Symmetric-relation auto-expansion (related_to)
  - Class-hierarchy ancestor walk + PII inheritance
  - ImpactAnalyzer BFS over technical + business + semantic edges
"""
import pytest

from app.ontology import (
    ClassNode, RelationEdge, InstanceRef,
    LineageTechnicalEdge, BusinessLineage,
    OntologyGraph, ImpactAnalyzer,
)


# --------------------------------------------------------------------------- #
# Test fixtures: a miniature ontology mirroring the PRD seed.
# --------------------------------------------------------------------------- #
def _classes():
    return [
        ClassNode("C001", "Entity",      "实体",     None,    False, 0),
        ClassNode("C020", "Product",     "商品",     "C001",  False, 0),
        ClassNode("C021", "Category",    "品类",     "C020",  True,  0),   # transitive
        ClassNode("C022", "SKU",         "SKU",      "C020",  False, 0),
        ClassNode("C030", "Order",       "订单",     "C001",  False, 1),
        ClassNode("C011", "User",        "用户",     "C001",  False, 3),   # high PII
        ClassNode("C012", "MemberLevel", "会员等级", "C011",  False, 0),
        ClassNode("C060", "Campaign",    "活动",     "C001",  False, 0),
    ]


def _relations():
    return [
        # Category3 -> Category2 -> Category1 chain (self-loop on C021).
        RelationEdge("C021", "C021", "parent_of", is_transitive=True, cardinality="N:1"),
        RelationEdge("C022", "C021", "belongs_to"),
        RelationEdge("C030", "C011", "placed_by"),
        RelationEdge("C030", "C100", "related_to", is_symmetric=True, cardinality="N:N"),
        RelationEdge("C030", "C060", "promoted_by", cardinality="N:N"),
    ]


# --------------------------------------------------------------------------- #
# Transitive closure
# --------------------------------------------------------------------------- #
def test_transitive_closure_walks_self_loop():
    """parent_of is a self-loop on Category (C021); closure should walk
    arbitrarily deep without infinite recursion."""
    g = OntologyGraph(_classes(), _relations())
    # Even though the relation is C021 -> C021, the closure of C021
    # includes C021 only once (already the start node, so empty result).
    out = g.transitive_closure("C021", "parent_of")
    assert out == []  # only node reachable is itself, which is excluded

    # If we add distinct category instances, the closure walks them.
    classes = _classes() + [
        ClassNode("C021A", "Cat3", "三级", "C021", False, 0),
        ClassNode("C021B", "Cat2", "二级", "C021", False, 0),
    ]
    relations = _relations() + [
        RelationEdge("C021A", "C021B", "parent_of", is_transitive=True),
        RelationEdge("C021B", "C021",  "parent_of", is_transitive=True),
    ]
    g2 = OntologyGraph(classes, relations)
    out = g2.transitive_closure("C021A", "parent_of")
    assert "C021B" in out
    assert "C021" in out


def test_transitive_closure_respects_non_transitive():
    """Only edges with is_transitive=True are followed."""
    g = OntologyGraph(_classes(), _relations())
    # belongs_to is not transitive - closure must be empty.
    assert g.transitive_closure("C022", "belongs_to") == []


def test_transitive_closure_cycle_guard():
    """Cyclic data must not cause infinite loops."""
    classes = [ClassNode("A", "A", "A", None, False, 0),
               ClassNode("B", "B", "B", None, False, 0),
               ClassNode("C", "C", "C", None, False, 0)]
    relations = [
        RelationEdge("A", "B", "parent_of", is_transitive=True),
        RelationEdge("B", "C", "parent_of", is_transitive=True),
        RelationEdge("C", "A", "parent_of", is_transitive=True),  # cycle
    ]
    g = OntologyGraph(classes, relations)
    out = g.transitive_closure("A", "parent_of", max_depth=10)
    assert set(out) == {"B", "C"}  # each visited once


# --------------------------------------------------------------------------- #
# Symmetric expansion
# --------------------------------------------------------------------------- #
def test_symmetric_relation_expands_both_directions():
    """related_to is symmetric; both A->B and B->A should be traversable."""
    g = OntologyGraph(_classes(), _relations())
    # C030 -> C100 declared; reverse should exist automatically.
    forward = g.neighbors("C030", relation_types=["related_to"])
    reverse = g.neighbors("C100", relation_types=["related_to"])
    assert ("C100" in [n[0] for n in forward])
    assert ("C030" in [n[0] for n in reverse])


def test_non_symmetric_relation_is_one_way():
    g = OntologyGraph(_classes(), _relations())
    # placed_by is one-way C030 -> C011.
    assert "C011" in [n[0] for n in g.neighbors("C030", relation_types=["placed_by"])]
    assert "C030" not in [n[0] for n in g.neighbors("C011", relation_types=["placed_by"])]


# --------------------------------------------------------------------------- #
# Ancestor walk + PII inheritance
# --------------------------------------------------------------------------- #
def test_ancestors_walk_parent_chain():
    g = OntologyGraph(_classes(), _relations())
    # C012 MemberLevel -> C011 User -> C001 Entity
    assert g.ancestors("C012") == ["C011", "C001"]


def test_ancestors_root_returns_empty():
    g = OntologyGraph(_classes(), _relations())
    assert g.ancestors("C001") == []


def test_ancestors_cycle_guard():
    """Self-referencing parent must not loop."""
    classes = [
        ClassNode("X", "X", "X", "X", False, 0),  # X is its own parent
    ]
    g = OntologyGraph(classes, [])
    out = g.ancestors("X")
    # Self-cycle caught by `seen` set; output has the self once then stops.
    assert out == ["X"]


def test_effective_pii_level_takes_max_from_ancestors():
    """PRD §6.2: PII inheritance takes the max across the chain."""
    g = OntologyGraph(_classes(), _relations())
    # C012 MemberLevel itself is pii_level=0, but its ancestor User is 3.
    assert g.effective_pii_level("C012") == 3
    # A leaf with no PII ancestors stays 0.
    assert g.effective_pii_level("C022") == 0


def test_effective_pii_level_unknown_class():
    g = OntologyGraph(_classes(), _relations())
    assert g.effective_pii_level("UNKNOWN") == 0


# --------------------------------------------------------------------------- #
# Impact analysis
# --------------------------------------------------------------------------- #
def _instances():
    return [
        InstanceRef("dw.dwd_order_info_inc.order_id",     "dw", "dwd_order_info_inc", "order_id",     "C030"),
        InstanceRef("dw.dwd_order_info_inc.final_amount", "dw", "dwd_order_info_inc", "final_amount", "C030"),
        InstanceRef("dw.dws_user_order_day_1m.total_amount", "dw", "dws_user_order_day_1m", "total_amount", "C030"),
        InstanceRef("dw.dim_user_info.user_id",           "dw", "dim_user_info",      "user_id",      "C011"),
        InstanceRef("dw.dim_sku_info.sku_id",             "dw", "dim_sku_info",       "sku_id",       "C022"),
        InstanceRef("dw.ads_user_active_day.user_id",     "dw", "ads_user_active_day","user_id",      "C011"),
        InstanceRef("dw.dwd_activity_info.activity_id",   "dw", "dwd_activity_info",  "activity_id",  "C060"),
    ]


def _tech_edges():
    return [
        # final_amount (DWD) -> total_amount (DWS) via SUM aggregation.
        LineageTechnicalEdge("L1", "dw", "dwd_order_info_inc", "final_amount",
                             "dw", "dws_user_order_day_1m", "total_amount", "AGG"),
        # user_id propagates from dim_user into the active-day ads table.
        LineageTechnicalEdge("L2", "dw", "dim_user_info", "user_id",
                             "dw", "ads_user_active_day", "user_id", "DIRECT"),
    ]


def _business_edges():
    return [
        BusinessLineage("B001", "GMV",
                        related_columns=("dw.dwd_order_info_inc.final_amount",
                                         "dw.dwd_order_info_inc.order_status")),
    ]


def test_impact_analysis_propagates_through_technical_lineage():
    """Dropping `final_amount` should cascade to `total_amount` (DWS
    derived column) and then to the GMV business term."""
    g = OntologyGraph(_classes(), _relations())
    analyzer = ImpactAnalyzer(g, _instances(), _tech_edges(), _business_edges())

    result = analyzer.analyze("dw.dwd_order_info_inc.final_amount")
    assert "dw.dwd_order_info_inc.final_amount" in result.impacted_instances
    assert "dw.dws_user_order_day_1m.total_amount" in result.impacted_instances
    assert "GMV" in result.impacted_metrics
    # Impacted tables derived from instances.
    assert "dw.dwd_order_info_inc" in result.impacted_tables
    assert "dw.dws_user_order_day_1m" in result.impacted_tables


def test_impact_analysis_pulls_in_ontology_neighbors():
    """Canceling an activity (C060) should cascade to Orders via the
    `promoted_by` relation. This is the semantic-lineage layer
    described in PRD §6.3."""
    g = OntologyGraph(_classes(), _relations())
    analyzer = ImpactAnalyzer(g, _instances(), _tech_edges(), _business_edges())

    result = analyzer.analyze("dw.dwd_activity_info.activity_id")
    # Order instances pulled in via the C060 -> C030 promoted_by edge.
    assert "dw.dwd_order_info_inc.order_id" in result.impacted_instances
    assert "dw.dwd_order_info_inc.final_amount" in result.impacted_instances
    # GMV metric is hit transitively (order -> final_amount -> GMV).
    assert "GMV" in result.impacted_metrics


def test_impact_analysis_unknown_instance_returns_empty():
    g = OntologyGraph(_classes(), _relations())
    analyzer = ImpactAnalyzer(g, _instances(), _tech_edges(), _business_edges())
    result = analyzer.analyze("does.not.exist")
    assert result.impacted_instances == set()
    assert result.to_dict()["impact_count"] == 0


def test_impact_analysis_respects_max_depth():
    """max_depth cap prevents runaway traversals on dense graphs."""
    g = OntologyGraph(_classes(), _relations())
    analyzer = ImpactAnalyzer(g, _instances(), _tech_edges(), _business_edges())
    result = analyzer.analyze("dw.dwd_activity_info.activity_id", max_depth=0)
    # Depth 0 means only the start node is in the set.
    assert result.impacted_instances == {"dw.dwd_activity_info.activity_id"}


def test_impact_to_dict_serializable_shape():
    """The API layer JSON-serializes to_dict() output; verify the shape."""
    g = OntologyGraph(_classes(), _relations())
    analyzer = ImpactAnalyzer(g, _instances(), _tech_edges(), _business_edges())
    result = analyzer.analyze("dw.dwd_order_info_inc.final_amount")
    d = result.to_dict()
    assert set(d.keys()) == {
        "start_instance_id", "impact_count", "instances",
        "tables", "metrics", "classes", "traversal_path",
    }
    assert d["impact_count"] == len(d["instances"])
    assert d["impact_count"] > 0
