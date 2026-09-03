import { defineEval } from "eve/evals";

export default [defineEval({
  description: "会议纪要核心回归",
  tags: ["agentour-production-regression"],
  async test(t) {
    await t.send("项目复盘：王五周三前提交报告。风险是数据缺失。");
    t.messageIncludes("行动项").gate();
  },
})];
