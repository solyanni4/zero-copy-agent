import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_mcp_initialize():
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"}
        }
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    assert "protocolVersion" in data["result"]
    assert "capabilities" in data["result"]
    assert data["result"]["serverInfo"]["name"] == "zero-copy-agent"

def test_mcp_tools_list():
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 2
    tools = data["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "cascade_architect" in tool_names
    assert "local_gpu_code" in tool_names
    assert "gemini_deep_analysis" in tool_names
    assert "get_token_savings" in tool_names
    assert "compress_code_context" in tool_names

def test_mcp_call_savings():
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "get_token_savings",
            "arguments": {}
        }
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 3
    content = data["result"]["content"]
    assert len(content) > 0
    assert "Метрики экономии" in content[0]["text"]

def test_mcp_call_compress():
    code = """
    def hello_world():
        print("Hello")
        
        
        return True
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "compress_code_context",
            "arguments": {"code": code}
        }
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 4
    content = data["result"]["content"]
    assert len(content) > 0
    assert "Результат L1-сжатия" in content[0]["text"]

def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mcp_enabled"] is True
    assert "/sse" in data["mcp_sse_url"]

def test_mcp_sse_endpoint_event():
    with client.stream("GET", "/sse") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        lines = []
        for line in response.iter_lines():
            if line:
                lines.append(line)
            if len(lines) >= 2:
                break
        # First event must be endpoint
        assert any("event: endpoint" in l for l in lines)
        assert any("data: /messages?session_id=" in l for l in lines)

