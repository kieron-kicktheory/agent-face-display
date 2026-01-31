import { writeFileSync, mkdirSync, readFileSync, renameSync } from "fs";
import { dirname, basename } from "path";

// Tool name → face state mapping
const TOOL_STATE_MAP: Record<string, string> = {
  message: "composing",
  tts: "composing",
  web_search: "searching",
  web_fetch: "reading",
  Read: "reading",
  Edit: "coding",
  Write: "coding",
  exec: "executing",
  process: "executing",
  browser: "searching",
  memory_search: "thinking",
  memory_get: "reading",
  image: "thinking",
  sessions_spawn: "executing",
  sessions_send: "composing",
  sessions_list: "reading",
  sessions_history: "reading",
  cron: "executing",
  gateway: "executing",
  canvas: "coding",
};

// Channel ID → friendly name
const CHANNEL_NAMES: Record<string, string> = {
  "1465019963327647918": "#football-news",
  "1465086874509906154": "#ai-chat",
  "1465029440118460426": "#off-topic",
  "1467167690702459065": "#football-history",
  "1467195712008618278": "#team-updates",
  "1467156074636247104": "#team",
};

function truncate(s: string, max: number): string {
  if (!s || s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}

function resolveChannelName(target: string): string {
  if (!target) return "Discord";
  const clean = target.replace(/^channel:/, "");
  if (CHANNEL_NAMES[clean]) return CHANNEL_NAMES[clean];
  for (const [id, name] of Object.entries(CHANNEL_NAMES)) {
    if (target.includes(id)) return name;
  }
  return "Discord";
}

let _agentName: string | null = null;
let _signalPath: string | null = null;

function getAgentName(): string {
  if (_agentName) return _agentName;
  try {
    const config = JSON.parse(readFileSync(process.env.HOME + "/.agent-face/config.json", "utf-8"));
    _agentName = config?.agent?.name ?? "unknown";
    return _agentName!;
  } catch { return "unknown"; }
}

function getSignalPath(): string {
  if (_signalPath) return _signalPath;
  try {
    const config = JSON.parse(readFileSync(process.env.HOME + "/.agent-face/config.json", "utf-8"));
    _signalPath = config?.statusFile ?? "/tmp/clawdbot/agent-status.json";
    return _signalPath!;
  } catch { return "/tmp/clawdbot/agent-status.json"; }
}

function writeSignal(state: string, detail: string): void {
  const signalPath = getSignalPath();
  const payload = JSON.stringify({
    agent: getAgentName(),
    state,
    detail,
    ts: Math.floor(Date.now() / 1000),
  });
  try {
    mkdirSync(dirname(signalPath), { recursive: true });
    const tmpPath = signalPath + ".tmp";
    writeFileSync(tmpPath, payload);
    renameSync(tmpPath, signalPath);
  } catch { /* best effort */ }
}

function detailFromToolName(toolName: string): string {
  const map: Record<string, string> = {
    message: "Writing on Discord",
    tts: "Converting text to speech",
    web_search: "Searching the web",
    web_fetch: "Reading a webpage",
    Read: "Reading a file",
    Edit: "Editing code",
    Write: "Writing a file",
    exec: "Running a command",
    process: "Managing a process",
    browser: "Using the browser",
    memory_search: "Searching memory",
    memory_get: "Reading memory",
    image: "Analysing an image",
    sessions_spawn: "Spawning a sub-agent",
    sessions_send: "Messaging another session",
    sessions_list: "Checking sessions",
    sessions_history: "Reading session history",
    cron: "Managing cron jobs",
    gateway: "Gateway operation",
    canvas: "Working on canvas",
  };
  return map[toolName] ?? `Using ${toolName}`;
}

export default function register(api: any) {
  // tool_result_persist is the only tool hook that's actually wired up.
  // It fires synchronously after each tool call with the tool name.
  // We use it to signal the face with what tool just ran.
  api.on("tool_result_persist", (event: any, ctx: any) => {
    const toolName = event?.toolName ?? ctx?.toolName;
    if (!toolName) return;

    const state = TOOL_STATE_MAP[toolName] ?? "thinking";
    const detail = detailFromToolName(toolName);
    writeSignal(state, detail);

    // Don't modify the message — return undefined
    return undefined;
  });
}
