import { defineTool } from "eve/tools";
import { always } from "eve/tools/approval";
import { z } from "zod";

export default defineTool({
  description: "USER_READABLE_ACTION_DESCRIPTION",
  inputSchema: z.object({
    summary: z.string().describe("审批卡片中展示的操作摘要"),
    impact: z.string().describe("执行该操作可能产生的影响"),
  }),
  approval: always(),
  async execute({ summary, impact }) {
    return { summary, impact, completed: true };
  },
});
