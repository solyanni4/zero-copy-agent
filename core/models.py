import time
from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field

class ChatMessage(BaseModel):
    role: str
    content: str
    name: Optional[str] = None

class ChatCompletionRequest(BaseModel):
    model: str = "gemini-advanced"
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    user: Optional[str] = None
    response_format: Optional[Union[Dict[str, Any], Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    functions: Optional[List[Dict[str, Any]]] = None

class AddKeyRequest(BaseModel):
    provider: str
    api_key: str
    alias: Optional[str] = None

class PlaygroundRequest(BaseModel):
    prompt: str
    model: Optional[str] = None

class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str

class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: Optional[str] = "stop"

class DeltaMessage(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None

class StreamChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: Optional[str] = None

class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[Choice]
    usage: Usage

class ChatCompletionStreamResponse(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[StreamChoice]

class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = 1731500000
    owned_by: str = "zero-copy-agent"

class ModelListResponse(BaseModel):
    object: str = "list"
    data: List[ModelCard]
