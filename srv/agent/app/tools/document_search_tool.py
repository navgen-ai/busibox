"""Document search tool for RAG agents."""
import structlog
from typing import List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Tool, RunContext

from app.agents.core import BusiboxDeps

logger = structlog.get_logger()


class DocumentSearchInput(BaseModel):
    """Input schema for document search tool."""
    query: str = Field(description="Search query to find relevant documents")
    limit: int = Field(default=5, description="Maximum number of results (default 5, max 50)")
    mode: str = Field(default="hybrid", description="Search mode: hybrid, semantic, or keyword")
    file_ids: Optional[List[str]] = Field(default=None, description="Optional list of file IDs to filter")


class SearchResultItem(BaseModel):
    """Individual search result."""
    file_id: str = Field(description="Unique file identifier for citations")
    filename: str = Field(description="Name of the source document")
    text: str = Field(description="Relevant text excerpt from the document")
    score: float = Field(description="Relevance score")
    page_number: Optional[int] = Field(default=None, description="Page number if available")
    chunk_index: int = Field(default=0, description="Chunk index within the document")


class DocumentSearchOutput(BaseModel):
    """Output schema for document search tool."""
    found: bool = Field(description="Whether relevant documents were found")
    result_count: int = Field(description="Number of results returned")
    context: str = Field(description="Formatted context from search results for LLM")
    results: List[SearchResultItem] = Field(description="List of search results with metadata")
    graph_context: Optional[str] = Field(default=None, description="Graph entity context if available")
    error: Optional[str] = Field(default=None, description="Error message if search failed")


async def search_documents(
    ctx: RunContext[BusiboxDeps],
    query: str,
    limit: int = 5,
    mode: str = "hybrid",
    file_ids: Optional[List[str]] = None,
) -> DocumentSearchOutput:
    """
    Search through user documents to find relevant information.
    
    This tool performs semantic, keyword, or hybrid search across the user's
    document library. Results are automatically filtered based on user permissions.
    
    When called from a document-scoped chat, file_ids are automatically injected
    from the application context so the search is restricted to the relevant documents.
    
    Args:
        ctx: RunContext with authenticated BusiboxClient
        query: Search query string
        limit: Maximum number of results (default: 5, max: 50)
        mode: Search mode - "hybrid" (recommended), "semantic", or "keyword"
        file_ids: Optional list of file IDs to restrict search
    """
    try:
        effective_file_ids = file_ids
        if not effective_file_ids and ctx.deps.metadata:
            ctx_file_ids = ctx.deps.metadata.get("file_ids")
            if ctx_file_ids and isinstance(ctx_file_ids, list):
                effective_file_ids = ctx_file_ids
                logger.info(
                    "document_search: injecting file_ids from metadata context",
                    extra={"count": len(effective_file_ids)},
                )

        response = await ctx.deps.busibox_client.search(
            query=query,
            top_k=min(limit, 50),
            mode=mode,
            file_ids=effective_file_ids,
            rerank=True,
            expand_graph=True,
        )
        
        results = response.get("results", [])
        
        if not results or len(results) == 0:
            return DocumentSearchOutput(
                found=False,
                result_count=0,
                context="No relevant documents found for your query.",
                results=[],
            )
        
        formatted_results = []
        context_parts = []
        
        for idx, result in enumerate(results, 1):
            fid = result.get("file_id", "unknown")
            fname = result.get("filename") or f"Document {fid[:8]}"
            page_num = result.get("page_number") if result.get("page_number", 0) > 0 else None

            result_item = SearchResultItem(
                file_id=fid,
                filename=fname,
                text=result.get("text", ""),
                score=result.get("score", 0.0),
                page_number=page_num,
                chunk_index=result.get("chunk_index", 0),
            )
            formatted_results.append(result_item)
            
            source_parts = [fname]
            if page_num:
                source_parts.append(f"p.{page_num}")
            source_ref = ", ".join(source_parts)

            context_parts.append(
                f"--- Source {idx} [{source_ref}] (file_id:{fid}) ---\n{result.get('text', '')}"
            )
        
        full_context = "\n\n".join(context_parts)
        full_context += (
            "\n\nIMPORTANT: When citing information from these sources, always include "
            "a citation using this format: [Source: filename, p.N](doc:file_id) — "
            "for example: [Source: report.pdf, p.5](doc:abc-123-def)"
        )

        graph_context_str: Optional[str] = None
        graph_data = response.get("graph")
        if graph_data and graph_data.get("graph_context"):
            graph_context_str = graph_data["graph_context"]
            full_context += f"\n\n--- Graph Context ---\n{graph_context_str}"
        
        return DocumentSearchOutput(
            found=True,
            result_count=len(formatted_results),
            context=full_context,
            results=formatted_results,
            graph_context=graph_context_str,
        )
    
    except Exception as e:
        error_msg = f"Search failed: {str(e)}"
        return DocumentSearchOutput(
            found=False,
            result_count=0,
            context="",
            results=[],
            error=error_msg,
        )


document_search_tool = Tool(
    search_documents,
    takes_ctx=True,
    name="document_search",
    description="""Search through the user's uploaded documents to find relevant information.
Use this tool when:
- The user asks a question that might be answered by their documents
- You need to find specific information from uploaded files
- You want to provide context-aware answers based on the user's data

The tool performs hybrid search (combining semantic and keyword matching) for best results.
Results are automatically filtered based on user permissions (RLS).

IMPORTANT: When you use information from search results in your response, always cite the 
source using this format: [Source: filename, p.N](doc:file_id)

Example: "What does the compliance document say about data retention?"
""",
)








