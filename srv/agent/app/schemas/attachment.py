"""
Pydantic schemas for attachment handling decisions.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AttachmentDecision(BaseModel):
    """Structured output for attachment handling decisions."""
    
    action: Literal["none", "upload", "inline", "reject", "preprocess"] = Field(
        ...,
        description="Action to take on the attachment"
    )
    target: Literal["none", "doc-library", "temp"] = Field(
        ...,
        description="Target storage location"
    )
    model_hint: Literal["text", "multimodal", "code", "none"] = Field(
        default="none",
        description="Model hint for processing"
    )
    note: str = Field(
        ...,
        description="Brief explanation of the decision"
    )

