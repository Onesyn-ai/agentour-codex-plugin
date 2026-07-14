import { createDeepSeek } from "@ai-sdk/deepseek";
import { defineAgent } from "eve";

const berthURL = process.env.BERTH_URL?.replace(/\/$/, "");
const runtimeKey = process.env.BERTH_RUNTIME_KEY;

if (!berthURL) {
  throw new Error("BERTH_URL is required; refusing to fall back to localhost");
}
if (!runtimeKey) {
  throw new Error("BERTH_RUNTIME_KEY is required");
}

const provider = createDeepSeek({
  baseURL: `${berthURL}/v1/llm`,
  apiKey: runtimeKey,
});

export default defineAgent({
  model: provider("MODEL_ID"),
  modelContextWindowTokens: 1_000_000,
  system: `ROLE_AND_INSTRUCTIONS`,
});
