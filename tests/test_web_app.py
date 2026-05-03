import pytest

from web_app import Job, _compose_query, _safe_output_file, _update_job_from_line


def test_compose_query_from_form_fields():
    payload = {"company": "贵州茅台", "year": "2025", "quarter": "q4"}
    assert _compose_query(payload) == "贵州茅台 2025 Q4"


def test_update_job_from_logs_tracks_progress():
    job = Job(id="test", query="贵州茅台 2025 Q4")

    _update_job_from_line(job, "[main] 解析完成：{'company': '贵州茅台'}")
    _update_job_from_line(job, "[data_collection] 阶段一完成，初始化 238 条数据项")
    _update_job_from_line(job, "[data_collection] 阶段二：调用 realtime_search（轮次 12/30）")
    _update_job_from_line(job, "[data_collection] 阶段二完成，共 304 条数据项")
    _update_job_from_line(job, "[report_generation] 生成章节 3/5：盈利预测与估值")
    _update_job_from_line(job, "[output] 研报已保存至：output/test.md")

    assert job.metrics["initial_data"] == 238
    assert job.metrics["tool_calls"] == 12
    assert job.metrics["final_data"] == 304
    assert job.output_path == "output/test.md"
    assert job.steps["output"]["status"] == "done"


def test_safe_output_file_rejects_path_traversal():
    with pytest.raises((FileNotFoundError, ValueError)):
        _safe_output_file("../README.md")
