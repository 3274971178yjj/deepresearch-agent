"""DAG 数据结构与拓扑排序测试（M2 基础设施）。"""
import pytest

from src.planner.dag import DAG, DAGCycleError


def _build_dag() -> DAG:
    """构造典型三层 DAG:
    t1, t2 -> t3 -> t4; t2 -> t5
    """
    dag = DAG()
    for node in ["t1", "t2", "t3", "t4", "t5"]:
        dag.add_node(node)
    dag.add_edge("t1", "t3")  # t3 依赖 t1
    dag.add_edge("t2", "t3")
    dag.add_edge("t3", "t4")
    dag.add_edge("t2", "t5")
    return dag


class TestDagBasics:
    def test_add_node(self):
        dag = DAG()
        dag.add_node("a")
        assert dag.has_node("a")
        assert "a" in dag
        assert len(dag) == 1

    def test_dependencies_and_successors(self):
        dag = _build_dag()
        assert set(dag.get_dependencies("t3")) == {"t1", "t2"}
        assert set(dag.get_successors("t1")) == {"t3"}

    def test_add_edge_auto_creates_nodes(self):
        dag = DAG()
        dag.add_edge("a", "b")
        assert dag.has_node("a") and dag.has_node("b")


class TestTopologicalSort:
    def test_order_respects_dependencies(self):
        dag = _build_dag()
        order = dag.topological_sort()
        pos = {n: i for i, n in enumerate(order)}
        assert pos["t1"] < pos["t3"] < pos["t4"]
        assert pos["t2"] < pos["t5"]

    def test_cycle_raises(self):
        dag = DAG()
        dag.add_edge("a", "b")
        dag.add_edge("b", "c")
        dag.add_edge("c", "a")
        with pytest.raises(DAGCycleError):
            dag.topological_sort()

    def test_self_loop_rejected_at_add_edge(self):
        dag = DAG()
        with pytest.raises(DAGCycleError):
            dag.add_edge("a", "a")


class TestParallelGroups:
    def test_independent_nodes_grouped(self):
        dag = _build_dag()
        groups = dag.get_parallel_groups()
        assert isinstance(groups, list)
        # 第一层应包含无依赖的 t1 和 t2
        assert set(groups[0]) == {"t1", "t2"}
        # t4 必须在 t3 之后
        layer_of = {n: i for i, g in enumerate(groups) for n in g}
        assert layer_of["t3"] < layer_of["t4"]

    def test_groups_cover_all_nodes(self):
        dag = _build_dag()
        groups = dag.get_parallel_groups()
        flattened = [n for g in groups for n in g]
        assert sorted(flattened) == ["t1", "t2", "t3", "t4", "t5"]
