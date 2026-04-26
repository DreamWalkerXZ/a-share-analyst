from langgraph.graph import END, StateGraph

from src.agent.nodes import data_collection_node, output_node, report_generation_node
from src.agent.state import ReportState


def build_graph():
    graph = StateGraph(ReportState)
    graph.add_node("data_collection", data_collection_node)
    graph.add_node("report_generation", report_generation_node)
    graph.add_node("output", output_node)
    graph.set_entry_point("data_collection")
    graph.add_edge("data_collection", "report_generation")
    graph.add_edge("report_generation", "output")
    graph.add_edge("output", END)
    return graph.compile()
