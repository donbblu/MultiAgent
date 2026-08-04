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

## 模型 API Demo

复制 `.env.example` 为 `.env` 并填写 `MODEL_API_KEY`，然后运行：

```bash
python3 model_demo.py
```

默认配置使用注册表中的 DeepSeek 预设，但 Agent 和 Backend 不依赖该供应商。
Demo 将结果写入被 Git 忽略的 `model_generated_project/`。模型只负责返回
`ImplementationPlan`；Runtime
负责验证和写入。安全边界包括：

- 模型只允许修改 `app.py` 和 `README.md`。
- `.env`、`.git`、`.verification`、`.runs` 为受保护路径。
- 密钥不会进入模型上下文、任务状态或运行记录。
- Runtime 使用独立验收脚本，模型不能修改该脚本。
- 验证器只允许执行 `python3 .verification/test_acceptance.py`。
- 多文件写入先暂存，写入失败时回滚本轮已应用文件。
- API 请求限制超时、重试次数、输出 Token 和响应字节数。

## 通用 CLI

使用自然语言生成一个隔离的 Python 项目：

```bash
python3 coding_agent_cli.py \
  "写一个冒泡排序函数，返回新列表且不修改输入" \
  --name bubble-sort
```

可通过统一参数切换已注册供应商和模型：

```bash
python3 coding_agent_cli.py "需求" \
  --name my-task \
  --provider deepseek \
  --model deepseek-v4-pro
```

核心代码只使用 `ModelClient`、`ModelConfig`、`ModelClientFactory` 和
`StructuredCodingBackend`。供应商差异集中在注册预设与协议适配器中；新增兼容
供应商不需要修改 Coordinator、Coding Agent、Verification Agent 或 Runtime。

角色同样与 Agent 实例剥离。Coordinator 会按执行阶段动态注入 `RoleSpec`：

- `planner`：理解目标和边界，只读。
- `implementer`：首次实现，可在允许路径内写入。
- `tester`：运行白名单验证命令，不能写入；与 reviewer 并行执行。
- `fixer`：根据失败反馈返工，可在允许路径内写入。
- `reviewer`：独立只读审查，与 tester 并行执行。

角色包含职责、能力和约束，不包含模型或供应商信息。同一个通用 Worker 可以被
分配不同角色；Runtime 会检查角色能力，不能只依赖提示词约束。

## Role Memory

每次 Worker 执行前，`MemoryManager` 会从权威 `TaskContext` 创建不可变的
`RoleMemoryView`。Worker 和模型后端只消费这个裁剪后的视图，不直接把完整任务
状态作为模型上下文。

| Role | 项目文件预算 | 反馈 | 验证命令 | 可写结果 |
|---|---:|---|---|---|
| planner | 15,000 字符摘要预算 | 否 | 否 | planning_result |
| implementer | 40,000 字符 | 否 | 否 | implementation_result |
| tester | 不提供项目文件 | 否 | 是 | verification_result |
| fixer | 30,000 字符 | 是 | 否 | implementation_result |
| reviewer | 25,000 字符 | 否 | 否 | review_result |

所有 Memory Policy 默认 `secret_access=False`，尝试为任何角色开启密钥访问都会
在配置阶段被拒绝。项目文件仍先经过敏感文件过滤，再受 Role 预算二次裁剪。
`TaskContext` 继续由 Coordinator 独占更新，RoleMemoryView 仅作为单次执行输入。

## 并行质量阶段

首次实现或返工完成后，Coordinator 同时启动两个只读 Worker：

```text
Implementer / Fixer
        ↓
  ┌─────┴─────┐
  ↓           ↓
Tester     Reviewer
真实测试    模型代码审查
  └─────┬─────┘
        ↓
Coordinator 校验版本并合并
```

两个 Worker 获得相同的 `task_version`，返回不可变 `ResultEnvelope`。Coordinator
只有在任务 ID 和版本均匹配时才合并结果，防止迟到或其他任务的结果覆盖当前状态。
测试和审查都通过才完成任务；任一失败，其结构化反馈都会交给 Fixer。并行阶段只有
读取和白名单命令权限，不允许任何 Worker 写入 Workspace。

输出固定在 `agent-output/<name>/`。默认允许写入 `*.py`、`tests/*.py` 和
`README.md`，并运行 `python3 -m unittest discover -s tests -v`。可以补充标准：

```bash
python3 coding_agent_cli.py "需求" \
  --name my-task \
  --criterion "具体标准一" \
  --criterion "具体标准二"
```

已有输出目录默认拒绝覆盖；只有明确传入 `--continue-existing` 才会继续修改。
通用模式的测试由 Coding Agent 生成，适合功能验证和原型；高风险项目应像
`bubble_sort_demo.py` 一样，由 Runtime 或用户提供独立验收测试。

## 可视化界面

启动本地界面：

```bash
python3 web_server.py
```

然后访问 `http://127.0.0.1:8765`。用户可以直接输入需求，并依次看到：

- planner、implementer、tester、fixer 等角色的接手状态；
- Coordinator 的状态切换和角色交接；
- 实现 Agent 提交的文件变更摘要；
- 验证结果、失败反馈和返工过程；
- 最终生成目录、文件列表、模型及尝试次数。

界面展示的是结构化、可审计的工作事件，不展示模型私有推理。服务仅监听本机
`127.0.0.1`，请求继续使用 CLI 相同的输出目录校验、路径权限和命令白名单。

## 接入真实模型

实现 `CodingBackend` 协议即可：

```python
class MyLLMBackend:
    def create_plan(self, memory):
        # 只使用当前角色获准的 RoleMemoryView
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
