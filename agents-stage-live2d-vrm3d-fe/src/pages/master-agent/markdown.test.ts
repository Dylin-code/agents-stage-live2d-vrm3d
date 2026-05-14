import { describe, expect, it } from 'vitest'
import { renderMarkdown } from './markdown'

describe('renderMarkdown', () => {
  it('renders a markdown table into HTML', () => {
    const md = [
      '| brand | session | status |',
      '|---|---|---|',
      '| codex | abc | done |',
      '| claude | def | running |',
    ].join('\n')
    const html = renderMarkdown(md)
    expect(html).toContain('<table>')
    expect(html).toContain('<th>brand</th>')
    expect(html).toContain('<td>codex</td>')
  })

  it('escapes raw HTML so LLM output cannot inject elements', () => {
    const html = renderMarkdown('hello <script>alert(1)</script> world')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;')
  })

  it('renders fenced code blocks', () => {
    const html = renderMarkdown('```json\n{"a":1}\n```')
    expect(html).toContain('<pre>')
    expect(html).toContain('<code')
    // markdown-it escapes quotes inside code blocks for safety.
    expect(html).toContain('&quot;a&quot;')
  })

  it('returns empty string for empty input', () => {
    expect(renderMarkdown('')).toBe('')
  })

  it('preserves single newlines as <br> (breaks: true)', () => {
    const html = renderMarkdown('line1\nline2')
    expect(html).toContain('line1')
    expect(html).toContain('line2')
    expect(html).toContain('<br>')
  })
})
