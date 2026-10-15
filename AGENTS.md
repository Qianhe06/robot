# Filesystem access

- The project folder is `/home/user/桌面/robot/detection`.
- Reading any file or directory outside the project folder requires the user's explicit approval before access.
- Writing, editing, creating, moving, or deleting any file or directory outside the project folder requires the user's explicit approval before the operation.
- Do not infer approval from filesystem permissions or tool availability. Ask the user first and identify the exact external path and intended operation.

# Codex 编程规则

## 1. 基本原则

- 优先简单、直接、可读的实现，避免过度设计。
- 遵循现有架构，不随意增加抽象层、包装函数或新文件。
- 优先修改已有实现，及时清理确认无用的旧代码。
- 不为了假设中的未来需求添加兼容性代码。

## 2. 错误处理

- 只处理有明确依据、确实可能发生的异常。
- 开发阶段优先暴露错误，禁止用静默兜底掩盖问题。
- 不随意添加 try-except、重试、默认值或多层 if 判断。
- 涉及外部输入、硬件安全和资源释放时，保留必要保护。

## 3. 修改流程

- 修改前先理解相关代码、调用链和问题根因。
- 优先解决根本问题，禁止通过不断叠加补丁绕过问题。
- 同一问题修复两次仍失败，停止修改，重新分析根因和已有补丁。
- 修改时允许删除冗余代码，但不擅自删除仍被依赖的功能。
- 只修改当前任务相关内容，不顺手重构无关模块。

## 4. 沟通与验证

- 使用通俗中文，避免堆砌术语。
- 修改前简述原因和方案；简单修改可直接执行。
- 修改后运行相关测试，报告实际结果，不声称未经验证的成功。
- 简要说明改了什么、为什么改、是否引入额外复杂度。
- 发现架构问题时先提出建议，不擅自进行大规模重构。
