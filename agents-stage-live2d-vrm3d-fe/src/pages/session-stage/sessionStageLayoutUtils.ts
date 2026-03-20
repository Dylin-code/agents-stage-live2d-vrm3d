export function chooseGrid(count: number, width: number, height: number): { cols: number; rows: number } {
  const targetRatio = width / Math.max(1, height)
  let best = { cols: 1, rows: count }
  let bestScore = Number.POSITIVE_INFINITY
  for (let cols = 1; cols <= count; cols += 1) {
    const rows = Math.ceil(count / cols)
    const empty = cols * rows - count
    const ratio = cols / rows
    const score = empty * 3 + Math.abs(targetRatio - ratio)
    if (score < bestScore) {
      bestScore = score
      best = { cols, rows }
    }
  }
  return best
}

export function splitRows(count: number, rows: number, cols: number): number[] {
  const result = Array.from({ length: rows }, () => 0)
  let remaining = count
  for (let row = 0; row < rows; row += 1) {
    const rowsLeft = rows - row
    const rowCount = Math.min(cols, Math.ceil(remaining / rowsLeft))
    result[row] = rowCount
    remaining -= rowCount
  }
  return result
}

export function getSeatPosition(args: {
  seatIndex: number
  bounds: { left: number; top: number; width: number; height: number }
  rows: number
  rowCounts: number[]
}): { x: number; y: number } {
  const { seatIndex, bounds, rows, rowCounts } = args
  const cellHeight = bounds.height / rows
  let row = 0
  let indexInRow = seatIndex
  while (row < rowCounts.length && indexInRow >= rowCounts[row]) {
    indexInRow -= rowCounts[row]
    row += 1
  }
  if (row >= rowCounts.length) {
    row = rowCounts.length - 1
    indexInRow = Math.max(0, rowCounts[row] - 1)
  }
  const rowCount = Math.max(1, rowCounts[row] || 1)
  const cellWidth = bounds.width / Math.max(1, rowCount)
  const rowStartX = bounds.left + (bounds.width - cellWidth * rowCount) / 2
  const x = rowStartX + (indexInRow + 0.5) * cellWidth
  const rowDepthBias = rows <= 1 ? 0.9 : rows === 2 ? 0.84 : rows === 3 ? 0.79 : 0.75
  const y = bounds.top + (row + rowDepthBias) * cellHeight
  return { x, y }
}

export function getStageBounds(args: {
  fullWidth: number
  fullHeight: number
  isDesktop: boolean
  focusChatMode: boolean
  sessionSidebarWidth: number
  chatDockWidth: number
  chatDockHeight: number
  chatModalVisible: boolean
  stageLeftPadding: number
  stageTopPadding: number
  stageBottomPadding: number
}): { left: number; top: number; width: number; height: number } {
  const {
    fullWidth,
    fullHeight,
    isDesktop,
    focusChatMode,
    sessionSidebarWidth,
    chatDockWidth,
    chatDockHeight,
    chatModalVisible,
    stageLeftPadding,
    stageTopPadding,
    stageBottomPadding,
  } = args
  const showDesktopRightChat = isDesktop && focusChatMode
  const sidebarReserved = isDesktop && !focusChatMode ? Math.max(200, sessionSidebarWidth + 18) : 0
  const desiredChatReserved = showDesktopRightChat ? Math.max(380, chatDockWidth + 28) : 0
  const maxRightReserved = Math.max(0, fullWidth - stageLeftPadding - 220 - sidebarReserved)
  const chatRightReserved = Math.min(desiredChatReserved, maxRightReserved)
  const chatBottomReserved = chatModalVisible && !isDesktop
    ? Math.min(Math.max(260, chatDockHeight + 12), Math.floor(fullHeight * 0.58))
    : 0
  const left = stageLeftPadding
  const right = stageLeftPadding + sidebarReserved + chatRightReserved
  const top = stageTopPadding
  const width = Math.max(220, fullWidth - left - right)
  const bottom = stageBottomPadding + chatBottomReserved
  const height = Math.max(220, fullHeight - stageTopPadding - bottom)
  return { left, top, width, height }
}
