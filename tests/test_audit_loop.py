"""
Unit tests for the Closed-Loop Cascade Audit & Rework Engine.
Tests:
1. Local Hard Gate (0 tokens: Python AST, YAML, JSON, PowerShell param rule)
2. Architect Audit JSON contract parsing
3. Orchestrator Cascade: PASS on 1st attempt
4. Orchestrator Cascade: REJECT -> Rework -> PASS on 2nd attempt
5. Orchestrator Cascade: Local Hard Gate failure -> Rework -> PASS
6. Orchestrator Cascade: Exhaustion -> ESCALATED
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from core.audit import LocalHardGate, ArchitectAudit
from core.orchestrator import HybridOrchestrator
from core.storage import AnalyticsStorage


# ==============================================================================
# 1. Local Hard Gate Unit Tests
# ==============================================================================

def test_local_hard_gate_python_syntax():
    gate = LocalHardGate()

    # Valid Python code in markdown fence
    valid_py = "```python\ndef hello(name: str) -> str:\n    return f'Hello, {name}'\n```"
    passed, issues = gate.validate_code(valid_py)
    assert passed is True
    assert len(issues) == 0

    # Broken Python syntax
    broken_py = "```python\ndef hello(name:\n    return 42\n```"
    passed, issues = gate.validate_code(broken_py)
    assert passed is False
    assert len(issues) == 1
    assert issues[0]["type"] == "SyntaxError"
    assert issues[0]["severity"] == "critical"


def test_local_hard_gate_json_and_yaml():
    gate = LocalHardGate()

    # Valid JSON
    valid_json = "```json\n{\"status\": \"ok\", \"count\": 10}\n```"
    passed, issues = gate.validate_code(valid_json)
    assert passed is True

    # Broken JSON
    broken_json = "```json\n{\"status\": \"ok\",\n```"
    passed, issues = gate.validate_code(broken_json)
    assert passed is False
    assert any(i["type"] == "JsonSyntaxError" for i in issues)

    # Valid YAML
    valid_yaml = "```yaml\nserver:\n  host: 0.0.0.0\n  port: 8000\n```"
    passed, issues = gate.validate_code(valid_yaml)
    assert passed is True

    # Broken YAML
    broken_yaml = "```yaml\nserver:\n  host: [unclosed\n```"
    passed, issues = gate.validate_code(broken_yaml)
    assert passed is False
    assert any(i["type"] == "YamlSyntaxError" for i in issues)


def test_local_hard_gate_powershell_param_rule():
    gate = LocalHardGate()

    # Correct PowerShell: param() is first executable statement
    valid_ps1 = """```powershell
# Header comment
param (
    [switch]$Test
)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Write-Host "Started"
```"""
    passed, issues = gate.validate_code(valid_ps1)
    assert passed is True
    assert len(issues) == 0

    # Broken PowerShell: command executed before param()
    broken_ps1 = """```powershell
# Header comment
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
param (
    [switch]$Test
)
Write-Host "Started"
```"""
    passed, issues = gate.validate_code(broken_ps1)
    assert passed is False
    assert any(i["type"] == "PowerShellParamPositionError" for i in issues)


# ==============================================================================
# 2. Architect Audit Contract Tests
# ==============================================================================

def test_architect_audit_parsing_pass():
    auditor = ArchitectAudit()

    # Valid JSON in markdown block
    response = """Here is the audit verdict:
```json
{
  "verdict": "PASS",
  "issues": []
}
```"""
    res = auditor.parse_audit_response(response)
    assert res["verdict"] == "PASS"
    assert len(res["issues"]) == 0


def test_architect_audit_parsing_reject():
    auditor = ArchitectAudit()

    response = """```json
{
  "verdict": "REJECT",
  "issues": [
    {
      "file": "core/auth.py",
      "severity": "critical",
      "description": "Missing timeout in request",
      "fix_instruction": "Add timeout=30.0 to client"
    }
  ]
}
```"""
    res = auditor.parse_audit_response(response)
    assert res["verdict"] == "REJECT"
    assert len(res["issues"]) == 1
    assert res["issues"][0]["file"] == "core/auth.py"
    assert res["issues"][0]["severity"] == "critical"


# ==============================================================================
# 3. Closed-Loop Cascade Orchestrator Scenarios
# ==============================================================================

class MockEngine:
    """Mock engine simulating Browser and Architect interactions."""
    def __init__(self):
        self.responses = []
        self.query_history = []

    async def query(self, prompt: str, model_id: str) -> str:
        self.query_history.append((model_id, prompt))
        if self.responses:
            return self.responses.pop(0)
        return "Default Plan or Code"


@pytest.mark.asyncio
async def test_cascade_pass_first_attempt():
    """Scenario 1: Blueprint -> Clean Code -> Gate PASS -> Audit PASS -> Final Output."""
    engine = MockEngine()
    # 1. Plan response
    # 2. Architect Audit response (PASS)
    engine.responses = [
        "### Plan: 1. Create calculator class. 2. Add methods.",
        '```json\n{"verdict": "PASS", "issues": []}\n```'
    ]

    orch = HybridOrchestrator(browser_engine=engine)
    orch.ollama = AsyncMock()
    orch.ollama.is_authenticated = AsyncMock(return_value=True)
    orch.ollama.list_models = AsyncMock(return_value=["qwen2.5-coder:14b"])
    # Executor produces valid Python code
    orch.ollama.send_prompt = AsyncMock(return_value="```python\nclass Calc:\n    def add(self, a, b):\n        return a + b\n```")

    result = await orch._run_cascade_architect("Создай калькулятор", req_id="test_req_1", inject_signature=True)

    assert "Аудит: PASS" in result
    assert "class Calc:" in result
    assert "[Plan] Архитектурный план" in result
    assert "[Code] Реализация" in result


@pytest.mark.asyncio
async def test_cascade_reject_then_rework_then_pass():
    """Scenario 2: Plan -> Code 1 -> Audit REJECT -> Rework Code 2 -> Audit PASS."""
    engine = MockEngine()
    # 1. Plan
    # 2. Audit 1 (REJECT)
    # 3. Audit 2 (PASS)
    engine.responses = [
        "### Plan: Implement AuthService with token verification.",
        '```json\n{"verdict": "REJECT", "issues": [{"file": "auth.py", "severity": "critical", "description": "Token verification is empty", "fix_instruction": "Implement jwt.decode"}]}\n```',
        '```json\n{"verdict": "PASS", "issues": []}\n```'
    ]

    orch = HybridOrchestrator(browser_engine=engine)
    orch.ollama = AsyncMock()
    orch.ollama.is_authenticated = AsyncMock(return_value=True)
    orch.ollama.list_models = AsyncMock(return_value=["qwen2.5-coder:14b"])

    # Attempt 1 returns stub, Attempt 2 returns full code
    orch.ollama.send_prompt = AsyncMock(side_effect=[
        "```python\nclass Auth:\n    def verify(self): pass\n```",
        "```python\nclass Auth:\n    def verify(self, token):\n        return True\n```"
    ])

    result = await orch._run_cascade_architect("Создай сервис авторизации", req_id="test_req_2", inject_signature=True)

    assert "Аудит: PASS (2 итер.)" in result
    assert "return True" in result
    assert orch.ollama.send_prompt.call_count == 2


@pytest.mark.asyncio
async def test_cascade_local_hard_gate_failure_then_fixed():
    """Scenario 3: Code 1 fails Local Hard Gate (syntax error) -> Rework -> Code 2 passes -> Audit PASS."""
    engine = MockEngine()
    # 1. Plan
    # 2. Audit 1 for Code 2 (PASS)
    # Notice: Architect model is NOT called for Code 1 because Hard Gate caught it at 0 tokens!
    engine.responses = [
        "### Plan: Implement Database Handler.",
        '```json\n{"verdict": "PASS", "issues": []}\n```'
    ]

    orch = HybridOrchestrator(browser_engine=engine)
    orch.ollama = AsyncMock()
    orch.ollama.is_authenticated = AsyncMock(return_value=True)
    orch.ollama.list_models = AsyncMock(return_value=["qwen2.5-coder:14b"])

    # Attempt 1 has broken syntax (unclosed parenthesis)
    # Attempt 2 has clean syntax
    orch.ollama.send_prompt = AsyncMock(side_effect=[
        "```python\ndef broken_func(\n    return 1\n```",
        "```python\ndef working_func():\n    return 1\n```"
    ])

    result = await orch._run_cascade_architect("Создай базу", req_id="test_req_3", inject_signature=True)

    assert "working_func" in result
    assert "Аудит: PASS" in result
    # Verify engine.query was only called twice (Plan + 1 audit, NOT 2 audits!)
    assert len(engine.query_history) == 2


@pytest.mark.asyncio
async def test_cascade_exhaustion_escalated():
    """Scenario 4: Audit continuously rejects -> Retries exhausted -> Status ESCALATED."""
    engine = MockEngine()
    # 1. Plan
    # 2. Audit attempt 1 (REJECT)
    # 3. Audit attempt 2 (REJECT)
    # 4. Audit attempt 3 (REJECT)
    engine.responses = [
        "### Plan: Complex Cryptographic Ledger.",
        '```json\n{"verdict": "REJECT", "issues": [{"file": "ledger.py", "severity": "critical", "description": "Missing digital signature verification", "fix_instruction": "Implement ECDSA"}]}\n```',
        '```json\n{"verdict": "REJECT", "issues": [{"file": "ledger.py", "severity": "critical", "description": "Missing digital signature verification", "fix_instruction": "Implement ECDSA"}]}\n```',
        '```json\n{"verdict": "REJECT", "issues": [{"file": "ledger.py", "severity": "critical", "description": "Still missing signature check", "fix_instruction": "Implement ECDSA"}]}\n```',
    ]

    orch = HybridOrchestrator(browser_engine=engine)
    orch.max_rework_iterations = 2
    orch.ollama = AsyncMock()
    orch.ollama.is_authenticated = AsyncMock(return_value=True)
    orch.ollama.list_models = AsyncMock(return_value=["qwen2.5-coder:14b"])
    orch.ollama.send_prompt = AsyncMock(return_value="```python\nclass Ledger:\n    pass\n```")

    result = await orch._run_cascade_architect("Сделай защищенный реестр", req_id="test_req_4", inject_signature=True)

    assert "ESCALATED" in result
    assert "Требуется ручное подтверждение" in result
    assert "Still missing signature check" in result
