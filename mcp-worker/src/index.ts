import { createMcpHandler } from "agents/mcp/server";
import { McpServer } from "@modelcontextprotocol/server";

function createServer() {
  const server = new McpServer({
    name: "ESPN Fantasy Sync",
    version: "1.0.0",
  });

  server.registerTool(
    "get_fantasy_state",
    {
      description:
        "Returns the latest sanitized ESPN fantasy football league state, including teams, rosters, matchups, settings, and free agents.",
    },
    async () => {
      const response = await fetch(
        "https://espn-fantasy-sync.pages.dev/fantasy-state.json",
        {
          headers: {
            "Accept": "application/json",
          },
        }
      );

      if (!response.ok) {
        throw new Error(
          `Fantasy state fetch failed with HTTP ${response.status}`
        );
      }

      const state = await response.json();

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(state),
          },
        ],
      };
    }
  );

  return server;
}

const mcpHandler = createMcpHandler(createServer);

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/mcp") {
      return mcpHandler(request);
    }

    if (url.pathname === "/health") {
      return Response.json({
        ok: true,
        service: "espn-fantasy-mcp",
      });
    }

    return new Response("Not found", { status: 404 });
  },
};
