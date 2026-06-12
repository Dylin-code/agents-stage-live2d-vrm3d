import { describe, expect, it, vi } from 'vitest'
import {
  createForcedSelectionMouseEvent,
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

  it('creates a synthetic mouse event that forces xterm selection', () => {
    const original = new MouseEvent('mousedown', {
      bubbles: true,
      button: 0,
      buttons: 1,
      clientX: 20,
      clientY: 30,
    })

    const forced = createForcedSelectionMouseEvent(original)

    expect(forced.type).toBe('mousedown')
    expect(forced.shiftKey).toBe(true)
    expect(forced.button).toBe(0)
    expect(forced.buttons).toBe(1)
    expect(forced.clientX).toBe(20)
    expect(forced.clientY).toBe(30)
  })
})
