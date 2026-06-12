import { describe, expect, it, vi } from 'vitest'
import {
  buildTerminalSelectionRange,
  getCopyableTerminalSelectionText,
  getTerminalCellPoint,
  getTerminalSelectionText,
  isMouseEventClientSelectionCandidate,
  shouldHandleTerminalCopyShortcut,
  writeSelectionToClientClipboard,
  writeSelectionToClipboardEvent,
} from './terminalClientClipboard'

describe('terminalClientClipboard', () => {
  it('returns empty text when terminal has no selection', () => {
    const terminal = {
      hasSelection: () => false,
      getSelection: () => 'server text',
    }

    expect(getTerminalSelectionText(terminal)).toBe('')
  })

  it('returns xterm selection text when selected', () => {
    const terminal = {
      hasSelection: () => true,
      getSelection: () => 'client selected text',
    }

    expect(getTerminalSelectionText(terminal)).toBe('client selected text')
  })

  it('prefers live terminal selection over cached selection text', () => {
    const terminal = {
      hasSelection: () => true,
      getSelection: () => 'live text',
    }

    expect(getCopyableTerminalSelectionText(terminal, 'cached text')).toBe('live text')
  })

  it('falls back to cached selection text after xterm clears its live selection', () => {
    const terminal = {
      hasSelection: () => false,
      getSelection: () => '',
    }

    expect(getCopyableTerminalSelectionText(terminal, 'cached text')).toBe('cached text')
  })

  it('handles ctrl/cmd copy shortcuts unless alt is held', () => {
    expect(shouldHandleTerminalCopyShortcut({ key: 'c', ctrlKey: true, metaKey: false, altKey: false })).toBe(true)
    expect(shouldHandleTerminalCopyShortcut({ key: 'C', ctrlKey: false, metaKey: true, altKey: false })).toBe(true)
    expect(shouldHandleTerminalCopyShortcut({ key: 'c', ctrlKey: true, metaKey: false, altKey: true })).toBe(false)
    expect(shouldHandleTerminalCopyShortcut({ key: 'v', ctrlKey: true, metaKey: false, altKey: false })).toBe(false)
  })

  it('writes selection into a copy event clipboard', () => {
    const setData = vi.fn()
    const preventDefault = vi.fn()
    const event = { clipboardData: { setData }, preventDefault } as unknown as ClipboardEvent

    expect(writeSelectionToClipboardEvent(event, 'copy me')).toBe(true)
    expect(setData).toHaveBeenCalledWith('text/plain', 'copy me')
    expect(preventDefault).toHaveBeenCalledTimes(1)
  })

  it('skips copy event handling when no selection exists', () => {
    const setData = vi.fn()
    const preventDefault = vi.fn()
    const event = { clipboardData: { setData }, preventDefault } as unknown as ClipboardEvent

    expect(writeSelectionToClipboardEvent(event, '')).toBe(false)
    expect(setData).not.toHaveBeenCalled()
    expect(preventDefault).not.toHaveBeenCalled()
  })

  it('writes selection text through the browser clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)

    await expect(writeSelectionToClientClipboard('copy me', { writeText })).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith('copy me')
  })

  it('requires an unmodified primary-button mouse event for client selection mode', () => {
    expect(isMouseEventClientSelectionCandidate({ button: 0, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false })).toBe(true)
    expect(isMouseEventClientSelectionCandidate({ button: 1, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false })).toBe(false)
    expect(isMouseEventClientSelectionCandidate({ button: 0, altKey: false, ctrlKey: false, metaKey: false, shiftKey: true })).toBe(false)
  })

  it('maps mouse coordinates to a buffer cell point', () => {
    const point = getTerminalCellPoint(
      { clientX: 25, clientY: 45 },
      {
        cols: 10,
        rows: 5,
        screenRect: { left: 0, top: 0, width: 100, height: 100 },
        viewportY: 20,
      },
    )

    expect(point).toEqual({ col: 2, row: 22 })
  })

  it('clamps mouse coordinates to terminal selection bounds', () => {
    const point = getTerminalCellPoint(
      { clientX: 999, clientY: -10 },
      {
        cols: 10,
        rows: 5,
        screenRect: { left: 0, top: 0, width: 100, height: 100 },
        viewportY: 3,
      },
    )

    expect(point).toEqual({ col: 10, row: 3 })
  })

  it('builds a forward xterm select range', () => {
    expect(buildTerminalSelectionRange({ col: 2, row: 4 }, { col: 8, row: 4 }, 10)).toEqual({
      column: 2,
      row: 4,
      length: 6,
    })
  })

  it('builds a reversed multiline xterm select range', () => {
    expect(buildTerminalSelectionRange({ col: 4, row: 7 }, { col: 2, row: 6 }, 10)).toEqual({
      column: 2,
      row: 6,
      length: 12,
    })
  })

  it('skips zero-length drag selections', () => {
    expect(buildTerminalSelectionRange({ col: 2, row: 4 }, { col: 2, row: 4 }, 10)).toBeNull()
  })
})
