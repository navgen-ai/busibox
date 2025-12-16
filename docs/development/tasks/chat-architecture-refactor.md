# Chat Architecture Refactor Plan

**Status**: Planning  
**Priority**: High  
**Created**: 2025-12-16  
**Related**: Search/Insights Migration (Complete)

## Overview

Refactor the chat system to use a centralized architecture with proper routing through the chat-agent dispatcher and unified history storage in search-api.

## Current Issues

1. **UI Issues**:
   - Chat input z-index overlaps model dropdown
   - Missing "auto" model option for dispatcher routing
   - Not loading chat history
   - Not sending messages properly

2. **Architecture Issues**:
   - Messages go directly to liteLLM instead of chat-agent dispatcher
   - Each app manages its own chat history (database duplication)
   - No centralized chat state management
   - Mix of client and server components causing API route proliferation

## Proposed Architecture

### 1. Centralized Chat History & Insights (agent-api)

**Rationale**: 
- Chat history is core agent context - belongs with agent operations
- Insights are agent memories/context, not search functionality
- Keeps all agent-related state in one service
- search-api focuses purely on search (documents, web)

**New Endpoints** (agent-api):
```
# Chat History
POST   /chat/conversations              - Create conversation
GET    /chat/conversations              - List user's conversations
GET    /chat/conversations/{id}         - Get conversation with messages
POST   /chat/conversations/{id}/messages - Add message
DELETE /chat/conversations/{id}         - Delete conversation
PATCH  /chat/conversations/{id}         - Update conversation metadata

# Agent Insights/Memories (moved from search-api)
POST   /insights/init                   - Initialize insights collection
POST   /insights                        - Insert insights
POST   /insights/search                 - Search insights
DELETE /insights/conversation/{id}      - Delete conversation insights
DELETE /insights/user/{id}              - Delete user insights
GET    /insights/stats/{id}             - Get insight stats
```

**Schema** (PostgreSQL in agent-api):
```sql
CREATE TABLE conversations (
  id UUID PRIMARY KEY,
  user_id VARCHAR NOT NULL,
  title VARCHAR,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  metadata JSONB
);

CREATE TABLE messages (
  id UUID PRIMARY KEY,
  conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
  role VARCHAR NOT NULL, -- 'user', 'assistant', 'system'
  content TEXT NOT NULL,
  model VARCHAR, -- Which model generated this (for assistant messages)
  attachments JSONB, -- Array of attachment metadata
  search_results JSONB, -- Web/doc search results used
  created_at TIMESTAMP NOT NULL,
  metadata JSONB
);

CREATE INDEX idx_conversations_user_id ON conversations(user_id);
CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
```

### 2. Chat Flow Architecture

```
┌─────────────────┐
│  busibox-app    │
│  Chat Component │
│  (Server)       │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────┐
│  chat-agent dispatcher                              │
│  (agent-lxc:8000)                                   │
│                                                      │
│  Responsibilities:                                  │
│  - Model selection (if "auto")                      │
│  - Tool selection (web/doc search)                  │
│  - Route to appropriate backend                     │
│  - Aggregate responses                              │
└────────┬────────────────────────────────────────────┘
         │
         ├──────────────┬──────────────┬──────────────┐
         ▼              ▼              ▼              ▼
    ┌────────┐    ┌─────────┐   ┌─────────┐   ┌─────────┐
    │liteLLM │    │Web      │   │Doc      │   │Custom   │
    │        │    │Search   │   │Search   │   │Agents   │
    └────────┘    └─────────┘   └─────────┘   └─────────┘
         │              │              │              │
         └──────────────┴──────────────┴──────────────┘
                        │
                        ▼
                ┌───────────────────────────┐
                │  agent-api                │
                │  /chat/* - Store history  │
                │  /insights/* - Memories   │
                └───────────────────────────┘
```

### 3. busibox-app Chat Components

**Server Component Approach**:
```typescript
// src/components/chat/Chat.tsx (Server Component)
import { ChatMessages } from './ChatMessages';
import { ChatInput } from './ChatInput'; // Client component for interactivity

export async function Chat({ conversationId, userId }: Props) {
  // Fetch history server-side
  const messages = await fetchChatHistory(conversationId, userId);
  
  return (
    <div>
      <ChatMessages messages={messages} />
      <ChatInput 
        conversationId={conversationId}
        onSend={sendMessage} // Server action
      />
    </div>
  );
}

// Server action
async function sendMessage(formData: FormData) {
  'use server';
  
  const content = formData.get('content');
  const model = formData.get('model') || 'auto';
  
  // Call chat-agent dispatcher
  const response = await fetch('http://agent-lxc:8000/chat', {
    method: 'POST',
    body: JSON.stringify({
      conversation_id: conversationId,
      message: content,
      model,
      // ... other options
    }),
  });
  
  // Dispatcher handles:
  // 1. Storing message in search-api
  // 2. Routing to appropriate backend
  // 3. Returning response
  
  revalidatePath(`/chat/${conversationId}`);
}
```

**Benefits**:
- No API routes needed in consuming apps
- Server-side data fetching
- Automatic revalidation
- Simpler deployment

### 4. Model Selection

**Models**:
```typescript
const MODELS = [
  {
    id: 'auto',
    name: 'Auto (Recommended)',
    description: 'Let the dispatcher choose the best model and tools',
    capabilities: { multimodal: true, toolCalling: true },
  },
  {
    id: 'chat',
    name: 'Chat',
    description: 'General conversation',
    capabilities: { multimodal: false, toolCalling: false },
  },
  {
    id: 'research',
    name: 'Research',
    description: 'Research and analysis (slower, more powerful model)',
    capabilities: { multimodal: false, toolCalling: true },
  },
  {
    id: 'frontier',
    name: 'Frontier',
    description: 'Claude via AWS (supports tools)',
    capabilities: { multimodal: true, toolCalling: true },
  },
];
```

**Auto Mode Logic** (in dispatcher):
```python
def select_model_and_tools(message: str, attachments: list, history: list):
    """
    Analyze message and context to select model and tools.
    """
    needs_vision = has_image_attachments(attachments)
    needs_web_search = detect_web_search_intent(message)
    needs_doc_search = detect_doc_search_intent(message, history)
    
    if needs_vision:
        model = 'vision-model'
    elif needs_complex_reasoning(message):
        model = 'reasoning-model'
    else:
        model = 'chat-model'
    
    tools = []
    if needs_web_search:
        tools.append('web_search')
    if needs_doc_search:
        tools.append('doc_search')
    
    return model, tools
```

### 5. UI Fixes

**Z-index Issue**:
```typescript
// MessageInput.tsx
<div className="relative z-10"> {/* Input container */}
  <ModelSelector className="z-20" /> {/* Dropdown above input */}
  <textarea className="z-10" />
</div>
```

**Model Dropdown**:
- Add "auto" as first option (default)
- Show capabilities badges
- Disable search toggles if model doesn't support tools

## Implementation Plan

### Phase 1: agent-api Chat History & Insights Migration (Week 1) ✅ COMPLETED
- [x] Add PostgreSQL schema for conversations/messages (migration 003)
- [x] Implement chat history endpoints (conversations.py)
- [x] Move insights endpoints from search-api to agent-api
- [x] Update insights to use agent-api's Milvus connection
- [x] Add tests (test_chat_flow.py)
- [x] Update OpenAPI spec (agent-api.yaml)
- [x] Implement enhanced chat endpoint with routing
- [x] Add auto model selection service
- [x] Add streaming support
- [ ] Deploy to test environment (ready for deployment)

### Phase 2: Update busibox-app to use agent-api (Week 1)
- [ ] Update insights client to point to agent-api
- [ ] Add chat history client for agent-api
- [ ] Publish new version
- [ ] Update ai-portal and other apps

### Phase 3: chat-agent Dispatcher Updates (Week 1-2)
- [ ] Implement "auto" mode logic
- [ ] Add model/tool selection
- [ ] Integrate with search-api for history storage
- [ ] Add streaming support
- [ ] Update tests

### Phase 4: busibox-app Chat Components (Week 2)
- [ ] Refactor to server components where possible
- [ ] Add chat history client
- [ ] Fix UI issues (z-index, model dropdown)
- [ ] Add "auto" model option
- [ ] Update tests
- [ ] Publish new version

### Phase 5: AI Portal Integration (Week 2-3)
- [ ] Remove local chat history management
- [ ] Update to use new busibox-app components
- [ ] Remove unnecessary API routes
- [ ] Test end-to-end
- [ ] Deploy

### Phase 6: Deprecate search-api Insights & Documentation (Week 3)
- [ ] Mark search-api insights endpoints as deprecated
- [ ] Remove insights code from search-api (after migration complete)
- [ ] Document new architecture
- [ ] Create migration guide
- [ ] Update other apps (agent-client, etc.)

## Benefits

1. **Centralized State**: Single source of truth for chat history
2. **Simplified Apps**: No need for each app to manage chat state
3. **Better Routing**: Dispatcher intelligently routes requests
4. **Server Components**: Fewer API routes, better performance
5. **Consistent UX**: Same chat experience across all apps
6. **Easier Deployment**: Less database setup per app

## Migration Strategy

### Chat History Migration

1. Deploy agent-api chat endpoints
2. Deploy updated chat-agent dispatcher  
3. Publish busibox-app with new components (backward compatible)
4. Gradually migrate apps one by one
5. Deprecate old chat implementations

## Open Questions (Resolved)

1. **Streaming**: How to handle streaming with server components?
   [x] Use Server-Sent Events (SSE) with client component
  

2. **Real-time Updates**: How to handle new messages in multi-user conversations?
   [x] WebSocket connection to dispatcher?

3. **Attachments**: Store in search-api or keep in ingest-api?
   [x] Keep in ingest-api, reference by ID in messages

4. **Backwards Compatibility**: Support old chat implementations during migration?
   [x] No, remove old chat implementations

## Related Work

- ✅ Search/Insights Migration (Complete)
- ⏳ Chat Architecture Refactor (This document)
- ⏳ Agent Dispatcher Improvements
- ⏳ Multi-user Chat Support

## References

- `busibox/openapi/search-api.yaml` - Search API spec
- `agent-server/` - Chat-agent dispatcher
- `busibox-app/src/components/chat/` - Current chat components
- `ai-portal/src/app/chat/` - Current AI Portal chat

