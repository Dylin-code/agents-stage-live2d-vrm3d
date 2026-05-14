/**
 * Parse special master-agent chat commands and flags.
 *
 * Recognized:
 *
 * ```
 *   #new                → start a new conversation
 *   #new <text...>      → start a new conversation AND send <text> as
 *                         the first user message
 *   #full               → flag this message as permitting
 *                         ``permission_mode=full`` (no sandbox);
 *                         can appear anywhere in the text, gets
 *                         stripped from the message before sending
 * ```
 *
 * Flags compose with commands — e.g. ``#new #full do X`` starts a
 * new conversation, opens the full-access gate, and sends ``do X``
 * as the first user message.
 */

export type MasterChatCommand = 'new' | null

export interface ParsedChatCommand {
  command: MasterChatCommand
  remainder: string
  /** True when the user typed ``#full`` somewhere in the message;
   *  unlocks ``permission_mode=full`` on the backend for this turn. */
  permitFullAccess: boolean
}

const NEW_COMMAND_RE = /^#new(?:\b|$)\s*/i
const FULL_FLAG_RE = /(?:^|\s)#full\b/i
const FULL_FLAG_STRIP_RE = /(?:^|\s)#full\b\s*/gi

function extractFullFlag(text: string): { permitFullAccess: boolean; stripped: string } {
  if (!FULL_FLAG_RE.test(text)) {
    return { permitFullAccess: false, stripped: text }
  }
  return {
    permitFullAccess: true,
    stripped: text.replace(FULL_FLAG_STRIP_RE, ' ').replace(/\s+/g, ' ').trim(),
  }
}

export function parseChatCommand(rawInput: string): ParsedChatCommand {
  const trimmed = rawInput.trim()
  const newMatch = trimmed.match(NEW_COMMAND_RE)
  if (newMatch) {
    const afterNew = trimmed.slice(newMatch[0].length).trim()
    const { permitFullAccess, stripped } = extractFullFlag(afterNew)
    return { command: 'new', remainder: stripped, permitFullAccess }
  }
  const { permitFullAccess, stripped } = extractFullFlag(trimmed)
  return { command: null, remainder: stripped, permitFullAccess }
}
