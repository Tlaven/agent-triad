"""RetrievalLog 测试。"""

from src.common.knowledge_tree.retrieval.log import RetrievalLog


class TestRetrievalLog:
    def test_create(self):
        log = RetrievalLog.create("测试查询")
        assert log.query_text == "测试查询"
        assert len(log.query_id) == 12
        assert log.timestamp
        assert log.rag_results == []
        assert log.agent_satisfaction is None

    def test_to_dict(self):
        log = RetrievalLog.create("q")
        log.rag_results = [("dev/a.md", 0.85)]
        log.agent_satisfaction = True
        d = log.to_dict()
        assert d["query_text"] == "q"
        assert d["rag_results"] == [("dev/a.md", 0.85)]
        assert d["agent_satisfaction"] is True
        # query_vector 默认不包含
        assert "query_vector" not in d
