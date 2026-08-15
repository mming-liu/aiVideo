import { readdirSync, readFileSync, statSync } from "node:fs"
import { join, relative } from "node:path"
import { homedir } from "node:os"
import type { Plugin } from "@opencode-ai/plugin"

const CLAUDE_PROJECTS = join(homedir(), ".claude", "projects")
const EDIT_TOOLS = new Set(["Edit", "Write", "MultiEdit", "NotebookEdit"])
const MAX_SESSIONS = 3

type EditSnippet = { old: string; new: string }

type SessionInfo = {
  id: string
  started: number
  updated: number
  prompt: string
  summary: string
  edits: Map<string, number>
  details: Map<string, EditSnippet[]>
  bashCount: number
}

const norm = (p: string) => p.replace(/\\/g, "/")

const truncate = (s: string, n: number) => (s.length <= n ? s : s.slice(0, n) + "…")

const encodeProjectDir = (root: string) => root.replace(/[^a-zA-Z0-9]/g, "-")

function listTranscripts(projectDir: string): string[] {
  const dir = join(CLAUDE_PROJECTS, projectDir)
  let files: string[] = []
  try {
    files = readdirSync(dir)
      .filter((f) => f.endsWith(".jsonl"))
      .map((f) => join(dir, f))
  } catch {
    files = []
  }
  if (!files.length) {
    const lc = projectDir.toLowerCase()
    try {
      for (const d of readdirSync(CLAUDE_PROJECTS)) {
        if (d.toLowerCase() === lc) {
          const real = join(CLAUDE_PROJECTS, d)
          files = readdirSync(real)
            .filter((f) => f.endsWith(".jsonl"))
            .map((f) => join(real, f))
          break
        }
      }
    } catch {
      files = []
    }
  }
  return files
}

function parseSession(path: string, projectRoot: string): SessionInfo | null {
  let raw: string
  try {
    raw = readFileSync(path, "utf8")
  } catch {
    return null
  }
  const info: SessionInfo = {
    id: (path.split(/[\\/]/).pop() ?? "?").replace(/\.jsonl$/i, ""),
    started: Infinity,
    updated: -Infinity,
    prompt: "",
    summary: "",
    edits: new Map(),
    details: new Map(),
    bashCount: 0,
  }
  const root = norm(projectRoot).toLowerCase() + "/"
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim()) continue
    let o: any
    try {
      o = JSON.parse(line)
    } catch {
      continue
    }
    const ts = typeof o.timestamp === "string" ? Date.parse(o.timestamp) : NaN
    if (!isNaN(ts)) {
      if (ts < info.started) info.started = ts
      if (ts > info.updated) info.updated = ts
    }
    const content = o.message?.content
    if (!Array.isArray(content)) continue
    if (o.type === "assistant") {
      for (const blk of content) {
        if (!blk) continue
        if (blk.type === "tool_use") {
          const name = blk.name
          const input = blk.input ?? {}
          if (EDIT_TOOLS.has(name)) {
            const fp = norm(String(input.file_path ?? input.filepath ?? input.notebook_path ?? ""))
            if (fp && fp.toLowerCase().startsWith(root)) {
              info.edits.set(fp, (info.edits.get(fp) ?? 0) + 1)
              let oldStr = ""
              let newStr = ""
              if (name === "Edit" || name === "Write") {
                oldStr = String(input.old_string ?? "")
                newStr = String(input.new_string ?? input.content ?? "")
              } else if (name === "MultiEdit" && Array.isArray(input.edits)) {
                const first = input.edits[0]
                if (first) {
                  oldStr = String(first.old_string ?? "")
                  newStr = String(first.new_string ?? "")
                }
              }
              if (oldStr || newStr) {
                const arr = info.details.get(fp) ?? []
                if (arr.length < 5) {
                  arr.push({ old: truncate(oldStr, 200), new: truncate(newStr, 200) })
                  info.details.set(fp, arr)
                }
              }
            }
          } else if (name === "Bash") {
            info.bashCount += 1
          }
        } else if (blk.type === "text" && typeof blk.text === "string" && blk.text.trim()) {
          info.summary = blk.text
        }
      }
    } else if (o.type === "user") {
      for (const blk of content) {
        if (blk?.type === "text" && typeof blk.text === "string" && blk.text.trim()) {
          const t = blk.text.trim()
          if (t.startsWith("[Request interrupted") || t.startsWith("Interrupted")) continue
          if (!info.prompt) info.prompt = t
        }
      }
    }
  }
  if (info.started === Infinity) info.started = info.updated === -Infinity ? Date.now() : info.updated
  if (info.updated === -Infinity) info.updated = info.started
  return info
}

function relPath(fp: string, root: string): string {
  const r = relative(root, fp)
  return r && !r.startsWith("..") ? norm(r) : norm(fp)
}

function renderInfo(info: SessionInfo, root: string): string {
  const out: string[] = []
  const when = info.updated > 0 ? new Date(info.updated).toLocaleString("zh-CN", { hour12: false }) : "?"
  out.push(`- 会话 ${info.id}（更新于 ${when}）`)
  if (info.prompt) out.push(`  任务: ${truncate(info.prompt.replace(/\s+/g, " ").trim(), 160)}`)
  const edits = [...info.edits.entries()].sort((a, b) => b[1] - a[1])
  if (edits.length) {
    out.push(`  修改 ${edits.length} 个文件:`)
    for (const [fp, cnt] of edits.slice(0, 12)) out.push(`    - ${relPath(fp, root)}（${cnt} 次编辑）`)
    if (edits.length > 12) out.push(`    - ...等 ${edits.length - 12} 个文件`)
  } else {
    out.push(`  未修改项目文件（Bash 调用 ${info.bashCount} 次）`)
  }
  if (info.summary) out.push(`  小结: ${truncate(info.summary.replace(/\s+/g, " ").trim(), 300)}`)
  return out.join("\n")
}

function mergeSessions(root: string): { text: string; details: Map<string, EditSnippet[]> } | null {
  const files = listTranscripts(encodeProjectDir(root))
  if (!files.length) return null
  files.sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)
  const recent = files.slice(0, MAX_SESSIONS)
  const merged: SessionInfo = {
    id: recent.length > 1 ? `最近 ${recent.length} 个` : ((recent[0].split(/[\\/]/).pop() ?? "").replace(/\.jsonl$/i, "").slice(0, 8) || "?"),
    started: Infinity,
    updated: -Infinity,
    prompt: "",
    summary: "",
    edits: new Map(),
    details: new Map(),
    bashCount: 0,
  }
  for (const f of recent) {
    const info = parseSession(f, root)
    if (!info) continue
    if (info.started < merged.started) merged.started = info.started
    if (info.updated > merged.updated) merged.updated = info.updated
    merged.bashCount += info.bashCount
    if (!merged.prompt && info.prompt) merged.prompt = info.prompt
    if (!merged.summary && info.summary) merged.summary = info.summary
    for (const [fp, cnt] of info.edits) merged.edits.set(fp, (merged.edits.get(fp) ?? 0) + cnt)
    for (const [fp, arr] of info.details) {
      const cur = merged.details.get(fp) ?? []
      merged.details.set(fp, cur.concat(arr).slice(0, 5))
    }
  }
  if (merged.edits.size === 0 && !merged.prompt && !merged.summary) return null
  return {
    text: `## Claude Code 最近改动（自动注入的上下文，供参考）\n${renderInfo(merged, root)}`,
    details: merged.details,
  }
}

export const ClaudeCodeBridge: Plugin = async ({ directory, worktree, client }) => {
  const root = directory || worktree || process.cwd()
  const encoded = encodeProjectDir(root)
  let cacheKey = ""
  let cacheReport: ReturnType<typeof mergeSessions> | null = null

  const getReport = () => {
    const files = listTranscripts(encoded)
    if (!files.length) return null
    files.sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)
    const recent = files.slice(0, MAX_SESSIONS)
    const key = recent
      .map((f) => {
        const st = statSync(f)
        return `${f}|${st.mtimeMs}|${st.size}`
      })
      .join(";")
    if (key === cacheKey) return cacheReport
    cacheKey = key
    cacheReport = mergeSessions(root)
    return cacheReport
  }

  try {
    await client.app.log({
      body: { service: "claude-code-bridge", level: "info", message: `ready for ${encoded}` },
    })
  } catch {}

  const hooks: Record<string, any> = {
    "experimental.chat.system.transform": async (_input, output) => {
      const report = getReport()
      if (report) output.system.push(report.text)
    },
  }

  try {
    const mod = await import("@opencode-ai/plugin")
    const tool = mod.tool
    hooks.tool = {
      claude_changes: tool({
        description:
          "读取 Claude Code 会话转录，获取 Claude Code 最近对本项目的文件修改与编辑片段，用于感知已改动内容并接续工作。可用 file 参数只看某个文件的修改。",
        args: {
          sessions: tool.schema.number().optional().describe("回溯最近 N 个会话，默认 3，最大 10"),
          file: tool.schema.string().optional().describe("只看指定文件的修改（绝对路径或项目相对路径）"),
        },
        async execute(args, _ctx) {
          const n = Math.min(Math.max(Math.floor(args.sessions ?? 3), 1), 10)
          const files = listTranscripts(encoded)
          if (!files.length) return "未找到 Claude Code 会话转录。"
          files.sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)
          const recent = files.slice(0, n)
          const out: string[] = []
          const want = args.file ? norm(args.file) : null
          for (const f of recent) {
            const info = parseSession(f, root)
            if (!info) continue
            const edits = [...info.edits.entries()].sort((a, b) => b[1] - a[1])
            if (!edits.length) continue
            const sid = info.id.slice(0, 8)
            const time = info.updated > 0 ? new Date(info.updated).toLocaleString("zh-CN", { hour12: false }) : "?"
            out.push(`\n[会话 ${sid} · ${time}]`)
            for (const [fp, cnt] of edits) {
              const rp = relPath(fp, root)
              if (want) {
                const w = want.toLowerCase()
                if (!fp.toLowerCase().endsWith(w) && !rp.toLowerCase().includes(w)) continue
                const snips = info.details.get(fp) ?? []
                out.push(`- ${rp}（${cnt} 次编辑）`)
                for (const s of snips) {
                  if (s.old) out.push(`  旧: ${s.old}`)
                  if (s.new) out.push(`  新: ${s.new}`)
                }
              } else {
                out.push(`- ${rp}（${cnt} 次编辑）`)
              }
            }
          }
          if (out.length <= 1) {
            return `最近 ${n} 个会话中未找到${want ? `与 "${args.file}" 匹配` : ""}的修改。`
          }
          return out.join("\n")
        },
      }),
    }
  } catch {}

  return hooks
}
