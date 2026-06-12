import type { Terminal } from '@xterm/xterm'

export interface ClientClipboard {
  writeText(text: string): Promise<void>
}

export type TerminalSelectionReader = Pick<Terminal, 'getSelection' | 'hasSelection'>

export interface TerminalCellPoint {
  col: number
  row: number
}

export interface TerminalViewportMetrics {
  cols: number
  rows: number
  screenRect: Pick<DOMRect, 'height' | 'left' | 'top' | 'width'>
  viewportY: number
}

export interface TerminalSelectionRange {
  column: number
  row: number
  length: number
}

export function getTerminalSelectionText(terminal: TerminalSelectionReader | null): string {
  if (!terminal || !terminal.hasSelection()) return ''
  return terminal.getSelection()
}

export function getCopyableTerminalSelectionText(terminal: TerminalSelectionReader | null, cachedSelectionText: string): string {
  return getTerminalSelectionText(terminal) || cachedSelectionText
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

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export function getTerminalCellPoint(
  event: Pick<MouseEvent, 'clientX' | 'clientY'>,
  metrics: TerminalViewportMetrics,
): TerminalCellPoint | null {
  if (metrics.cols <= 0 || metrics.rows <= 0 || metrics.screenRect.width <= 0 || metrics.screenRect.height <= 0) {
    return null
  }

  const cellWidth = metrics.screenRect.width / metrics.cols
  const cellHeight = metrics.screenRect.height / metrics.rows
  if (cellWidth <= 0 || cellHeight <= 0) return null

  const localX = event.clientX - metrics.screenRect.left
  const localY = event.clientY - metrics.screenRect.top
  const oneBasedCol = Math.ceil((localX + cellWidth / 2) / cellWidth)
  const oneBasedRow = Math.ceil(localY / cellHeight)

  return {
    col: clamp(oneBasedCol, 1, metrics.cols + 1) - 1,
    row: clamp(oneBasedRow, 1, metrics.rows) - 1 + metrics.viewportY,
  }
}

export function buildTerminalSelectionRange(
  start: TerminalCellPoint,
  end: TerminalCellPoint,
  cols: number,
): TerminalSelectionRange | null {
  if (cols <= 0) return null
  const startOffset = start.row * cols + start.col
  const endOffset = end.row * cols + end.col
  if (startOffset === endOffset) return null

  const selectionStart = Math.min(startOffset, endOffset)
  const selectionEnd = Math.max(startOffset, endOffset)
  return {
    column: selectionStart % cols,
    row: Math.floor(selectionStart / cols),
    length: selectionEnd - selectionStart,
  }
}
