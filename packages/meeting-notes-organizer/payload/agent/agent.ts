import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { defineAgent } from "eve";

const baseURL = (process.env.AGENTOUR_URL || "https://agentour.ai").replace(/\/$/, "");
const provider = createOpenAICompatible({ name: "agentour", baseURL: `${baseURL}/v1/llm`, apiKey: process.env.AGENTOUR_RUNTIME_TOKEN || "" });

export default defineAgent({ model: provider("gpt-5.6-sol"), modelContextWindowTokens: 1000000 });
