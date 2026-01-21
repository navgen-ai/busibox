"""
Embedding API Configuration.

Environment variables:
- FASTEMBED_MODEL: Model name (default: BAAI/bge-large-en-v1.5)
- EMBEDDING_BATCH_SIZE: Batch size for embedding generation (default: 32)
- PORT: Server port (default: 8004)
"""

import os
from dataclasses import dataclass


# Model dimensions for FastEmbed BGE models
MODEL_DIMENSIONS = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    # Short aliases
    "bge-small-en-v1.5": 384,
    "bge-base-en-v1.5": 768,
    "bge-large-en-v1.5": 1024,
}


@dataclass
class Config:
    """Embedding API configuration."""
    
    # Model configuration
    model_name: str = os.getenv("FASTEMBED_MODEL", "BAAI/bge-large-en-v1.5")
    batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
    
    # Server configuration
    port: int = int(os.getenv("PORT", "8005"))
    
    @property
    def dimension(self) -> int:
        """Get embedding dimension for the configured model."""
        return MODEL_DIMENSIONS.get(self.model_name, 1024)


# Global config instance
config = Config()
