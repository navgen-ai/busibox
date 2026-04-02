"""
Unit tests for reranking configuration, score normalization, and search pipeline scoring.

Tests cover:
- MilvusSearchService reranker config resolution (mode aliases, enable/disable, legacy compat)
- Sigmoid score normalization in reranker outputs
- RRF min-max score normalization
- Config class RERANKING_MODE derivation from environment variables
"""

import math
import os
import pytest
from unittest.mock import Mock, patch, AsyncMock
from services.milvus_search import MilvusSearchService


# =============================================================================
# Reranker Configuration Tests
# =============================================================================


@pytest.mark.unit
class TestRerankerConfig:
    """Test MilvusSearchService reranker configuration resolution."""

    def test_reranking_mode_none_disables_reranker(self, mock_config):
        """When reranking_mode='none', reranker should be disabled even if enable_reranking=True."""
        mock_config["reranking_mode"] = "none"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)
        assert service.reranker_enabled is False
        assert service.reranking_mode == "none"

    def test_reranking_mode_vllm_maps_to_qwen3_gpu(self, mock_config):
        """Mode 'vllm' should be canonicalized to 'qwen3-gpu'."""
        mock_config["reranking_mode"] = "vllm"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)
        assert service.reranking_mode == "qwen3-gpu"
        assert service.reranker_enabled is True

    def test_reranking_mode_local_maps_to_baai_cpu(self, mock_config):
        """Mode 'local' should be canonicalized to 'baai-cpu'."""
        mock_config["reranking_mode"] = "local"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)
        assert service.reranking_mode == "baai-cpu"
        assert service.reranker_enabled is True

    def test_reranking_mode_qwen3_gpu_direct(self, mock_config):
        """Mode 'qwen3-gpu' should be used directly."""
        mock_config["reranking_mode"] = "qwen3-gpu"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)
        assert service.reranking_mode == "qwen3-gpu"
        assert service.reranker_enabled is True

    def test_reranking_mode_baai_cpu_direct(self, mock_config):
        """Mode 'baai-cpu' should be used directly."""
        mock_config["reranking_mode"] = "baai-cpu"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)
        assert service.reranking_mode == "baai-cpu"
        assert service.reranker_enabled is True

    def test_enable_reranking_false_overrides_mode(self, mock_config):
        """When enable_reranking=False, reranker should be disabled regardless of mode."""
        mock_config["reranking_mode"] = "vllm"
        mock_config["enable_reranking"] = False
        service = MilvusSearchService(mock_config)
        assert service.reranker_enabled is False

    def test_no_reranking_mode_defaults_to_none(self, mock_config):
        """When reranking_mode is not in config, defaults to 'none'."""
        mock_config.pop("reranking_mode", None)
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)
        assert service.reranking_mode == "none"
        assert service.reranker_enabled is False

    def test_legacy_enable_reranking_only(self, mock_config):
        """Legacy config with only enable_reranking=True and no reranking_mode
        should keep reranker disabled (mode defaults to 'none').
        
        This tests the MilvusSearchService behavior. The Config class has its
        own fallback that derives 'local' from ENABLE_RERANKING=true env var.
        """
        mock_config.pop("reranking_mode", None)
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)
        assert service.reranker_enabled is False


# =============================================================================
# Config Class RERANKING_MODE Derivation Tests
# =============================================================================


@pytest.mark.unit
class TestConfigRerankingMode:
    """Test Config class derives RERANKING_MODE correctly from env vars."""

    def test_explicit_reranking_mode_honored(self):
        """Explicit RERANKING_MODE env var should be used directly."""
        env = {
            "RERANKING_MODE": "vllm",
            "ENABLE_RERANKING": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            from importlib import reload
            import shared.config as config_module
            reload(config_module)
            assert config_module.config.reranking_mode == "vllm"

    def test_no_reranking_mode_with_enable_true_defaults_local(self):
        """Without RERANKING_MODE, ENABLE_RERANKING=true should derive 'local'."""
        env = {"ENABLE_RERANKING": "true"}
        # Remove RERANKING_MODE if set
        clean_env = {k: v for k, v in os.environ.items() if k != "RERANKING_MODE"}
        clean_env.update(env)
        with patch.dict(os.environ, clean_env, clear=True):
            from importlib import reload
            import shared.config as config_module
            reload(config_module)
            assert config_module.config.reranking_mode == "local"

    def test_no_reranking_mode_with_enable_false_defaults_none(self):
        """Without RERANKING_MODE, ENABLE_RERANKING=false should derive 'none'."""
        env = {"ENABLE_RERANKING": "false"}
        clean_env = {k: v for k, v in os.environ.items() if k != "RERANKING_MODE"}
        clean_env.update(env)
        with patch.dict(os.environ, clean_env, clear=True):
            from importlib import reload
            import shared.config as config_module
            reload(config_module)
            assert config_module.config.reranking_mode == "none"

    def test_explicit_none_overrides_enable_true(self):
        """Explicit RERANKING_MODE=none should win even if ENABLE_RERANKING=true."""
        env = {
            "RERANKING_MODE": "none",
            "ENABLE_RERANKING": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            from importlib import reload
            import shared.config as config_module
            reload(config_module)
            assert config_module.config.reranking_mode == "none"


# =============================================================================
# Sigmoid Score Normalization Tests
# =============================================================================


@pytest.mark.unit
class TestSigmoidNormalization:
    """Test that reranker scores are properly normalized via sigmoid."""

    def _sigmoid(self, x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x))

    @pytest.mark.asyncio
    async def test_vllm_reranker_normalizes_scores(self, mock_config):
        """_rerank_with_vllm should apply sigmoid to raw logit scores."""
        mock_config["reranking_mode"] = "qwen3-gpu"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)

        raw_scores = [5.2, -1.3, 0.0, 8.7]
        results = [
            {"file_id": f"f{i}", "chunk_index": 0, "text": f"text {i}", "score": 0.5, "page_number": 1, "metadata": {}}
            for i in range(len(raw_scores))
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"score": s} for s in raw_scores]
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            reranked = await service._rerank_with_vllm("test query", results, top_k=None, reranker_model="qwen3-gpu")

        assert len(reranked) == 4
        for r in reranked:
            assert 0.0 <= r["score"] <= 1.0, f"Score {r['score']} not in [0, 1]"
            assert "rerank_score" in r
            assert "original_score" in r
            expected = self._sigmoid(r["rerank_score"])
            assert abs(r["score"] - expected) < 1e-6

        # Sorted by score descending
        scores = [r["score"] for r in reranked]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_local_reranker_normalizes_scores(self, mock_config):
        """_rerank_with_local_model should apply sigmoid to raw CrossEncoder scores."""
        mock_config["reranking_mode"] = "baai-cpu"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)

        raw_scores = [3.1, -2.0, 0.5]
        results = [
            {"file_id": f"f{i}", "chunk_index": 0, "text": f"text {i}", "score": 0.5, "page_number": 1, "metadata": {}}
            for i in range(len(raw_scores))
        ]

        mock_cross_encoder = Mock()
        mock_cross_encoder.predict.return_value = raw_scores

        with patch.object(service, '_cpu_reranker', mock_cross_encoder, create=True):
            with patch('services.milvus_search.CrossEncoder', return_value=mock_cross_encoder):
                reranked = await service._rerank_with_local_model("test query", results, top_k=None)

        assert len(reranked) == 3
        for r in reranked:
            assert 0.0 <= r["score"] <= 1.0
            expected = self._sigmoid(r["rerank_score"])
            assert abs(r["score"] - expected) < 1e-6

        scores = [r["score"] for r in reranked]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_negative_logits_produce_below_half(self, mock_config):
        """Negative reranker logits should produce scores < 0.5 via sigmoid."""
        mock_config["reranking_mode"] = "qwen3-gpu"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)

        results = [
            {"file_id": "f0", "chunk_index": 0, "text": "irrelevant doc", "score": 0.5, "page_number": 1, "metadata": {}}
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"score": -5.0}]}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            reranked = await service._rerank_with_vllm("test", results, top_k=None, reranker_model="qwen3-gpu")

        assert reranked[0]["score"] < 0.5
        assert reranked[0]["rerank_score"] == -5.0

    @pytest.mark.asyncio
    async def test_zero_logit_produces_half(self, mock_config):
        """A logit of 0 should produce score = 0.5 via sigmoid."""
        mock_config["reranking_mode"] = "qwen3-gpu"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)

        results = [
            {"file_id": "f0", "chunk_index": 0, "text": "borderline doc", "score": 0.5, "page_number": 1, "metadata": {}}
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"score": 0.0}]}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            reranked = await service._rerank_with_vllm("test", results, top_k=None, reranker_model="qwen3-gpu")

        assert abs(reranked[0]["score"] - 0.5) < 1e-6


# =============================================================================
# RRF Score Normalization Tests
# =============================================================================


@pytest.mark.unit
class TestRRFScoreNormalization:
    """Test that RRF fusion scores are normalized to 0-1 range."""

    def _make_result(self, file_id, chunk_index=0, score=0.5):
        return {
            "file_id": file_id,
            "chunk_index": chunk_index,
            "text": f"text for {file_id}",
            "score": score,
            "page_number": 1,
            "metadata": {},
        }

    def test_fused_scores_in_0_1_range(self, mock_config):
        """All RRF fused scores should be normalized to [0, 1]."""
        service = MilvusSearchService(mock_config)

        dense = [
            self._make_result("d1", score=0.95),
            self._make_result("d2", score=0.85),
            self._make_result("d3", score=0.75),
        ]
        dense[0]["dense_score"] = 0.95
        dense[1]["dense_score"] = 0.85
        dense[2]["dense_score"] = 0.75

        sparse = [
            self._make_result("d2", score=0.90),
            self._make_result("d4", score=0.80),
            self._make_result("d1", score=0.70),
        ]
        sparse[0]["sparse_score"] = 0.90
        sparse[1]["sparse_score"] = 0.80
        sparse[2]["sparse_score"] = 0.70

        fused = service._fuse_results_rrf(dense, sparse, dense_weight=0.7, sparse_weight=0.3, k=60)

        for r in fused:
            assert 0.0 <= r["score"] <= 1.0, f"Score {r['score']} for {r['file_id']} not in [0, 1]"

    def test_best_result_gets_score_1(self, mock_config):
        """The top-ranked result after RRF should have score = 1.0."""
        service = MilvusSearchService(mock_config)

        dense = [self._make_result("d1", score=0.9), self._make_result("d2", score=0.8)]
        dense[0]["dense_score"] = 0.9
        dense[1]["dense_score"] = 0.8

        sparse = [self._make_result("d3", score=0.7)]
        sparse[0]["sparse_score"] = 0.7

        fused = service._fuse_results_rrf(dense, sparse, dense_weight=0.7, sparse_weight=0.3, k=60)

        assert fused[0]["score"] == 1.0

    def test_single_result_gets_score_1(self, mock_config):
        """A single result should get score = 1.0."""
        service = MilvusSearchService(mock_config)

        dense = [self._make_result("d1", score=0.9)]
        dense[0]["dense_score"] = 0.9

        fused = service._fuse_results_rrf(dense, [], dense_weight=0.7, sparse_weight=0.3, k=60)

        assert len(fused) == 1
        assert fused[0]["score"] == 1.0

    def test_raw_rrf_score_preserved(self, mock_config):
        """Raw RRF scores should be preserved in 'rrf_score' field."""
        service = MilvusSearchService(mock_config)

        dense = [self._make_result("d1", score=0.9), self._make_result("d2", score=0.8)]
        dense[0]["dense_score"] = 0.9
        dense[1]["dense_score"] = 0.8

        sparse = [self._make_result("d1", score=0.85)]
        sparse[0]["sparse_score"] = 0.85

        fused = service._fuse_results_rrf(dense, sparse, dense_weight=0.7, sparse_weight=0.3, k=60)

        for r in fused:
            assert "rrf_score" in r, f"Missing rrf_score for {r['file_id']}"
            assert r["rrf_score"] > 0, "Raw RRF score should be positive"

    def test_identical_scores_all_get_1(self, mock_config):
        """If all results have the same RRF score, all should get 1.0."""
        service = MilvusSearchService(mock_config)

        # Two docs each appearing only in dense at the same rank position is not possible,
        # but we can create equal scores by having docs appear in only one list each at same rank
        dense = [self._make_result("d1", score=0.9)]
        dense[0]["dense_score"] = 0.9
        sparse = [self._make_result("d2", score=0.9)]
        sparse[0]["sparse_score"] = 0.9

        # With k=60, rank=1: dense gives 0.7/61, sparse gives 0.3/61
        # But d1 only in dense (0.7/61 ≈ 0.01148) and d2 only in sparse (0.3/61 ≈ 0.00492)
        # These won't be equal. Let's use equal weights instead.
        fused = service._fuse_results_rrf(dense, sparse, dense_weight=0.5, sparse_weight=0.5, k=60)

        # Both at rank 1 in their respective list, equal weights → equal RRF scores
        assert fused[0]["score"] == 1.0
        assert fused[1]["score"] == 1.0

    def test_doc_in_both_lists_ranks_higher(self, mock_config):
        """A document appearing in both dense and sparse should rank higher."""
        service = MilvusSearchService(mock_config)

        dense = [
            self._make_result("shared", score=0.9),
            self._make_result("dense_only", score=0.8),
        ]
        dense[0]["dense_score"] = 0.9
        dense[1]["dense_score"] = 0.8

        sparse = [
            self._make_result("shared", score=0.85),
            self._make_result("sparse_only", score=0.7),
        ]
        sparse[0]["sparse_score"] = 0.85
        sparse[1]["sparse_score"] = 0.7

        fused = service._fuse_results_rrf(dense, sparse, dense_weight=0.7, sparse_weight=0.3, k=60)

        assert fused[0]["file_id"] == "shared"
        assert fused[0]["score"] == 1.0

    def test_empty_results_return_empty(self, mock_config):
        """Empty inputs should return empty list."""
        service = MilvusSearchService(mock_config)
        fused = service._fuse_results_rrf([], [], dense_weight=0.7, sparse_weight=0.3, k=60)
        assert fused == []

    def test_scores_monotonically_decrease(self, mock_config):
        """Normalized scores should be sorted descending."""
        service = MilvusSearchService(mock_config)

        dense = [self._make_result(f"d{i}", score=0.9 - i * 0.1) for i in range(5)]
        for i, r in enumerate(dense):
            r["dense_score"] = 0.9 - i * 0.1

        sparse = [self._make_result(f"s{i}", score=0.85 - i * 0.1) for i in range(3)]
        for i, r in enumerate(sparse):
            r["sparse_score"] = 0.85 - i * 0.1

        fused = service._fuse_results_rrf(dense, sparse, dense_weight=0.7, sparse_weight=0.3, k=60)

        scores = [r["score"] for r in fused]
        assert scores == sorted(scores, reverse=True)


# =============================================================================
# Hybrid Search Reranker Integration Tests
# =============================================================================


@pytest.mark.unit
class TestHybridSearchRerankerDecision:
    """Test that hybrid_search correctly decides whether to apply reranking."""

    @pytest.mark.asyncio
    async def test_hybrid_skips_reranker_when_mode_none(self, mock_config):
        """With reranking_mode='none', hybrid search should not call rerank_results."""
        mock_config["reranking_mode"] = "none"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)
        service.connected = True

        dense_results = [
            {"file_id": "d1", "chunk_index": 0, "text": "a", "score": 0.9, "dense_score": 0.9, "page_number": 1, "metadata": {}}
        ]

        with patch.object(service, 'semantic_search', return_value=dense_results):
            with patch.object(service, 'keyword_search', return_value=[]):
                with patch.object(service, 'get_accessible_partitions', return_value=["personal_test"]):
                    with patch.object(service, 'rerank_results', new_callable=AsyncMock) as mock_rerank:
                        await service.hybrid_search(
                            query_embedding=[0.1] * 768,
                            query_text="test",
                            user_id="test",
                            top_k=10,
                        )
                        mock_rerank.assert_not_called()

    @pytest.mark.asyncio
    async def test_hybrid_calls_reranker_when_mode_set(self, mock_config):
        """With reranking_mode='vllm' and enable_reranking=True, hybrid search should call rerank_results."""
        mock_config["reranking_mode"] = "vllm"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)
        service.connected = True

        dense_results = [
            {"file_id": "d1", "chunk_index": 0, "text": "a", "score": 0.9, "dense_score": 0.9, "page_number": 1, "metadata": {}}
        ]

        with patch.object(service, 'semantic_search', return_value=dense_results):
            with patch.object(service, 'keyword_search', return_value=[]):
                with patch.object(service, 'get_accessible_partitions', return_value=["personal_test"]):
                    with patch.object(service, 'rerank_results', new_callable=AsyncMock, return_value=dense_results) as mock_rerank:
                        await service.hybrid_search(
                            query_embedding=[0.1] * 768,
                            query_text="test",
                            user_id="test",
                            top_k=10,
                        )
                        mock_rerank.assert_called_once()

    @pytest.mark.asyncio
    async def test_hybrid_respects_use_reranker_false(self, mock_config):
        """use_reranker=False should skip reranking even if mode is configured."""
        mock_config["reranking_mode"] = "vllm"
        mock_config["enable_reranking"] = True
        service = MilvusSearchService(mock_config)
        service.connected = True

        dense_results = [
            {"file_id": "d1", "chunk_index": 0, "text": "a", "score": 0.9, "dense_score": 0.9, "page_number": 1, "metadata": {}}
        ]

        with patch.object(service, 'semantic_search', return_value=dense_results):
            with patch.object(service, 'keyword_search', return_value=[]):
                with patch.object(service, 'get_accessible_partitions', return_value=["personal_test"]):
                    with patch.object(service, 'rerank_results', new_callable=AsyncMock) as mock_rerank:
                        await service.hybrid_search(
                            query_embedding=[0.1] * 768,
                            query_text="test",
                            user_id="test",
                            top_k=10,
                            use_reranker=False,
                        )
                        mock_rerank.assert_not_called()
