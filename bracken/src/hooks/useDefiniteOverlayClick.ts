import { useRef, type PointerEvent } from 'react'

export function useDefiniteOverlayClick(onClose: () => void) {
  const startedOnOverlay = useRef(false)

  return {
    onPointerDown: (event: PointerEvent<HTMLElement>) => {
      startedOnOverlay.current = event.target === event.currentTarget
    },
    onPointerUp: (event: PointerEvent<HTMLElement>) => {
      if (startedOnOverlay.current && event.target === event.currentTarget) {
        onClose()
      }
      startedOnOverlay.current = false
    },
    onPointerCancel: () => {
      startedOnOverlay.current = false
    },
  }
}
