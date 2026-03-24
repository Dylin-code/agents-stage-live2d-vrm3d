/**
 * markdown-it plugin & click handler for local file paths.
 *
 * Detects absolute file paths (e.g. /Users/.../file.py:123) in:
 *   1. Markdown link hrefs  – rewrites the <a> to use data attributes
 *   2. Plain text            – wraps them in clickable <a> tags
 *
 * A companion event-delegation handler sends a POST to the backend
 * `/api/session-bridge/open-file` endpoint to open the file in the
 * user's editor (VS Code / Cursor / system default).
 */
import type MarkdownIt from 'markdown-it'

import { getDefaultServerUrl } from './serverUrl'

// ---------------------------------------------------------------------------
// Pattern helpers
// ---------------------------------------------------------------------------

/** Matches absolute UNIX-like paths, optionally followed by :line or :line:col */
const LOCAL_PATH_RE = /\/(?:Users|home|tmp|var|opt|etc|mnt|srv|root)\/[^\s"'<>)}\]]+/

/** Same pattern but global, for scanning plain-text tokens */
const LOCAL_PATH_GLOBAL_RE = new RegExp(LOCAL_PATH_RE.source, 'g')

const FILE_LINK_CLASS = 'local-file-link'

/** CSS class name exposed for external styling / testing */
export { FILE_LINK_CLASS }

/** Split a path:line:col into { path, line } */
function parsePathAndLine(raw: string): { path: string; line?: number } {
  // Strip trailing punctuation that is unlikely part of a real path
  const cleaned = raw.replace(/[.,;:!?)}\]]+$/, '')
  const match = cleaned.match(/^(.+?):(\d+)(?::\d+)?$/)
  if (match) {
    return { path: match[1], line: parseInt(match[2], 10) }
  }
  return { path: cleaned }
}

// ---------------------------------------------------------------------------
// markdown-it plugin
// ---------------------------------------------------------------------------

/**
 * Register the plugin on a markdown-it instance.
 *
 * 1. Overrides `link_open` to detect local-path hrefs and tag them.
 * 2. Adds a `core` rule that scans text tokens for bare file paths
 *    and wraps them in link tokens.
 */
export function markdownFileLinkPlugin(md: MarkdownIt): void {
  // --- 1. Fix existing markdown links whose href is a local path ----------
  const defaultLinkOpen =
    md.renderer.rules.link_open ??
    function (tokens, idx, options, _env, self) {
      return self.renderToken(tokens, idx, options)
    }

  md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
    const token = tokens[idx]
    const href = token.attrGet('href') ?? ''

    if (LOCAL_PATH_RE.test(href)) {
      const { path, line } = parsePathAndLine(href)
      token.attrSet('href', '#')
      token.attrSet('data-file-path', path)
      if (line !== undefined) token.attrSet('data-file-line', String(line))
      // Preserve existing classes if any
      const existing = token.attrGet('class') ?? ''
      token.attrSet('class', existing ? `${existing} ${FILE_LINK_CLASS}` : FILE_LINK_CLASS)
      token.attrSet('title', `Open in editor: ${path}${line !== undefined ? `:${line}` : ''}`)
    }

    return defaultLinkOpen(tokens, idx, options, env, self)
  }

  // --- 2. Wrap bare file paths in plain text into clickable links ---------
  md.core.ruler.after('inline', 'local_file_links', (state) => {
    for (const blockToken of state.tokens) {
      if (blockToken.type !== 'inline' || !blockToken.children) continue

      // Skip tokens inside code spans / fenced blocks
      const parent = blockToken
      if (parent.type === 'fence' || parent.type === 'code_block') continue

      const newChildren: MarkdownIt.Token[] = []
      let inCode = false

      for (const child of blockToken.children) {
        // Track code_inline boundaries to avoid transforming code content
        if (child.type === 'code_inline') {
          inCode = false
          newChildren.push(child)
          continue
        }
        if (child.type !== 'text' || inCode) {
          newChildren.push(child)
          continue
        }

        const text = child.content
        LOCAL_PATH_GLOBAL_RE.lastIndex = 0
        let lastIndex = 0
        let match: RegExpExecArray | null

        let hasMatch = false
        while ((match = LOCAL_PATH_GLOBAL_RE.exec(text)) !== null) {
          hasMatch = true
          const before = text.slice(lastIndex, match.index)
          if (before) {
            const t = new state.Token('text', '', 0)
            t.content = before
            newChildren.push(t)
          }

          const rawPath = match[0]
          const { path, line } = parsePathAndLine(rawPath)
          // Figure out how much of the rawPath was actually consumed
          // (parsePathAndLine may strip trailing punctuation)
          const consumed = path.length + (line !== undefined ? `:${line}`.length : 0)
          const trailing = rawPath.slice(consumed)

          const openToken = new state.Token('link_open', 'a', 1)
          openToken.attrSet('href', '#')
          openToken.attrSet('data-file-path', path)
          if (line !== undefined) openToken.attrSet('data-file-line', String(line))
          openToken.attrSet('class', FILE_LINK_CLASS)
          openToken.attrSet('title', `Open in editor: ${path}${line !== undefined ? `:${line}` : ''}`)
          newChildren.push(openToken)

          const linkText = new state.Token('text', '', 0)
          linkText.content = `${path}${line !== undefined ? `:${line}` : ''}`
          newChildren.push(linkText)

          const closeToken = new state.Token('link_close', 'a', -1)
          newChildren.push(closeToken)

          if (trailing) {
            const t = new state.Token('text', '', 0)
            t.content = trailing
            newChildren.push(t)
          }

          lastIndex = match.index + rawPath.length
        }

        if (!hasMatch) {
          newChildren.push(child)
        } else {
          const remaining = text.slice(lastIndex)
          if (remaining) {
            const t = new state.Token('text', '', 0)
            t.content = remaining
            newChildren.push(t)
          }
        }
      }

      blockToken.children = newChildren
    }
  })
}

// ---------------------------------------------------------------------------
// Click handler (event delegation)
// ---------------------------------------------------------------------------

/**
 * Attach once to a container element. Intercepts clicks on `.local-file-link`
 * anchors and sends a POST to the backend to open the file.
 */
export function setupFilePathClickHandler(
  container: HTMLElement,
  serverUrl?: string,
): () => void {
  const base = serverUrl ?? getDefaultServerUrl()

  const handler = async (e: Event) => {
    const target = e.target as HTMLElement
    const link = target.closest(`.${FILE_LINK_CLASS}`) as HTMLAnchorElement | null
    if (!link) return

    e.preventDefault()
    e.stopPropagation()

    const path = link.dataset.filePath
    if (!path) return
    const line = link.dataset.fileLine ? parseInt(link.dataset.fileLine, 10) : undefined

    try {
      await fetch(`${base}/api/session-bridge/open-file`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, line }),
      })
    } catch (err) {
      console.warn('[markdownFileLink] failed to open file:', err)
    }
  }

  container.addEventListener('click', handler)
  return () => container.removeEventListener('click', handler)
}
