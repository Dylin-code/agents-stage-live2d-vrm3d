/**
 * Master-agent markdown renderer.
 *
 * Used by ``MasterChatPanel`` to render assistant + user turn text so
 * LLM output that includes tables / code blocks / lists looks correct
 * instead of a wall of plain text. ``html: false`` is intentional —
 * the content is untrusted (it's whatever the local LLM emits and
 * whatever the user types) so we let markdown-it escape ``<``/``>``
 * for us rather than allowing raw HTML.
 */

import markdownit from 'markdown-it'

let cached: ReturnType<typeof markdownit> | null = null

function getRenderer(): ReturnType<typeof markdownit> {
  if (cached) return cached
  cached = markdownit({
    html: false,
    breaks: true,
    linkify: true,
    typographer: false,
  })
  return cached
}

export function renderMarkdown(text: string): string {
  if (!text) return ''
  try {
    return getRenderer().render(text)
  } catch {
    // markdown-it shouldn't throw on user input but defensively fall
    // back to escaped plain text so a parse glitch never blanks the
    // chat bubble.
    return escapeHtml(text).replace(/\n/g, '<br>')
  }
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
