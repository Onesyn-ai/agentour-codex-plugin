import { defineTool } from "eve/tools";
import { z } from "zod";

export default defineTool({
  description: "USER_READABLE_TOOL_DESCRIPTION",
  inputSchema: z.object({
    input: z.string().describe("USER_READABLE_INPUT_DESCRIPTION"),
  }),
  async execute({ input }) {
    return { result: input };
  },
});
