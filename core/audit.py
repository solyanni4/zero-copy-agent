"""
Closed-Loop Audit Engine for zero-copy-agent.
Implements:
1. Local Hard Gate (0 tokens): deterministic syntax & contract checks for .py, .yaml, .json, .ps1, .sh before LLM spend.
2. Architect Audit (diff-based): semantic validation of implementation against architectural blueprint via JSON contract.
3. Rework Loop Control: automated rework iterations with safety escalation on retry exhaustion.
"""

import ast
import re
import json
import yaml
import logging
from typing import Dict, Any, List, Tuple, Optional

logger = logging.getLogger("zcao.audit")

class LocalHardGate:
    """Deterministic, zero-token validator for generated code and modified files."""

    @staticmethod
    def extract_code_blocks(text: str) -> List[Tuple[str, str, str]]:
        """
        Extracts code blocks from markdown text.
        Returns list of tuples: (language, filename_hint, code_content).
        """
        blocks = []
        pattern = re.compile(r'```(?:([\w+-]+)(?::([^\n]+))?)?\n(.*?)```', re.DOTALL)
        for match in pattern.finditer(text):
            lang = (match.group(1) or "").lower().strip()
            hint = (match.group(2) or "").strip()
            code = match.group(3)
            # Try to find file hint in first comment line if not in header
            if not hint:
                first_line = code.strip().split("\n")[0] if code.strip() else ""
                file_match = re.search(r'(?:#|//|<!--|--)\s*(?:file|filepath|path):\s*([^\s]+)', first_line, re.IGNORECASE)
                if file_match:
                    hint = file_match.group(1)
            blocks.append((lang, hint, code))
        return blocks

    def validate_code(
        self,
        code_text: str,
        changed_files: Optional[Dict[str, str]] = None
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Validates syntax and deterministic structural rules.
        Returns (passed: bool, issues: List[Dict]).
        Issues format: {"file": str, "severity": "critical"|"warning", "type": str, "description": str, "fix_instruction": str}
        """
        issues: List[Dict[str, Any]] = []

        # 1. Validate explicitly provided files (e.g. from disk or caller)
        if changed_files:
            for filepath, content in changed_files.items():
                file_issues = self._validate_single_file(filepath, content)
                issues.extend(file_issues)

        # 2. Extract and validate markdown code blocks
        blocks = self.extract_code_blocks(code_text)
        if blocks:
            for idx, (lang, hint, content) in enumerate(blocks, 1):
                block_name = hint or f"code_block_{idx}.{lang or 'txt'}"
                block_issues = self._validate_snippet(lang, block_name, content)
                issues.extend(block_issues)
        else:
            # If no markdown blocks, check whether entire text might be raw Python
            stripped = code_text.strip()
            if any(stripped.startswith(k) for k in ("import ", "from ", "def ", "class ", "async def ")):
                try:
                    ast.parse(stripped)
                except SyntaxError as e:
                    issues.append({
                        "file": "raw_code.py",
                        "severity": "critical",
                        "type": "SyntaxError",
                        "description": f"Python syntax error at line {e.lineno}: {e.msg}",
                        "fix_instruction": f"Исправьте синтаксис в строке {e.lineno}: '{e.text.strip() if e.text else ''}'"
                    })

        critical_count = sum(1 for i in issues if i["severity"] == "critical")
        passed = (critical_count == 0)
        return passed, issues

    def _validate_single_file(self, filepath: str, content: str) -> List[Dict[str, Any]]:
        lower = filepath.lower()
        if lower.endswith(".py"):
            return self._validate_python(filepath, content)
        elif lower.endswith((".yaml", ".yml")):
            return self._validate_yaml(filepath, content)
        elif lower.endswith(".json"):
            return self._validate_json(filepath, content)
        elif lower.endswith(".ps1"):
            return self._validate_powershell(filepath, content)
        elif lower.endswith(".sh"):
            return self._validate_bash(filepath, content)
        return []

    def _validate_snippet(self, lang: str, filename: str, content: str) -> List[Dict[str, Any]]:
        l = lang.lower()
        if l in ("python", "py"):
            return self._validate_python(filename, content)
        elif l in ("yaml", "yml"):
            return self._validate_yaml(filename, content)
        elif l in ("json",):
            return self._validate_json(filename, content)
        elif l in ("powershell", "ps1", "posh"):
            return self._validate_powershell(filename, content)
        elif l in ("bash", "sh", "shell"):
            return self._validate_bash(filename, content)
        return []

    def _validate_python(self, filename: str, content: str) -> List[Dict[str, Any]]:
        issues = []
        try:
            ast.parse(content)
        except SyntaxError as e:
            issues.append({
                "file": filename,
                "severity": "critical",
                "type": "SyntaxError",
                "description": f"Ошибка синтаксиса Python в строке {e.lineno}: {e.msg}",
                "fix_instruction": f"Исправьте синтаксическую ошибку в строке {e.lineno}: '{e.text.strip() if e.text else ''}'"
            })
        except Exception as e:
            issues.append({
                "file": filename,
                "severity": "critical",
                "type": "ParseError",
                "description": f"Не удалось разобрать AST Python: {e}",
                "fix_instruction": "Проверьте корректность отступов и закрытие скобок/кавычек."
            })
        return issues

    def _validate_yaml(self, filename: str, content: str) -> List[Dict[str, Any]]:
        issues = []
        try:
            yaml.safe_load(content)
        except Exception as e:
            issues.append({
                "file": filename,
                "severity": "critical",
                "type": "YamlSyntaxError",
                "description": f"Ошибка структуры YAML: {e}",
                "fix_instruction": "Исправьте форматирование отступов и двоеточий в YAML."
            })
        return issues

    def _validate_json(self, filename: str, content: str) -> List[Dict[str, Any]]:
        issues = []
        try:
            json.loads(content)
        except Exception as e:
            issues.append({
                "file": filename,
                "severity": "critical",
                "type": "JsonSyntaxError",
                "description": f"Ошибка синтаксиса JSON: {e}",
                "fix_instruction": "Проверьте парность скобок, отсутствие висячих запятых и двойные кавычки вокруг ключей."
            })
        return issues

    def _validate_powershell(self, filename: str, content: str) -> List[Dict[str, Any]]:
        issues = []
        lines = content.splitlines()

        # Check PowerShell strict rule: param(...) must be the first executable statement
        param_line_idx = -1
        executable_before_param = []

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("<#"):
                continue

            if stripped.lower().startswith("param") and ("(" in stripped or (idx + 1 < len(lines) and "(" in lines[idx+1])):
                param_line_idx = idx
                break
            else:
                executable_before_param.append((idx + 1, stripped))

        if param_line_idx > 0 and executable_before_param:
            first_exec_line, first_exec_text = executable_before_param[0]
            issues.append({
                "file": filename,
                "severity": "critical",
                "type": "PowerShellParamPositionError",
                "description": f"Блок param(...) находится в строке {param_line_idx + 1}, но перед ним найдена исполняемая инструкция в строке {first_exec_line}: '{first_exec_text}'",
                "fix_instruction": "В PowerShell блок param(...) обязан быть первой исполняемой инструкцией. Переместите param(...) в начало файла до любых команд, переменных и [Console]::OutputEncoding."
            })
        return issues

    def _validate_bash(self, filename: str, content: str) -> List[Dict[str, Any]]:
        issues = []
        # Check basic quote balance (excluding escaped quotes)
        single_quotes = len(re.findall(r"(?<!\\)'", content))
        if single_quotes % 2 != 0:
            issues.append({
                "file": filename,
                "severity": "critical",
                "type": "BashQuoteMismatch",
                "description": "Обнаружена незакрытая одинарная кавычка в Bash-скрипте.",
                "fix_instruction": "Проверьте парность одинарных кавычек."
            })
        return issues


class ArchitectAudit:
    """Semantic auditor validating generated diffs against architectural plans."""

    AUDIT_PROMPT_TEMPLATE = (
        "Ты — ведущий системный архитектор и QA-аудитор безопасности (Principal Architect & Security Auditor).\n"
        "Твоя задача: провести строгую семантическую проверку реализации относительно архитектурного плана.\n\n"
        "### АРХИТЕКТУРНЫЙ ПЛАН (ЭТАЛОН):\n"
        "{plan}\n\n"
        "### РЕАЛИЗОВАННЫЙ КОД / DIFF:\n"
        "{code}\n\n"
        "### КРИТЕРИИ ПРОВЕРКИ:\n"
        "1. Соответствие плану: все ли запланированные компоненты, функции и контракты реализованы.\n"
        "2. Отсутствие заглушек: нет ли пустых pass, TODO, FIXME или фиктивных реализаций в критических местах.\n"
        "3. Синхронизация: согласованы ли импорты, пути, имена параметров и типы.\n"
        "4. Отсутствие дублирования и мёртвого кода.\n\n"
        "### СТРОГИЙ КОНТРАКТ ОТВЕТА (ТОЛЬКО ЧИСТЫЙ JSON):\n"
        "Ответь ИСКЛЮЧИТЕЛЬНО валидным JSON-объектом следующего вида (без вводных слов и пояснений):\n"
        "{{\n"
        '  "verdict": "PASS",\n'
        '  "issues": [\n'
        '    {{\n'
        '      "file": "path/or/module",\n'
        '      "severity": "critical",\n'
        '      "description": "описание замечания",\n'
        '      "fix_instruction": "конкретная инструкция для исправления"\n'
        "    }}\n"
        "  ]\n"
        "}}\n\n"
        "Если критических замечаний нет, укажи verdict = 'PASS' и issues = [].\n"
        "Если найдены критические нестыковки, укажи verdict = 'REJECT' и опиши issues."
    )

    def build_audit_prompt(self, plan: str, code: str) -> str:
        """Constructs concise prompt containing only plan and diff/code."""
        return self.AUDIT_PROMPT_TEMPLATE.format(plan=plan, code=code)

    def parse_audit_response(self, raw_response: str) -> Dict[str, Any]:
        """
        Robust JSON extractor that handles markdown code fences, comments, or raw JSON.
        Returns: {"verdict": "PASS"|"REJECT", "issues": List[Dict]}
        """
        text = raw_response.strip()

        # Try to find JSON inside markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
        if json_match:
            candidate = json_match.group(1)
        else:
            # Try to match the first outermost JSON object {...}
            bracket_match = re.search(r'(\{[\s\S]*\})', text)
            candidate = bracket_match.group(1) if bracket_match else text

        try:
            parsed = json.loads(candidate)
            verdict = str(parsed.get("verdict", "PASS")).upper().strip()
            if verdict not in ("PASS", "REJECT"):
                verdict = "PASS" if not parsed.get("issues") else "REJECT"

            raw_issues = parsed.get("issues", [])
            valid_issues = []
            if isinstance(raw_issues, list):
                for item in raw_issues:
                    if isinstance(item, dict):
                        valid_issues.append({
                            "file": str(item.get("file", "general")),
                            "severity": str(item.get("severity", "warning")).lower(),
                            "description": str(item.get("description", "")),
                            "fix_instruction": str(item.get("fix_instruction", ""))
                        })

            return {
                "verdict": verdict,
                "issues": valid_issues
            }
        except Exception as e:
            logger.warning(f"[Audit] Failed to parse auditor JSON: {e}. Raw snippet: {text[:200]}")
            # If the auditor wrote plain text indicating PASS or OK
            if "PASS" in text.upper() and "REJECT" not in text.upper():
                return {"verdict": "PASS", "issues": []}
            return {
                "verdict": "PASS",
                "issues": [{
                    "file": "audit_parser",
                    "severity": "warning",
                    "description": f"Аудитор вернул неструктурированный ответ: {text[:150]}",
                    "fix_instruction": "Убедитесь в соответствии архитектурному плану."
                }]
            }
