# Plan28：功能分支无差异并入 main 的 Git 策略

日期：2026-08-25

讨论主题：在保留 `codex/multimodal-coding-mvp` 全部内容的前提下，让 `main` 与该分支完全一致，并避免无意混入、删除或改写提交历史。

## 目标与背景

用户希望把当前功能分支合入 `main`，最终 `main` 的文件、目录和提交内容必须与功能分支一致；同时希望该分支在 GitHub 上表现为已经合并。讨论重点是直接 merge 的后果、是否需要备份原 `main`、能否依靠历史回滚，以及如何在执行前证明不会产生额外内容。

## 候选方案对比

| 方案 | 核心思路 | 优点 | 缺点、成本与风险 | 适用条件 |
|---|---|---|---|---|
| 普通 `git merge` | 让 Git 自动选择 Fast-forward 或三方合并 | 命令简单；无分叉时自动快进 | 有分叉时可能生成 merge commit、冲突或合并双方内容，不能预先保证结果目录树与功能分支完全一致 | 接受 Git 自动合并结果，且已检查分支关系 |
| `git merge --ff-only` | 仅当 `main` 是功能分支祖先时移动 `main` 指针 | 不改写历史、不制造 merge commit；结果天然与功能分支同一提交、同一目录树 | 如果 `main` 与功能分支已经分叉会直接失败，需要另选策略 | `main` 是功能分支祖先，本次实际条件 |
| 用功能分支强制覆盖 `main` | reset 本地 `main` 后 force push | 即使分叉也能令远端 `main` 指向目标提交 | 会改写远端分支历史，可能覆盖他人提交和保护规则；恢复与协作风险高 | 只有明确批准历史重写且无安全替代时 |
| 先建立备份分支再整合 | 给旧 `main` 创建额外引用后再 merge 或覆盖 | 旧指针更直观、恢复方便 | 增加分支管理成本；在不改写历史的快进场景不是必需 | 分支关系不清、计划强推或需要显式审计标签时 |

## 最终选择

选择 `git merge --ff-only codex/multimodal-coding-mvp`。执行前先确认工作区干净、远端地址和当前分支正确，并验证 `origin/main` 是目标功能分支的祖先；随后切换到 `main`、仅快进拉取远端、仅快进合并功能分支，最后普通推送 `main`。

实际结果：`main`、`origin/main`、本地与远端 `codex/multimodal-coding-mvp` 均指向提交 `f66e71e02c206dd361f18f58f669824ae7de6cab`，目录树均为 `b86e823c665f68a3a6968b21fad58d60c26c96e0`。

## 选择理由

- 祖先检查证明不存在需要调和的双边历史，Fast-forward 可以满足“最终内容与当前功能分支完全一致”。
- 该方案只移动分支指针，不新增 merge commit、不删除提交、不修改文件内容，也不需要 force push。
- 原 `main` 提交仍是新 `main` 历史中的祖先，可通过提交哈希定位，因此本次无需额外备份分支。
- 普通 merge 虽会在当前条件下得到同样结果，但 `--ff-only` 把“不得意外产生三方合并”变成命令级门禁。

## 架构或流程

```text
检查 clean worktree / origin / branch
  → fetch origin
  → 验证 origin/main 是 feature ancestor
  → switch main
  → pull --ff-only origin main
  → merge --ff-only feature
  → 对比 commit hash 与 tree hash
  → push origin main
  → 验证远端引用
```

## 执行步骤

1. 读取 `git status`、当前分支和 `origin`，确认没有未归属修改或错误远端。
2. 获取远端引用，并用 merge-base 祖先检查确认快进条件。
3. 切换到 `main`，使用 `git pull --ff-only origin main` 同步远端。
4. 使用 `git merge --ff-only codex/multimodal-coding-mvp`，任何分叉都应停止而不是自动三方合并。
5. 比较两个分支的 commit hash、tree hash，并确认功能分支已经是 `main` 的祖先。
6. 使用普通 `git push origin main` 推送，不使用 force；推送后再次确认本地与远端引用一致。

## 约束与风险

- 禁止使用 `reset --hard`、force push、rebase、amend 或删除分支来伪造合并结果。
- 工作区不干净、远端不匹配或祖先检查失败时必须停止。
- Fast-forward 不会在 Network 图上产生独立 merge 节点；两个分支可能重叠在同一提交上，这是正确结果而不是合并失败。
- GitHub 贡献图只统计默认分支等满足条件的提交，并可能延迟刷新；显示问题不能作为重新改写历史的理由。

## 待验证事项

- GitHub 页面完成缓存刷新后，`main` 和功能分支是否仍显示相同最新提交。
- 提交作者邮箱 `1361260084@qq.com` 是否已关联并验证到用户的 GitHub 账号，以确保历史提交被归入个人贡献图。

## 待办事项

- 等待 GitHub 最长 24 小时刷新贡献统计；若仍缺失，优先检查提交邮箱关联与私有贡献显示设置。
- 功能分支暂不删除；确认不再需要独立引用后，再由用户单独决定是否清理本地或远端分支。
