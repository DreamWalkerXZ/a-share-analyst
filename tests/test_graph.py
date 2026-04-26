from src.agent.graph import build_graph


def test_graph_compilable():
    graph = build_graph()
    assert graph is not None


def test_graph_has_expected_nodes():
    graph = build_graph()
    node_names = list(graph.get_graph().nodes.keys())
    assert "data_collection" in node_names
    assert "report_generation" in node_names
    assert "output" in node_names
