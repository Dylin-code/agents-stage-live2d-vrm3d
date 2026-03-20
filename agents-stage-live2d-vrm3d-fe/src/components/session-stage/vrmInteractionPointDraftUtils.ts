import type { InteractionPointData } from './vrmInteractionPointUtils'

export function normalizeSeatOffset(
  actionType: string,
  seatOffset?: { x: number; y: number; z: number },
): { x: number; y: number; z: number } | undefined {
  if (actionType !== 'sit') return undefined
  return seatOffset
    ? { ...seatOffset }
    : { x: 0, y: 0, z: 0 }
}

export function applyDraftActionType(
  draft: InteractionPointData,
  actionType: string,
): InteractionPointData {
  return {
    ...draft,
    action: {
      ...draft.action,
      type: actionType,
      seatOffset: normalizeSeatOffset(actionType, draft.action.seatOffset),
    },
  }
}
