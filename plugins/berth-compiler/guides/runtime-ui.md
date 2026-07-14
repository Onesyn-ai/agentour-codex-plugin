# Runtime activity UX

Status messages describe what the user should understand, not the implementation.

Good:

- 正在读取合同附件…
- 正在核对付款条件…
- 正在生成审查报告…
- 等待你的审批

Bad:

- load skill
- tool call: parse_pdf
- runtime booting
- waiting for LLM

Waiting for approval is paused. Do not display running or thinking, and do not count approval wait time as active execution time.
