"""
Unit tests for Milvus search service.

These tests focus on pure functions that don't require a real Milvus connection.
Search functionality is tested in integration tests.
"""

import pytest
from unittest.mock import Mock, patch
from services.milvus_search import MilvusSearchService


@pytest.mark.unit
class TestMilvusSearchService:
    """Test MilvusSearchService class."""
    
    def test_init(self, mock_config):
        """Test service initialization."""
        service = MilvusSearchService(mock_config)
        
        assert service.host == "localhost"
        assert service.port == 19530
        assert service.collection_name == "test_collection"
        assert not service.connected
    
    @patch('services.milvus_search.connections')
    @patch('services.milvus_search.Collection')
    def test_connect(self, mock_collection_class, mock_connections, mock_config):
        """Test connecting to Milvus."""
        service = MilvusSearchService(mock_config)
        mock_collection = Mock()
        mock_collection_class.return_value = mock_collection
        
        service.connect()
        
        assert service.connected
        mock_connections.connect.assert_called_once()
        mock_collection.load.assert_called_once()
    
    def test_rrf_fusion(self, mock_config):
        """Test Reciprocal Rank Fusion algorithm."""
        service = MilvusSearchService(mock_config)
        
        dense_results = [
            {"file_id": "doc1", "chunk_index": 0, "text": "a", "score": 0.9, "dense_score": 0.9, "page_number": 1, "metadata": {}},
            {"file_id": "doc2", "chunk_index": 0, "text": "b", "score": 0.8, "dense_score": 0.8, "page_number": 1, "metadata": {}},
        ]
        
        sparse_results = [
            {"file_id": "doc2", "chunk_index": 0, "text": "b", "score": 0.95, "sparse_score": 0.95, "page_number": 1, "metadata": {}},
            {"file_id": "doc3", "chunk_index": 0, "text": "c", "score": 0.7, "sparse_score": 0.7, "page_number": 1, "metadata": {}},
        ]
        
        fused = service._fuse_results_rrf(
            dense_results=dense_results,
            sparse_results=sparse_results,
            dense_weight=0.7,
            sparse_weight=0.3,
            k=60,
        )
        
        # doc2 should rank first (appears in both)
        assert fused[0]["file_id"] == "doc2"
        assert "dense_score" in fused[0]
        assert "sparse_score" in fused[0]
    
    def test_health_check(self, mock_config):
        """Test health check."""
        service = MilvusSearchService(mock_config)
        service.connected = True
        
        mock_collection = Mock()
        mock_collection.query = Mock(return_value=[])
        service.collection = mock_collection
        
        healthy = service.health_check()
        
        assert healthy is True
        mock_collection.query.assert_called_once()

