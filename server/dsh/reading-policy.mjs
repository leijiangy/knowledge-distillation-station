// Cordis public hooks only. The official web plugin owns fetching and parsing.
export const name = 'reading-policy';
export const inject = ['tools', 'llm'];

export function apply(ctx) {
  const inputBudget = 100000;
  const outputBudget = 20000;
  let inputUsed = 0, outputUsed = 0, calls = 0, fetches = 0;
  ctx.on('tools/pre-execute', async (exec, next) => {
    if (exec.name !== 'web_fetch') throw new Error('READING_TOOL_NOT_ALLOWED');
    if (++fetches > 4) throw new Error('已达到资料读取上限，请依据已取得的资料完成解释。');
    return next();
  });
  ctx.on('llm/stream', async function* (options, next) {
    // Conservative byte bound used only for admission, never for settlement.
    const inputBound = Buffer.byteLength(JSON.stringify({
      messages: options.messages, tools: options.tools, system: options.system,
    }), 'utf8') + 4096;
    if (++calls > 5 || !Number.isSafeInteger(options.maxTokens)
        || inputUsed + inputBound > inputBudget
        || outputUsed + options.maxTokens > outputBudget) {
      throw new Error('READING_BUDGET_EXCEEDED');
    }
    let usage;
    for await (const chunk of next()) {
      if (chunk.type === 'usage') usage = chunk.usage;
      yield chunk;
    }
    const cacheRead = usage?.cacheReadTokens ?? 0;
    if (!usage || ![usage.inputTokens, usage.outputTokens, usage.totalTokens, cacheRead]
        .every(value => Number.isSafeInteger(value) && value >= 0)
        || usage.totalTokens !== usage.inputTokens + cacheRead + usage.outputTokens) {
      throw new Error('READING_USAGE_UNAVAILABLE');
    }
    inputUsed += usage.inputTokens + cacheRead;
    outputUsed += usage.outputTokens;
    if (inputUsed > inputBudget || outputUsed > outputBudget) {
      throw new Error('READING_BUDGET_EXCEEDED');
    }
  });
}
