"""RetrievalLog 测试。"""

from src.common.knowledge_tree.retrieval.log import RetrievalLog


class TestRetrievalLog:
    def test_create(self):
        log = RetrievalLog.create("测试查询")
        assert log.query_text == "测试查询"
        assert len(log.query_id) == 12
        assert log.timestamp
        assert log.fusion_mode == "none"
        assert log.tree_success is False

    def test_to_dict(self):
        log = RetrievalLog.create("q")
        log.tree_path = ["root", "n1"]
        log.fusion_mode = "tree"
        log.agent_satisfaction = True
        d = log.to_dict()
        assert d["query_text"] == "q"
        assert d["tree_path"] == ["root", "n1"]
        assert d["fusion_mode"] == "tree"
        assert d["agent_satisfaction"] is True
        # query_vector 默认不包含
        assert "query_vector" not in d
