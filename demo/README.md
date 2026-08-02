# Coding Multi-Agent Workflow

这个项目实现了一个可扩展的 Coding Multi-Agent 核心。它接收用户的项目要求，由三个 Agent 协作生成文件、执行真实测试，并根据测试反馈自动返工。

## 三个 Agent

1. **Coordinator**
   - 校验用户目标和验收标准。
   - 管理 `规划 → 编码 → 验证 → 返工/完成` 状态机。
   - 控制最大尝试次数，记录完整执行历史。
   - 实现异常时不会跳过验证或误报成功。

2. **WorkspaceCodingAgent**
   - 调用可插拔 `CodingBackend` 生成结构化 `ImplementationPlan`。
   - 将模型决策与文件执行隔离。
   - 通过受限工作区真正创建或覆盖项目文件。
   - 将验证反馈带入下一轮计划。

3. **CommandVerificationAgent**
   - 在生成项目目录内执行真实验收命令。
   - 捕获退出码、stdout、stderr 和超时。
   - 失败时生成结构化反馈，交回 Coding Agent。
   - 不拥有文件修改职责。

## 第一阶段能力

- `TaskContext` 包含用户原始需求、技术栈、约束、允许路径、禁止操作和假设。
- `PlanValidator` 严格检查模型返回类型、必填字段、重复路径、允许范围和内容规模。
- `ProjectContextBuilder` 根据需求与反馈筛选相关文件，并优先读取项目配置和说明文件。
- `CommandPolicy` 使用可执行程序白名单，并拒绝安装、发布、部署等危险参数。
- `VerificationResult.criteria_results` 为每条验收标准保存通过状态和证据。
- `RunRecorder` 在 `.runs/<task-id>/` 保存 JSONL 事件和最新任务快照。

## 工作流

```text
用户需求 + 验收标准 + 验证命令
                ↓
Coordinator 校验和调度
                ↓
CodingBackend 生成 ImplementationPlan
                ↓
WorkspaceCodingAgent 安全写入项目文件
                ↓
CommandVerificationAgent 运行真实测试
        ├─ 全部通过 → COMPLETED
        └─ 失败反馈 → 下一轮 Coding Agent
                         └─ 超过上限 → FAILED
```

## 运行

无需安装第三方依赖，要求 Python 3.10+：

```bash
cd /Users/donbblu/codex/multiAgent/demo
python3 main.py
```

示例会在 `generated_project/` 创建一个 Python 项目。第一次实现故意遗漏空输入处理，验证失败后第二轮自动修复。

同时会生成运行记录：

```text
.runs/TASK-001/
├── events.jsonl
└── task.json
```

运行框架测试：

```bash
python3 -m unittest discover -s tests -v
```

## 接入真实模型

实现 `CodingBackend` 协议即可：

```python
class MyLLMBackend:
    def create_plan(self, task, context_files):
        # 将 task.model_input() 与筛选后的 context_files 发给模型
        # 校验模型的结构化响应后返回 ImplementationPlan
        return ImplementationPlan(...)
```

然后注入 Agent：

```python
workspace = ProjectWorkspace(Path("目标项目目录"))
coding = WorkspaceCodingAgent(MyLLMBackend(), workspace)
verifier = CommandVerificationAgent(workspace)
result = Coordinator(coding, verifier).run(task)
```

## 当前安全边界

- 文件路径必须是项目内相对路径，拒绝绝对路径和 `..` 路径穿越。
- 验证命令使用参数数组和 `shell=False`，不解析 shell 拼接语句。
- 命令有超时限制，输出和退出码会保留为验证证据。
- 生产环境仍应增加命令白名单、进程/网络隔离、文件数量与大小限制，以及高风险操作审批。
