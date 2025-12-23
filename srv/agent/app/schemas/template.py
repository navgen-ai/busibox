"""
Pydantic schemas for template generation.
"""

from typing import List, Literal

from pydantic import BaseModel, Field


class TemplateSection(BaseModel):
    """A section within a summary template."""
    
    name: str = Field(..., description="Clear section title")
    description: str = Field(..., description="What this section captures")
    type: Literal["text", "number", "array", "object"] = Field(
        ...,
        description="Data type for this section"
    )
    prompt: str = Field(..., description="Detailed extraction instructions")
    required: bool = Field(
        default=False,
        description="Whether this section is critical"
    )


class GeneratedTemplate(BaseModel):
    """Structured output for template generation."""
    
    name: str = Field(..., description="Descriptive template name")
    description: str = Field(..., description="Context and usage information")
    sections: List[TemplateSection] = Field(
        ...,
        description="Template sections for extraction",
        min_length=1
    )

