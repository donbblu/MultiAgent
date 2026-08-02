from __future__ import annotations


class CommandPolicyError(ValueError):
    pass


class CommandPolicy:
    """只允许已登记的确定性验证命令。"""

    DEFAULT_EXECUTABLES = {"python3", "python", "pytest", "npm", "npx", "cargo", "go"}
    FORBIDDEN_ARGUMENTS = {"install", "uninstall", "publish", "deploy", "push"}

    def __init__(self, allowed_executables: set[str] | None = None) -> None:
        self.allowed_executables = allowed_executables or self.DEFAULT_EXECUTABLES

    def validate(self, command: list[str]) -> None:
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise CommandPolicyError("命令必须是非空字符串列表")
        if command[0] not in self.allowed_executables:
            raise CommandPolicyError(f"命令不在白名单: {command[0]}")
        forbidden = self.FORBIDDEN_ARGUMENTS.intersection(part.lower() for part in command[1:])
        if forbidden:
            raise CommandPolicyError(f"验证阶段禁止参数: {', '.join(sorted(forbidden))}")
