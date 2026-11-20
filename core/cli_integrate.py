"""
CLI Command Interface: `zca integrate` for zero-copy-agent.
Provides terminal subcommands:
  - zca integrate list
  - zca integrate apply <target>
  - zca integrate revert <target>
  - zca integrate print-config <target>
"""

import sys
import argparse
from typing import List, Optional

from core.ide_setup import (
    scan_all,
    configure_targets,
    history_manager,
    test_connection,
    print_config_snippet
)

def cmd_list():
    """Prints table of detected IDEs and AI agents."""
    results = scan_all()
    print("\n" + "=" * 70)
    print("  🚀 zero-copy-agent | Сканирование поддерживаемых IDE и агентов")
    print("=" * 70)
    print(f"  {'Инструмент':<22} | {'Статус':<15} | {'Конфигурация'}")
    print("  " + "-" * 66)
    for key, info in results.items():
        if info["configured"]:
            status = "🟢 Подключен"
        elif info["installed"]:
            status = "🟡 Не настроен"
        else:
            status = "⚪ Не найден"
        name = info["name"]
        details = info["details"]
        print(f"  {name:<22} | {status:<15} | {details}")
    print("=" * 70)
    print("  Команды подключения: zca integrate apply <target>")
    print("  Откат конфигурации:  zca integrate revert <target>")
    print("  Ручная вставка:      zca integrate print-config <target>\n")

def cmd_apply(targets: List[str], dry_run: bool = False):
    """Applies configuration to requested target(s)."""
    print(f"\n🚀 Применение настроек для: {', '.join(targets)}...")
    results = configure_targets(targets, dry_run=dry_run)
    for name, ok, msg in results:
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}: {msg}")
    print("\n✓ Все изменения защищены точками отката (~/.zero-copy-agent/backups).\n")

def cmd_revert(target: str):
    """Rolls back the target from its previous backup."""
    print(f"\n🔄 Выполняю откат конфигурации для: {target}...")
    results = history_manager.rollback(target)
    for line in results:
        print(f"  {line}")
    print()

def cmd_print_config(target: str, port: int = 8000):
    """Prints configuration snippet for manual insertion or env export."""
    print(f"\n📋 Конфигурационный сниппет для [{target}] (шлюз на порту {port}):\n")
    snippet = print_config_snippet(target, port=port)
    print(snippet)
    print()

def cmd_test(target: str, port: int = 8000):
    """Verifies connection to zero-copy-agent gateway."""
    print(f"\n🔍 Проверка связи шлюза для [{target}]...")
    ok, msg = test_connection(target, port=port)
    icon = "✅" if ok else "❌"
    print(f"  {icon} {msg}\n")

def main():
    parser = argparse.ArgumentParser(
        prog="zca integrate",
        description="Двухрежимная интеграция zero-copy-agent с IDE и CLI-агентами"
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Подкоманда интеграции")

    # list
    subparsers.add_parser("list", help="Вывести список обнаруженных IDE и агентов")

    # apply
    parser_apply = subparsers.add_parser("apply", help="Подключить целевую IDE или агента")
    parser_apply.add_argument("target", help="Цель: all, antigravity, vscode, cursor, windsurf, claude, aider")
    parser_apply.add_argument("--dry-run", action="store_true", help="Симуляция без записи на диск")

    # revert
    parser_revert = subparsers.add_parser("revert", help="Откатить конфигурацию целевой IDE из бэкапа")
    parser_revert.add_argument("target", nargs="?", default="all", help="Цель: all, antigravity, vscode, cursor, windsurf, claude, aider")

    # print-config
    parser_print = subparsers.add_parser("print-config", help="Вывести готовый сниппет конфигурации")
    parser_print.add_argument("target", help="Цель: antigravity, vscode, cursor, windsurf, claude, aider")
    parser_print.add_argument("--port", type=int, default=8000, help="Порт локального шлюза")

    # test
    parser_test = subparsers.add_parser("test", help="Проверить связь со шлюзом")
    parser_test.add_argument("target", nargs="?", default="gateway", help="Цель проверки")
    parser_test.add_argument("--port", type=int, default=8000, help="Порт шлюза")

    args = parser.parse_args()

    if args.subcommand == "list" or not args.subcommand:
        cmd_list()
    elif args.subcommand == "apply":
        targets = [args.target] if args.target != "all" else ["all"]
        cmd_apply(targets, dry_run=args.dry_run)
    elif args.subcommand == "revert":
        cmd_revert(args.target)
    elif args.subcommand == "print-config":
        cmd_print_config(args.target, port=args.port)
    elif args.subcommand == "test":
        cmd_test(args.target, port=args.port)

if __name__ == "__main__":
    main()
