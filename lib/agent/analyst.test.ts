import { describe, expect, it } from "vitest"
import { analyzeQuestion } from "@/lib/agent/analyst"
import { getCatalog } from "@/lib/data/catalog"

const catalog = getCatalog()

describe("analyst", () => {
  it("lets the model judge a ranking from measured rows", async () => {
    const result = await analyzeQuestion(
      "Which New England airports look promising for more capacity?",
      catalog,
      [],
      async (body) => {
        const messages = (body as { messages: Array<{ role: string; content?: string }> }).messages
        const tool = [...messages].reverse().find((message) => message.role === "tool")
        if (!tool?.content) {
          return {
            choices: [
              {
                message: {
                  content: "",
                  tool_calls: [
                    {
                      id: "call-rank",
                      function: {
                        name: "look_up",
                        arguments: JSON.stringify({ region: "new england", metric: "score", terminal: true, limit: 8 }),
                      },
                    },
                  ],
                },
              },
            ],
          }
        }
        const score = (JSON.parse(tool.content) as { rows: Array<{ score: number | null }> }).rows.find((row) => row.score != null)?.score
        return { choices: [{ message: { content: `BOS looks promising on the measured list. Its capacity score is ${score}.` } }] }
      },
    )
    expect(result.refusal).toBe(false)
    expect(result.reply).toContain("BOS looks promising")
    expect(result.rows.length).toBeGreaterThan(0)
  })

  it("drops a reply that adds a figure the files did not return", async () => {
    const result = await analyzeQuestion("Which New England airports look promising?", catalog, [], async (body) => {
      const messages = (body as { messages: Array<{ role: string }> }).messages
      const sawTool = messages.some((message) => message.role === "tool")
      if (!sawTool) {
        return {
          choices: [
            {
              message: {
                content: "",
                tool_calls: [
                  {
                    id: "call-rank",
                    function: { name: "look_up", arguments: JSON.stringify({ region: "new england", metric: "score" }) },
                  },
                ],
              },
            },
          ],
        }
      }
      return { choices: [{ message: { content: "A new terminal would cost 9999999." } }] }
    })
    expect(result.reply).not.toContain("9999999")
    expect(result.notes.join(" ")).toContain("measured answer")
  })

  it("declines when the model says the files do not contain the figure", async () => {
    const result = await analyzeQuestion("What share of Anchorage flights are long haul?", catalog, [], async () => ({
      choices: [
        {
          message: {
            content: "",
            tool_calls: [
              {
                id: "call-no",
                function: {
                  name: "decline",
                  arguments: JSON.stringify({ reason: "Stage length is not in the loaded files, so I cannot give a long-haul share." }),
                },
              },
            ],
          },
        },
      ],
    }))
    expect(result.refusal).toBe(true)
    expect(result.reply).toContain("Stage length")
  })

  it("does not fall back to the rules parser when no model key is set", async () => {
    const previous = process.env.OPENAI_API_KEY
    delete process.env.OPENAI_API_KEY
    const result = await analyzeQuestion("How much freight moved through Anchorage in 2024?", catalog, [])
    if (previous) process.env.OPENAI_API_KEY = previous
    expect(result.refusal).toBe(true)
    expect(result.reply).toContain("OPENAI_API_KEY")
    expect(result.reply).toContain("rules parser")
  })
})
