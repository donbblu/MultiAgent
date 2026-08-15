# VisionForge Vue MVP Template

这是 VisionForge 第一阶段使用的固定 Vue 3 页面项目。模型只允许修改
`src/**` 和必要的 `public/**`；依赖、Vite 配置和 Runtime 验收配置由 Harness
保护。

依赖版本固定在 `package.json`。安装依赖和执行构建属于 Runtime 或人工准备步骤，
Agent 不得运行 `install`、修改锁文件或扩大允许路径。
