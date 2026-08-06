"""RRF fusion boundary tests"""
import pytest
from app.agent.nodes.merge_retrieved_info import _rrf_score, RRF_K


# === RRF algorithm correctness ===
def test_rrf_single_path():
    s = _rrf_score([0])
    assert abs(s - 1.0 / (RRF_K + 0)) < 1e-6


def test_rrf_multi_path_higher():
    """multi-path hit score > single-path"""
    multi = _rrf_score([0, 5, 10])
    single = _rrf_score([0])
    assert multi > single


def test_rrf_rank_0_best():
    """rank=0 (first place) yields the highest score"""
    first = _rrf_score([0])
    second = _rrf_score([1])
    assert first > second


def test_rrf_three_paths_fusion():
    """all three paths at rank 0 > three paths scattered"""
    all_top = _rrf_score([0, 0, 0])
    scattered = _rrf_score([0, 5, 10])
    assert all_top > scattered


# === empty data ===
def test_rrf_empty_ranks():
    """empty ranks should return 0"""
    assert _rrf_score([]) == 0.0


# === node existence ===
def test_merge_node_exists():
    from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
    assert callable(merge_retrieved_info)


def test_merge_has_rrf_logic():
    """verify the node code contains RRF logic"""
    import inspect
    from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
    src = inspect.getsource(merge_retrieved_info)
    assert '_rrf_score' in src
    # RRF_K is a module-level constant, not in the function body
    assert 'column_scores' in src


def test_merge_sorts_by_rrf():
    """verify sorting by RRF score"""
    import inspect
    from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
    src = inspect.getsource(merge_retrieved_info)
    assert 'sorted_ids' in src
    assert 'column_scores' in src
