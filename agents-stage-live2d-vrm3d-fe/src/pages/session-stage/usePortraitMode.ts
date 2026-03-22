import { onMounted, onUnmounted, ref, watch, type Ref } from 'vue'

const PORTRAIT_MEDIA_QUERY = '(orientation: portrait) and (max-width: 920px)'
const SWIPE_THRESHOLD_PX = 50

export function usePortraitMode(totalSessions: Ref<number>) {
  const isPortraitMode = ref(false)
  const portraitSessionIndex = ref(0)
  const sidebarVisible = ref(false)
  const agentSettingsExpanded = ref(false)

  let mediaQuery: MediaQueryList | null = null

  function handleMediaChange(event: MediaQueryListEvent | MediaQueryList): void {
    isPortraitMode.value = event.matches
    if (!event.matches) {
      sidebarVisible.value = false
    }
  }

  // Clamp index when totalSessions shrinks
  watch(totalSessions, (count) => {
    if (count <= 0) {
      portraitSessionIndex.value = 0
    } else if (portraitSessionIndex.value >= count) {
      portraitSessionIndex.value = count - 1
    }
  })

  onMounted(() => {
    mediaQuery = window.matchMedia(PORTRAIT_MEDIA_QUERY)
    isPortraitMode.value = mediaQuery.matches
    mediaQuery.addEventListener('change', handleMediaChange as EventListener)
  })

  onUnmounted(() => {
    mediaQuery?.removeEventListener('change', handleMediaChange as EventListener)
  })

  // --- Swipe gesture ---
  let swipeStartX = 0
  let swipeStartY = 0
  let isSwiping = false

  function onTouchStart(event: TouchEvent): void {
    if (!isPortraitMode.value) return
    const touch = event.touches[0]
    if (!touch) return
    swipeStartX = touch.clientX
    swipeStartY = touch.clientY
    isSwiping = true
  }

  function onTouchEnd(event: TouchEvent): void {
    if (!isPortraitMode.value || !isSwiping) return
    isSwiping = false
    const touch = event.changedTouches[0]
    if (!touch) return

    const deltaX = touch.clientX - swipeStartX
    const deltaY = touch.clientY - swipeStartY

    // Only trigger horizontal swipe if horizontal movement > vertical
    if (Math.abs(deltaX) < SWIPE_THRESHOLD_PX) return
    if (Math.abs(deltaY) > Math.abs(deltaX)) return

    const max = Math.max(0, totalSessions.value - 1)
    if (deltaX < 0) {
      // Swipe left → next session
      portraitSessionIndex.value = Math.min(portraitSessionIndex.value + 1, max)
    } else {
      // Swipe right → prev session
      portraitSessionIndex.value = Math.max(portraitSessionIndex.value - 1, 0)
    }
  }

  function setupSwipeGesture(el: Ref<HTMLElement | null>): void {
    watch(el, (newEl, oldEl) => {
      if (oldEl) {
        oldEl.removeEventListener('touchstart', onTouchStart as EventListener)
        oldEl.removeEventListener('touchend', onTouchEnd as EventListener)
      }
      if (newEl) {
        newEl.addEventListener('touchstart', onTouchStart as EventListener, { passive: true })
        newEl.addEventListener('touchend', onTouchEnd as EventListener, { passive: true })
      }
    }, { immediate: true })
  }

  return {
    isPortraitMode,
    portraitSessionIndex,
    sidebarVisible,
    agentSettingsExpanded,
    setupSwipeGesture,
  }
}
