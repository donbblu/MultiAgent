# Coding Multi-Agent 动态调度方案（Plan00）

## 1. 目标

构建一个面向软件开发任务的 Multi-Agent 系统。系统中的 Worker Agent 不绑定固定角色，而是由调度器根据任务依赖、能力要求、空闲状态和资源冲突，为空闲 Agent 动态注入下一阶段的角色与工作内容。

核心原则：

> Agent 是通用执行资源，任务定义临时角色，调度器控制协作，状态库保存事实，独立验证保证质量。

## 2. 适用场景

该方案适用于可以拆分为多个专业步骤、并且需要并行处理或独立验证的 Coding 任务，例如：

- 代码库探索、需求分析和影响范围识别
- 前端、后端或多个模块的并行开发
- 单元测试、集成测试和回归测试
- 代码审查、安全检查和兼容性检查
- 缺陷复现、原因定位、修复与验证
- 大型重构、依赖升级和迁移工作

简单问答、单文件小修改或强串行任务通常使用单 Agent 更高效。

## 3. 总体架构

```text
用户需求
   ↓
Orchestrator：理解目标、拆解任务、维护工作流
   ↓
Task Store / Ready Queue：保存任务、依赖和状态
   ↓
Scheduler：匹配空闲 Agent、能力、权限和工作区
   ├─ 通用 Worker Agent 1
   ├─ 通用 Worker Agent 2
   └─ 通用 Worker Agent 3
          ↓
Artifact Store：代码变更、日志、测试与审查证据
          ↓
Orchestrator：验收、重试、重新规划或交付
```

### 3.1 Orchestrator

Orchestrator 是系统控制面，负责：

- 理解用户目标并定义验收标准
- 将目标拆解为具有依赖关系的任务图
- 创建后续任务并调整优先级
- 判断任务是否可以并行
- 处理失败、阻塞、超时和用户审批
- 汇总最终修改、验证证据和遗留风险

Orchestrator 不应作为普通 Worker 被随机调度，否则系统可能失去全局状态维护者。

### 3.2 Scheduler

Scheduler 负责：

- 查找处于空闲状态的 Agent
- 检查能力、工具、权限和环境是否匹配
- 检查文件、模块和工作区冲突
- 执行职责隔离规则
- 创建任务租约并完成原子分配
- 回收失联、超时或失败的任务

### 3.3 通用 Worker Pool

Worker Agent 没有永久业务角色。它在领取任务后，根据任务包临时成为 Explorer、Planner、Implementer、Tester 或 Reviewer；任务结束后清理临时上下文并重新进入空闲状态。

### 3.4 Task Store 与 Artifact Store

Task Store 保存：

- 用户原始目标与验收条件
- 任务依赖、优先级和状态
- Agent 分配、租约和重试次数
- 权限要求和读写范围

Artifact Store 保存：

- 代码 patch 或 commit
- 测试日志与失败证据
- 审查发现与风险说明
- 每轮任务的结构化输出

## 4. 动态角色模型

角色不属于 Agent，而属于每次调度的任务包：

```text
通用 Agent + 探索任务包 = 本轮 Explorer
通用 Agent + 编码任务包 = 本轮 Implementer
通用 Agent + 验证任务包 = 本轮 Tester
```

系统可以使用以下临时角色：

| 临时角色 | 主要职责 | 默认权限 |
|---|---|---|
| Explorer | 探索代码结构、调用链、规范和风险 | 只读，可运行有限查询 |
| Planner | 制定文件级实施方案和测试策略 | 只读 |
| Implementer | 修改代码并补充相关测试 | 限定目录写入，可运行测试 |
| Tester | 独立验证行为与回归风险 | 只读，可运行测试 |
| Reviewer | 审查正确性、安全性和兼容性 | 只读，可运行检查 |
| Integrator | 合并多个独立变更并解决冲突 | 限定写入，需要更高权限 |

## 5. 任务包协议

调度器必须向 Worker 发送完整、可验证的任务契约，而不是只发送一句角色提示。

```json
{
  "task_id": "task-102",
  "task_type": "test",
  "role": "verification_engineer",
  "objective": "验证登录限流功能",
  "responsibilities": [
    "运行相关测试",
    "覆盖边界场景",
    "提供失败证据"
  ],
  "inputs": {
    "requirement": "连续失败超过 5 次时返回 429",
    "relevant_files": [
      "src/auth/rate-limit.ts",
      "tests/auth/rate-limit.test.ts"
    ],
    "base_revision": "abc123"
  },
  "acceptance_criteria": [
    "第 6 次失败返回 429",
    "窗口结束后可以重新登录",
    "现有认证测试全部通过"
  ],
  "allowed_tools": ["read_file", "run_tests"],
  "read_scope": ["src/auth/**", "tests/auth/**"],
  "write_scope": [],
  "prohibited_actions": [
    "修改业务实现",
    "安装新依赖",
    "推送或部署"
  ],
  "timeout_seconds": 900,
  "max_attempts": 2,
  "expected_output": {
    "status": "passed | failed | blocked",
    "evidence": [],
    "findings": [],
    "recommended_next_tasks": []
  }
}
```

任务包至少需要明确：目标、上下文、验收标准、权限、禁止操作、超时、重试次数和输出格式。

## 6. Agent 与任务状态

### 6.1 Agent 状态

```text
STARTING → IDLE → RESERVED → RUNNING
                              ├─ BLOCKED
                              ├─ FAILED
                              └─ COMPLETED
                                      ↓
                                  RESETTING
                                      ↓
                                    IDLE
```

- `IDLE`：可以接收任务。
- `RESERVED`：已被调度器选中，尚未确认领取。
- `RUNNING`：正在执行任务。
- `BLOCKED`：等待输入、依赖或权限。
- `RESETTING`：清理角色上下文、临时权限和工作区。
- `FAILED`：执行失败，等待重试或重新规划。

`RESERVED` 状态和原子更新用于避免多个调度器同时选中同一个 Agent。

### 6.2 任务状态

```text
PENDING → READY → LEASED → RUNNING
                           ├─ BLOCKED
                           ├─ FAILED
                           ├─ CANCELLED
                           └─ SUCCEEDED
```

任务只有在以下条件满足后才能进入 `READY`：

- 所有前置任务已经成功
- 所需输入和代码版本已经确定
- 必要的用户审批已经完成
- 不存在文件或模块写入冲突
- 所需工具、权限和运行环境可用

## 7. 任务依赖与执行流程

Coding 工作流应表示为 DAG，而不是简单的先进先出队列。

```text
探索代码库
   ↓
制定方案
   ├─ 修改后端 ─┐
   └─ 修改前端 ─┼→ 集成测试 → 独立审查 → 完成
                 ┘
```

典型状态流转：

```text
RECEIVED
  ↓
DISCOVERING
  ↓
PLANNING
  ↓
WAITING_APPROVAL（仅高风险操作）
  ↓
IMPLEMENTING
  ↓
TESTING ──失败──→ IMPLEMENTING
  ↓
REVIEWING ─发现问题→ IMPLEMENTING
  ↓
COMPLETED
```

建议限制循环次数：

- 编码与测试最多循环 3 次
- 相同错误重复出现后触发重新规划
- 超出原始文件或模块范围时返回 Orchestrator
- 无法确定关键需求时请求用户确认

## 8. 调度策略

不要简单选择第一个空闲 Agent。调度器应综合计算匹配分数：

```text
匹配分数 =
  能力匹配度
+ 相关模块上下文收益
+ 历史成功率
+ 资源适配程度
- 文件冲突风险
- 当前负载
- 近期失败惩罚
```

Worker Profile 示例：

```json
{
  "agent_id": "worker-03",
  "status": "idle",
  "capabilities": ["typescript", "python", "testing", "code_review"],
  "available_tools": ["filesystem", "terminal"],
  "security_level": "standard",
  "workspace": "worktree-03",
  "recent_context": ["auth-module"],
  "metrics": {
    "success_rate": 0.91,
    "average_duration_seconds": 420
  }
}
```

基础调度顺序：

1. 选择优先级最高且状态为 `READY` 的任务。
2. 筛选能力、工具和权限匹配的空闲 Agent。
3. 排除存在工作区或文件写入冲突的 Agent。
4. 排除违反职责隔离规则的 Agent。
5. 优先选择具有相关模块上下文的 Agent。
6. 原子创建租约，将 Agent 更新为 `RESERVED`。
7. 注入临时角色并开始执行。

## 9. 租约、心跳与幂等

每次任务分配都应创建 Lease：

```json
{
  "lease_id": "lease-778",
  "task_id": "task-102",
  "agent_id": "worker-03",
  "expires_at": "2026-08-02T14:30:00+08:00",
  "heartbeat_interval_seconds": 30
}
```

Worker 执行时持续发送心跳。发生 Agent 崩溃、心跳超时、执行超时或主动放弃时，调度器回收租约，并根据策略重试任务。

任务重试可能导致重复执行，因此创建提交、PR、外部消息和部署等副作用操作必须使用唯一操作 ID，并确保幂等。

## 10. 工作区与并发隔离

每个 Worker 建议使用独立 Git worktree 或沙箱：

```text
main workspace
├─ worktree-agent-01
├─ worktree-agent-02
└─ worktree-agent-03
```

任务必须声明读写范围：

```json
{
  "task_id": "task-201",
  "read_scope": ["src/auth/**"],
  "write_scope": ["src/auth/login.ts"]
}
```

并发规则：

- 多个只读任务可以并行。
- 修改不同且低耦合的文件可以并行。
- 修改同一文件的任务默认串行。
- 修改强耦合模块时，即使文件不同也应评估冲突。
- 测试任务必须绑定明确的代码版本或 commit。
- Worker 输出 patch 或 commit，由集成流程统一合并。

## 11. 上下文管理

默认采用无状态 Worker：每次只注入当前任务必需的上下文，完成后清理。

为了降低重复读取成本，可以增加模块亲和性：刚完成认证模块探索的 Agent，在不违反职责隔离的情况下，可以优先承担该模块的后续实现任务。

上下文策略：

- Explorer 首先生成相关文件、调用链和约束摘要。
- Orchestrator 只向 Worker 传递必要内容。
- Worker 缺少信息时提出明确的上下文请求。
- 每轮任务结束生成结构化摘要和证据。
- 使用路径和代码版本引用文件，避免使用过期副本。
- 独立验证任务避免继承实现 Agent 的主观结论。

## 12. 职责隔离与质量保证

Agent 可以动态切换角色，但生产者与验证者必须保持独立。

```json
{
  "separation_rules": [
    {
      "producer_task": "implementation",
      "validator_task": "test",
      "must_use_different_agent": true
    },
    {
      "producer_task": "implementation",
      "validator_task": "review",
      "must_use_different_agent": true
    }
  ]
}
```

系统需要记录任务血缘，避免同一个 Agent 既完成实现又执行最终测试或审查。

Reviewer 的输出应包含：

- 具体文件与位置
- 严重程度
- 触发或复现条件
- 对验收标准的影响
- 建议的后续任务

## 13. 权限与安全边界

权限应随任务临时授予，并在任务结束后撤销。

以下操作建议始终要求用户审批：

- 删除文件或数据
- 修改数据库结构
- 安装或升级依赖
- 访问生产数据或密钥
- 推送远程仓库
- 创建、合并 PR
- 部署到测试或生产环境

Orchestrator 负责审批状态；Worker 不得自行扩大任务范围或权限。

## 14. 失败与重试策略

失败应分类处理：

1. **执行失败**：代码、命令或测试失败，创建修复任务。
2. **环境失败**：依赖、权限或测试环境不可用，创建环境处理任务。
3. **规划失败**：任务定义不清或方案错误，返回 Orchestrator 重新拆解。

建议策略：

```text
第一次失败 → 换 Agent 重试，并保留失败证据
第二次同类失败 → 返回 Orchestrator 重新规划
第三次仍失败 → 请求用户介入
```

新 Agent 应看到前次失败的事实和日志，但不必继承全部推理过程，以减少错误思路的锚定效应。

## 15. MVP 实施计划

### 阶段一：最小可用版本

- 1 个固定 Orchestrator
- 3 个通用 Worker Agent
- 基于数据库的 Task Store
- 支持任务依赖、状态和优先级
- 实现 `IDLE / RESERVED / RUNNING / RESETTING`
- 实现 Lease、心跳和超时回收
- 每个 Worker 使用独立工作目录
- 实现文件读写范围检查
- 强制实现者与验证者不是同一个 Agent
- 最多进行两次自动重试

### 阶段二：提高工程质量

- 引入 DAG 可视化与任务血缘
- 增加能力匹配和模块亲和性调度
- 引入 patch/commit 集成流程
- 增加安全、兼容性和性能审查任务
- 增加人工审批节点和审计日志

### 阶段三：优化效率与规模

- 根据历史数据学习调度权重
- 动态扩缩 Worker Pool
- 支持跨仓库和多语言项目
- 增加任务缓存与上下文复用
- 建立成本、延迟和质量之间的调度策略

## 16. 评估指标

系统上线后重点记录：

- 首次测试通过率
- 编码与测试的平均循环次数
- Review 发现的问题数量和严重程度
- 用户接受修改的比例
- 单任务的模型、工具和时间成本
- Agent 空闲率与任务排队时间
- Agent 冲突、重复执行和任务回收次数
- 从需求接收到验证完成的总时长
- 人工介入与审批次数

## 17. 初始技术决策

第一版建议采用以下默认决策：

- 使用固定 Orchestrator 管理全局状态。
- Worker 不绑定永久业务角色。
- 角色通过结构化任务包动态注入。
- Task Store 是任务状态的唯一事实来源。
- 使用租约而不是仅依赖内存中的忙闲状态。
- 每个 Worker 使用独立 worktree 或沙箱。
- 默认禁止两个 Worker 同时修改同一文件。
- 实现与最终验证必须由不同 Agent 完成。
- 高风险或外部副作用操作必须由用户批准。
- 达到失败阈值后重新规划，不进行无限重试。

这套结构能够在保留动态资源调度优势的同时，控制上下文污染、代码冲突、重复副作用和自我验证偏差。
