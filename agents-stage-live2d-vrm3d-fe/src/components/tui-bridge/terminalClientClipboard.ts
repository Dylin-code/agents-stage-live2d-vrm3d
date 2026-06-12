import type { Terminal } from '@xterm/xterm'

export interface ClientClipboard {
  writeText(text: string): Promise<void>
}

export type TerminalSelectionReader = Pick<Terminal, 'getSelection' | 'hasSelection'>

export function getTerminalSelectionText(terminal: TerminalSelectionReader | null): string {
  if (!terminal || !terminal.hasSelection()) return ''
  return terminal.getSelection()
}

export function shouldHandleTerminalCopyShortcut(event: Pick<KeyboardEvent, 'altKey' | 'ctrlKey' | 'key' | 'metaKey'>): boolean {
  if (event.altKey) return false
  return (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c'
}

export function writeSelectionToClipboardEvent(event: ClipboardEvent, text: string): boolean {
  if (!text || !event.clipboardData) return false
  event.clipboardData.setData('text/plain', text)
  event.preventDefault()
  return true
}

export async function writeSelectionToClientClipboard(
  text: string,
  clipboard: ClientClipboard | undefined = navigator.clipboard,
): Promise<boolean> {
  if (!text || !clipboard?.writeText) return false
  await clipboard.writeText(text)
  return true
}

export function isMouseEventClientSelectionCandidate(event: Pick<MouseEvent, 'altKey' | 'button' | 'ctrlKey' | 'metaKey' | 'shiftKey'>): boolean {
  return event.button === 0 && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
}

export function createForcedSelectionMouseEvent(event: MouseEvent): MouseEvent {
  return new MouseEvent(event.type, {
    altKey: event.altKey,
    bubbles: true,
    button: event.button,
    buttons: event.buttons,
    cancelable: true,
    clientX: event.clientX,
    clientY: event.clientY,
    ctrlKey: event.ctrlKey,
    detail: event.detail,
    metaKey: event.metaKey,
    screenX: event.screenX,
    screenY: event.screenY,
    shiftKey: true,
    view: event.view,
  })
}
