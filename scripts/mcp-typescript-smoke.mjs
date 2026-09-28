import {
  Client,
  StreamableHTTPClientTransport,
} from "@modelcontextprotocol/client";

const url = process.argv[2];
if (!url) {
  throw new Error("usage: node mcp-typescript-smoke.mjs <mcp-url>");
}

const client = new Client({ name: "workbench-typescript-smoke", version: "1.0.0" });
const transport = new StreamableHTTPClientTransport(new URL(url));

try {
  await client.connect(transport);
  const tools = await client.listTools();
  const resources = await client.listResourceTemplates();
  const requiredTools = [
    "workbench_create_item",
    "workbench_transition_item",
    "workbench_link_items",
    "workbench_prepare_handoff",
    "workbench_capture_idea",
    "workbench_plan_feature",
    "workbench_record_decision",
  ];
  for (const name of requiredTools) {
    if (!tools.tools.some((tool) => tool.name === name)) {
      throw new Error(`missing MCP tool: ${name}`);
    }
  }
  if (
    !resources.resourceTemplates.some(
      (resource) => resource.uriTemplate === "workbench://projects/{project_slug}/context",
    )
  ) {
    throw new Error("missing project context resource template");
  }
  for (const uriTemplate of [
    "workbench://projects/{project_slug}/backlog",
    "workbench://projects/{project_slug}/decisions",
    "workbench://items/{item_id}",
  ]) {
    if (!resources.resourceTemplates.some((resource) => resource.uriTemplate === uriTemplate)) {
      throw new Error(`missing resource template: ${uriTemplate}`);
    }
  }
  process.stdout.write(
    JSON.stringify({ tools: tools.tools.length, resources: resources.resourceTemplates.length }),
  );
} finally {
  await client.close();
}
